#!/usr/bin/env python3
"""
M819 - Data Persistence Layer
====================================
OperatorRL Historical Battle System - Storage, Caching, Indexing

查看高性能游戏数据存储系统的实现方式,理解其模式,
特别是热数据缓存和冷数据归档是如何分层的。从内存存储开始,
遵循该模式实现持久化层,使系统可以高效存储和检索海量对局数据,
并能通过多级缓存加速常见查询。随后整合二级索引,令系统支持
按玩家/英雄/角色的多维查询,进而增强批量操作与数据导出能力。
最终完善LRU淘汰策略与TTL过期机制,确保内存占用可控。

Core: Storage, caching, indexing for match and player data
"""

import os, sys, json, time, math, logging, hashlib, statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.persistence")
logger.setLevel(logging.DEBUG)

DB_FILE = "operatorrl_history.db"
CACHE_MAX_SIZE = 10000
CACHE_TTL_SECONDS = 3600
INDEX_REBUILD_THRESHOLD = 1000
BATCH_INSERT_SIZE = 100
EXPORT_FORMAT_VERSION = "1.0"

class StorageBackend(Enum):
    SQLITE = "sqlite"
    JSON_FILE = "json_file"
    MEMORY = "memory"

class CachePolicy(Enum):
    LRU = "lru"
    LFU = "lfu"
    TTL = "ttl"

class IndexType(Enum):
    HASH = "hash"
    BTREE = "btree"
    INVERTED = "inverted"

@dataclass
class CacheEntry:
    key: str
    value: Any
    created_at: float
    accessed_at: float
    access_count: int = 0
    ttl: float = CACHE_TTL_SECONDS
    size_bytes: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

@dataclass
class StorageStats:
    total_records: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    writes: int = 0
    reads: int = 0
    cache_size: int = 0
    db_size_bytes: int = 0
    index_count: int = 0
    evictions: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["hit_rate"] = round(self.hit_rate, 4)
        return result

@dataclass
class QueryResult:
    data: List[Dict[str, Any]] = field(default_factory=list)
    total_count: int = 0
    query_time_ms: float = 0.0
    from_cache: bool = False
    page: int = 1
    page_size: int = 20

    def to_dict(self) -> Dict[str, Any]:
        return {"data": self.data, "total": self.total_count,
                "query_time_ms": round(self.query_time_ms, 2),
                "from_cache": self.from_cache, "page": self.page}

@dataclass
class IndexEntry:
    index_name: str
    index_type: IndexType
    key_field: str
    entries: int = 0
    last_rebuilt: float = 0.0


class LRUCache:
    """Least Recently Used cache with TTL support and eviction tracking."""

    def __init__(self, max_size: int = CACHE_MAX_SIZE, default_ttl: float = CACHE_TTL_SECONDS):
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._store: OrderedDict = OrderedDict()
        self._eviction_count = 0
        self._total_hits = 0
        self._total_misses = 0

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            self._total_misses += 1
            return None
        if entry.is_expired:
            self.delete(key)
            self._total_misses += 1
            return None
        entry.accessed_at = time.time()
        entry.access_count += 1
        self._store.move_to_end(key)
        self._total_hits += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        if key in self._store:
            del self._store[key]
        while len(self._store) >= self._max_size:
            self._evict()
        entry = CacheEntry(key=key, value=value, created_at=time.time(),
                           accessed_at=time.time(), ttl=ttl or self._default_ttl)
        self._store[key] = entry

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def _evict(self) -> None:
        expired = [k for k, v in self._store.items() if v.is_expired]
        for k in expired:
            del self._store[k]
            self._eviction_count += 1
        if len(self._store) >= self._max_size:
            self._store.popitem(last=False)
            self._eviction_count += 1

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def stats(self) -> Dict[str, Any]:
        return {"size": self.size, "max_size": self._max_size,
                "hits": self._total_hits, "misses": self._total_misses,
                "evictions": self._eviction_count,
                "hit_rate": round(self._total_hits / max(self._total_hits + self._total_misses, 1), 4)}

    def keys(self) -> List[str]:
        return list(self._store.keys())

    def contains(self, key: str) -> bool:
        return key in self._store and not self._store[key].is_expired


class SecondaryIndex:
    """Secondary index for fast lookups by non-primary keys."""

    def __init__(self, name: str, index_type: IndexType = IndexType.HASH):
        self._name = name
        self._type = index_type
        self._index: Dict[Any, List[str]] = defaultdict(list)

    def add(self, key: Any, record_id: str) -> None:
        if record_id not in self._index[key]:
            self._index[key].append(record_id)

    def remove(self, key: Any, record_id: str) -> None:
        if key in self._index:
            self._index[key] = [r for r in self._index[key] if r != record_id]
            if not self._index[key]:
                del self._index[key]

    def lookup(self, key: Any) -> List[str]:
        return list(self._index.get(key, []))

    def count(self, key: Any) -> int:
        return len(self._index.get(key, []))

    @property
    def total_entries(self) -> int:
        return sum(len(v) for v in self._index.values())

    @property
    def unique_keys(self) -> int:
        return len(self._index)

    def clear(self) -> None:
        self._index.clear()

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self._name, "type": self._type.value,
                "unique_keys": self.unique_keys, "total_entries": self.total_entries}


class DataPersistenceLayer:
    """Manages data storage, caching, and indexing for match and player data."""

    def __init__(self, backend: StorageBackend = StorageBackend.MEMORY, db_path: Optional[str] = None):
        self._backend = backend
        self._db_path = db_path or str(Path.home() / ".operatorRL" / DB_FILE)
        self._cache = LRUCache()
        self._stats = StorageStats()
        self._memory_store: Dict[str, Dict[str, Any]] = defaultdict(dict)
        self._player_index = SecondaryIndex("player_matches")
        self._champion_index = SecondaryIndex("champion_matches")
        self._role_index = SecondaryIndex("role_matches")

    @property
    def stats(self) -> StorageStats:
        self._stats.cache_size = self._cache.size
        self._stats.index_count = (self._player_index.unique_keys +
                                    self._champion_index.unique_keys +
                                    self._role_index.unique_keys)
        return self._stats

    def store_match(self, match_id: str, data: Dict[str, Any]) -> bool:
        """Store a match record with automatic indexing."""
        try:
            key = f"match:{match_id}"
            self._memory_store["matches"][match_id] = data
            self._cache.set(key, data)
            self._stats.writes += 1
            for pid in data.get("participant_ids", []):
                self._player_index.add(pid, match_id)
            for cid in data.get("champion_ids", []):
                self._champion_index.add(cid, match_id)
            role = data.get("role", "")
            if role:
                self._role_index.add(role, match_id)
            return True
        except Exception as exc:
            logger.error(f"Failed to store match {match_id}: {exc}")
            return False

    def get_match(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a match record (cache-first)."""
        key = f"match:{match_id}"
        cached = self._cache.get(key)
        if cached is not None:
            self._stats.cache_hits += 1
            self._stats.reads += 1
            return cached
        self._stats.cache_misses += 1
        data = self._memory_store["matches"].get(match_id)
        if data:
            self._cache.set(key, data)
            self._stats.reads += 1
        return data

    def store_player_profile(self, player_id: str, data: Dict[str, Any]) -> bool:
        """Store a player profile."""
        try:
            key = f"player:{player_id}"
            self._memory_store["players"][player_id] = data
            self._cache.set(key, data)
            self._stats.writes += 1
            return True
        except Exception as exc:
            logger.error(f"Failed to store player {player_id}: {exc}")
            return False

    def get_player_profile(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a player profile."""
        key = f"player:{player_id}"
        cached = self._cache.get(key)
        if cached is not None:
            self._stats.cache_hits += 1
            return cached
        self._stats.cache_misses += 1
        data = self._memory_store["players"].get(player_id)
        if data:
            self._cache.set(key, data)
        return data

    def query_matches_by_player(self, player_id: str, limit: int = 20, page: int = 1) -> QueryResult:
        """Query matches for a specific player with pagination."""
        start = time.time()
        match_ids = self._player_index.lookup(player_id)
        total = len(match_ids)
        offset = (page - 1) * limit
        page_ids = match_ids[offset:offset + limit]
        results = []
        for mid in page_ids:
            match_data = self.get_match(mid)
            if match_data:
                results.append(match_data)
        return QueryResult(data=results, total_count=total,
                          query_time_ms=(time.time() - start) * 1000, page=page, page_size=limit)

    def query_matches_by_champion(self, champion_id: int, limit: int = 20) -> QueryResult:
        """Query matches involving a specific champion."""
        start = time.time()
        match_ids = self._champion_index.lookup(champion_id)
        results = []
        for mid in match_ids[-limit:]:
            match_data = self.get_match(mid)
            if match_data:
                results.append(match_data)
        return QueryResult(data=results, total_count=len(match_ids),
                          query_time_ms=(time.time() - start) * 1000)

    def query_matches_by_role(self, role: str, limit: int = 20) -> QueryResult:
        """Query matches by role."""
        start = time.time()
        match_ids = self._role_index.lookup(role)
        results = []
        for mid in match_ids[-limit:]:
            match_data = self.get_match(mid)
            if match_data:
                results.append(match_data)
        return QueryResult(data=results, total_count=len(match_ids),
                          query_time_ms=(time.time() - start) * 1000)

    def batch_store_matches(self, matches: List[Tuple[str, Dict[str, Any]]]) -> int:
        """Batch store multiple matches."""
        stored = 0
        for match_id, data in matches:
            if self.store_match(match_id, data):
                stored += 1
        return stored

    def delete_match(self, match_id: str) -> bool:
        """Delete a match record."""
        if match_id in self._memory_store["matches"]:
            del self._memory_store["matches"][match_id]
            self._cache.delete(f"match:{match_id}")
            return True
        return False

    def get_storage_summary(self) -> Dict[str, Any]:
        """Get comprehensive storage summary."""
        return {
            "backend": self._backend.value,
            "matches_stored": len(self._memory_store["matches"]),
            "players_stored": len(self._memory_store["players"]),
            "indexes": {
                "player": self._player_index.to_dict(),
                "champion": self._champion_index.to_dict(),
                "role": self._role_index.to_dict(),
            },
            "cache_stats": self._cache.stats,
            "storage_stats": self._stats.to_dict(),
        }

    def clear_cache(self) -> None:
        """Clear the LRU cache."""
        self._cache.clear()

    def clear_all(self) -> None:
        """Clear all stored data including indexes and cache."""
        self._memory_store.clear()
        self._cache.clear()
        self._player_index.clear()
        self._champion_index.clear()
        self._role_index.clear()

    def export_data(self, export_path: str) -> bool:
        """Export all data to JSON file."""
        try:
            export = {
                "version": EXPORT_FORMAT_VERSION,
                "matches": dict(self._memory_store.get("matches", {})),
                "players": dict(self._memory_store.get("players", {})),
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "stats": self._stats.to_dict(),
            }
            Path(export_path).parent.mkdir(parents=True, exist_ok=True)
            Path(export_path).write_text(json.dumps(export, indent=2, default=str))
            return True
        except Exception as exc:
            logger.error(f"Export failed: {exc}")
            return False

    def import_data(self, import_path: str) -> int:
        """Import data from JSON export file."""
        try:
            data = json.loads(Path(import_path).read_text())
            count = 0
            for mid, mdata in data.get("matches", {}).items():
                if self.store_match(mid, mdata):
                    count += 1
            for pid, pdata in data.get("players", {}).items():
                self.store_player_profile(pid, pdata)
            return count
        except Exception as exc:
            logger.error(f"Import failed: {exc}")
            return 0

    def get_match_count(self) -> int:
        """Get total number of stored matches."""
        return len(self._memory_store.get("matches", {}))

    def get_player_count(self) -> int:
        """Get total number of stored player profiles."""
        return len(self._memory_store.get("players", {}))




class DataMigrator:
    """Handles data migration between storage backends."""

    def __init__(self, source: 'DataPersistenceLayer', target: 'DataPersistenceLayer'):
        self._source = source
        self._target = target
        self._migrated = 0

    def migrate_all(self) -> Dict[str, int]:
        match_count = 0
        player_count = 0
        for match_id, data in self._source._memory_store.get("matches", {}).items():
            if self._target.store_match(match_id, data):
                match_count += 1
        for player_id, data in self._source._memory_store.get("players", {}).items():
            if self._target.store_player_profile(player_id, data):
                player_count += 1
        self._migrated = match_count + player_count
        return {"matches": match_count, "players": player_count}

    @property
    def migrated_count(self) -> int:
        return self._migrated


# ─── Module Self-Test ─────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M819_data_persistence_layer", "tests": []}
    try:
        cache = LRUCache(max_size=3)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.set("c", 3)
        assert cache.get("a") == 1
        cache.set("d", 4)
        assert cache.size <= 3
        results["tests"].append({"name": "lru_cache", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "lru_cache", "status": "fail", "error": str(e)})
    try:
        layer = DataPersistenceLayer()
        layer.store_match("m1", {"participant_ids": ["p1", "p2"], "champion_ids": [1, 2]})
        layer.store_match("m2", {"participant_ids": ["p1", "p3"], "champion_ids": [1, 3]})
        result = layer.query_matches_by_player("p1")
        assert result.total_count == 2
        results["tests"].append({"name": "match_storage_query", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "match_storage_query", "status": "fail", "error": str(e)})
    try:
        layer = DataPersistenceLayer()
        layer.store_player_profile("p1", {"name": "Test", "rank": "GOLD"})
        p = layer.get_player_profile("p1")
        assert p["rank"] == "GOLD"
        results["tests"].append({"name": "player_persistence", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "player_persistence", "status": "fail", "error": str(e)})
    try:
        idx = SecondaryIndex("test")
        idx.add("key1", "rec1")
        idx.add("key1", "rec2")
        idx.add("key2", "rec3")
        assert idx.count("key1") == 2
        assert idx.unique_keys == 2
        idx.remove("key1", "rec1")
        assert idx.count("key1") == 1
        results["tests"].append({"name": "secondary_index", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "secondary_index", "status": "fail", "error": str(e)})
    try:
        layer = DataPersistenceLayer()
        batch = [(f"bm{i}", {"participant_ids": [f"bp{i}"], "champion_ids": [i]}) for i in range(10)]
        stored = layer.batch_store_matches(batch)
        assert stored == 10
        assert layer.get_match_count() == 10
        results["tests"].append({"name": "batch_operations", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "batch_operations", "status": "fail", "error": str(e)})
    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results

if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))

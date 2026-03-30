"""
Online Feature Store — Real-time feature caching & serving for inference.

Provides a low-latency feature cache that stores pre-computed feature
vectors keyed by entity (player/champion/team), supports TTL-based
expiration, batch retrieval, and feature versioning for A/B serving.

Location: agentlightning/inference/online_feature_store.py

Reference (拿来主义):
  查看 agentlightning/store/experience_store.py 上现有 ExperienceStore 的
  容量管理+game过滤方式, 理解其模式, 特别是 deque(maxlen=capacity)容量管理
  如何与 sample/filter 采样逻辑分离。
  从 agentos/governance/model_versioner.py 这个好例子开始 — 它提供了
  save→load→list_versions→diff 的清晰四步契约。
  遵循该模式实现 OnlineFeatureStore, 让 StateEncoderNetwork(M537) 和
  ActionSampler(M536) 可以在推理时以 O(1) 延迟获取预计算特征, 并能
  支持特征版本管理(训练集特征 v1 vs v2 共存).

Design Notes (Knuth-level critique):
  User:
    - TTL prevents stale features from corrupting real-time decisions
    - Batch get avoids N round-trips in team-level feature retrieval
    - Version isolation prevents A/B test feature contamination
  System:
    - Dict-based store is O(1) read/write; TTL check is lazy on access
    - Memory bounded by max_entries; LRU eviction when capacity exceeded
    - Evolution callback on every store/evict for self-monitoring
"""

from __future__ import annotations

import logging
import time
import threading
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.online_feature_store.v1"

_DEFAULT_TTL: float = 300.0  # 5 minutes
_DEFAULT_MAX_ENTRIES: int = 10000


class FeatureEntry:
    """Single feature record with metadata.

    Attributes:
        key: Entity identifier.
        features: Feature vector or dict.
        version: Feature schema version.
        created_at: Insertion timestamp.
        ttl: Time-to-live in seconds.
        access_count: Number of reads since insertion.
    """

    __slots__ = ("key", "features", "version", "created_at", "ttl", "access_count")

    def __init__(
        self,
        key: str,
        features: Any,
        version: str = "v1",
        ttl: float = _DEFAULT_TTL,
    ) -> None:
        self.key = key
        self.features = features
        self.version = version
        self.created_at = time.time()
        self.ttl = ttl
        self.access_count: int = 0

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Check if entry has exceeded its TTL."""
        t = now if now is not None else time.time()
        return (t - self.created_at) > self.ttl

    def to_dict(self) -> Dict[str, Any]:
        """Serialize entry to dict."""
        return {
            "key": self.key,
            "features": self.features,
            "version": self.version,
            "created_at": self.created_at,
            "ttl": self.ttl,
            "access_count": self.access_count,
        }


class OnlineFeatureStore:
    """Real-time feature cache with TTL, versioning, and LRU eviction.

    Thread-safe feature store for serving pre-computed feature vectors
    during live game inference. Supports versioned features for A/B
    testing different feature schemas simultaneously.

    Attributes:
        max_entries: Maximum cache capacity.
        default_ttl: Default TTL for new entries.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        default_ttl: float = _DEFAULT_TTL,
    ) -> None:
        self.max_entries = max_entries
        self.default_ttl = default_ttl
        self._store: OrderedDict[str, FeatureEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._stats = {
            "total_puts": 0,
            "total_gets": 0,
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "expirations": 0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Core CRUD ---

    def put(
        self,
        key: str,
        features: Any,
        version: str = "v1",
        ttl: Optional[float] = None,
    ) -> None:
        """Store a feature vector.

        Args:
            key: Entity identifier (e.g. "player:summoner123").
            features: Feature vector (list[float] or dict).
            version: Feature schema version.
            ttl: Override TTL in seconds, or use default.
        """
        effective_ttl = ttl if ttl is not None else self.default_ttl
        entry = FeatureEntry(key=key, features=features, version=version, ttl=effective_ttl)

        with self._lock:
            if key in self._store:
                del self._store[key]
            self._store[key] = entry
            self._stats["total_puts"] += 1
            self._maybe_evict()

        self._fire_evolution("feature_stored", {"key": key, "version": version})

    def get(
        self,
        key: str,
        version: Optional[str] = None,
    ) -> Optional[Any]:
        """Retrieve a feature vector.

        Args:
            key: Entity identifier.
            version: If specified, only return if version matches.

        Returns:
            Feature vector, or None if not found / expired / version mismatch.
        """
        with self._lock:
            self._stats["total_gets"] += 1
            entry = self._store.get(key)

            if entry is None:
                self._stats["misses"] += 1
                return None

            now = time.time()
            if entry.is_expired(now):
                del self._store[key]
                self._stats["expirations"] += 1
                self._stats["misses"] += 1
                return None

            if version is not None and entry.version != version:
                self._stats["misses"] += 1
                return None

            # LRU: move to end
            self._store.move_to_end(key)
            entry.access_count += 1
            self._stats["hits"] += 1
            return entry.features

    def batch_get(
        self,
        keys: List[str],
        version: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve features for multiple keys.

        Args:
            keys: List of entity identifiers.
            version: Optional version filter.

        Returns:
            Dict of key → features for found entries.
        """
        results: Dict[str, Any] = {}
        for key in keys:
            val = self.get(key, version=version)
            if val is not None:
                results[key] = val
        return results

    def batch_put(
        self,
        items: List[Tuple[str, Any]],
        version: str = "v1",
        ttl: Optional[float] = None,
    ) -> int:
        """Store multiple feature vectors.

        Args:
            items: List of (key, features) tuples.
            version: Feature schema version.
            ttl: Override TTL.

        Returns:
            Number of items stored.
        """
        count = 0
        for key, features in items:
            self.put(key, features, version=version, ttl=ttl)
            count += 1
        return count

    def delete(self, key: str) -> bool:
        """Delete a feature entry.

        Args:
            key: Entity identifier.

        Returns:
            True if deleted, False if not found.
        """
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
        return False

    # --- Query & Inspection ---

    def has(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if entry.is_expired():
                del self._store[key]
                self._stats["expirations"] += 1
                return False
            return True

    def keys(self, version: Optional[str] = None) -> List[str]:
        """List all non-expired keys, optionally filtered by version.

        Args:
            version: If specified, only return keys with this version.

        Returns:
            List of keys.
        """
        now = time.time()
        result: List[str] = []
        expired_keys: List[str] = []
        with self._lock:
            for k, entry in self._store.items():
                if entry.is_expired(now):
                    expired_keys.append(k)
                    continue
                if version is not None and entry.version != version:
                    continue
                result.append(k)
            for k in expired_keys:
                del self._store[k]
                self._stats["expirations"] += 1
        return result

    def size(self) -> int:
        """Number of entries (including potentially expired)."""
        return len(self._store)

    def versions(self) -> List[str]:
        """List all unique feature versions in the store."""
        seen: set = set()
        with self._lock:
            for entry in self._store.values():
                seen.add(entry.version)
        return sorted(seen)

    def get_entry_metadata(self, key: str) -> Optional[Dict[str, Any]]:
        """Get full metadata for a feature entry.

        Args:
            key: Entity identifier.

        Returns:
            Dict with key, version, created_at, ttl, access_count
            or None if not found.
        """
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._store[key]
                self._stats["expirations"] += 1
                return None
            return entry.to_dict()

    # --- Maintenance ---

    def purge_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries purged.
        """
        now = time.time()
        to_remove: List[str] = []
        with self._lock:
            for k, entry in self._store.items():
                if entry.is_expired(now):
                    to_remove.append(k)
            for k in to_remove:
                del self._store[k]
            self._stats["expirations"] += len(to_remove)
        return len(to_remove)

    def clear(self) -> None:
        """Remove all entries."""
        with self._lock:
            self._store.clear()

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with total_puts, total_gets, hits, misses,
            evictions, expirations, hit_rate.
        """
        with self._lock:
            stats = dict(self._stats)
        total = stats["hits"] + stats["misses"]
        stats["hit_rate"] = stats["hits"] / total if total > 0 else 0.0
        stats["current_size"] = self.size()
        return stats

    def export_snapshot(self, version: Optional[str] = None) -> List[Dict[str, Any]]:
        """Export all non-expired entries as dicts.

        Args:
            version: Optional version filter.

        Returns:
            List of entry dicts.
        """
        now = time.time()
        snapshot: List[Dict[str, Any]] = []
        with self._lock:
            for entry in self._store.values():
                if entry.is_expired(now):
                    continue
                if version is not None and entry.version != version:
                    continue
                snapshot.append(entry.to_dict())
        return snapshot

    def import_snapshot(self, entries: List[Dict[str, Any]]) -> int:
        """Import entries from a snapshot.

        Args:
            entries: List of entry dicts (from export_snapshot).

        Returns:
            Number of entries imported.
        """
        count = 0
        for data in entries:
            self.put(
                key=data["key"],
                features=data["features"],
                version=data.get("version", "v1"),
                ttl=data.get("ttl", self.default_ttl),
            )
            count += 1
        return count

    # --- Internal ---

    def _maybe_evict(self) -> None:
        """Evict oldest entries if over capacity. Must hold lock."""
        while len(self._store) > self.max_entries:
            evicted_key, _ = self._store.popitem(last=False)
            self._stats["evictions"] += 1
            logger.debug("Evicted feature entry: %s", evicted_key)

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

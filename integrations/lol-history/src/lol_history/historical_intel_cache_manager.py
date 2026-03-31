"""
HistoricalIntelCacheManager — Centralized cache for all historical intelligence data.

Architecture (拿来主义):
  history_cold_start_handler.py — cold start / cache warming patterns
  seraphine_lcu_deep_client.py — TTL-based caching

Location: integrations/lol-history/src/lol_history/historical_intel_cache_manager.py

Design Notes (Knuth-level critique):
  User:
    - Single cache layer for all intel modules; avoids redundant API calls.
    - Pre-warm at lobby start; auto-expire after game ends.
  System:
    - Namespaced keys prevent collision between modules.
    - LRU eviction when cache exceeds max entries.
    - Stats expose hit/miss rates for tuning TTL.
"""
from __future__ import annotations
import logging, time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.historical_intel_cache_manager.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class HistoricalIntelCacheManager:
    """Centralized cache manager for historical intelligence.

    Public API: get, set, invalidate, invalidate_namespace, warm, clear, get_stats
    """
    def __init__(self, max_entries: int = 500, default_ttl_s: float = 600.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._max_entries = max_entries
        self._default_ttl = default_ttl_s
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _make_key(self, namespace: str, key: str) -> str:
        return f"{namespace}:{key}"

    def _evict_expired(self) -> int:
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, v in self._cache.items()
                    if now - v.get("_ts", 0) > v.get("_ttl", self._default_ttl)]
        for k in expired:
            del self._cache[k]
        return len(expired)

    def _evict_lru(self) -> None:
        """Evict oldest entries if over capacity."""
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)
            self._evictions += 1

    def get(self, namespace: str, key: str) -> Optional[Any]:
        """Get a cached value.

        Args:
            namespace: Module namespace (e.g. "opponent_scout", "matchup_predictor").
            key: Cache key within namespace.

        Returns:
            Cached value or None if miss/expired.
        """
        self._op_count += 1
        full_key = self._make_key(namespace, key)
        entry = self._cache.get(full_key)
        if entry is None:
            self._misses += 1
            return None

        # Check expiry
        age = time.time() - entry.get("_ts", 0)
        ttl = entry.get("_ttl", self._default_ttl)
        if age > ttl:
            del self._cache[full_key]
            self._misses += 1
            return None

        # Move to end (LRU)
        self._cache.move_to_end(full_key)
        self._hits += 1
        return entry.get("_value")

    def set(self, namespace: str, key: str, value: Any,
            ttl_s: float = None) -> Dict[str, Any]:
        """Set a cached value.

        Args:
            namespace: Module namespace.
            key: Cache key.
            value: Value to cache (any serializable data).
            ttl_s: Time-to-live in seconds (default: self._default_ttl).
        """
        self._op_count += 1
        full_key = self._make_key(namespace, key)
        self._cache[full_key] = {
            "_value": value,
            "_ts": time.time(),
            "_ttl": ttl_s if ttl_s is not None else self._default_ttl,
            "_namespace": namespace,
        }
        self._cache.move_to_end(full_key)
        self._evict_lru()
        return {"status": "ok", "key": full_key, "cache_size": len(self._cache)}

    def invalidate(self, namespace: str, key: str) -> Dict[str, Any]:
        """Invalidate a specific cache entry."""
        self._op_count += 1
        full_key = self._make_key(namespace, key)
        removed = full_key in self._cache
        if removed:
            del self._cache[full_key]
        return {"status": "ok", "removed": removed}

    def invalidate_namespace(self, namespace: str) -> Dict[str, Any]:
        """Invalidate all entries in a namespace."""
        self._op_count += 1
        prefix = f"{namespace}:"
        to_remove = [k for k in self._cache if k.startswith(prefix)]
        for k in to_remove:
            del self._cache[k]
        return {"status": "ok", "removed": len(to_remove), "namespace": namespace}

    def warm(self, entries: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Bulk warm the cache.

        Args:
            entries: {namespace:key: {"value": ..., "ttl_s": ...}, ...}
        """
        self._op_count += 1
        warmed = 0
        for full_key, entry in entries.items():
            parts = full_key.split(":", 1)
            if len(parts) != 2:
                continue
            ns, key = parts
            self.set(ns, key, entry.get("value"), entry.get("ttl_s"))
            warmed += 1
        self._fire("warmed", {"entries": warmed})
        return {"status": "ok", "warmed": warmed, "cache_size": len(self._cache)}

    def clear(self) -> Dict[str, Any]:
        """Clear all cache entries."""
        self._op_count += 1
        count = len(self._cache)
        self._cache.clear()
        return {"status": "ok", "cleared": count}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        self._evict_expired()
        total_requests = self._hits + self._misses
        hit_rate = _safe_div(self._hits, total_requests)

        # Namespace breakdown
        ns_counts: Dict[str, int] = {}
        for entry in self._cache.values():
            ns = entry.get("_namespace", "unknown")
            ns_counts[ns] = ns_counts.get(ns, 0) + 1

        return {
            "op_count": self._op_count,
            "cache_size": len(self._cache),
            "max_entries": self._max_entries,
            "hits": self._hits, "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "evictions": self._evictions,
            "namespace_breakdown": ns_counts,
        }

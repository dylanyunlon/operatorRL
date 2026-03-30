"""
Inference Cache — Cache inference results for identical/similar states.

Provides a hash-based cache for model inference outputs. When the same
(or sufficiently similar) game state is seen again within a short window,
returns the cached result instead of re-running the model forward pass.

Location: agentlightning/inference/inference_cache.py

Reference (拿来主义):
  查看 agentlightning/inference/online_feature_store.py(M546) 上现有
  OnlineFeatureStore 的 TTL/LRU缓存方式, 理解其模式, 特别是
  put→get→expire 的生命周期如何与 OrderedDict LRU 驱逐分离。
  从 agentlightning/store/experience_store.py 这个好例子开始 — 它的
  deque(maxlen=capacity) 展示了有界存储的基本模式。
  遵循该模式实现 InferenceCache, 让推理管线可以在高频决策场景(如LoL
  14fps状态采样)中避免重复推理, 并能通过相似度哈希合并近似相同的状态.

Design Notes (Knuth-level critique):
  User:
    - Cache hit avoids redundant model forward pass (~10-50ms savings)
    - Similarity hash allows cache hits for states that differ only in noise
    - Hit rate metrics help tune cache parameters
  System:
    - FNV-1a hash is O(n) in feature count, fast for numeric vectors
    - TTL is game-speed-aware: fast games need shorter TTL
    - Thread-safe for concurrent pipeline access
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.inference_cache.v1"

_DEFAULT_CAPACITY: int = 5000
_DEFAULT_TTL: float = 2.0  # 2 seconds — suitable for real-time games
_DEFAULT_PRECISION: int = 2  # decimal places for similarity hashing


class CacheEntry:
    """Single cached inference result."""

    __slots__ = ("key", "result", "created_at", "ttl", "hit_count")

    def __init__(self, key: str, result: Dict[str, Any], ttl: float) -> None:
        self.key = key
        self.result = result
        self.created_at = time.time()
        self.ttl = ttl
        self.hit_count: int = 0

    def is_expired(self, now: Optional[float] = None) -> bool:
        t = now if now is not None else time.time()
        return (t - self.created_at) > self.ttl


class InferenceCache:
    """Hash-based inference result cache.

    Caches model outputs keyed by a hash of the input state.
    Supports exact-match and similarity-based (quantized) hashing.

    Attributes:
        capacity: Maximum cache entries.
        default_ttl: Default TTL in seconds.
        precision: Decimal places for similarity hashing.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        capacity: int = _DEFAULT_CAPACITY,
        default_ttl: float = _DEFAULT_TTL,
        precision: int = _DEFAULT_PRECISION,
    ) -> None:
        self.capacity = capacity
        self.default_ttl = default_ttl
        self.precision = precision
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()
        self._stats = {
            "total_lookups": 0,
            "hits": 0,
            "misses": 0,
            "inserts": 0,
            "evictions": 0,
            "expirations": 0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Core Operations ---

    def get(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Look up cached result for a state.

        Args:
            state: Input state dict.

        Returns:
            Cached result dict, or None if miss.
        """
        key = self._hash_state(state)
        with self._lock:
            self._stats["total_lookups"] += 1
            entry = self._cache.get(key)
            if entry is None:
                self._stats["misses"] += 1
                return None
            if entry.is_expired():
                del self._cache[key]
                self._stats["expirations"] += 1
                self._stats["misses"] += 1
                return None
            self._cache.move_to_end(key)
            entry.hit_count += 1
            self._stats["hits"] += 1
            return entry.result

    def put(
        self,
        state: Dict[str, Any],
        result: Dict[str, Any],
        ttl: Optional[float] = None,
    ) -> str:
        """Cache an inference result.

        Args:
            state: Input state dict.
            result: Model output to cache.
            ttl: Override TTL in seconds.

        Returns:
            Cache key string.
        """
        key = self._hash_state(state)
        effective_ttl = ttl if ttl is not None else self.default_ttl
        entry = CacheEntry(key=key, result=result, ttl=effective_ttl)

        with self._lock:
            if key in self._cache:
                del self._cache[key]
            self._cache[key] = entry
            self._stats["inserts"] += 1
            self._evict_if_needed()

        return key

    def get_or_compute(
        self,
        state: Dict[str, Any],
        compute_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        ttl: Optional[float] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Get cached result or compute and cache.

        Args:
            state: Input state dict.
            compute_fn: Function to compute result if cache miss.
            ttl: Override TTL.

        Returns:
            Tuple of (result, was_cached).
        """
        cached = self.get(state)
        if cached is not None:
            return cached, True

        result = compute_fn(state)
        self.put(state, result, ttl=ttl)
        return result, False

    def invalidate(self, state: Dict[str, Any]) -> bool:
        """Remove a specific state from cache.

        Args:
            state: State to invalidate.

        Returns:
            True if removed, False if not found.
        """
        key = self._hash_state(state)
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
        return False

    def clear(self) -> int:
        """Clear all cache entries.

        Returns:
            Number of entries cleared.
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
        return count

    def purge_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries purged.
        """
        now = time.time()
        to_remove: List[str] = []
        with self._lock:
            for k, entry in self._cache.items():
                if entry.is_expired(now):
                    to_remove.append(k)
            for k in to_remove:
                del self._cache[k]
            self._stats["expirations"] += len(to_remove)
        return len(to_remove)

    # --- Stats ---

    def size(self) -> int:
        """Number of cached entries."""
        return len(self._cache)

    def hit_rate(self) -> float:
        """Cache hit rate."""
        total = self._stats["hits"] + self._stats["misses"]
        return self._stats["hits"] / total if total > 0 else 0.0

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            stats = dict(self._stats)
        stats["current_size"] = self.size()
        stats["hit_rate"] = self.hit_rate()
        stats["capacity"] = self.capacity
        return stats

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        with self._lock:
            self._stats = {
                "total_lookups": 0,
                "hits": 0,
                "misses": 0,
                "inserts": 0,
                "evictions": 0,
                "expirations": 0,
            }

    # --- Internal ---

    def _hash_state(self, state: Dict[str, Any]) -> str:
        """Compute hash of a state dict with quantization for similarity.

        Rounds floats to self.precision decimal places before hashing,
        so similar states (differing only in low-order noise) get same hash.
        """
        quantized = self._quantize(state)
        serialized = json.dumps(quantized, sort_keys=True, default=str)
        return hashlib.md5(serialized.encode()).hexdigest()

    def _quantize(self, obj: Any) -> Any:
        """Recursively quantize floats to configured precision."""
        if isinstance(obj, float):
            return round(obj, self.precision)
        elif isinstance(obj, dict):
            return {k: self._quantize(v) for k, v in sorted(obj.items())}
        elif isinstance(obj, (list, tuple)):
            return [self._quantize(v) for v in obj]
        return obj

    def _evict_if_needed(self) -> None:
        """Evict LRU entries if over capacity. Must hold lock."""
        while len(self._cache) > self.capacity:
            self._cache.popitem(last=False)
            self._stats["evictions"] += 1

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

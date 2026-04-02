"""
EventDedupFilter — Universal event deduplication with TTL-based expiry.
=========================================================================
lolbot-HyperAI · Common Filters

Provides event_id dedup, content-hash dedup, and TTL-based cache expiry.
Used as a pre-filter by perception and all event subscribers.

Architecture position:
    modules/common/filters/event_dedup_filter.py   ← YOU ARE HERE
    ├─ Used by: EventStreamProcessor
    ├─ Used by: ObjectiveTracker
    └─ Used by: any module that processes GameEvent streams

Apollo reference:
    modules/perception/lidar/lib/object_filter_bank.cc

Design notes:
    - Two-tier dedup: fast event_id set + content hash for ID-less events
    - TTL expiry: entries older than ttl_s are pruned on access
    - Memory bounded: max_entries cap with LRU eviction
    - Thread-safe via internal lock (optional, disabled by default for Proc() use)
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Dict, Optional, Set

_DEFAULT_TTL_S = 300.0   # 5 minutes
_DEFAULT_MAX_ENTRIES = 10000
_PRUNE_INTERVAL = 500    # prune every N inserts


@dataclass
class DedupStats:
    """Dedup filter statistics."""
    total_checked: int = 0
    duplicates_found: int = 0
    entries_pruned: int = 0
    current_size: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_checked": self.total_checked,
            "duplicates_found": self.duplicates_found,
            "entries_pruned": self.entries_pruned,
            "current_size": self.current_size,
            "dedup_rate": (
                round(self.duplicates_found / max(1, self.total_checked), 3)
            ),
        }


class EventDedupFilter:
    """Two-tier event deduplication filter.

    Usage::
        dedup = EventDedupFilter(ttl_s=300.0)

        # By event ID (fast path)
        if dedup.is_duplicate_id(event.event_id):
            continue  # skip

        # By content hash (for ID-less events)
        if dedup.is_duplicate_hash(event_data_dict):
            continue  # skip

        # Combined check
        if dedup.check(event_id=evt.event_id, content=evt_dict):
            continue  # skip
    """

    def __init__(
        self,
        ttl_s: float = _DEFAULT_TTL_S,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        thread_safe: bool = False,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries

        # Tier 1: event_id → insert_time (OrderedDict for LRU)
        self._id_cache: OrderedDict[int, float] = OrderedDict()

        # Tier 2: content_hash → insert_time
        self._hash_cache: OrderedDict[str, float] = OrderedDict()

        self._stats = DedupStats()
        self._insert_count = 0

        self._lock: Optional[threading.Lock] = threading.Lock() if thread_safe else None

    def check(
        self,
        event_id: Optional[int] = None,
        content: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Combined dedup check.  Returns True if duplicate.

        Checks event_id first (fast), then content hash if no ID.
        Automatically registers the event if not a duplicate.
        """
        if self._lock:
            self._lock.acquire()
        try:
            self._stats.total_checked += 1

            # Tier 1: ID check
            if event_id is not None:
                if event_id in self._id_cache:
                    self._stats.duplicates_found += 1
                    return True
                self._register_id(event_id)

            # Tier 2: Hash check
            if content is not None:
                h = self._content_hash(content)
                if h in self._hash_cache:
                    self._stats.duplicates_found += 1
                    return True
                self._register_hash(h)

            # Periodic prune
            self._insert_count += 1
            if self._insert_count % _PRUNE_INTERVAL == 0:
                self._prune()

            return False
        finally:
            if self._lock:
                self._lock.release()

    def is_duplicate_id(self, event_id: int) -> bool:
        """Check if event_id has been seen. Registers if not."""
        if self._lock:
            self._lock.acquire()
        try:
            self._stats.total_checked += 1
            if event_id in self._id_cache:
                self._stats.duplicates_found += 1
                return True
            self._register_id(event_id)
            return False
        finally:
            if self._lock:
                self._lock.release()

    def is_duplicate_hash(self, content: Dict[str, Any]) -> bool:
        """Check content hash. Registers if not seen."""
        if self._lock:
            self._lock.acquire()
        try:
            self._stats.total_checked += 1
            h = self._content_hash(content)
            if h in self._hash_cache:
                self._stats.duplicates_found += 1
                return True
            self._register_hash(h)
            return False
        finally:
            if self._lock:
                self._lock.release()

    def reset(self) -> None:
        """Clear all caches."""
        if self._lock:
            self._lock.acquire()
        try:
            self._id_cache.clear()
            self._hash_cache.clear()
        finally:
            if self._lock:
                self._lock.release()

    @property
    def stats(self) -> DedupStats:
        self._stats.current_size = len(self._id_cache) + len(self._hash_cache)
        return self._stats

    # ─── Internal ────────────────────────────────────────────────────

    def _register_id(self, event_id: int) -> None:
        self._id_cache[event_id] = time.time()
        if len(self._id_cache) > self._max_entries:
            self._id_cache.popitem(last=False)  # evict oldest

    def _register_hash(self, h: str) -> None:
        self._hash_cache[h] = time.time()
        if len(self._hash_cache) > self._max_entries:
            self._hash_cache.popitem(last=False)

    def _prune(self) -> None:
        """Remove entries older than TTL."""
        now = time.time()
        cutoff = now - self._ttl_s
        pruned = 0

        # Prune ID cache
        while self._id_cache:
            oldest_id, oldest_time = next(iter(self._id_cache.items()))
            if oldest_time < cutoff:
                self._id_cache.popitem(last=False)
                pruned += 1
            else:
                break

        # Prune hash cache
        while self._hash_cache:
            oldest_hash, oldest_time = next(iter(self._hash_cache.items()))
            if oldest_time < cutoff:
                self._hash_cache.popitem(last=False)
                pruned += 1
            else:
                break

        self._stats.entries_pruned += pruned

    @staticmethod
    def _content_hash(content: Dict[str, Any]) -> str:
        """Compute a fast content hash for dedup."""
        try:
            raw = json.dumps(content, sort_keys=True, default=str)
        except (TypeError, ValueError):
            raw = str(content)
        return hashlib.md5(raw.encode()).hexdigest()[:16]

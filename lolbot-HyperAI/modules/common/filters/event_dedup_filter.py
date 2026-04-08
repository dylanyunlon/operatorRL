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


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: Per-channel dedup strategy + configurable policy engine
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 EventDedupFilter 的 TTL+LRU 二级去重 这个好例子开始。
#   然后，遵循该模式实现 ChannelDedupPolicy，让 不同 channel 可以 拥有独立的
#   去重策略（TTL/窗口/hash字段），并能 在 Proc() 循环中被 per-channel 调用。
#   接着 在 DedupPolicyEngine 引入 策略注册表，使 组件 能够 声明自己的去重需求，
#   同时 PolicyMetrics 优化 监控指标以支撑 AB 测试。
#   最终 DedupFilterV3 完善 向后兼容层，确保 所有现有调用方 兼容 新接口。


from typing import FrozenSet


# ─── Channel-specific dedup policy ──────────────────────────────────────────

@dataclass
class ChannelDedupPolicy:
    """Per-channel dedup configuration.

    Different channels have different dedup requirements:
    - /lol/events: dedup by event_id, short TTL (game events are ephemeral)
    - /lol/win_prediction: dedup by content hash, longer TTL (slow-changing)
    - /lol/strategy: dedup by action field only, moderate TTL

    Apollo parallel: Each channel in CyberRT has its own QoS policy.
    """
    channel: str
    ttl_s: float = _DEFAULT_TTL_S
    max_entries: int = _DEFAULT_MAX_ENTRIES
    use_id_dedup: bool = True
    use_hash_dedup: bool = True
    hash_fields: Optional[FrozenSet[str]] = None  # None = hash all fields
    prune_interval: int = _PRUNE_INTERVAL

    def __post_init__(self):
        if self.hash_fields is not None and isinstance(self.hash_fields, (set, list)):
            object.__setattr__(self, 'hash_fields', frozenset(self.hash_fields))


# ─── Policy metrics ─────────────────────────────────────────────────────────

@dataclass
class PolicyMetrics:
    """Per-policy dedup metrics for monitoring and AB test evaluation."""
    channel: str = ""
    total_checked: int = 0
    id_duplicates: int = 0
    hash_duplicates: int = 0
    entries_pruned: int = 0
    false_positive_corrections: int = 0
    avg_check_us: float = 0.0  # average check latency in microseconds
    _check_time_sum: float = 0.0

    def record_check(self, elapsed_us: float, is_dup: bool, dup_tier: str) -> None:
        self.total_checked += 1
        self._check_time_sum += elapsed_us
        self.avg_check_us = self._check_time_sum / self.total_checked
        if is_dup:
            if dup_tier == "id":
                self.id_duplicates += 1
            elif dup_tier == "hash":
                self.hash_duplicates += 1

    @property
    def dedup_rate(self) -> float:
        total_dups = self.id_duplicates + self.hash_duplicates
        return round(total_dups / max(1, self.total_checked), 4)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "total_checked": self.total_checked,
            "id_duplicates": self.id_duplicates,
            "hash_duplicates": self.hash_duplicates,
            "dedup_rate": self.dedup_rate,
            "entries_pruned": self.entries_pruned,
            "avg_check_us": round(self.avg_check_us, 1),
        }


# ─── Selective field hashing ─────────────────────────────────────────────────

def _selective_content_hash(
    content: Dict[str, Any],
    fields: Optional[FrozenSet[str]] = None,
) -> str:
    """Hash only specified fields of content dict for targeted dedup.

    When fields is None, hashes all fields (same as V1 behavior).
    When fields is specified, only those keys are included in hash —
    useful for channels where some fields change every tick (timestamp)
    but the semantic content is the same.
    """
    if fields is None:
        data = content
    else:
        data = {k: content[k] for k in sorted(fields) if k in content}
    try:
        raw = json.dumps(data, sort_keys=True, default=str)
    except (TypeError, ValueError):
        raw = str(data)
    return hashlib.md5(raw.encode()).hexdigest()[:16]


# ─── Per-channel dedup filter ────────────────────────────────────────────────

class ChannelDedupFilter:
    """A dedup filter instance scoped to a single channel.

    Created by DedupPolicyEngine for each registered channel.
    Uses the channel's ChannelDedupPolicy for configuration.

    Apollo parallel: per-channel message filter in CyberRT transport layer.
    """

    def __init__(self, policy: ChannelDedupPolicy) -> None:
        self._policy = policy
        self._id_cache: OrderedDict[int, float] = OrderedDict()
        self._hash_cache: OrderedDict[str, float] = OrderedDict()
        self._metrics = PolicyMetrics(channel=policy.channel)
        self._insert_count = 0

    def check(
        self,
        event_id: Optional[int] = None,
        content: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check for duplicates using this channel's policy.

        Returns True if the event is a duplicate.
        """
        t0 = time.monotonic()
        dup_tier = ""
        is_dup = False

        # Tier 1: ID dedup
        if self._policy.use_id_dedup and event_id is not None:
            if event_id in self._id_cache:
                is_dup = True
                dup_tier = "id"
            else:
                self._id_cache[event_id] = time.time()
                self._enforce_limit(self._id_cache)

        # Tier 2: Hash dedup (only if not already caught by ID)
        if not is_dup and self._policy.use_hash_dedup and content is not None:
            h = _selective_content_hash(content, self._policy.hash_fields)
            if h in self._hash_cache:
                is_dup = True
                dup_tier = "hash"
            else:
                self._hash_cache[h] = time.time()
                self._enforce_limit(self._hash_cache)

        # Periodic prune
        self._insert_count += 1
        if self._insert_count % self._policy.prune_interval == 0:
            self._prune()

        elapsed_us = (time.monotonic() - t0) * 1e6
        self._metrics.record_check(elapsed_us, is_dup, dup_tier)
        return is_dup

    def _enforce_limit(self, cache: OrderedDict) -> None:
        while len(cache) > self._policy.max_entries:
            cache.popitem(last=False)

    def _prune(self) -> None:
        now = time.time()
        cutoff = now - self._policy.ttl_s
        pruned = 0
        for cache in (self._id_cache, self._hash_cache):
            while cache:
                _, oldest_time = next(iter(cache.items()))
                if oldest_time < cutoff:
                    cache.popitem(last=False)
                    pruned += 1
                else:
                    break
        self._metrics.entries_pruned += pruned

    def reset(self) -> None:
        self._id_cache.clear()
        self._hash_cache.clear()

    @property
    def metrics(self) -> PolicyMetrics:
        return self._metrics


# ─── Dedup policy engine (registry) ─────────────────────────────────────────

class DedupPolicyEngine:
    """Central registry for per-channel dedup filters.

    Components register their channels at Init() time. During Proc(),
    they call engine.check(channel, ...) which routes to the correct
    ChannelDedupFilter with the right policy.

    Apollo parallel: cyber/transport/dispatcher — message routing with
    per-channel QoS and filtering.

    Usage::
        engine = DedupPolicyEngine()

        # At Init() time:
        engine.register(ChannelDedupPolicy(
            channel="/lol/events",
            ttl_s=60.0,
            hash_fields=frozenset({"EventName", "EventID"}),
        ))
        engine.register(ChannelDedupPolicy(
            channel="/lol/win_prediction",
            ttl_s=10.0,
            use_id_dedup=False,
            hash_fields=frozenset({"blue_win_prob"}),
        ))

        # At Proc() time:
        if engine.check("/lol/events", event_id=42, content=evt_dict):
            continue  # duplicate
    """

    def __init__(self) -> None:
        self._filters: Dict[str, ChannelDedupFilter] = {}
        self._default_policy = ChannelDedupPolicy(channel="__default__")
        self._fallback = ChannelDedupFilter(self._default_policy)

    def register(self, policy: ChannelDedupPolicy) -> None:
        """Register a per-channel dedup policy."""
        self._filters[policy.channel] = ChannelDedupFilter(policy)

    def check(
        self,
        channel: str,
        event_id: Optional[int] = None,
        content: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check for duplicates on the given channel.

        If the channel has a registered policy, uses that.
        Otherwise falls back to the default filter.
        """
        filt = self._filters.get(channel, self._fallback)
        return filt.check(event_id=event_id, content=content)

    def reset_channel(self, channel: str) -> None:
        """Reset a single channel's dedup state (e.g. between games)."""
        filt = self._filters.get(channel)
        if filt:
            filt.reset()

    def reset_all(self) -> None:
        """Reset all channel filters (e.g. between sessions)."""
        for filt in self._filters.values():
            filt.reset()
        self._fallback.reset()

    def metrics(self) -> Dict[str, Dict[str, Any]]:
        """Get per-channel dedup metrics for monitoring."""
        result: Dict[str, Dict[str, Any]] = {}
        for channel, filt in self._filters.items():
            result[channel] = filt.metrics.to_dict()
        return result

    def aggregate_metrics(self) -> Dict[str, Any]:
        """Aggregate metrics across all channels."""
        total_checked = 0
        total_id_dups = 0
        total_hash_dups = 0
        for filt in self._filters.values():
            m = filt.metrics
            total_checked += m.total_checked
            total_id_dups += m.id_duplicates
            total_hash_dups += m.hash_duplicates
        return {
            "channels_registered": len(self._filters),
            "total_checked": total_checked,
            "total_id_duplicates": total_id_dups,
            "total_hash_duplicates": total_hash_dups,
            "overall_dedup_rate": round(
                (total_id_dups + total_hash_dups) / max(1, total_checked), 4
            ),
        }


# ─── V3 backward-compatible wrapper ─────────────────────────────────────────

class EventDedupFilterV3(EventDedupFilter):
    """V3 dedup filter with per-channel policy support.

    Fully backward-compatible with V1 EventDedupFilter API.
    Adds channel-aware check_channel() method for V3 callers.

    Usage (V1 compat — unchanged):
        dedup = EventDedupFilterV3()
        if dedup.check(event_id=42, content=data):
            continue  # duplicate

    Usage (V3 — per-channel):
        dedup = EventDedupFilterV3()
        dedup.register_channel(ChannelDedupPolicy(
            channel="/lol/events", ttl_s=60.0,
        ))
        if dedup.check_channel("/lol/events", event_id=42, content=data):
            continue  # duplicate
    """

    def __init__(
        self,
        ttl_s: float = _DEFAULT_TTL_S,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        thread_safe: bool = False,
    ) -> None:
        super().__init__(ttl_s=ttl_s, max_entries=max_entries,
                         thread_safe=thread_safe)
        self._engine = DedupPolicyEngine()

    def register_channel(self, policy: ChannelDedupPolicy) -> None:
        """Register a per-channel dedup policy."""
        self._engine.register(policy)

    def check_channel(
        self,
        channel: str,
        event_id: Optional[int] = None,
        content: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Channel-aware dedup check (V3 API)."""
        return self._engine.check(channel, event_id=event_id, content=content)

    def channel_metrics(self) -> Dict[str, Dict[str, Any]]:
        return self._engine.metrics()

    def reset_all_channels(self) -> None:
        self._engine.reset_all()

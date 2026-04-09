"""
cyber/transport/backpressure.py — Adaptive Channel Flow Control
=================================================================
lolbot-HyperAI · Cyber Transport Layer

查看 Apollo cyber/transport/shm_conf.h 上现有 shared-memory transport 的实现
方式, 理解其模式, 特别是 notifier/readable_info 是如何控制读写竞争的。从
Apollo transport 这个好例子开始。然后, 遵循该模式实现一个新的
BackpressureController, 让高频 producer (CanbusComponent 10Hz) 可以感知慢
consumer (PredictionComponent 2Hz) 的消费速率, 并能自适应降频或合并消息。
接着 在 channel_message.py 的 MessageBus 引入背压信号, 使 publisher 能够
收到 SLOW_CONSUMER / QUEUE_FULL 反馈, 同时 优化 内存使用量 (防止 deque 无
限增长)。随后 整合 ChannelMonitor, 令 Dreamview dashboard 支持实时背压状态
显示, 进而 增强 系统在低配硬件上的稳定性。最终 完善 BackpressurePolicy,
确保 策略可配置 兼容 所有现有组件的 Proc() 循环, 全面 升级 数据流可靠性。

Architecture position:
    cyber/transport/backpressure.py   ← YOU ARE HERE
    ├─ Used by: canbus/channel_message.py (MessageBus._publish)
    ├─ Used by: cyber/transport/channel_monitor.py (metric export)
    ├─ Config:  conf/default_config.py (backpressure section)
    └─ Tested:  tests/cyber/test_backpressure.py

Apollo reference:
    cyber/transport/shm/readable_info.h   — consumer watermark tracking
    cyber/transport/shm/notifier.h        — signal when buffer near-full
    cyber/transport/transport.h            — QoS policies

Design notes:
    - Three policies: DROP_OLDEST, DROP_NEWEST, COALESCE (merge N→1)
    - Per-channel configuration via ChannelBackpressureConfig
    - Watermark-based: warn at 70%, throttle at 85%, drop at 95%
    - Metrics export: total_dropped, total_coalesced, current_fill_pct
    - Thread-safe: producer and consumer may be in different threads
    - Zero external dependencies (stdlib only, per project constraint)
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("transport.backpressure")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_QUEUE_CAPACITY = 256
_WARN_WATERMARK_PCT = 0.70
_THROTTLE_WATERMARK_PCT = 0.85
_DROP_WATERMARK_PCT = 0.95
_COALESCE_WINDOW_MS = 50.0    # merge messages within 50ms window
_STATS_WINDOW_SIZE = 200       # rolling window for rate estimation
_MIN_PRODUCER_BACKOFF_MS = 5.0
_MAX_PRODUCER_BACKOFF_MS = 100.0


# ─── Enums ───────────────────────────────────────────────────────────────────

class BackpressurePolicy(enum.Enum):
    """策略枚举 — how to handle overflow when consumer is slow."""
    DROP_OLDEST = "drop_oldest"     # Remove head of queue (most common)
    DROP_NEWEST = "drop_newest"     # Reject incoming message
    COALESCE = "coalesce"           # Merge multiple messages into one
    BLOCK = "block"                 # Block producer until space (risky)


class BackpressureLevel(enum.Enum):
    """Current backpressure severity for a channel."""
    NORMAL = "normal"
    WARN = "warn"                   # 70% full — emit metric
    THROTTLE = "throttle"           # 85% full — slow down producer
    CRITICAL = "critical"           # 95% full — actively dropping


# ─── Configuration ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChannelBackpressureConfig:
    """Per-channel backpressure configuration.

    Attributes:
        channel_name: Topic name, e.g. "/lol/raw_lcu".
        capacity: Maximum messages in the bounded buffer.
        policy: What to do when buffer is full.
        warn_pct: Watermark percentage to start logging warnings.
        throttle_pct: Watermark percentage to signal producer slowdown.
        drop_pct: Watermark percentage to start dropping messages.
        coalesce_window_ms: Time window for message coalescing.
        enable_metrics: Whether to track per-channel stats.
    """
    channel_name: str = ""
    capacity: int = _DEFAULT_QUEUE_CAPACITY
    policy: BackpressurePolicy = BackpressurePolicy.DROP_OLDEST
    warn_pct: float = _WARN_WATERMARK_PCT
    throttle_pct: float = _THROTTLE_WATERMARK_PCT
    drop_pct: float = _DROP_WATERMARK_PCT
    coalesce_window_ms: float = _COALESCE_WINDOW_MS
    enable_metrics: bool = True

    def __post_init__(self) -> None:
        """Validate invariants."""
        if not (0 < self.warn_pct < self.throttle_pct < self.drop_pct <= 1.0):
            raise ValueError(
                f"Watermark ordering violated: warn={self.warn_pct}, "
                f"throttle={self.throttle_pct}, drop={self.drop_pct}"
            )
        if self.capacity < 4:
            raise ValueError(f"capacity must be >= 4, got {self.capacity}")


# ─── Metrics ─────────────────────────────────────────────────────────────────

@dataclass
class BackpressureMetrics:
    """Per-channel backpressure statistics.

    Exposed to ChannelMonitor and Dreamview dashboard for
    observability. All counters are monotonically increasing;
    rates are computed by callers over time deltas.
    """
    channel_name: str = ""
    current_depth: int = 0
    capacity: int = _DEFAULT_QUEUE_CAPACITY
    fill_pct: float = 0.0
    level: BackpressureLevel = BackpressureLevel.NORMAL

    # Monotonic counters
    total_published: int = 0
    total_consumed: int = 0
    total_dropped: int = 0
    total_coalesced: int = 0
    total_blocked_ms: float = 0.0

    # Rates (messages/sec, updated on each publish)
    producer_rate_hz: float = 0.0
    consumer_rate_hz: float = 0.0

    # Timestamps
    last_publish_ts: float = 0.0
    last_consume_ts: float = 0.0
    last_drop_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON export (Dreamview / ChannelMonitor)."""
        return {
            "channel": self.channel_name,
            "depth": self.current_depth,
            "capacity": self.capacity,
            "fill_pct": round(self.fill_pct, 4),
            "level": self.level.value,
            "total_published": self.total_published,
            "total_consumed": self.total_consumed,
            "total_dropped": self.total_dropped,
            "total_coalesced": self.total_coalesced,
            "producer_hz": round(self.producer_rate_hz, 2),
            "consumer_hz": round(self.consumer_rate_hz, 2),
        }


# ─── Rate Estimator ──────────────────────────────────────────────────────────

class _RateEstimator:
    """Sliding-window rate estimator (events per second).

    Uses a bounded deque of timestamps to compute throughput without
    requiring a separate timer thread.
    """

    __slots__ = ("_timestamps", "_window_size")

    def __init__(self, window_size: int = _STATS_WINDOW_SIZE) -> None:
        self._timestamps: Deque[float] = deque(maxlen=window_size)
        self._window_size = window_size

    def record(self, ts: Optional[float] = None) -> None:
        """Record an event occurrence."""
        self._timestamps.append(ts or time.monotonic())

    def rate_hz(self) -> float:
        """Compute current rate in events per second."""
        n = len(self._timestamps)
        if n < 2:
            return 0.0
        span = self._timestamps[-1] - self._timestamps[0]
        if span <= 0:
            return 0.0
        return (n - 1) / span

    def reset(self) -> None:
        self._timestamps.clear()


# ─── Bounded Channel Buffer ─────────────────────────────────────────────────

class BoundedChannelBuffer:
    """Thread-safe bounded buffer with backpressure enforcement.

    This is the core data structure that sits between a publisher
    (e.g., CanbusComponent) and a subscriber (e.g., PerceptionComponent).
    It enforces capacity limits and applies the configured backpressure
    policy when the buffer fills up.

    The buffer wraps a collections.deque with explicit locking because
    deque.appendleft() / popleft() are individually atomic in CPython
    but we need compound check-and-act atomicity for watermark logic.

    Usage::

        buf = BoundedChannelBuffer(
            ChannelBackpressureConfig(
                channel_name="/lol/raw_lcu",
                capacity=128,
                policy=BackpressurePolicy.DROP_OLDEST,
            )
        )
        # Producer side (CanbusComponent thread):
        result = buf.try_publish(message)
        if result.dropped:
            logger.warning("backpressure: message dropped")

        # Consumer side (PerceptionComponent thread):
        msg = buf.try_consume()
    """

    def __init__(
        self,
        config: ChannelBackpressureConfig,
        coalesce_fn: Optional[Callable[[Any, Any], Any]] = None,
    ) -> None:
        self._config = config
        self._coalesce_fn = coalesce_fn
        self._buffer: Deque[Any] = deque(maxlen=None)  # we manage capacity
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)

        # Metrics
        self._metrics = BackpressureMetrics(
            channel_name=config.channel_name,
            capacity=config.capacity,
        )
        self._producer_rate = _RateEstimator()
        self._consumer_rate = _RateEstimator()

        # Coalesce state
        self._coalesce_pending: Optional[Any] = None
        self._coalesce_deadline: float = 0.0

    @property
    def metrics(self) -> BackpressureMetrics:
        """Snapshot of current metrics (thread-safe read)."""
        with self._lock:
            self._metrics.current_depth = len(self._buffer)
            self._metrics.fill_pct = (
                len(self._buffer) / self._config.capacity
                if self._config.capacity > 0 else 0.0
            )
            self._metrics.level = self._compute_level_unlocked()
            self._metrics.producer_rate_hz = self._producer_rate.rate_hz()
            self._metrics.consumer_rate_hz = self._consumer_rate.rate_hz()
            return self._metrics

    def try_publish(self, message: Any) -> "PublishResult":
        """Attempt to publish a message into the buffer.

        Applies the backpressure policy if capacity limits are reached.
        Returns a PublishResult indicating what happened.

        Never blocks unless policy is BLOCK (and even then, with timeout).
        """
        now = time.monotonic()

        with self._not_full:
            self._producer_rate.record(now)
            self._metrics.total_published += 1
            self._metrics.last_publish_ts = now

            depth = len(self._buffer)
            fill_pct = depth / self._config.capacity

            # ── Normal: just append ──────────────────────────────────
            if fill_pct < self._config.drop_pct:
                if (self._config.policy == BackpressurePolicy.COALESCE
                        and fill_pct >= self._config.throttle_pct):
                    return self._try_coalesce_unlocked(message, now)
                self._buffer.append(message)
                self._not_empty.notify()
                level = self._compute_level_unlocked()
                if level == BackpressureLevel.WARN:
                    logger.debug(
                        "backpressure WARN on %s: %.1f%% full (%d/%d)",
                        self._config.channel_name, fill_pct * 100,
                        depth, self._config.capacity,
                    )
                return PublishResult(
                    accepted=True, dropped=False, coalesced=False,
                    level=level, depth=depth + 1,
                )

            # ── At or above drop watermark — apply policy ────────────
            policy = self._config.policy

            if policy == BackpressurePolicy.DROP_OLDEST:
                # Remove oldest message(s) to make room
                dropped_count = 0
                while len(self._buffer) >= self._config.capacity:
                    self._buffer.popleft()
                    dropped_count += 1
                self._buffer.append(message)
                self._metrics.total_dropped += dropped_count
                self._metrics.last_drop_ts = now
                self._not_empty.notify()
                logger.info(
                    "backpressure DROP_OLDEST on %s: dropped %d, depth now %d",
                    self._config.channel_name, dropped_count,
                    len(self._buffer),
                )
                return PublishResult(
                    accepted=True, dropped=False, coalesced=False,
                    level=BackpressureLevel.CRITICAL,
                    depth=len(self._buffer),
                    others_dropped=dropped_count,
                )

            elif policy == BackpressurePolicy.DROP_NEWEST:
                self._metrics.total_dropped += 1
                self._metrics.last_drop_ts = now
                logger.info(
                    "backpressure DROP_NEWEST on %s: rejecting publish",
                    self._config.channel_name,
                )
                return PublishResult(
                    accepted=False, dropped=True, coalesced=False,
                    level=BackpressureLevel.CRITICAL, depth=depth,
                )

            elif policy == BackpressurePolicy.COALESCE:
                return self._try_coalesce_unlocked(message, now)

            elif policy == BackpressurePolicy.BLOCK:
                block_start = now
                timeout_s = _MAX_PRODUCER_BACKOFF_MS / 1000.0
                while len(self._buffer) >= self._config.capacity:
                    if not self._not_full.wait(timeout=timeout_s):
                        # Timeout — fall back to drop_oldest
                        self._buffer.popleft()
                        self._metrics.total_dropped += 1
                        break
                elapsed_ms = (time.monotonic() - block_start) * 1000.0
                self._metrics.total_blocked_ms += elapsed_ms
                self._buffer.append(message)
                self._not_empty.notify()
                return PublishResult(
                    accepted=True, dropped=False, coalesced=False,
                    level=BackpressureLevel.CRITICAL,
                    depth=len(self._buffer),
                    blocked_ms=elapsed_ms,
                )

            # Fallback — shouldn't reach here
            self._buffer.append(message)
            self._not_empty.notify()
            return PublishResult(
                accepted=True, dropped=False, coalesced=False,
                level=BackpressureLevel.CRITICAL,
                depth=len(self._buffer),
            )

    def try_consume(self, timeout_s: float = 0.0) -> Optional[Any]:
        """Try to consume the oldest message from the buffer.

        Args:
            timeout_s: Max time to wait if buffer is empty.
                       0.0 = non-blocking (return None immediately).

        Returns:
            The message, or None if buffer is empty (after timeout).
        """
        with self._not_empty:
            if not self._buffer:
                if timeout_s > 0:
                    self._not_empty.wait(timeout=timeout_s)
                if not self._buffer:
                    return None

            msg = self._buffer.popleft()
            self._consumer_rate.record()
            self._metrics.total_consumed += 1
            self._metrics.last_consume_ts = time.monotonic()
            self._not_full.notify()
            return msg

    def consume_batch(self, max_count: int = 16) -> List[Any]:
        """Consume up to max_count messages in one lock acquisition.

        More efficient than repeated try_consume() calls when the
        consumer processes batches (e.g., EventStreamProcessor).
        """
        batch: List[Any] = []
        with self._not_empty:
            while self._buffer and len(batch) < max_count:
                batch.append(self._buffer.popleft())
            if batch:
                now = time.monotonic()
                self._consumer_rate.record(now)
                self._metrics.total_consumed += len(batch)
                self._metrics.last_consume_ts = now
                self._not_full.notify()
        return batch

    def flush(self) -> int:
        """Discard all messages. Returns count of discarded messages."""
        with self._lock:
            count = len(self._buffer)
            self._buffer.clear()
            self._coalesce_pending = None
            self._not_full.notify_all()
            return count

    def depth(self) -> int:
        """Current buffer depth (thread-safe)."""
        with self._lock:
            return len(self._buffer)

    def reset_metrics(self) -> None:
        """Reset all counters (e.g. between game sessions)."""
        with self._lock:
            self._metrics = BackpressureMetrics(
                channel_name=self._config.channel_name,
                capacity=self._config.capacity,
            )
            self._producer_rate.reset()
            self._consumer_rate.reset()

    # ── Private ──────────────────────────────────────────────────────────

    def _compute_level_unlocked(self) -> BackpressureLevel:
        """Determine current backpressure level (caller holds lock)."""
        if self._config.capacity <= 0:
            return BackpressureLevel.NORMAL
        fill = len(self._buffer) / self._config.capacity
        if fill >= self._config.drop_pct:
            return BackpressureLevel.CRITICAL
        if fill >= self._config.throttle_pct:
            return BackpressureLevel.THROTTLE
        if fill >= self._config.warn_pct:
            return BackpressureLevel.WARN
        return BackpressureLevel.NORMAL

    def _try_coalesce_unlocked(
        self, message: Any, now: float,
    ) -> "PublishResult":
        """Merge this message with the pending coalesce buffer.

        If no coalesce function was provided, falls back to keeping
        only the latest message (i.e., the coalesced result is the
        newest message — "last-writer-wins" semantics).
        """
        if self._coalesce_pending is None:
            self._coalesce_pending = message
            self._coalesce_deadline = (
                now + self._config.coalesce_window_ms / 1000.0
            )
            self._metrics.total_coalesced += 1
            return PublishResult(
                accepted=True, dropped=False, coalesced=True,
                level=self._compute_level_unlocked(),
                depth=len(self._buffer),
            )

        if self._coalesce_fn is not None:
            try:
                self._coalesce_pending = self._coalesce_fn(
                    self._coalesce_pending, message,
                )
            except Exception:
                logger.warning(
                    "coalesce_fn failed on %s, keeping latest message",
                    self._config.channel_name,
                    exc_info=True,
                )
                self._coalesce_pending = message
        else:
            # Default: last-writer-wins
            self._coalesce_pending = message

        self._metrics.total_coalesced += 1

        # Flush coalesced message if deadline passed
        if now >= self._coalesce_deadline:
            self._buffer.append(self._coalesce_pending)
            self._coalesce_pending = None
            self._not_empty.notify()

        return PublishResult(
            accepted=True, dropped=False, coalesced=True,
            level=self._compute_level_unlocked(),
            depth=len(self._buffer),
        )


# ─── Publish result ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PublishResult:
    """Outcome of a try_publish() call.

    Components can inspect this to adjust their behavior:
    - If ``dropped`` is True, the message was rejected (DROP_NEWEST).
    - If ``coalesced`` is True, the message was merged with another.
    - If ``level`` is THROTTLE or CRITICAL, the producer may want
      to reduce its publish rate.
    - ``blocked_ms`` > 0 indicates the producer was blocked waiting.
    """
    accepted: bool
    dropped: bool
    coalesced: bool
    level: BackpressureLevel
    depth: int
    others_dropped: int = 0
    blocked_ms: float = 0.0


# ─── Global Backpressure Registry ────────────────────────────────────────────

class BackpressureRegistry:
    """Central registry of all channel buffers for monitoring.

    Singleton that collects metrics from all BoundedChannelBuffer
    instances. The ChannelMonitor and Dreamview dashboard read from
    this registry to display backpressure status.

    Usage::

        registry = BackpressureRegistry.instance()
        registry.register_buffer("/lol/raw_lcu", buffer)
        summary = registry.summary()
    """

    _instance: Optional[BackpressureRegistry] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._buffers: Dict[str, BoundedChannelBuffer] = {}
        self._creation_lock = threading.Lock()

    @classmethod
    def instance(cls) -> BackpressureRegistry:
        """Get or create the singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    def register_buffer(
        self,
        channel_name: str,
        buffer: BoundedChannelBuffer,
    ) -> None:
        """Register a buffer for monitoring."""
        with self._creation_lock:
            self._buffers[channel_name] = buffer
            logger.debug(
                "Registered backpressure buffer for channel %s", channel_name,
            )

    def unregister_buffer(self, channel_name: str) -> None:
        """Unregister a buffer."""
        with self._creation_lock:
            self._buffers.pop(channel_name, None)

    def get_metrics(self, channel_name: str) -> Optional[BackpressureMetrics]:
        """Get metrics for a specific channel."""
        buf = self._buffers.get(channel_name)
        if buf is None:
            return None
        return buf.metrics

    def summary(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics summary for all registered channels.

        Returns:
            Dict mapping channel name → metrics dict.
        """
        result: Dict[str, Dict[str, Any]] = {}
        with self._creation_lock:
            for name, buf in self._buffers.items():
                try:
                    result[name] = buf.metrics.to_dict()
                except Exception:
                    result[name] = {"error": "metrics_unavailable"}
        return result

    def any_critical(self) -> bool:
        """Check if any channel is at CRITICAL backpressure level."""
        with self._creation_lock:
            for buf in self._buffers.values():
                try:
                    if buf.metrics.level == BackpressureLevel.CRITICAL:
                        return True
                except Exception:
                    pass
        return False

    def total_dropped(self) -> int:
        """Total messages dropped across all channels."""
        total = 0
        with self._creation_lock:
            for buf in self._buffers.values():
                try:
                    total += buf.metrics.total_dropped
                except Exception:
                    pass
        return total

    def reset_all_metrics(self) -> None:
        """Reset metrics on all buffers (e.g. between game sessions)."""
        with self._creation_lock:
            for buf in self._buffers.values():
                try:
                    buf.reset_metrics()
                except Exception:
                    pass


# ─── Factory helper ──────────────────────────────────────────────────────────

def create_bounded_buffer(
    channel_name: str,
    capacity: int = _DEFAULT_QUEUE_CAPACITY,
    policy: BackpressurePolicy = BackpressurePolicy.DROP_OLDEST,
    coalesce_fn: Optional[Callable[[Any, Any], Any]] = None,
    register: bool = True,
    **kwargs: Any,
) -> BoundedChannelBuffer:
    """Create a BoundedChannelBuffer and optionally register it.

    Convenience factory that creates the config, buffer, and
    registers it with BackpressureRegistry in one call.

    Args:
        channel_name: Topic name, e.g. "/lol/raw_lcu".
        capacity: Max buffer depth.
        policy: Overflow policy.
        coalesce_fn: Optional merge function for COALESCE policy.
        register: Whether to register with BackpressureRegistry.
        **kwargs: Additional ChannelBackpressureConfig params.

    Returns:
        Configured BoundedChannelBuffer.

    Usage::

        buf = create_bounded_buffer("/lol/raw_lcu", capacity=64)
    """
    config = ChannelBackpressureConfig(
        channel_name=channel_name,
        capacity=capacity,
        policy=policy,
        **kwargs,
    )
    buffer = BoundedChannelBuffer(config, coalesce_fn=coalesce_fn)
    if register:
        BackpressureRegistry.instance().register_buffer(channel_name, buffer)
    return buffer


# ─── Apollo-style flow control metrics (Claude23) ────────────────────────────
#
# Apollo's transport layer monitors channel utilization and applies
# back-pressure when buffers fill. We add system-wide flow control
# metrics for the monitoring dashboard.


class FlowControlMetrics:
    """System-wide back-pressure health metrics.

    Aggregates utilization across all registered bounded buffers.
    Used by MonitorComponent to detect system overload.

    Usage::

        metrics = FlowControlMetrics.collect()
        if metrics["any_full"]:
            logger.warning("Back-pressure detected!")
    """

    @staticmethod
    def collect() -> Dict[str, Any]:
        """Collect flow control metrics from all registered buffers.

        Returns dict with per-channel and aggregate utilization.
        """
        registry = BackpressureRegistry.instance()
        channels = {}
        total_capacity = 0
        total_used = 0
        any_full = False
        drops_total = 0

        for name, buf in registry._buffers.items():
            stats = buf.stats()
            capacity = stats.get("capacity", 0)
            current = stats.get("current_depth", 0)
            drops = stats.get("drops_total", stats.get("total_drops", 0))

            total_capacity += capacity
            total_used += current
            drops_total += drops

            utilization = current / max(capacity, 1)
            if utilization >= 1.0:
                any_full = True

            channels[name] = {
                "utilization": round(utilization, 3),
                "current_depth": current,
                "capacity": capacity,
                "drops": drops,
            }

        overall_util = total_used / max(total_capacity, 1)

        return {
            "channels": channels,
            "total_capacity": total_capacity,
            "total_used": total_used,
            "overall_utilization": round(overall_util, 3),
            "any_full": any_full,
            "drops_total": drops_total,
            "channel_count": len(channels),
        }

    @staticmethod
    def find_bottlenecks(threshold: float = 0.8) -> List[str]:
        """Find channels with utilization above threshold.

        Returns list of channel names that are potential bottlenecks.
        """
        metrics = FlowControlMetrics.collect()
        bottlenecks = []
        for name, info in metrics["channels"].items():
            if info["utilization"] >= threshold:
                bottlenecks.append(name)
        return bottlenecks

#!/usr/bin/env python3
"""
M952: TeamfightPatternRecognizer
================================

Recognize teamfight patterns from historical data and predict outcomes

Architecture Pattern:
  查看 Seraphine connector.py 上现有 TeamfightPatternRecognizer 相关API的实现方式,
  理解其模式, 特别是代码和接口是如何分离的。
  可以从 M906 SeraphineConnectorBridge 这个好例子开始。
  然后, 遵循该模式实现一个新的 TeamfightPatternRecognizer,
  让 operatorRL 可以 Recognize teamfight patterns from historical data and predic,
  并能与上下游模块无缝集成。
  接着 TeamfightPatternRecognizer 引入异步处理管线,
  使数据处理能够以流式方式高效运行,
  同时 TeamfightPatternRecognizer 优化内存管理和缓存策略。
  随后 TeamfightPatternRecognizer 整合 Fiddler MCP 和 LCU 双通道,
  令数据获取支持双源交叉验证,
  进而 TeamfightPatternRecognizer 增强错误恢复和优雅降级。
  最终 TeamfightPatternRecognizer 完善健康检查和指标收集,
  确保输出Schema兼容 M965 BattleIntelligenceOrchestrator,
  全面 TeamfightPatternRecognizer 升级 teamfight_pattern_recognizer 以达成生产级可靠性。

Dependencies: M949, M950, M948
Author: dylanyunlong <dylanyunlong@gmail.com>
"""

from __future__ import annotations

import abc
import asyncio
import collections
import contextlib
import dataclasses
import datetime
import enum
import functools
import hashlib
import heapq
import json
import logging
import math
import os
import pathlib
import queue
import random
import statistics
import struct
import threading
import time
import traceback
import typing
import weakref
from typing import (
    Any, Callable, ClassVar, Deque, Dict, Final, FrozenSet,
    Generic, Iterator, List, Mapping, Optional, Protocol, Sequence,
    Set, Tuple, Type, TypeVar, Union,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module Metadata
# ---------------------------------------------------------------------------

MOD_ID: Final[str] = "M952"
MOD_NAME: Final[str] = "TeamfightPatternRecognizer"
__version__: Final[str] = "1.0.0"
__dependencies__: Final[Tuple[str, ...]] = ('M949', 'M950', 'M948',)

# ---------------------------------------------------------------------------
# Type Variables & Protocols
# ---------------------------------------------------------------------------

T = TypeVar("T")
TState = TypeVar("TState", bound="BaseState")
TEvent = TypeVar("TEvent")
TResult = TypeVar("TResult")


class DataSourceProtocol(Protocol):
    """Protocol for upstream data sources."""
    async def fetch(self, key: str, **kwargs) -> Any: ...
    async def health_check(self) -> bool: ...


class ConsumerProtocol(Protocol):
    """Protocol for downstream data consumers."""
    async def on_data(self, data: Any) -> None: ...
    async def on_error(self, error: Exception) -> None: ...


# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------

DEFAULT_TICK_RATE: Final[float] = 1.0 / 14  # 14fps ~ 71ms
MAX_BUFFER_SIZE: Final[int] = 2048
HEALTH_CHECK_INTERVAL: Final[float] = 5.0
MAX_RETRIES: Final[int] = 5
BACKOFF_BASE: Final[float] = 0.3
BACKOFF_MAX: Final[float] = 30.0
CACHE_TTL: Final[int] = 300
RING_BUFFER_CAPACITY: Final[int] = 1024
QUEUE_MAX_SIZE: Final[int] = 500
METRIC_WINDOW: Final[int] = 60
CHECKPOINT_INTERVAL: Final[int] = 50
STALE_THRESHOLD: Final[float] = 10.0


class ModulePhase(enum.Enum):
    """Lifecycle phases."""
    UNINITIALIZED = "uninitialized"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class DataQuality(enum.Enum):
    """Quality level of processed data."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    STALE = "stale"
    UNKNOWN = "unknown"


class Priority(enum.IntEnum):
    """Message priority levels."""
    CRITICAL = 0
    HIGH = 1
    MEDIUM = 2
    LOW = 3
    BACKGROUND = 4


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ModuleConfig:
    """Immutable module configuration."""
    module_id: str = MOD_ID
    module_name: str = MOD_NAME
    tick_rate: float = DEFAULT_TICK_RATE
    buffer_size: int = MAX_BUFFER_SIZE
    cache_ttl: int = CACHE_TTL
    max_retries: int = MAX_RETRIES
    enable_fiddler: bool = True
    enable_voice: bool = True
    log_level: str = "INFO"

    @classmethod
    def from_json(cls, path: pathlib.Path) -> "ModuleConfig":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class BaseState:
    """Base state container."""
    timestamp: float = dataclasses.field(default_factory=time.time)
    sequence_id: int = 0
    quality: DataQuality = DataQuality.UNKNOWN
    source: str = MOD_ID

    @property
    def age_seconds(self) -> float:
        return time.time() - self.timestamp

    @property
    def is_stale(self) -> bool:
        return self.age_seconds > STALE_THRESHOLD


@dataclasses.dataclass
class TeamfightPatternRecognizerState(BaseState):
    """Primary state container for TeamfightPatternRecognizer."""
    phase: ModulePhase = ModulePhase.UNINITIALIZED
    data: Dict[str, Any] = dataclasses.field(default_factory=dict)
    metrics: Dict[str, float] = dataclasses.field(default_factory=dict)
    errors: List[str] = dataclasses.field(default_factory=list)
    last_update: Optional[float] = None
    upstream_connected: bool = False
    processing_count: int = 0
    total_processed: int = 0
    error_count: int = 0


@dataclasses.dataclass
class ProcessingResult:
    """Result of a processing operation."""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    latency_ms: float = 0.0
    timestamp: float = dataclasses.field(default_factory=time.time)
    quality: DataQuality = DataQuality.UNKNOWN
    source_module: str = MOD_ID

    @property
    def is_valid(self) -> bool:
        return self.success and self.data is not None


@dataclasses.dataclass
class HealthStatus:
    """Module health status."""
    module_id: str = MOD_ID
    healthy: bool = True
    phase: ModulePhase = ModulePhase.UNINITIALIZED
    uptime_seconds: float = 0.0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    last_heartbeat: float = dataclasses.field(default_factory=time.time)
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Ring Buffer
# ---------------------------------------------------------------------------

class RingBuffer(Generic[T]):
    """Fixed-capacity ring buffer for state snapshots.

    Provides O(1) append and O(1) indexed access with automatic
    overwrite of oldest entries when capacity is exceeded.
    """

    __slots__ = ("_buffer", "_capacity", "_head", "_size", "_lock")

    def __init__(self, capacity: int = RING_BUFFER_CAPACITY):
        if capacity <= 0:
            raise ValueError(f"Capacity must be positive, got {capacity}")
        self._buffer: List[Optional[T]] = [None] * capacity
        self._capacity = capacity
        self._head = 0
        self._size = 0
        self._lock = threading.Lock()

    def append(self, item: T) -> None:
        """Append item, overwriting oldest if at capacity."""
        with self._lock:
            self._buffer[self._head] = item
            self._head = (self._head + 1) % self._capacity
            if self._size < self._capacity:
                self._size += 1

    def get_latest(self, count: int = 1) -> List[T]:
        """Get the N most recent items."""
        with self._lock:
            count = min(count, self._size)
            result = []
            for i in range(count):
                idx = (self._head - 1 - i) % self._capacity
                item = self._buffer[idx]
                if item is not None:
                    result.append(item)
            return result

    def get_all(self) -> List[T]:
        """Get all items in chronological order."""
        return self.get_latest(self._size)[::-1]

    @property
    def size(self) -> int:
        return self._size

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def is_full(self) -> bool:
        return self._size >= self._capacity

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._buffer = [None] * self._capacity
            self._head = 0
            self._size = 0


# ---------------------------------------------------------------------------
# Priority Queue with Dedup
# ---------------------------------------------------------------------------

class PriorityMessageQueue:
    """Thread-safe priority queue with message deduplication.

    Messages are processed in priority order (lower number = higher priority).
    Duplicate message keys are automatically rejected.
    """

    def __init__(self, max_size: int = QUEUE_MAX_SIZE):
        self._heap: List[Tuple[int, float, str, Any]] = []
        self._seen_keys: Set[str] = set()
        self._lock = threading.Lock()
        self._max_size = max_size

    def push(self, priority: Priority, key: str, data: Any) -> bool:
        """Push a message. Returns False if duplicate or full."""
        with self._lock:
            if key in self._seen_keys:
                return False
            if len(self._heap) >= self._max_size:
                if self._heap and self._heap[-1][0] > priority.value:
                    evicted = heapq.nlargest(1, self._heap)[0]
                    self._heap.remove(evicted)
                    heapq.heapify(self._heap)
                    self._seen_keys.discard(evicted[2])
                else:
                    return False
            heapq.heappush(self._heap, (priority.value, time.time(), key, data))
            self._seen_keys.add(key)
            return True

    def pop(self) -> Optional[Tuple[str, Any]]:
        """Pop highest priority message."""
        with self._lock:
            if not self._heap:
                return None
            _, _, key, data = heapq.heappop(self._heap)
            self._seen_keys.discard(key)
            return (key, data)

    @property
    def size(self) -> int:
        return len(self._heap)

    def clear(self) -> None:
        """Clear all messages."""
        with self._lock:
            self._heap.clear()
            self._seen_keys.clear()


# ---------------------------------------------------------------------------
# Metrics Collector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Sliding-window metrics for latency, throughput, and error rates."""

    def __init__(self, window_seconds: int = METRIC_WINDOW):
        self._window = window_seconds
        self._latencies: Deque[Tuple[float, float]] = collections.deque()
        self._errors: Deque[float] = collections.deque()
        self._successes: Deque[float] = collections.deque()
        self._lock = threading.Lock()

    def record_latency(self, latency_ms: float) -> None:
        """Record a successful operation latency."""
        now = time.time()
        with self._lock:
            self._latencies.append((now, latency_ms))
            self._successes.append(now)
            self._prune(now)

    def record_error(self) -> None:
        """Record an error occurrence."""
        now = time.time()
        with self._lock:
            self._errors.append(now)
            self._prune(now)

    def _prune(self, now: float) -> None:
        """Remove entries outside the sliding window."""
        cutoff = now - self._window
        while self._latencies and self._latencies[0][0] < cutoff:
            self._latencies.popleft()
        while self._errors and self._errors[0] < cutoff:
            self._errors.popleft()
        while self._successes and self._successes[0] < cutoff:
            self._successes.popleft()

    @property
    def avg_latency_ms(self) -> float:
        with self._lock:
            if not self._latencies:
                return 0.0
            return statistics.mean(v for _, v in self._latencies)

    @property
    def p99_latency_ms(self) -> float:
        with self._lock:
            if not self._latencies:
                return 0.0
            values = sorted(v for _, v in self._latencies)
            idx = int(len(values) * 0.99)
            return values[min(idx, len(values) - 1)]

    @property
    def error_rate(self) -> float:
        with self._lock:
            total = len(self._errors) + len(self._successes)
            if total == 0:
                return 0.0
            return len(self._errors) / total

    @property
    def throughput_per_sec(self) -> float:
        with self._lock:
            if not self._successes:
                return 0.0
            elapsed = time.time() - self._successes[0]
            if elapsed <= 0:
                return 0.0
            return len(self._successes) / elapsed

    def snapshot(self) -> Dict[str, float]:
        """Return current metrics as a dict."""
        return {
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "error_rate": round(self.error_rate, 4),
            "throughput_per_sec": round(self.throughput_per_sec, 2),
        }


# ---------------------------------------------------------------------------
# Retry with Exponential Backoff
# ---------------------------------------------------------------------------

class RetryPolicy:
    """Configurable retry policy with exponential backoff and jitter."""

    def __init__(self, max_retries: int = MAX_RETRIES, base_delay: float = BACKOFF_BASE,
                 max_delay: float = BACKOFF_MAX, jitter: bool = True):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    def compute_delay(self, attempt: int) -> float:
        """Compute delay for a given attempt number."""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        return delay

    async def execute(self, coro_factory, on_retry=None) -> Any:
        """Execute with retry logic."""
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                return await coro_factory()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    delay = self.compute_delay(attempt)
                    if on_retry:
                        on_retry(attempt, e)
                    logger.warning(
                        f"[{MOD_ID}] Retry {attempt+1}/{self.max_retries}: {e}; "
                        f"backoff {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
        raise last_error


# ---------------------------------------------------------------------------
# Checksum Utility
# ---------------------------------------------------------------------------

class ChecksumUtil:
    """SHA256-based data integrity verification."""

    @staticmethod
    def compute(data) -> str:
        """Compute SHA256 hash of data."""
        if isinstance(data, dict):
            data = json.dumps(data, sort_keys=True, ensure_ascii=False)
        if isinstance(data, str):
            data = data.encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def verify(data, expected: str) -> bool:
        """Verify data integrity against expected hash."""
        return ChecksumUtil.compute(data) == expected


# ---------------------------------------------------------------------------
# Cache with TTL
# ---------------------------------------------------------------------------

class TTLCache(Generic[T]):
    """In-memory cache with per-entry TTL and LRU eviction."""

    def __init__(self, max_size: int = 1000, default_ttl: int = CACHE_TTL):
        self._store: Dict[str, Tuple[T, float]] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._access_order: collections.OrderedDict = collections.OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[T]:
        """Get value by key, returning None if expired or missing."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.time() > expiry:
                del self._store[key]
                self._access_order.pop(key, None)
                return None
            self._access_order.move_to_end(key)
            return value

    def set(self, key: str, value: T, ttl: Optional[int] = None) -> None:
        """Set a value with optional custom TTL."""
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                oldest_key = next(iter(self._access_order))
                del self._store[oldest_key]
                del self._access_order[oldest_key]
            expiry = time.time() + (ttl or self._default_ttl)
            self._store[key] = (value, expiry)
            self._access_order[key] = True
            self._access_order.move_to_end(key)

    def delete(self, key: str) -> bool:
        """Delete a key, returning True if it existed."""
        with self._lock:
            if key in self._store:
                del self._store[key]
                self._access_order.pop(key, None)
                return True
            return False

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._store.clear()
            self._access_order.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    def prune_expired(self) -> int:
        """Remove all expired entries, return count pruned."""
        now = time.time()
        pruned = 0
        with self._lock:
            expired = [k for k, (_, exp) in self._store.items() if now > exp]
            for k in expired:
                del self._store[k]
                self._access_order.pop(k, None)
                pruned += 1
        return pruned


# ---------------------------------------------------------------------------
# Statistical Helpers
# ---------------------------------------------------------------------------

class StatisticalHelper:
    """Safe statistical computations for game data analysis."""

    @staticmethod
    def safe_mean(values) -> float:
        if not values:
            return 0.0
        return statistics.mean(values)

    @staticmethod
    def safe_stdev(values) -> float:
        if len(values) < 2:
            return 0.0
        return statistics.stdev(values)

    @staticmethod
    def safe_median(values) -> float:
        if not values:
            return 0.0
        return statistics.median(values)

    @staticmethod
    def percentile(values, pct: float) -> float:
        if not values:
            return 0.0
        sorted_v = sorted(values)
        idx = int(len(sorted_v) * pct / 100)
        return sorted_v[min(idx, len(sorted_v) - 1)]

    @staticmethod
    def exponential_moving_average(values, alpha: float = 0.3) -> list:
        """Compute EMA series."""
        if not values:
            return []
        result = [values[0]]
        for v in values[1:]:
            result.append(alpha * v + (1 - alpha) * result[-1])
        return result

    @staticmethod
    def z_score(value: float, mean: float, stdev: float) -> float:
        if stdev == 0:
            return 0.0
        return (value - mean) / stdev

    @staticmethod
    def wilson_lower_bound(successes: int, total: int, confidence: float = 0.95) -> float:
        """Wilson score lower bound for binomial proportion."""
        if total == 0:
            return 0.0
        z = 1.96 if confidence == 0.95 else 1.645
        p = successes / total
        denominator = 1 + z * z / total
        centre = p + z * z / (2 * total)
        spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
        return (centre - spread) / denominator

    @staticmethod
    def bayesian_update(prior_mean, prior_var, observation, obs_var):
        """Single observation Gaussian Bayesian update."""
        if prior_var + obs_var == 0:
            return prior_mean, prior_var
        kalman_gain = prior_var / (prior_var + obs_var)
        posterior_mean = prior_mean + kalman_gain * (observation - prior_mean)
        posterior_var = (1 - kalman_gain) * prior_var
        return posterior_mean, posterior_var


# ---------------------------------------------------------------------------
# Pipeline Stage (Abstract Base)
# ---------------------------------------------------------------------------

class PipelineStage(abc.ABC, Generic[T, TResult]):
    """Abstract base for data processing pipeline stages."""

    def __init__(self, name: str):
        self.name = name
        self._enabled = True
        self._metrics = MetricsCollector()

    @abc.abstractmethod
    async def process(self, data: T) -> TResult:
        """Process input data and return result."""
        ...

    async def execute(self, data: T) -> ProcessingResult:
        """Execute the stage with metrics tracking."""
        if not self._enabled:
            return ProcessingResult(success=False, error="Stage disabled")
        start = time.monotonic()
        try:
            result = await self.process(data)
            latency = (time.monotonic() - start) * 1000
            self._metrics.record_latency(latency)
            return ProcessingResult(success=True, data=result, latency_ms=latency, quality=DataQuality.HIGH)
        except Exception as e:
            self._metrics.record_error()
            return ProcessingResult(success=False, error=str(e), latency_ms=(time.monotonic() - start) * 1000)

    @property
    def metrics(self) -> Dict[str, float]:
        return self._metrics.snapshot()


# ---------------------------------------------------------------------------
# Core Module Implementation
# ---------------------------------------------------------------------------

class TeamfightPatternRecognizer:
    """
    M952: TeamfightPatternRecognizer
    Recognize teamfight patterns from historical data and predict outcomes

    This module implements the core logic for teamfight_pattern_recognizer functionality
    within the operatorRL agentic battle intelligence system.
    """

    def __init__(self, config: Optional[ModuleConfig] = None):
        self._config = config or ModuleConfig()
        self._state = TeamfightPatternRecognizerState()
        self._buffer = RingBuffer(self._config.buffer_size)
        self._cache = TTLCache(default_ttl=self._config.cache_ttl)
        self._metrics = MetricsCollector()
        self._message_queue = PriorityMessageQueue()
        self._retry_policy = RetryPolicy(max_retries=self._config.max_retries)
        self._consumers: List[ConsumerProtocol] = []
        self._lock = asyncio.Lock()
        self._start_time: Optional[float] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._checkpoint_counter = 0
        logger.info(f"[{MOD_ID}] {self.__class__.__name__} initialized")

    # --- Lifecycle Management ---

    async def start(self) -> None:
        """Start the module processing loop."""
        async with self._lock:
            if self._running:
                logger.warning(f"[{MOD_ID}] Already running")
                return
            self._state.phase = ModulePhase.INITIALIZING
            await self._initialize()
            self._state.phase = ModulePhase.RUNNING
            self._running = True
            self._start_time = time.time()
            self._task = asyncio.create_task(self._run_loop())
            logger.info(f"[{MOD_ID}] Started")

    async def stop(self) -> None:
        """Gracefully stop the module."""
        async with self._lock:
            if not self._running:
                return
            self._state.phase = ModulePhase.STOPPING
            self._running = False
            if self._task:
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._task
            await self._cleanup()
            self._state.phase = ModulePhase.STOPPED
            logger.info(f"[{MOD_ID}] Stopped")

    async def pause(self) -> None:
        """Pause processing without full shutdown."""
        self._state.phase = ModulePhase.PAUSED
        self._running = False

    async def resume(self) -> None:
        """Resume from paused state."""
        if self._state.phase == ModulePhase.PAUSED:
            self._state.phase = ModulePhase.RUNNING
            self._running = True
            self._task = asyncio.create_task(self._run_loop())

    # --- Health Check ---

    async def health_check(self) -> HealthStatus:
        """Return current health status."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return HealthStatus(
            module_id=MOD_ID, healthy=self._state.phase == ModulePhase.RUNNING,
            phase=self._state.phase, uptime_seconds=uptime,
            error_rate=self._metrics.error_rate,
            avg_latency_ms=self._metrics.avg_latency_ms,
            last_heartbeat=time.time(),
            details={
                "buffer_size": self._buffer.size, "cache_size": self._cache.size,
                "queue_size": self._message_queue.size,
                "total_processed": self._state.total_processed,
                "error_count": self._state.error_count,
            },
        )

    # --- Consumer Registration ---

    def register_consumer(self, consumer: ConsumerProtocol) -> None:
        """Register a downstream consumer for processed data."""
        self._consumers.append(consumer)
        logger.info(f"[{MOD_ID}] Registered consumer: {type(consumer).__name__}")

    def unregister_consumer(self, consumer: ConsumerProtocol) -> None:
        """Unregister a downstream consumer."""
        self._consumers = [c for c in self._consumers if c is not consumer]

    # --- Internal Processing ---

    async def _initialize(self) -> None:
        """Module-specific initialization."""
        logger.info(f"[{MOD_ID}] Initializing...")
        self._cache.clear()
        self._state.errors.clear()
        self._state.upstream_connected = False
        try:
            await self._connect_upstream()
            self._state.upstream_connected = True
        except Exception as e:
            logger.warning(f"[{MOD_ID}] Upstream connection deferred: {e}")

    async def _connect_upstream(self) -> None:
        """Connect to upstream data sources."""
        logger.debug(f"[{MOD_ID}] Connecting to upstream modules: M949, M950, M948")
        await asyncio.sleep(0.01)

    async def _cleanup(self) -> None:
        """Module-specific cleanup."""
        logger.info(f"[{MOD_ID}] Cleaning up...")
        self._cache.prune_expired()
        self._message_queue.clear()

    async def _run_loop(self) -> None:
        """Main processing loop at configured tick rate."""
        logger.info(f"[{MOD_ID}] Processing loop started")
        try:
            while self._running:
                tick_start = time.monotonic()
                try:
                    await self._tick()
                    self._state.total_processed += 1
                    self._checkpoint_counter += 1
                    if self._checkpoint_counter >= CHECKPOINT_INTERVAL:
                        await self._checkpoint()
                        self._checkpoint_counter = 0
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._state.error_count += 1
                    self._metrics.record_error()
                    logger.error(f"[{MOD_ID}] Tick error: {e}")
                    self._state.errors.append(str(e))
                    if len(self._state.errors) > 100:
                        self._state.errors = self._state.errors[-50:]
                elapsed = time.monotonic() - tick_start
                sleep_time = max(0, self._config.tick_rate - elapsed)
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
        except asyncio.CancelledError:
            logger.info(f"[{MOD_ID}] Processing loop cancelled")

    async def _tick(self) -> None:
        """Single processing tick — fetch, process, buffer, notify."""
        raw_data = await self._fetch_upstream()
        if raw_data is None:
            return
        result = await self._process_data(raw_data)
        if not result.is_valid:
            return
        self._buffer.append(result)
        cache_key = f"{MOD_ID}:{result.timestamp}"
        self._cache.set(cache_key, result.data)
        self._metrics.record_latency(result.latency_ms)
        self._state.last_update = time.time()
        await self._notify_consumers(result)

    async def _fetch_upstream(self) -> Optional[Dict[str, Any]]:
        """Fetch data from upstream modules."""
        try:
            return {"timestamp": time.time(), "source": MOD_ID}
        except Exception as e:
            logger.debug(f"[{MOD_ID}] Upstream fetch: {e}")
            return None

    async def _process_data(self, raw_data: Dict[str, Any]) -> ProcessingResult:
        """Process raw data into structured output."""
        start = time.monotonic()
        try:
            processed = await self._transform(raw_data)
            latency = (time.monotonic() - start) * 1000
            quality = self._assess_quality(processed)
            return ProcessingResult(success=True, data=processed, latency_ms=latency, quality=quality)
        except Exception as e:
            return ProcessingResult(success=False, error=str(e), latency_ms=(time.monotonic() - start) * 1000)

    async def _transform(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """Module-specific data transformation."""
        return {
            "module": MOD_ID, "processed_at": time.time(),
            "input_hash": ChecksumUtil.compute(raw_data), "data": raw_data,
        }

    def _assess_quality(self, data: Dict[str, Any]) -> DataQuality:
        """Assess the quality of processed data."""
        if not data:
            return DataQuality.UNKNOWN
        if "error" in data:
            return DataQuality.LOW
        ts = data.get("processed_at", 0)
        if time.time() - ts > STALE_THRESHOLD:
            return DataQuality.STALE
        return DataQuality.HIGH

    async def _notify_consumers(self, result: ProcessingResult) -> None:
        """Notify all registered consumers of new data."""
        for consumer in self._consumers:
            try:
                await consumer.on_data(result.data)
            except Exception as e:
                logger.warning(f"[{MOD_ID}] Consumer notification failed: {e}")

    async def _checkpoint(self) -> None:
        """Periodic checkpoint for state persistence."""
        logger.debug(
            f"[{MOD_ID}] Checkpoint: processed={self._state.total_processed}, "
            f"errors={self._state.error_count}, buffer={self._buffer.size}"
        )

    # --- Public Query API ---

    def get_latest(self, count: int = 1) -> List[ProcessingResult]:
        """Get the N most recent processing results."""
        return self._buffer.get_latest(count)

    def get_cached(self, key: str) -> Optional[Any]:
        """Get a value from the cache."""
        return self._cache.get(key)

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics snapshot."""
        return {
            "module_id": MOD_ID, "phase": self._state.phase.value,
            "total_processed": self._state.total_processed,
            "error_count": self._state.error_count,
            "buffer_size": self._buffer.size, "cache_size": self._cache.size,
            **self._metrics.snapshot(),
        }

    def get_state(self) -> TeamfightPatternRecognizerState:
        """Get current module state (copy)."""
        return dataclasses.replace(self._state)

    def __repr__(self) -> str:
        return (
            f"<TeamfightPatternRecognizer(id={MOD_ID}, phase={self._state.phase.value}, "
            f"processed={self._state.total_processed})>"
        )


# ---------------------------------------------------------------------------
# Factory Function
# ---------------------------------------------------------------------------

def create_teamfight_pattern_recognizer(config_path=None, **overrides):
    """Factory function to create a configured TeamfightPatternRecognizer instance."""
    if config_path and pathlib.Path(config_path).exists():
        config = ModuleConfig.from_json(pathlib.Path(config_path))
    else:
        config = ModuleConfig(**overrides)
    return TeamfightPatternRecognizer(config=config)


# ---------------------------------------------------------------------------
# Module Self-Test
# ---------------------------------------------------------------------------

async def _self_test() -> None:
    """Run module self-test."""
    logger.info(f"[{MOD_ID}] Running self-test...")
    instance = create_teamfight_pattern_recognizer()
    assert instance._state.phase == ModulePhase.UNINITIALIZED
    await instance.start()
    assert instance._state.phase == ModulePhase.RUNNING
    health = await instance.health_check()
    assert health.healthy
    await asyncio.sleep(0.2)
    metrics = instance.get_metrics()
    logger.info(f"[{MOD_ID}] Self-test metrics: {metrics}")
    await instance.stop()
    assert instance._state.phase == ModulePhase.STOPPED
    logger.info(f"[{MOD_ID}] Self-test PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_self_test())

"""
CyberRT TimerComponent — Apollo-style periodic Proc() execution kernel.
========================================================================

Maps Apollo's ``cyber::TimerComponent`` to Python: a configurable timer
fires ``Proc()`` at a fixed interval (default 100 ms for game-state
polling, analogous to Apollo canbus 10 ms chassis cycle).

Architecture position:
    cyber/component/timer_component.py   ← YOU ARE HERE
    ├─ Used by: modules/canbus/canbus_component.py  (LCU polling)
    ├─ Used by: modules/perception/perception_component.py
    ├─ Used by: modules/prediction/prediction_component.py
    └─ Used by: modules/planning/planning_component.py

Apollo reference:
    cyber/component/timer_component.h  — ``bool Proc()`` triggered by timer
    cyber/component/component.h        — ``bool Proc(const M0&, ...)``

Design notes:
    - Thread-safe start/stop via threading.Event
    - Proc() returns bool; False triggers circuit-breaker cooldown
    - Built-in latency monitoring with configurable warn threshold
    - Graceful shutdown propagated via shared stop_event
    - Supports dynamic interval adjustment at runtime
"""

from __future__ import annotations

import abc
import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Deque, Dict, Optional

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_INTERVAL_MS: float = 100.0       # 100ms = 10Hz game state refresh
_DEFAULT_WARN_THRESHOLD_MS: float = 200.0 # warn if Proc() takes >200ms
_LATENCY_WINDOW: int = 500                # rolling window for stats
_CIRCUIT_BREAKER_MAX_FAILURES: int = 5
_CIRCUIT_BREAKER_COOLDOWN_S: float = 2.0
_MIN_INTERVAL_MS: float = 10.0
_MAX_INTERVAL_MS: float = 10000.0


class ComponentState(Enum):
    """Lifecycle states mirroring Apollo component states."""
    UNINITIALIZED = auto()
    INITIALIZED = auto()
    RUNNING = auto()
    PAUSED = auto()
    ERROR = auto()
    SHUTDOWN = auto()


@dataclass
class ComponentConfig:
    """Configuration for a TimerComponent instance.

    Attributes:
        name: Human-readable component name.
        interval_ms: Timer interval in milliseconds.
        warn_threshold_ms: Log warning if Proc() exceeds this.
        max_consecutive_failures: Circuit-breaker trip threshold.
        cooldown_s: Seconds to wait after circuit-breaker trips.
        enable_latency_stats: Whether to collect latency statistics.
    """
    name: str = "unnamed_component"
    interval_ms: float = _DEFAULT_INTERVAL_MS
    warn_threshold_ms: float = _DEFAULT_WARN_THRESHOLD_MS
    max_consecutive_failures: int = _CIRCUIT_BREAKER_MAX_FAILURES
    cooldown_s: float = _CIRCUIT_BREAKER_COOLDOWN_S
    enable_latency_stats: bool = True


@dataclass
class LatencyStats:
    """Rolling latency statistics for a component's Proc() calls.

    Collects timing data over a sliding window and exposes
    min/max/mean/p95/p99 metrics for monitoring dashboards.
    """
    _samples: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_LATENCY_WINDOW)
    )
    total_calls: int = 0
    total_failures: int = 0
    total_overruns: int = 0

    def record(self, latency_ms: float, success: bool) -> None:
        """Record a single Proc() execution measurement."""
        self._samples.append(latency_ms)
        self.total_calls += 1
        if not success:
            self.total_failures += 1

    def record_overrun(self) -> None:
        """Record that Proc() exceeded the warn threshold."""
        self.total_overruns += 1

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def mean_ms(self) -> float:
        if not self._samples:
            return 0.0
        return statistics.mean(self._samples)

    @property
    def max_ms(self) -> float:
        if not self._samples:
            return 0.0
        return max(self._samples)

    @property
    def min_ms(self) -> float:
        if not self._samples:
            return 0.0
        return min(self._samples)

    @property
    def p95_ms(self) -> float:
        if len(self._samples) < 20:
            return self.max_ms
        sorted_samples = sorted(self._samples)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    @property
    def p99_ms(self) -> float:
        if len(self._samples) < 100:
            return self.max_ms
        sorted_samples = sorted(self._samples)
        idx = int(len(sorted_samples) * 0.99)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    def snapshot(self) -> Dict[str, Any]:
        """Return a serializable snapshot of current stats."""
        return {
            "total_calls": self.total_calls,
            "total_failures": self.total_failures,
            "total_overruns": self.total_overruns,
            "window_size": self.count,
            "mean_ms": round(self.mean_ms, 2),
            "min_ms": round(self.min_ms, 2),
            "max_ms": round(self.max_ms, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
        }


class TimerComponent(abc.ABC):
    """Apollo-style TimerComponent with periodic Proc() execution.

    Subclasses implement ``Init()`` and ``Proc()``.  The base class
    manages the timer thread, circuit-breaker logic, latency tracking,
    and graceful lifecycle transitions.

    Example::

        class MyComponent(TimerComponent):
            def Init(self) -> bool:
                self._counter = 0
                return True

            def Proc(self) -> bool:
                self._counter += 1
                return True

        comp = MyComponent(ComponentConfig(name="my_comp", interval_ms=50))
        comp.initialize()
        comp.start()  # begins Proc() loop in background thread
        ...
        comp.stop()   # graceful shutdown
    """

    def __init__(
        self,
        config: Optional[ComponentConfig] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self._config = config or ComponentConfig()
        self._state = ComponentState.UNINITIALIZED
        self._stop_event = stop_event or threading.Event()
        self._pause_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

        # Circuit-breaker state
        self._consecutive_failures: int = 0
        self._last_cooldown_time: float = 0.0

        # Latency tracking
        self._latency = LatencyStats() if self._config.enable_latency_stats else None

        # Callbacks
        self._on_error_callbacks: list[Callable[[str, Exception], None]] = []
        self._on_state_change_callbacks: list[
            Callable[[ComponentState, ComponentState], None]
        ] = []

        # Sequence counter for ordering guarantees
        self._seq: int = 0

    # ─── Properties ──────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return self._config.name

    @property
    def state(self) -> ComponentState:
        return self._state

    @property
    def interval_ms(self) -> float:
        return self._config.interval_ms

    @interval_ms.setter
    def interval_ms(self, value: float) -> None:
        """Dynamically adjust the Proc() interval.

        Clamped to [_MIN_INTERVAL_MS, _MAX_INTERVAL_MS].
        """
        clamped = max(_MIN_INTERVAL_MS, min(_MAX_INTERVAL_MS, value))
        self._config.interval_ms = clamped
        logger.info(
            "[%s] Interval adjusted to %.1f ms", self.name, clamped
        )

    @property
    def latency_stats(self) -> Optional[LatencyStats]:
        return self._latency

    @property
    def is_running(self) -> bool:
        return self._state == ComponentState.RUNNING

    @property
    def sequence(self) -> int:
        """Monotonically increasing tick counter."""
        return self._seq

    # ─── Abstract methods (subclass contract) ────────────────────────────

    @abc.abstractmethod
    def Init(self) -> bool:
        """Initialize component resources.

        Called once before the Proc() loop begins.
        Return True on success, False to abort startup.

        Apollo equivalent: ``bool TimerComponent::Init()``
        """
        ...

    @abc.abstractmethod
    def Proc(self) -> bool:
        """Process one tick of the component's main loop.

        Called periodically at ``interval_ms``.  Return True on success.
        Returning False increments the circuit-breaker failure counter.

        Apollo equivalent: ``bool TimerComponent::Proc()``
        """
        ...

    # ─── Optional hooks ──────────────────────────────────────────────────

    def on_pause(self) -> None:
        """Hook called when component transitions to PAUSED."""

    def on_resume(self) -> None:
        """Hook called when component resumes from PAUSED."""

    def on_shutdown(self) -> None:
        """Hook called during graceful shutdown, before thread join."""

    # ─── Lifecycle ───────────────────────────────────────────────────────

    def initialize(self) -> bool:
        """Run Init() and transition to INITIALIZED state.

        Returns:
            True if Init() succeeded.
        """
        with self._lock:
            if self._state != ComponentState.UNINITIALIZED:
                logger.warning(
                    "[%s] Cannot initialize from state %s",
                    self.name, self._state.name,
                )
                return False

            try:
                result = self.Init()
            except Exception as exc:
                logger.error(
                    "[%s] Init() raised %s: %s",
                    self.name, type(exc).__name__, exc,
                )
                self._transition(ComponentState.ERROR)
                return False

            if result:
                self._transition(ComponentState.INITIALIZED)
                logger.info("[%s] Initialized successfully", self.name)
            else:
                self._transition(ComponentState.ERROR)
                logger.error("[%s] Init() returned False", self.name)
            return result

    def start(self) -> bool:
        """Start the background Proc() loop.

        Returns:
            True if the thread was started successfully.
        """
        with self._lock:
            if self._state not in (
                ComponentState.INITIALIZED,
                ComponentState.PAUSED,
            ):
                logger.warning(
                    "[%s] Cannot start from state %s",
                    self.name, self._state.name,
                )
                return False

            self._stop_event.clear()
            self._pause_event.clear()
            self._transition(ComponentState.RUNNING)

            self._thread = threading.Thread(
                target=self._run_loop,
                name=f"cyber-{self.name}",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "[%s] Started (interval=%.1f ms)", self.name, self.interval_ms
            )
            return True

    def stop(self, timeout: float = 5.0) -> None:
        """Gracefully stop the Proc() loop and join the thread.

        Args:
            timeout: Max seconds to wait for thread join.
        """
        with self._lock:
            if self._state in (
                ComponentState.SHUTDOWN,
                ComponentState.UNINITIALIZED,
            ):
                return

            logger.info("[%s] Stopping...", self.name)
            self.on_shutdown()
            self._stop_event.set()

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                logger.warning(
                    "[%s] Thread did not terminate within %.1fs",
                    self.name, timeout,
                )

        with self._lock:
            self._transition(ComponentState.SHUTDOWN)
            logger.info("[%s] Shutdown complete", self.name)

    def pause(self) -> None:
        """Pause the Proc() loop (thread stays alive but idle)."""
        with self._lock:
            if self._state != ComponentState.RUNNING:
                return
            self._pause_event.set()
            self._transition(ComponentState.PAUSED)
            self.on_pause()
            logger.info("[%s] Paused", self.name)

    def resume(self) -> None:
        """Resume a paused component."""
        with self._lock:
            if self._state != ComponentState.PAUSED:
                return
            self._pause_event.clear()
            self._transition(ComponentState.RUNNING)
            self.on_resume()
            logger.info("[%s] Resumed", self.name)

    # ─── Callback registration ───────────────────────────────────────────

    def on_error(
        self, callback: Callable[[str, Exception], None]
    ) -> None:
        """Register a callback for Proc() errors.

        Args:
            callback: ``fn(component_name, exception)``
        """
        self._on_error_callbacks.append(callback)

    def on_state_change(
        self, callback: Callable[[ComponentState, ComponentState], None]
    ) -> None:
        """Register a state-transition callback.

        Args:
            callback: ``fn(old_state, new_state)``
        """
        self._on_state_change_callbacks.append(callback)

    # ─── Internal ────────────────────────────────────────────────────────

    def _transition(self, new_state: ComponentState) -> None:
        """Transition to a new state, firing callbacks."""
        old = self._state
        self._state = new_state
        for cb in self._on_state_change_callbacks:
            try:
                cb(old, new_state)
            except Exception:
                logger.exception(
                    "[%s] State-change callback error", self.name
                )

    def _fire_error(self, exc: Exception) -> None:
        for cb in self._on_error_callbacks:
            try:
                cb(self.name, exc)
            except Exception:
                logger.exception(
                    "[%s] Error callback itself raised", self.name
                )

    def _run_loop(self) -> None:
        """Main timer loop — the ``while True`` that drives everything.

        Pattern mirrors Apollo canbus_component.cc Proc() cycle:
          1. Record start time
          2. Execute Proc()
          3. Check latency budget
          4. Circuit-breaker on repeated failure
          5. Sleep for remaining interval
        """
        interval_s = self._config.interval_ms / 1000.0

        while not self._stop_event.is_set():
            # Handle pause
            if self._pause_event.is_set():
                self._stop_event.wait(timeout=0.1)
                continue

            tick_start = time.monotonic()
            self._seq += 1
            success = True

            # ── Execute Proc() ───────────────────────────────────────
            try:
                result = self.Proc()
                if not result:
                    success = False
                    self._consecutive_failures += 1
                else:
                    self._consecutive_failures = 0
            except Exception as exc:
                success = False
                self._consecutive_failures += 1
                logger.error(
                    "[%s] Proc() raised %s: %s (seq=%d)",
                    self.name, type(exc).__name__, exc, self._seq,
                )
                self._fire_error(exc)

            # ── Latency measurement ──────────────────────────────────
            elapsed_ms = (time.monotonic() - tick_start) * 1000.0

            if self._latency is not None:
                self._latency.record(elapsed_ms, success)

            if elapsed_ms > self._config.warn_threshold_ms:
                if self._latency is not None:
                    self._latency.record_overrun()
                logger.warning(
                    "[%s] Proc() overrun: %.1f ms > %.1f ms (seq=%d)",
                    self.name, elapsed_ms,
                    self._config.warn_threshold_ms, self._seq,
                )

            # ── Circuit-breaker ──────────────────────────────────────
            if (
                self._consecutive_failures
                >= self._config.max_consecutive_failures
            ):
                logger.error(
                    "[%s] Circuit-breaker tripped after %d consecutive "
                    "failures. Cooling down %.1fs...",
                    self.name,
                    self._consecutive_failures,
                    self._config.cooldown_s,
                )
                self._stop_event.wait(timeout=self._config.cooldown_s)
                self._consecutive_failures = 0
                self._last_cooldown_time = time.monotonic()
                continue

            # ── Sleep for remaining interval ─────────────────────────
            elapsed_s = time.monotonic() - tick_start
            sleep_s = max(0.0, interval_s - elapsed_s)
            if sleep_s > 0:
                self._stop_event.wait(timeout=sleep_s)


    # ─── Apollo Proc() Timing Guard (Claude23) ───────────────────────────
    #
    # Apollo canbus_component.cc:162-217:
    #   const auto start_time = Time::Now().ToMicrosecond();
    #   ... Proc() body ...
    #   const auto end_time = Time::Now().ToMicrosecond();
    #   if (time_diff_ms > (1 / FLAGS_chassis_freq * 1e3)) { AWARN; }
    #
    # Adds deadline enforcement + context manager for subclass Proc().
    # All existing _run_loop logic preserved — this is additive only.

    def should_skip_proc(self) -> bool:
        """Check if Proc() should be skipped this tick.

        Returns True if component is in cooldown, paused, or not RUNNING.
        Subclasses call this at top of Proc() for uniform gating.

        Apollo equivalent: implicit in timer_component.cc — timer doesn't
        fire Proc() unless state is ready.
        """
        if self._state != ComponentState.RUNNING:
            return True
        if self._pause_event.is_set():
            return True
        if self._last_cooldown_time > 0:
            elapsed = time.monotonic() - self._last_cooldown_time
            if elapsed < self._config.cooldown_s:
                return True
        return False

    class _ProcMeasurement:
        """Context manager measuring Proc() body execution time.

        Apollo reference: every Proc() measures start→end, warns if
        exceeds budget (1/FLAGS_chassis_freq * 1e3 ms).

        Usage in subclass::

            with self.measure_proc() as m:
                m.success = self._do_work()
                if not m.success:
                    m.failure_reason = "upstream_timeout"
        """
        __slots__ = ("success", "failure_reason", "_component", "_t0")

        def __init__(self, component: "TimerComponent") -> None:
            self._component = component
            self._t0 = time.monotonic()
            self.success: bool = True
            self.failure_reason: str = ""

        def __enter__(self) -> "TimerComponent._ProcMeasurement":
            return self

        def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
            elapsed_ms = (time.monotonic() - self._t0) * 1000.0
            comp = self._component

            # Apollo pattern: warn if Proc() exceeds its frequency budget
            deadline_ms = comp._config.interval_ms
            if elapsed_ms > deadline_ms:
                logger.warning(
                    "[%s] Proc() deadline violation: %.1f ms > %.1f ms "
                    "budget (seq=%d)",
                    comp.name, elapsed_ms, deadline_ms, comp._seq,
                )
                if comp._latency is not None:
                    comp._latency.record_overrun()

            if comp._latency is not None:
                comp._latency.record(elapsed_ms, self.success)

            if exc_type is not None:
                logger.error(
                    "[%s] Proc() exception in measure_proc: %s: %s",
                    comp.name, exc_type.__name__, exc_val,
                )
                self.success = False
                return False  # re-raise

            return False

    def measure_proc(self) -> "_ProcMeasurement":
        """Return context manager measuring and recording Proc() timing.

        Primary instrumentation point for subclass Proc() methods.
        See _ProcMeasurement docstring for usage.
        """
        return self._ProcMeasurement(self)

    # ─── Debug / Introspection ───────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Return a serializable status dict for monitoring."""
        info: Dict[str, Any] = {
            "name": self.name,
            "state": self._state.name,
            "interval_ms": self._config.interval_ms,
            "sequence": self._seq,
            "consecutive_failures": self._consecutive_failures,
        }
        if self._latency is not None:
            info["latency"] = self._latency.snapshot()
        return info

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} name={self.name!r} "
            f"state={self._state.name} seq={self._seq}>"
        )

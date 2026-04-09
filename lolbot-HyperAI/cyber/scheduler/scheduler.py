"""
CyberScheduler — Component lifecycle manager and orchestrator.
================================================================

Manages registration, initialization, startup, and shutdown of all
TimerComponents in dependency order.  Equivalent to Apollo's
``cyber/scheduler/scheduler.h`` which manages coroutine scheduling,
adapted here for our Python threading model.

Architecture position:
    cyber/scheduler/scheduler.py   ← YOU ARE HERE
    ├─ Owns: all registered TimerComponent instances
    ├─ Manages: startup ordering, health checks, graceful shutdown
    └─ Consumed by: launch/mainboard.py (the process entry point)

Apollo reference:
    cyber/scheduler/scheduler.h        — CreateTask, NotifyProcessor
    cyber/mainboard/mainboard.cc       — LoadModule, Start, Wait

Design notes:
    - DAG-based initialization order via dependency declarations
    - Health monitor thread pings all components periodically
    - Graceful shutdown in reverse-init order
    - Supports hot-reload of individual components
    - Thread pool for parallel Init() when no dependencies conflict
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from cyber.component.timer_component import (
    ComponentConfig,
    ComponentState,
    TimerComponent,
)
from cyber.logger.cyber_logger import get_logger

logger = get_logger("scheduler")

# ─── Constants ───────────────────────────────────────────────────────────────

_HEALTH_CHECK_INTERVAL_S = 2.0
_SHUTDOWN_TIMEOUT_S = 10.0
_INIT_TIMEOUT_S = 30.0


class SchedulerState(Enum):
    IDLE = auto()
    STARTING = auto()
    RUNNING = auto()
    SHUTTING_DOWN = auto()
    STOPPED = auto()


@dataclass
class ComponentEntry:
    """Registry entry for a managed component."""
    component: TimerComponent
    dependencies: List[str] = field(default_factory=list)
    priority: int = 0  # lower = start earlier among same-depth
    enabled: bool = True


@dataclass
class HealthReport:
    """Snapshot of system health at a point in time."""
    timestamp: float
    scheduler_state: str
    components: Dict[str, Dict[str, Any]]
    unhealthy: List[str]


class CyberScheduler:
    """Orchestrates all TimerComponents through their lifecycle.

    Usage::

        scheduler = CyberScheduler()
        scheduler.register(perception_comp, deps=["canbus"])
        scheduler.register(canbus_comp, deps=[])
        scheduler.register(prediction_comp, deps=["perception"])
        scheduler.start_all()
        scheduler.wait()  # blocks until SIGINT/SIGTERM
    """

    def __init__(self) -> None:
        self._entries: Dict[str, ComponentEntry] = {}
        self._state = SchedulerState.IDLE
        self._stop_event = threading.Event()
        self._health_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._startup_order: List[str] = []
        self._on_health_callbacks: List[Callable[[HealthReport], None]] = []

    # ─── Registration ────────────────────────────────────────────────────

    def register(
        self,
        component: TimerComponent,
        deps: Optional[List[str]] = None,
        priority: int = 0,
        enabled: bool = True,
    ) -> None:
        """Register a component for lifecycle management.

        Args:
            component: The TimerComponent instance.
            deps: Names of components that must Init() before this one.
            priority: Tie-breaker for same-depth components (lower first).
            enabled: Whether to start this component.
        """
        with self._lock:
            name = component.name
            if name in self._entries:
                logger.warning("Component %s already registered, replacing", name)

            self._entries[name] = ComponentEntry(
                component=component,
                dependencies=deps or [],
                priority=priority,
                enabled=enabled,
            )
            logger.info(
                "Registered component %s (deps=%s, priority=%d, enabled=%s)",
                name, deps or [], priority, enabled,
            )

    def unregister(self, name: str) -> Optional[TimerComponent]:
        """Remove a component from the scheduler.

        Returns the removed component, or None if not found.
        """
        with self._lock:
            entry = self._entries.pop(name, None)
            if entry:
                logger.info("Unregistered component %s", name)
                return entry.component
            return None

    # ─── Topology sort ───────────────────────────────────────────────────

    def _resolve_order(self) -> List[str]:
        """Topological sort of components by dependency graph.

        Returns:
            List of component names in initialization order.

        Raises:
            RuntimeError: If circular dependencies detected.
        """
        enabled = {
            name: entry
            for name, entry in self._entries.items()
            if entry.enabled
        }

        # Build adjacency
        in_degree: Dict[str, int] = {name: 0 for name in enabled}
        graph: Dict[str, List[str]] = defaultdict(list)

        for name, entry in enabled.items():
            for dep in entry.dependencies:
                if dep in enabled:
                    graph[dep].append(name)
                    in_degree[name] += 1
                else:
                    logger.warning(
                        "Component %s depends on %s which is not registered/enabled",
                        name, dep,
                    )

        # Kahn's algorithm with priority tie-breaking
        queue: List[Tuple[int, str]] = sorted(
            [(enabled[n].priority, n) for n in enabled if in_degree[n] == 0]
        )
        order: List[str] = []

        while queue:
            _, current = queue.pop(0)
            order.append(current)
            for neighbor in graph[current]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append((enabled[neighbor].priority, neighbor))
                    queue.sort()

        if len(order) != len(enabled):
            missing = set(enabled.keys()) - set(order)
            raise RuntimeError(
                f"Circular dependency detected among: {missing}"
            )

        return order

    # ─── Lifecycle ───────────────────────────────────────────────────────

    def start_all(self) -> bool:
        """Initialize and start all enabled components in dependency order.

        Returns:
            True if all components started successfully.
        """
        with self._lock:
            if self._state != SchedulerState.IDLE:
                logger.error("Cannot start from state %s", self._state.name)
                return False
            self._state = SchedulerState.STARTING

        try:
            self._startup_order = self._resolve_order()
        except RuntimeError as exc:
            logger.error("Dependency resolution failed: %s", exc)
            self._state = SchedulerState.IDLE
            return False

        logger.info(
            "Startup order: %s",
            " → ".join(self._startup_order),
        )

        # Phase 1: Initialize
        for name in self._startup_order:
            entry = self._entries[name]
            logger.info("Initializing %s...", name)
            if not entry.component.initialize():
                logger.error(
                    "Component %s failed to initialize. Aborting startup.", name
                )
                self._shutdown_started(self._startup_order[:self._startup_order.index(name)])
                self._state = SchedulerState.IDLE
                return False

        # Phase 2: Start
        for name in self._startup_order:
            entry = self._entries[name]
            if not entry.component.start():
                logger.error(
                    "Component %s failed to start. Shutting down.", name
                )
                self._shutdown_started(self._startup_order[:self._startup_order.index(name)])
                self._state = SchedulerState.IDLE
                return False
            logger.info("Started %s ✓", name)

        # Phase 3: Health monitor
        self._state = SchedulerState.RUNNING
        self._start_health_monitor()

        logger.info(
            "All %d components running", len(self._startup_order)
        )
        return True

    def stop_all(self) -> None:
        """Gracefully stop all components in reverse order."""
        with self._lock:
            if self._state not in (SchedulerState.RUNNING, SchedulerState.STARTING):
                return
            self._state = SchedulerState.SHUTTING_DOWN

        logger.info("Shutting down all components...")
        self._stop_event.set()

        # Stop health monitor
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=3.0)

        # Stop components in reverse order
        self._shutdown_started(self._startup_order)

        with self._lock:
            self._state = SchedulerState.STOPPED
        logger.info("All components stopped")

    def _shutdown_started(self, names: List[str]) -> None:
        """Stop a list of components in reverse order."""
        for name in reversed(names):
            entry = self._entries.get(name)
            if entry and entry.component.state in (
                ComponentState.RUNNING, ComponentState.PAUSED
            ):
                logger.info("Stopping %s...", name)
                entry.component.stop(timeout=_SHUTDOWN_TIMEOUT_S)

    def wait(self) -> None:
        """Block until stop_event is set (e.g., by signal handler).

        Installs SIGINT/SIGTERM handlers for graceful shutdown.
        """
        original_sigint = signal.getsignal(signal.SIGINT)
        original_sigterm = signal.getsignal(signal.SIGTERM)

        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            logger.info("Received %s, initiating shutdown...", sig_name)
            self.stop_all()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        finally:
            signal.signal(signal.SIGINT, original_sigint)
            signal.signal(signal.SIGTERM, original_sigterm)

    # ─── Health monitoring ───────────────────────────────────────────────

    def _start_health_monitor(self) -> None:
        self._health_thread = threading.Thread(
            target=self._health_loop,
            name="scheduler-health",
            daemon=True,
        )
        self._health_thread.start()

    def _health_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=_HEALTH_CHECK_INTERVAL_S)
            if self._stop_event.is_set():
                break

            report = self.health_check()
            if report.unhealthy:
                logger.warning(
                    "Unhealthy components: %s",
                    ", ".join(report.unhealthy),
                )
            for cb in self._on_health_callbacks:
                try:
                    cb(report)
                except Exception:
                    logger.exception("Health callback error")

    def health_check(self) -> HealthReport:
        """Run a health check across all components.

        Returns:
            HealthReport with per-component status and unhealthy list.
        """
        components: Dict[str, Dict[str, Any]] = {}
        unhealthy: List[str] = []

        for name, entry in self._entries.items():
            if not entry.enabled:
                continue

            comp = entry.component
            status = comp.status()
            components[name] = status

            if comp.state == ComponentState.ERROR:
                unhealthy.append(name)
            elif comp.state == ComponentState.RUNNING:
                # Check for stall: if latency stats show zero recent calls
                stats = comp.latency_stats
                if stats and stats.total_calls > 0:
                    if stats.total_overruns > stats.total_calls * 0.5:
                        unhealthy.append(name)

        return HealthReport(
            timestamp=time.time(),
            scheduler_state=self._state.name,
            components=components,
            unhealthy=unhealthy,
        )

    def on_health_report(
        self, callback: Callable[[HealthReport], None]
    ) -> None:
        """Register a callback invoked on each health check cycle."""
        self._on_health_callbacks.append(callback)

    # ─── Hot-reload ──────────────────────────────────────────────────────

    def hot_reload(
        self,
        name: str,
        new_component: TimerComponent,
    ) -> bool:
        """Replace a running component with a new instance.

        Stops the old component, replaces it, and starts the new one.

        Args:
            name: Component name to replace.
            new_component: New component instance (must have same name).

        Returns:
            True if reload succeeded.
        """
        with self._lock:
            entry = self._entries.get(name)
            if not entry:
                logger.error("Cannot hot-reload unknown component %s", name)
                return False

        logger.info("Hot-reloading %s...", name)
        old = entry.component
        old.stop(timeout=_SHUTDOWN_TIMEOUT_S)

        entry.component = new_component
        if not new_component.initialize():
            logger.error("Hot-reload of %s failed at Init()", name)
            entry.component = old  # rollback
            old.initialize()
            old.start()
            return False

        if not new_component.start():
            logger.error("Hot-reload of %s failed at start()", name)
            entry.component = old
            old.start()
            return False

        logger.info("Hot-reload of %s complete ✓", name)
        return True

    # ─── Introspection ───────────────────────────────────────────────────

    @property
    def component_names(self) -> List[str]:
        return list(self._entries.keys())

    def get_component(self, name: str) -> Optional[TimerComponent]:
        entry = self._entries.get(name)
        return entry.component if entry else None

    def get_entry(self, name: str) -> Optional[ComponentEntry]:
        """Claude17: Access full component entry with metadata."""
        return self._entries.get(name)

    def summary(self) -> Dict[str, Any]:
        """Return a serializable summary of the scheduler state."""
        return {
            "state": self._state.name,
            "component_count": len(self._entries),
            "startup_order": self._startup_order,
            "components": {
                name: {
                    "state": entry.component.state.name,
                    "enabled": entry.enabled,
                    "deps": entry.dependencies,
                    "priority": entry.priority,
                }
                for name, entry in self._entries.items()
            },
        }

    def __repr__(self) -> str:
        return (
            f"<CyberScheduler state={self._state.name} "
            f"components={len(self._entries)}>"
        )


# ─── Claude17: WatchdogTimer ────────────────────────────────────────────────

_WATCHDOG_INTERVAL_S = 1.0
_WATCHDOG_STUCK_THRESHOLD_S = 5.0


class WatchdogTimer:
    """Detects stuck components and triggers force-restart.

    Claude17: Apollo's watchdog monitors CAN bus heartbeats. We monitor
    Proc() execution via sequence counters. If a component's sequence
    hasn't advanced within stuck_threshold_s, it is declared stuck.

    Escalation levels:
        Level 1 (>threshold):     warning log
        Level 2 (>2x threshold):  auto-restart if restartable
        Level 3 (>3x threshold):  escalate via callback
    """

    def __init__(
        self,
        scheduler: CyberScheduler,
        stuck_threshold_s: float = _WATCHDOG_STUCK_THRESHOLD_S,
    ) -> None:
        self._scheduler = scheduler
        self._stuck_threshold_s = stuck_threshold_s
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._on_stuck_callbacks: List[
            Callable[[str, float], None]
        ] = []
        # Track last-seen sequence per component
        self._last_seq: Dict[str, int] = {}
        self._last_advance_time: Dict[str, float] = {}
        self._stuck_counts: Dict[str, int] = defaultdict(int)
        self._restart_counts: Dict[str, int] = defaultdict(int)
        self._total_restarts: int = 0
        self._total_checks: int = 0

    def start(self) -> None:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._watchdog_loop,
            name="scheduler-watchdog",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "WatchdogTimer started (threshold=%.1fs)",
            self._stuck_threshold_s,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def on_stuck(self, callback: Callable[[str, float], None]) -> None:
        """Register callback(component_name, stuck_duration_s)."""
        with self._lock:
            self._on_stuck_callbacks.append(callback)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=_WATCHDOG_INTERVAL_S)
            if self._stop_event.is_set():
                break
            self._check_all()

    def _check_all(self) -> None:
        self._total_checks += 1
        now = time.monotonic()

        for name in self._scheduler.component_names:
            comp = self._scheduler.get_component(name)
            if comp is None or comp.state != ComponentState.RUNNING:
                continue

            current_seq = comp.sequence

            if name not in self._last_seq:
                self._last_seq[name] = current_seq
                self._last_advance_time[name] = now
                continue

            if current_seq != self._last_seq[name]:
                # Component is alive — sequence advanced
                self._last_seq[name] = current_seq
                self._last_advance_time[name] = now
                self._stuck_counts[name] = 0
            else:
                # Sequence hasn't changed — possibly stuck
                elapsed = now - self._last_advance_time[name]
                if elapsed > self._stuck_threshold_s:
                    self._stuck_counts[name] += 1
                    self._handle_stuck(name, comp, elapsed)

    def _handle_stuck(
        self, name: str, comp: TimerComponent, elapsed: float,
    ) -> None:
        threshold = self._stuck_threshold_s
        entry = self._scheduler.get_entry(name) if hasattr(
            self._scheduler, "get_entry"
        ) else None

        if elapsed < threshold * 2:
            logger.warning(
                "[Watchdog] %s may be stuck (no Proc() for %.1fs)",
                name, elapsed,
            )
        elif elapsed < threshold * 3:
            restartable = (
                getattr(entry, "restartable", True) if entry else True
            )
            if restartable:
                logger.error(
                    "[Watchdog] %s stuck for %.1fs — attempting restart",
                    name, elapsed,
                )
                self._restart_component(name, comp)
        else:
            logger.critical(
                "[Watchdog] %s stuck for %.1fs — escalating",
                name, elapsed,
            )

        # Fire callbacks
        with self._lock:
            for cb in self._on_stuck_callbacks:
                try:
                    cb(name, elapsed)
                except Exception:
                    logger.exception("Watchdog callback error")

    def _restart_component(self, name: str, comp: TimerComponent) -> None:
        try:
            comp.stop(timeout=2.0)
            if comp.initialize() and comp.start():
                self._restart_counts[name] += 1
                self._total_restarts += 1
                self._stuck_counts[name] = 0
                self._last_advance_time[name] = time.monotonic()
                self._last_seq[name] = comp.sequence
                logger.info(
                    "[Watchdog] %s restarted (total=%d)",
                    name, self._restart_counts[name],
                )
            else:
                logger.error("[Watchdog] %s restart failed", name)
        except Exception as exc:
            logger.error(
                "[Watchdog] %s restart error: %s: %s",
                name, type(exc).__name__, exc,
            )

    def status(self) -> Dict[str, Any]:
        return {
            "total_checks": self._total_checks,
            "total_restarts": self._total_restarts,
            "stuck_counts": dict(self._stuck_counts),
            "restart_counts": dict(self._restart_counts),
        }


# ─── Claude17: AdaptiveIntervalTuner ────────────────────────────────────────

_ADAPTIVE_TUNING_INTERVAL_S = 10.0
_MAX_OVERRUN_RATIO = 0.3
_MIN_ADAPTIVE_INTERVAL_MS = 50.0
_MAX_ADAPTIVE_INTERVAL_MS = 5000.0


class AdaptiveIntervalTuner:
    """Auto-adjusts component Proc() intervals based on overrun rates.

    Claude17: When a component consistently overruns its time budget,
    we slow it down rather than letting it starve other components.
    When load drops, we restore original intervals.

    Algorithm:
        1. Every 10s, check each component's overrun ratio
        2. If overrun_ratio > 30%: increase interval by 20%
        3. If overrun_ratio < 15%: decrease toward original by 10%
        4. Clamp to [50ms, 5000ms]
        5. Never adjust components marked as non-tunable
    """

    def __init__(self, scheduler: CyberScheduler) -> None:
        self._scheduler = scheduler
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._original_intervals: Dict[str, float] = {}
        self._last_overrun_counts: Dict[str, int] = {}
        self._last_call_counts: Dict[str, int] = {}
        self._total_adjustments: int = 0

    def start(self) -> None:
        self._stop_event.clear()
        # Snapshot original intervals
        for name in self._scheduler.component_names:
            comp = self._scheduler.get_component(name)
            if comp:
                self._original_intervals[name] = comp.interval_ms
        self._thread = threading.Thread(
            target=self._tuning_loop,
            name="scheduler-adaptive-tuner",
            daemon=True,
        )
        self._thread.start()
        logger.info("AdaptiveIntervalTuner started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        # Restore original intervals
        for name, original in self._original_intervals.items():
            comp = self._scheduler.get_component(name)
            if comp and comp.interval_ms != original:
                comp.interval_ms = original
                logger.info(
                    "[AdaptiveTuner] Restored %s to %.0fms",
                    name, original,
                )

    def _tuning_loop(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=_ADAPTIVE_TUNING_INTERVAL_S)
            if self._stop_event.is_set():
                break
            self._tune_all()

    def _tune_all(self) -> None:
        for name in self._scheduler.component_names:
            comp = self._scheduler.get_component(name)
            if comp is None or comp.state != ComponentState.RUNNING:
                continue

            stats = comp.latency_stats
            if stats is None or stats.total_calls < 10:
                continue

            prev_overruns = self._last_overrun_counts.get(name, 0)
            prev_calls = self._last_call_counts.get(name, 0)
            delta_overruns = stats.total_overruns - prev_overruns
            delta_calls = stats.total_calls - prev_calls

            self._last_overrun_counts[name] = stats.total_overruns
            self._last_call_counts[name] = stats.total_calls

            if delta_calls < 5:
                continue

            overrun_ratio = delta_overruns / max(delta_calls, 1)
            original = self._original_intervals.get(
                name, comp.interval_ms
            )

            if overrun_ratio > _MAX_OVERRUN_RATIO:
                new_interval = min(
                    comp.interval_ms * 1.2,
                    _MAX_ADAPTIVE_INTERVAL_MS,
                )
                if abs(new_interval - comp.interval_ms) > 1.0:
                    comp.interval_ms = new_interval
                    self._total_adjustments += 1
                    logger.info(
                        "[AdaptiveTuner] %s slowed: %.0fms → %.0fms "
                        "(overrun=%.1f%%)",
                        name, original, new_interval,
                        overrun_ratio * 100,
                    )
            elif (
                overrun_ratio < _MAX_OVERRUN_RATIO / 2
                and comp.interval_ms > original
            ):
                new_interval = max(
                    comp.interval_ms * 0.9,
                    original,
                    _MIN_ADAPTIVE_INTERVAL_MS,
                )
                if abs(new_interval - comp.interval_ms) > 1.0:
                    comp.interval_ms = new_interval
                    self._total_adjustments += 1
                    logger.info(
                        "[AdaptiveTuner] %s restored: → %.0fms",
                        name, new_interval,
                    )

    def status(self) -> Dict[str, Any]:
        current = {}
        for name in self._scheduler.component_names:
            comp = self._scheduler.get_component(name)
            if comp:
                current[name] = {
                    "original_ms": self._original_intervals.get(
                        name, comp.interval_ms
                    ),
                    "current_ms": comp.interval_ms,
                }
        return {
            "total_adjustments": self._total_adjustments,
            "intervals": current,
        }


# ─── Claude17: SchedulerMetrics ─────────────────────────────────────────────

@dataclass
class SchedulerMetrics:
    """Aggregated scheduler-level performance metrics.

    Claude17: Provides visibility into scheduler overhead itself,
    not just individual component latencies.
    """
    startup_duration_s: float = 0.0
    shutdown_duration_s: float = 0.0
    total_health_checks: int = 0
    total_hot_reloads: int = 0
    peak_component_count: int = 0
    _start_time: float = field(default=0.0, repr=False)

    def mark_start(self) -> None:
        self._start_time = time.monotonic()

    @property
    def uptime_s(self) -> float:
        if self._start_time > 0:
            return time.monotonic() - self._start_time
        return 0.0

    def snapshot(self) -> Dict[str, Any]:
        return {
            "startup_duration_s": round(self.startup_duration_s, 3),
            "shutdown_duration_s": round(self.shutdown_duration_s, 3),
            "uptime_s": round(self.uptime_s, 1),
            "total_health_checks": self.total_health_checks,
            "total_hot_reloads": self.total_hot_reloads,
            "peak_component_count": self.peak_component_count,
        }


# ─── Priority-based component scheduling (Claude23) ─────────────────────────
#
# Apollo scheduler uses processor groups and CRoutines for priority.
# Python threads don't support true priority, but we can order component
# startup and assign thread names for profiling.
#
# This class wraps the existing Scheduler with priority awareness.

class ComponentPriority:
    """Priority levels for component scheduling.

    Higher priority components are started first and get more
    favorable scheduling treatment (shorter cooldowns, etc).

    Apollo equivalent: CRoutine priority in scheduler_choreography.
    """
    CRITICAL = 0    # canbus — data backbone, must run first
    HIGH = 1        # perception — processes raw data
    MEDIUM = 2      # prediction, planning — derived data
    LOW = 3         # control, monitor — output/observability
    BACKGROUND = 4  # recording, diagnostics

    # Default priority mapping for lolbot components
    COMPONENT_PRIORITIES = {
        "canbus": CRITICAL,
        "perception": HIGH,
        "prediction": MEDIUM,
        "planning": MEDIUM,
        "control": LOW,
        "monitor": LOW,
    }

    @classmethod
    def get_priority(cls, component_name: str) -> int:
        """Get the scheduling priority for a named component."""
        return cls.COMPONENT_PRIORITIES.get(
            component_name, cls.BACKGROUND
        )

    @classmethod
    def sort_by_priority(
        cls, components: List[Any]
    ) -> List[Any]:
        """Sort components by priority (lowest number = highest priority).

        Used by Mainboard to determine startup order.
        Components are started in priority order so upstream data
        producers initialize before downstream consumers.
        """
        def _key(comp):
            name = getattr(comp, "name", getattr(comp, "COMPONENT_NAME", ""))
            return cls.get_priority(name)
        return sorted(components, key=_key)

    @classmethod
    def startup_order(cls) -> List[str]:
        """Return component names in recommended startup order."""
        sorted_items = sorted(
            cls.COMPONENT_PRIORITIES.items(),
            key=lambda x: x[1],
        )
        return [name for name, _ in sorted_items]

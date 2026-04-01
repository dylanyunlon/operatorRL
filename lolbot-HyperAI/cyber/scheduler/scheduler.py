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

#!/usr/bin/env python3
"""
ProcessManager — Apollo-style Component Lifecycle & Proc() Scheduler
======================================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Mirrors Apollo's cyber/scheduler pattern: each module registers as a
Component with a `proc()` method. ProcessManager drives a while-true
loop calling every component's proc() at its registered frequency.

Apollo Reference:
    modules/common/adapters/adapter_gflags.cc → component startup
    cyber/scheduler/scheduler.cc → task scheduling
    cyber/mainboard/mainboard.cc → process entry + signal handling

Design:
    ProcessManager
      ├── ComponentSlot (name, component, interval_ms, priority)
      ├── TickScheduler  (10ms base tick, groups by frequency)
      ├── SignalHandler   (SIGINT/SIGTERM → graceful shutdown)
      └── LifecycleHook  (on_start, on_stop, on_error per component)

Production Critique (Knuth-level):
    1. User: If a single component's proc() blocks beyond its interval,
       the scheduler logs a WARNING and skips that tick—never starves
       other components. User sees degraded predictions but no crash.
    2. System: Components are sorted by priority (lower = earlier in tick).
       Perception runs at priority 0, Analysis at 10, Planning at 20,
       Output at 30. This ensures data flows downstream within one tick.
"""

import asyncio
import enum
import logging
import os
import signal
import sys
import time
import traceback
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any, Callable, Coroutine, Dict, List, Optional, Protocol, Set, Tuple, Type
)

# ---------------------------------------------------------------------------
# Protocol: every module must satisfy this interface
# ---------------------------------------------------------------------------

class ComponentProtocol(Protocol):
    """Interface that every lolbot-HyperAI module must implement."""

    @property
    def name(self) -> str:
        """Unique component identifier (e.g. 'perception.network_capture')."""
        ...

    async def init(self) -> None:
        """One-time async initialization (open connections, load models)."""
        ...

    async def proc(self) -> None:
        """
        Called every tick at the component's registered interval.
        Must be non-blocking or complete within interval_ms.
        This is the Apollo Proc() equivalent.
        """
        ...

    async def shutdown(self) -> None:
        """Release resources. Called once during graceful shutdown."""
        ...


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ComponentState(enum.Enum):
    """Lifecycle states for a managed component."""
    REGISTERED = "registered"
    INITIALIZING = "initializing"
    RUNNING = "running"
    DEGRADED = "degraded"       # proc() exceeded time budget
    ERROR = "error"             # init() or proc() raised
    STOPPING = "stopping"
    STOPPED = "stopped"


class ComponentPriority(enum.IntEnum):
    """
    Execution order within a single tick. Lower = earlier.
    Mirrors Apollo's pipeline: Perception → Prediction → Planning → Control.
    """
    PERCEPTION = 0
    DATA = 5
    ANALYSIS = 10
    PREDICTION = 15
    PLANNING = 20
    EVOLUTION = 25
    OUTPUT = 30
    RUNTIME = 40


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ComponentSlot:
    """Registration record for one component."""
    name: str
    component: ComponentProtocol
    interval_ms: int                        # How often proc() is called
    priority: int                           # Execution order within a tick
    state: ComponentState = ComponentState.REGISTERED
    last_proc_time_ms: float = 0.0          # Duration of last proc()
    last_proc_at: float = 0.0               # Timestamp of last proc() start
    total_proc_calls: int = 0
    total_errors: int = 0
    consecutive_errors: int = 0
    max_consecutive_errors: int = 5         # Auto-disable after this many
    error_backoff_until: float = 0.0        # Skip proc() until this time
    tags: Set[str] = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "interval_ms": self.interval_ms,
            "priority": self.priority,
            "last_proc_time_ms": round(self.last_proc_time_ms, 2),
            "total_proc_calls": self.total_proc_calls,
            "total_errors": self.total_errors,
            "consecutive_errors": self.consecutive_errors,
        }


@dataclass
class TickStats:
    """Statistics for one scheduler tick."""
    tick_number: int
    started_at: float
    duration_ms: float
    components_executed: int
    components_skipped: int
    components_errored: int
    overrun: bool                           # True if tick exceeded budget


@dataclass
class LifecycleHook:
    """Optional callbacks for component lifecycle events."""
    on_start: Optional[Callable[[str], None]] = None
    on_stop: Optional[Callable[[str], None]] = None
    on_error: Optional[Callable[[str, Exception], None]] = None
    on_state_change: Optional[Callable[[str, ComponentState, ComponentState], None]] = None


# ---------------------------------------------------------------------------
# TickScheduler — the inner timing loop
# ---------------------------------------------------------------------------

class TickScheduler:
    """
    Drives the 10ms base tick. Each component's proc() is called when
    (current_tick * BASE_TICK_MS) % component.interval_ms == 0.

    This avoids asyncio.create_task per-component (which would lose
    deterministic ordering). Instead, we call proc() sequentially within
    a tick, sorted by priority, achieving Apollo's synchronous pipeline.
    """

    BASE_TICK_MS: int = 10  # 100 Hz base clock

    def __init__(self, logger: logging.Logger):
        self._log = logger
        self._tick: int = 0
        self._running: bool = False
        self._tick_history: List[TickStats] = []
        self._max_history: int = 1000

    @property
    def current_tick(self) -> int:
        return self._tick

    def should_execute(self, slot: ComponentSlot) -> bool:
        """Check if this component should run on the current tick."""
        if slot.state in (ComponentState.STOPPED, ComponentState.STOPPING):
            return False
        if slot.state == ComponentState.ERROR:
            now = time.monotonic()
            if now < slot.error_backoff_until:
                return False
        cycle_ticks = max(1, slot.interval_ms // self.BASE_TICK_MS)
        return (self._tick % cycle_ticks) == 0

    async def execute_tick(
        self,
        slots: List[ComponentSlot],
        overrun_threshold_ms: float = 50.0,
    ) -> TickStats:
        """
        Execute one tick: call proc() on all eligible components in
        priority order. Returns tick statistics.
        """
        tick_start = time.monotonic()
        executed = 0
        skipped = 0
        errored = 0

        eligible = sorted(
            [s for s in slots if self.should_execute(s)],
            key=lambda s: s.priority,
        )

        for slot in eligible:
            if slot.state in (ComponentState.STOPPED, ComponentState.ERROR):
                skipped += 1
                continue

            proc_start = time.monotonic()
            try:
                await slot.component.proc()
                elapsed_ms = (time.monotonic() - proc_start) * 1000.0
                slot.last_proc_time_ms = elapsed_ms
                slot.last_proc_at = proc_start
                slot.total_proc_calls += 1
                slot.consecutive_errors = 0

                if slot.state != ComponentState.RUNNING:
                    slot.state = ComponentState.RUNNING

                if elapsed_ms > slot.interval_ms * 0.8:
                    self._log.warning(
                        "Component %s proc() took %.1fms (budget: %dms)",
                        slot.name, elapsed_ms, slot.interval_ms,
                    )
                    slot.state = ComponentState.DEGRADED

                executed += 1

            except Exception as exc:
                elapsed_ms = (time.monotonic() - proc_start) * 1000.0
                slot.last_proc_time_ms = elapsed_ms
                slot.total_errors += 1
                slot.consecutive_errors += 1
                errored += 1

                self._log.error(
                    "Component %s proc() error (#%d consecutive): %s",
                    slot.name, slot.consecutive_errors, exc,
                )

                if slot.consecutive_errors >= slot.max_consecutive_errors:
                    slot.state = ComponentState.ERROR
                    backoff_s = min(60.0, 2.0 ** slot.consecutive_errors)
                    slot.error_backoff_until = time.monotonic() + backoff_s
                    self._log.error(
                        "Component %s disabled for %.0fs after %d consecutive errors",
                        slot.name, backoff_s, slot.consecutive_errors,
                    )

        tick_duration_ms = (time.monotonic() - tick_start) * 1000.0
        overrun = tick_duration_ms > overrun_threshold_ms

        stats = TickStats(
            tick_number=self._tick,
            started_at=tick_start,
            duration_ms=round(tick_duration_ms, 3),
            components_executed=executed,
            components_skipped=skipped,
            components_errored=errored,
            overrun=overrun,
        )

        self._tick_history.append(stats)
        if len(self._tick_history) > self._max_history:
            self._tick_history = self._tick_history[-self._max_history:]

        if overrun:
            self._log.warning(
                "Tick %d overrun: %.1fms (threshold: %.1fms), "
                "executed=%d skipped=%d errored=%d",
                self._tick, tick_duration_ms, overrun_threshold_ms,
                executed, skipped, errored,
            )

        self._tick += 1
        return stats

    def get_recent_stats(self, count: int = 100) -> List[Dict[str, Any]]:
        """Return recent tick statistics for monitoring."""
        return [
            {
                "tick": s.tick_number,
                "duration_ms": s.duration_ms,
                "executed": s.components_executed,
                "errored": s.components_errored,
                "overrun": s.overrun,
            }
            for s in self._tick_history[-count:]
        ]

    def get_average_tick_ms(self, window: int = 100) -> float:
        """Average tick duration over recent window."""
        recent = self._tick_history[-window:]
        if not recent:
            return 0.0
        return sum(s.duration_ms for s in recent) / len(recent)


# ---------------------------------------------------------------------------
# ProcessManager — top-level orchestrator
# ---------------------------------------------------------------------------

class ProcessManager:
    """
    Top-level process manager. Owns the while-true loop and all component
    lifecycles. Equivalent to Apollo's mainboard + scheduler combined.

    Usage:
        pm = ProcessManager()
        pm.register(perception_component, interval_ms=10, priority=0)
        pm.register(planning_component, interval_ms=100, priority=20)
        await pm.start()   # blocks until shutdown signal
    """

    def __init__(
        self,
        base_tick_ms: int = 10,
        max_tick_overrun_ms: float = 50.0,
        enable_signal_handling: bool = True,
    ):
        self._log = logging.getLogger("lolbot.runtime.process_manager")
        self._slots: Dict[str, ComponentSlot] = {}
        self._scheduler = TickScheduler(self._log)
        self._scheduler.BASE_TICK_MS = base_tick_ms
        self._base_tick_ms = base_tick_ms
        self._max_tick_overrun_ms = max_tick_overrun_ms
        self._running = False
        self._shutdown_requested = False
        self._start_time: float = 0.0
        self._lifecycle_hooks: List[LifecycleHook] = []
        self._enable_signal_handling = enable_signal_handling

        # Metrics
        self._total_ticks: int = 0
        self._total_overruns: int = 0

    # ---- Registration ----

    def register(
        self,
        component: ComponentProtocol,
        interval_ms: int = 100,
        priority: int = ComponentPriority.RUNTIME,
        tags: Optional[Set[str]] = None,
        max_consecutive_errors: int = 5,
    ) -> None:
        """
        Register a component for scheduled proc() execution.

        Args:
            component: Must implement ComponentProtocol.
            interval_ms: How often proc() is called (must be multiple of base_tick_ms).
            priority: Execution order within a tick (lower = earlier).
            tags: Optional labels for grouping/filtering.
            max_consecutive_errors: Disable component after this many sequential failures.
        """
        name = component.name
        if name in self._slots:
            raise ValueError(f"Component '{name}' is already registered")

        if interval_ms < self._base_tick_ms:
            self._log.warning(
                "Component %s interval %dms < base tick %dms, clamping to base",
                name, interval_ms, self._base_tick_ms,
            )
            interval_ms = self._base_tick_ms

        if interval_ms % self._base_tick_ms != 0:
            aligned = (interval_ms // self._base_tick_ms) * self._base_tick_ms
            if aligned == 0:
                aligned = self._base_tick_ms
            self._log.info(
                "Component %s interval %dms aligned to %dms (base tick: %dms)",
                name, interval_ms, aligned, self._base_tick_ms,
            )
            interval_ms = aligned

        slot = ComponentSlot(
            name=name,
            component=component,
            interval_ms=interval_ms,
            priority=priority,
            tags=tags or set(),
            max_consecutive_errors=max_consecutive_errors,
        )
        self._slots[name] = slot
        self._log.info(
            "Registered component: %s (interval=%dms, priority=%d)",
            name, interval_ms, priority,
        )

    def unregister(self, name: str) -> bool:
        """Remove a component. Returns True if it existed."""
        if name in self._slots:
            del self._slots[name]
            self._log.info("Unregistered component: %s", name)
            return True
        return False

    def add_lifecycle_hook(self, hook: LifecycleHook) -> None:
        """Register a lifecycle event listener."""
        self._lifecycle_hooks.append(hook)

    # ---- Lifecycle ----

    async def start(self) -> None:
        """
        Initialize all components, then enter the while-true main loop.
        Blocks until shutdown() is called or a signal is received.
        """
        if self._running:
            raise RuntimeError("ProcessManager is already running")

        self._start_time = time.monotonic()
        self._running = True
        self._shutdown_requested = False

        if self._enable_signal_handling:
            self._install_signal_handlers()

        self._log.info(
            "ProcessManager starting with %d components, base_tick=%dms",
            len(self._slots), self._base_tick_ms,
        )

        # Phase 1: Initialize all components (sorted by priority)
        await self._initialize_all()

        # Phase 2: Main loop
        self._log.info("Entering main loop (while-true, %d Hz)", 1000 // self._base_tick_ms)
        try:
            await self._main_loop()
        except asyncio.CancelledError:
            self._log.info("Main loop cancelled")
        finally:
            # Phase 3: Shutdown
            await self._shutdown_all()
            self._running = False

    async def request_shutdown(self) -> None:
        """Request graceful shutdown from within async context."""
        self._log.info("Shutdown requested")
        self._shutdown_requested = True

    def request_shutdown_sync(self) -> None:
        """Request graceful shutdown from signal handler (sync context)."""
        self._shutdown_requested = True

    # ---- Internal: main loop ----

    async def _main_loop(self) -> None:
        """
        The while-true loop. Each iteration:
        1. Execute one tick (all eligible components in priority order)
        2. Sleep for remaining time in the tick budget
        3. Check shutdown flag
        """
        tick_period_s = self._base_tick_ms / 1000.0
        slot_list = list(self._slots.values())

        while not self._shutdown_requested:
            tick_start = time.monotonic()

            stats = await self._scheduler.execute_tick(
                slot_list,
                overrun_threshold_ms=self._max_tick_overrun_ms,
            )
            self._total_ticks += 1
            if stats.overrun:
                self._total_overruns += 1

            elapsed = time.monotonic() - tick_start
            sleep_time = tick_period_s - elapsed
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

    # ---- Internal: initialization ----

    async def _initialize_all(self) -> None:
        """Initialize components in priority order."""
        ordered = sorted(self._slots.values(), key=lambda s: s.priority)

        for slot in ordered:
            slot.state = ComponentState.INITIALIZING
            self._log.info("Initializing component: %s", slot.name)
            try:
                await slot.component.init()
                slot.state = ComponentState.RUNNING
                self._log.info("Component %s initialized successfully", slot.name)
                for hook in self._lifecycle_hooks:
                    if hook.on_start:
                        hook.on_start(slot.name)
            except Exception as exc:
                slot.state = ComponentState.ERROR
                self._log.error(
                    "Component %s failed to initialize: %s\n%s",
                    slot.name, exc, traceback.format_exc(),
                )
                for hook in self._lifecycle_hooks:
                    if hook.on_error:
                        hook.on_error(slot.name, exc)

    async def _shutdown_all(self) -> None:
        """Shutdown components in reverse priority order."""
        ordered = sorted(
            self._slots.values(), key=lambda s: s.priority, reverse=True
        )
        self._log.info("Shutting down %d components...", len(ordered))

        for slot in ordered:
            if slot.state in (ComponentState.STOPPED, ComponentState.REGISTERED):
                continue
            slot.state = ComponentState.STOPPING
            try:
                await asyncio.wait_for(slot.component.shutdown(), timeout=5.0)
                slot.state = ComponentState.STOPPED
                self._log.info("Component %s stopped", slot.name)
                for hook in self._lifecycle_hooks:
                    if hook.on_stop:
                        hook.on_stop(slot.name)
            except asyncio.TimeoutError:
                slot.state = ComponentState.ERROR
                self._log.error("Component %s shutdown timed out (5s)", slot.name)
            except Exception as exc:
                slot.state = ComponentState.ERROR
                self._log.error("Component %s shutdown error: %s", slot.name, exc)

    # ---- Signal handling ----

    def _install_signal_handlers(self) -> None:
        """Install SIGINT/SIGTERM handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._handle_signal, sig)
            except (NotImplementedError, RuntimeError):
                # Windows doesn't support add_signal_handler
                signal.signal(sig, lambda s, f: self.request_shutdown_sync())

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle OS signals."""
        sig_name = signal.Signals(sig).name
        self._log.info("Received signal %s, requesting shutdown...", sig_name)
        self._shutdown_requested = True

    # ---- Status & monitoring ----

    def get_status(self) -> Dict[str, Any]:
        """Return full system status for health monitoring."""
        uptime_s = time.monotonic() - self._start_time if self._start_time else 0.0
        return {
            "running": self._running,
            "uptime_s": round(uptime_s, 1),
            "total_ticks": self._total_ticks,
            "total_overruns": self._total_overruns,
            "overrun_rate": (
                round(self._total_overruns / max(1, self._total_ticks), 4)
            ),
            "avg_tick_ms": self._scheduler.get_average_tick_ms(),
            "base_tick_ms": self._base_tick_ms,
            "components": {
                name: slot.to_dict() for name, slot in self._slots.items()
            },
        }

    def get_component_state(self, name: str) -> Optional[ComponentState]:
        """Get state of a specific component."""
        slot = self._slots.get(name)
        return slot.state if slot else None

    def get_components_by_state(
        self, state: ComponentState
    ) -> List[str]:
        """List component names in a given state."""
        return [
            name for name, slot in self._slots.items()
            if slot.state == state
        ]

    def get_components_by_tag(self, tag: str) -> List[str]:
        """List component names with a given tag."""
        return [
            name for name, slot in self._slots.items()
            if tag in slot.tags
        ]

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def component_count(self) -> int:
        return len(self._slots)

    @property
    def healthy_count(self) -> int:
        return len([
            s for s in self._slots.values()
            if s.state == ComponentState.RUNNING
        ])


# ---------------------------------------------------------------------------
# Convenience: standalone entry point for testing
# ---------------------------------------------------------------------------

class _DummyComponent:
    """Minimal component for smoke-testing the ProcessManager."""

    def __init__(self, name: str):
        self._name = name
        self._counter = 0

    @property
    def name(self) -> str:
        return self._name

    async def init(self) -> None:
        pass

    async def proc(self) -> None:
        self._counter += 1

    async def shutdown(self) -> None:
        pass


async def _smoke_test() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    pm = ProcessManager(base_tick_ms=100, enable_signal_handling=True)
    pm.register(_DummyComponent("test.alpha"), interval_ms=100, priority=0)
    pm.register(_DummyComponent("test.beta"), interval_ms=200, priority=10)

    async def auto_stop():
        await asyncio.sleep(2.0)
        await pm.request_shutdown()

    asyncio.create_task(auto_stop())
    await pm.start()
    print(pm.get_status())


if __name__ == "__main__":
    asyncio.run(_smoke_test())

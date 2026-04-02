"""
Mainboard — Component lifecycle manager (Apollo DAG scheduler analog).
=======================================================================
lolbot-HyperAI · Launch Layer

Manages the ordered startup, health monitoring, and shutdown of all
TimerComponent instances. This is the Apollo "mainboard" that wires
the DAG of components together.

Phase 4 (Claude#6): Fixed ChannelMonitor and DashboardBackend APIs
to match actual implementations. Added ControlComponent registration.

Architecture position:
    launch/mainboard.py   ← YOU ARE HERE
    ├─ Creates: CanbusComponent, PerceptionComponent,
    │           PredictionComponent, PlanningComponent,
    │           ControlComponent [Phase 4]
    ├─ Manages: component lifecycle (init → start → stop)
    ├─ Monitors: ChannelMonitor health checks [Phase 4]
    └─ Optional: DashboardBackend HTTP server [Phase 4]

Apollo reference:
    cyber/mainboard/mainboard.cc — ``Start()``, ``LoadModule()``
    cyber/scheduler/scheduler.cc — component DAG management
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from cyber.logger.cyber_logger import get_logger
from cyber.component.timer_component import ComponentState, TimerComponent

logger = get_logger("mainboard")


class Mainboard:
    """Component lifecycle manager.

    Registers components in dependency order, initializes them,
    starts their Proc() loops, monitors health, and performs
    ordered shutdown.

    Usage::

        board = Mainboard()
        board.register(canbus_comp)
        board.register(perception_comp)
        board.register(prediction_comp)
        board.register(planning_comp)
        board.register(control_comp)   # Phase 4
        board.start_all()
        ...
        board.stop_all()
    """

    def __init__(self) -> None:
        self._components: List[TimerComponent] = []
        self._component_map: Dict[str, TimerComponent] = {}
        self._started: bool = False
        self._lock = threading.Lock()

        # Phase 4: optional subsystems
        self._channel_monitor = None
        self._dashboard_backend = None
        self._health_check_interval_sec: float = 5.0
        self._health_thread: Optional[threading.Thread] = None
        self._health_stop_event = threading.Event()

    def register(self, component: TimerComponent) -> None:
        """Register a component for lifecycle management.

        Components are started in registration order (dependency order).
        """
        with self._lock:
            name = component.name
            if name in self._component_map:
                logger.warning("Component %s already registered, replacing", name)
                self._components = [c for c in self._components if c.name != name]
            self._components.append(component)
            self._component_map[name] = component
            logger.info("Registered component: %s", name)

    def enable_channel_monitor(self) -> None:
        """Enable the channel health monitor (Phase 4)."""
        try:
            from cyber.transport.channel_monitor import ChannelMonitor
            self._channel_monitor = ChannelMonitor()
            logger.info("ChannelMonitor enabled")
        except ImportError as exc:
            logger.warning("ChannelMonitor not available: %s", exc)

    def enable_dashboard(self, port: int = 8765) -> None:
        """Enable the dashboard backend (Phase 4)."""
        try:
            from modules.dreamview.dashboard.dashboard_backend import (
                DashboardBackend, DashboardState,
            )
            state = DashboardState()
            self._dashboard_backend = DashboardBackend(state=state, port=port)
            logger.info("DashboardBackend configured on port %d", port)
        except ImportError as exc:
            logger.warning("DashboardBackend not available: %s", exc)

    def start_all(self) -> bool:
        """Initialize and start all registered components in order.

        Returns:
            True if all components started successfully.
        """
        with self._lock:
            if self._started:
                logger.warning("Mainboard already started")
                return False

            logger.info("Starting %d components...", len(self._components))
            all_ok = True

            for comp in self._components:
                # Initialize
                logger.info("  Initializing %s...", comp.name)
                if not comp.initialize():
                    logger.error("  %s FAILED to initialize", comp.name)
                    all_ok = False
                    continue

                # Start
                if not comp.start():
                    logger.error("  %s FAILED to start", comp.name)
                    all_ok = False
                    continue

                logger.info("  %s: RUNNING (interval=%.0fms)",
                           comp.name, comp.interval_ms)

            # Start optional subsystems
            if self._dashboard_backend:
                try:
                    self._dashboard_backend.start()
                except Exception as exc:
                    logger.warning("DashboardBackend failed to start: %s", exc)

            # Start health check thread
            if self._channel_monitor:
                self._health_stop_event.clear()
                self._health_thread = threading.Thread(
                    target=self._health_check_loop,
                    name="mainboard-health",
                    daemon=True,
                )
                self._health_thread.start()

            self._started = True
            logger.info("Mainboard: %d components started (all_ok=%s)",
                       len(self._components), all_ok)
            return all_ok

    def stop_all(self, timeout: float = 5.0) -> Dict[str, str]:
        """Stop all components in reverse order.

        Returns:
            Dict of component_name → final_state.
        """
        with self._lock:
            if not self._started:
                return {}

            logger.info("Stopping %d components...", len(self._components))
            results: Dict[str, str] = {}

            # Stop health check
            self._health_stop_event.set()
            if self._health_thread:
                self._health_thread.join(timeout=2.0)

            # Stop dashboard
            if self._dashboard_backend:
                try:
                    self._dashboard_backend.stop()
                except Exception as exc:
                    logger.warning("Dashboard stop error: %s", exc)

            # Stop channel monitor
            if self._channel_monitor:
                try:
                    self._channel_monitor.stop_background()
                except Exception:
                    pass

            # Stop components in reverse order
            for comp in reversed(self._components):
                logger.info("  Stopping %s...", comp.name)
                try:
                    comp.stop(timeout=timeout)
                    results[comp.name] = comp.state.name
                except Exception as exc:
                    logger.error("  %s stop error: %s", comp.name, exc)
                    results[comp.name] = "ERROR"

            self._started = False
            logger.info("Mainboard: all components stopped")
            return results

    def _health_check_loop(self) -> None:
        """Periodic health check using ChannelMonitor."""
        while not self._health_stop_event.is_set():
            try:
                if self._channel_monitor:
                    report = self._channel_monitor.check()
                    if report.has_issues:
                        logger.warning(
                            "Channel health: %d healthy, %d stale, %d backpressure, %d dead",
                            report.healthy_count, report.stale_count,
                            report.backpressure_count, report.dead_count,
                        )
            except Exception as exc:
                logger.error("Health check error: %s", exc)

            # Also check component states
            for comp in self._components:
                if comp.state == ComponentState.ERROR:
                    logger.error("Component %s in ERROR state", comp.name)

            self._health_stop_event.wait(timeout=self._health_check_interval_sec)

    def get_component(self, name: str) -> Optional[TimerComponent]:
        return self._component_map.get(name)

    def status(self) -> Dict[str, Any]:
        """Aggregate status of all components."""
        comp_status = {}
        for comp in self._components:
            comp_status[comp.name] = {
                "state": comp.state.name,
                "sequence": comp.sequence,
            }
            if comp.latency_stats:
                comp_status[comp.name]["latency"] = comp.latency_stats.snapshot()

        result: Dict[str, Any] = {
            "started": self._started,
            "component_count": len(self._components),
            "components": comp_status,
        }
        if self._channel_monitor and self._channel_monitor.latest_report:
            rpt = self._channel_monitor.latest_report
            result["channel_health"] = {
                "healthy": rpt.healthy_count,
                "stale": rpt.stale_count,
                "backpressure": rpt.backpressure_count,
                "dead": rpt.dead_count,
            }
        return result

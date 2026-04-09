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
        # Claude17: startup timing and restart tracking
        self._startup_duration_s: float = 0.0
        self._total_restarts: int = 0

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
            _t0 = time.monotonic()

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
            self._startup_duration_s = time.monotonic() - _t0
            logger.info("Mainboard: %d components started (all_ok=%s, %.2fs)",
                       len(self._components), all_ok,
                       self._startup_duration_s)
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
        # Claude17: add startup timing and watchdog info
        result["startup_duration_s"] = round(self._startup_duration_s, 3)
        result["total_restarts"] = self._total_restarts
        return result

    # ─── Claude17: Component Restart ─────────────────────────────────────

    def restart_component(
        self, name: str, timeout: float = 5.0
    ) -> bool:
        """Stop and restart a specific component by name.

        Claude17: Enables targeted recovery without full system restart.
        Preserves all other components' state.

        Args:
            name: Component name to restart.
            timeout: Max seconds to wait for stop.

        Returns:
            True if restart succeeded.
        """
        with self._lock:
            comp = self._component_map.get(name)
            if comp is None:
                logger.error("Cannot restart unknown component: %s", name)
                return False

        logger.info("Restarting component: %s", name)
        try:
            comp.stop(timeout=timeout)
            if comp.initialize() and comp.start():
                self._total_restarts += 1
                logger.info("Component %s restarted successfully", name)
                return True
            else:
                logger.error("Component %s restart failed", name)
                return False
        except Exception as exc:
            logger.error(
                "Component %s restart error: %s: %s",
                name, type(exc).__name__, exc,
            )
            return False

    def get_startup_order(self) -> List[str]:
        """Return component names in their registration (startup) order.

        Claude17: Useful for dependency debugging and DAG visualization.
        """
        return [c.name for c in self._components]

    def get_running_components(self) -> List[str]:
        """Return names of currently RUNNING components."""
        return [
            c.name for c in self._components
            if c.state == ComponentState.RUNNING
        ]

    def get_failed_components(self) -> List[str]:
        """Return names of components in ERROR state."""
        return [
            c.name for c in self._components
            if c.state == ComponentState.ERROR
        ]

    def pause_component(self, name: str) -> bool:
        """Pause a specific component (its thread stays alive but idle).

        Claude17: Useful for graceful degradation — pause non-critical
        components when system is overloaded.
        """
        comp = self._component_map.get(name)
        if comp is None:
            return False
        comp.pause()
        logger.info("Component %s paused", name)
        return True

    def resume_component(self, name: str) -> bool:
        """Resume a paused component."""
        comp = self._component_map.get(name)
        if comp is None:
            return False
        comp.resume()
        logger.info("Component %s resumed", name)
        return True

    def component_summary(self) -> str:
        """Return a human-readable summary of all component states.

        Claude17: For CLI diagnostics and structured logging.
        """
        lines = [f"Mainboard: {len(self._components)} components"]
        for comp in self._components:
            state = comp.state.name
            seq = comp.sequence
            latency = ""
            if comp.latency_stats and comp.latency_stats.total_calls > 0:
                latency = f" (mean={comp.latency_stats.mean_ms:.1f}ms)"
            lines.append(
                f"  {comp.name:20s} {state:12s} seq={seq:6d}{latency}"
            )
        return "\n".join(lines)


    # ─── Apollo-style dependency-ordered lifecycle (Claude23) ────────────
    #
    # Apollo mainboard loads modules in DAG order. We add explicit
    # dependency validation and health probe after start.

    def health_probe(self, timeout_s: float = 5.0) -> Dict[str, bool]:
        """Probe all components for health after startup.

        Waits up to timeout_s for each component to report RUNNING state.
        Returns map of component_name → is_healthy.

        Apollo equivalent: mainboard waits for module Init() success
        before proceeding to the next module.
        """
        import time as _time
        results = {}
        deadline = _time.monotonic() + timeout_s

        with self._lock:
            # Claude24 fix: _components is a List, _component_map is the Dict
            components = list(self._component_map.items())

        for name, comp in components:
            healthy = False
            while _time.monotonic() < deadline:
                try:
                    state = comp.state
                    if hasattr(state, 'name'):
                        state_name = state.name
                    else:
                        state_name = str(state)

                    if state_name == "RUNNING":
                        healthy = True
                        break
                    elif state_name in ("ERROR", "SHUTDOWN"):
                        break
                except Exception:
                    break
                _time.sleep(0.1)
            results[name] = healthy

        return results

    def validate_dependencies(self) -> List[str]:
        """Check that all component dependencies are registered.

        Returns list of missing dependencies (empty = all OK).

        Apollo equivalent: DAG validation in mainboard module loading.
        Components declare DEPENDENCIES class attribute listing required
        upstream components.
        """
        missing = []
        with self._lock:
            # Claude24 fix: _component_map is the Dict, _components is List
            registered_names = set(self._component_map.keys())
            for name, comp in self._component_map.items():
                deps = getattr(comp, "DEPENDENCIES", [])
                for dep in deps:
                    if dep not in registered_names:
                        missing.append(
                            f"{name} requires '{dep}' but it's not registered"
                        )
        return missing

    def restart_component(self, name: str, timeout: float = 5.0) -> bool:
        """Restart a single component by name.

        Stops then re-initializes and starts the component.
        Returns True if restart succeeded.

        Useful for recovering from ERROR state without full system restart.
        """
        with self._lock:
            # Claude24 fix: _component_map is the Dict
            comp = self._component_map.get(name)
            if comp is None:
                return False

        try:
            comp.stop(timeout=timeout)
            ok = comp.initialize()
            if ok:
                ok = comp.start()
            self._total_restarts += 1
            return ok
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error(
                "Failed to restart %s: %s", name, exc
            )
            return False

    # ─── Claude24: Pipeline Diagnostics Integration ──────────────────────

    def enable_pipeline_diagnostics(
        self,
        auto_report_interval_sec: float = 10.0,
    ) -> None:
        """Enable pipeline flow diagnostics (Apollo cyber_monitor equivalent).

        Claude24: Integrates PipelineDiagnostics into the Mainboard lifecycle.
        The diagnostics thread prints a channel flow report periodically.
        """
        try:
            from launch.pipeline_diagnostics import PipelineDiagnostics
            self._pipeline_diag = PipelineDiagnostics()
            self._pipeline_diag_interval = auto_report_interval_sec
            self._pipeline_diag.register_channels([
                "/lol/raw_lcu", "/lol/raw_fiddler",
                "/lol/game_state", "/lol/events",
                "/lol/win_prediction", "/lol/teamfight_assessment",
                "/lol/strategy", "/lol/macro_decision",
                "/lol/voice_command",
            ])
            self._pipeline_diag.on_anomaly(
                lambda atype, desc: logger.warning(
                    "Pipeline anomaly [%s]: %s", atype, desc
                )
            )
            logger.info(
                "Pipeline diagnostics enabled (interval=%.0fs)",
                auto_report_interval_sec,
            )
        except ImportError as exc:
            logger.warning("PipelineDiagnostics not available: %s", exc)
            self._pipeline_diag = None

    @property
    def pipeline_diagnostics(self):
        """Access the PipelineDiagnostics instance (if enabled)."""
        return getattr(self, "_pipeline_diag", None)

    def diagnostics_snapshot(self) -> dict:
        """Return pipeline diagnostics snapshot (if enabled)."""
        diag = getattr(self, "_pipeline_diag", None)
        if diag is None:
            return {}
        return diag.snapshot()

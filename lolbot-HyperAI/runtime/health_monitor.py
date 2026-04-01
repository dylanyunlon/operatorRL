#!/usr/bin/env python3
"""
HealthMonitor — System-wide Health Check & Alerting
=====================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Continuously monitors all registered components, external dependencies
(Riot API, Fiddler MCP, LCU), and system resources (CPU, memory).
Publishes health events onto the message bus for the Evolution layer
to consume — unhealthy systems trigger self-evolution proposals.

Apollo Reference:
    modules/monitor/software/summary_monitor.cc → component monitoring
    cyber/common/global_data.cc → system health state

Design:
    HealthMonitor
      ├── ComponentHealthTracker   (per-component heartbeat + latency)
      ├── DependencyChecker        (external services reachability)
      ├── ResourceMonitor          (CPU / memory / disk thresholds)
      ├── AlertManager             (dedup, cooldown, severity levels)
      └── HealthReport             (aggregated snapshot for dashboard)

Production Critique (Knuth-level):
    1. User: If Riot API is down, the health monitor marks data layer
       as DEGRADED but the system continues with cached data. The user
       hears a voice notification: "Using cached data — live stats
       temporarily unavailable." No crash, no frozen UI.
    2. System: Health checks themselves must never block the main loop.
       All checks are budgeted to complete within 5ms total per tick.
       If a dependency check requires network I/O, it runs asynchronously
       in a dedicated background task with a 2-second timeout.
"""

import asyncio
import enum
import logging
import os
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class HealthStatus(enum.Enum):
    """Overall or per-component health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class AlertSeverity(enum.Enum):
    """Alert severity levels matching standard observability tiers."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class DependencyType(enum.Enum):
    """External dependency categories."""
    RIOT_API = "riot_api"
    FIDDLER_MCP = "fiddler_mcp"
    LCU_CLIENT = "lcu_client"
    TTS_SERVICE = "tts_service"
    NETWORK = "network"
    FILESYSTEM = "filesystem"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Heartbeat:
    """Single heartbeat record from a component."""
    component_name: str
    timestamp: float
    latency_ms: float
    healthy: bool
    message: str = ""


@dataclass
class ComponentHealth:
    """Aggregated health state for one component."""
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_heartbeat: float = 0.0
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    error_rate: float = 0.0
    heartbeat_count: int = 0
    consecutive_misses: int = 0
    last_error: str = ""

    _latencies: Deque[float] = field(
        default_factory=lambda: deque(maxlen=200)
    )
    _errors: Deque[bool] = field(
        default_factory=lambda: deque(maxlen=200)
    )

    def record_heartbeat(self, hb: Heartbeat) -> None:
        """Incorporate a new heartbeat measurement."""
        self.last_heartbeat = hb.timestamp
        self.heartbeat_count += 1
        self._latencies.append(hb.latency_ms)
        self._errors.append(not hb.healthy)

        if hb.healthy:
            self.consecutive_misses = 0
        else:
            self.consecutive_misses += 1
            self.last_error = hb.message

        self._recompute()

    def record_miss(self) -> None:
        """Called when expected heartbeat was not received."""
        self.consecutive_misses += 1
        self._errors.append(True)
        self._recompute()

    def _recompute(self) -> None:
        """Recompute aggregate metrics from recent windows."""
        if self._latencies:
            self.avg_latency_ms = sum(self._latencies) / len(self._latencies)
            sorted_lat = sorted(self._latencies)
            idx = int(len(sorted_lat) * 0.99)
            self.p99_latency_ms = sorted_lat[min(idx, len(sorted_lat) - 1)]

        if self._errors:
            self.error_rate = sum(1 for e in self._errors if e) / len(self._errors)

        # Determine status
        if self.consecutive_misses >= 10:
            self.status = HealthStatus.UNHEALTHY
        elif self.consecutive_misses >= 3 or self.error_rate > 0.3:
            self.status = HealthStatus.DEGRADED
        elif self.heartbeat_count > 0:
            self.status = HealthStatus.HEALTHY
        else:
            self.status = HealthStatus.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "last_heartbeat": self.last_heartbeat,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "error_rate": round(self.error_rate, 4),
            "heartbeat_count": self.heartbeat_count,
            "consecutive_misses": self.consecutive_misses,
            "last_error": self.last_error,
        }


@dataclass
class DependencyHealth:
    """Health state for an external dependency."""
    dep_type: DependencyType
    reachable: bool = False
    last_check: float = 0.0
    latency_ms: float = 0.0
    consecutive_failures: int = 0
    last_error: str = ""

    @property
    def status(self) -> HealthStatus:
        if self.consecutive_failures == 0 and self.reachable:
            return HealthStatus.HEALTHY
        elif self.consecutive_failures < 3:
            return HealthStatus.DEGRADED
        return HealthStatus.UNHEALTHY

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.dep_type.value,
            "status": self.status.value,
            "reachable": self.reachable,
            "latency_ms": round(self.latency_ms, 2),
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
        }


@dataclass
class Alert:
    """A health alert with deduplication support."""
    alert_id: str
    severity: AlertSeverity
    source: str
    message: str
    created_at: float
    resolved: bool = False
    resolved_at: Optional[float] = None
    occurrence_count: int = 1
    last_occurrence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity.value,
            "source": self.source,
            "message": self.message,
            "occurrence_count": self.occurrence_count,
            "resolved": self.resolved,
        }


@dataclass
class ResourceSnapshot:
    """System resource utilization snapshot."""
    timestamp: float
    cpu_percent: float = 0.0
    memory_used_mb: float = 0.0
    memory_total_mb: float = 0.0
    disk_free_mb: float = 0.0
    python_threads: int = 0
    asyncio_tasks: int = 0

    @property
    def memory_percent(self) -> float:
        if self.memory_total_mb <= 0:
            return 0.0
        return (self.memory_used_mb / self.memory_total_mb) * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_used_mb": round(self.memory_used_mb, 1),
            "memory_percent": round(self.memory_percent, 1),
            "disk_free_mb": round(self.disk_free_mb, 1),
            "python_threads": self.python_threads,
            "asyncio_tasks": self.asyncio_tasks,
        }


# ---------------------------------------------------------------------------
# AlertManager — deduplication and cooldown
# ---------------------------------------------------------------------------

class AlertManager:
    """
    Manages alerts with deduplication and cooldown to prevent alert storms.
    Same (source, message_hash) within cooldown_s → increment count, not new alert.
    """

    def __init__(self, cooldown_s: float = 60.0, max_active: int = 100):
        self._log = logging.getLogger("lolbot.runtime.alert_manager")
        self._cooldown_s = cooldown_s
        self._max_active = max_active
        self._active: Dict[str, Alert] = {}
        self._history: Deque[Alert] = deque(maxlen=500)
        self._callbacks: List[Callable[[Alert], None]] = []
        self._alert_counter = 0

    def on_alert(self, callback: Callable[[Alert], None]) -> None:
        """Register callback for new/updated alerts."""
        self._callbacks.append(callback)

    def fire(
        self,
        severity: AlertSeverity,
        source: str,
        message: str,
    ) -> Alert:
        """
        Fire an alert. Deduplicated by (source, message).
        Returns the alert object (new or existing).
        """
        dedup_key = f"{source}:{hash(message) & 0xFFFFFFFF:08x}"
        now = time.monotonic()

        if dedup_key in self._active:
            existing = self._active[dedup_key]
            if now - existing.last_occurrence < self._cooldown_s:
                existing.occurrence_count += 1
                existing.last_occurrence = now
                return existing
            else:
                # Cooldown expired — treat as new occurrence
                existing.occurrence_count += 1
                existing.last_occurrence = now
                existing.severity = severity
                self._notify(existing)
                return existing

        self._alert_counter += 1
        alert = Alert(
            alert_id=f"ALR-{self._alert_counter:06d}",
            severity=severity,
            source=source,
            message=message,
            created_at=now,
            last_occurrence=now,
        )

        self._active[dedup_key] = alert
        self._notify(alert)

        # Evict oldest if over limit
        if len(self._active) > self._max_active:
            oldest_key = min(
                self._active, key=lambda k: self._active[k].last_occurrence
            )
            evicted = self._active.pop(oldest_key)
            evicted.resolved = True
            evicted.resolved_at = now
            self._history.append(evicted)

        return alert

    def resolve(self, source: str, message: str) -> bool:
        """Resolve an active alert. Returns True if found."""
        dedup_key = f"{source}:{hash(message) & 0xFFFFFFFF:08x}"
        if dedup_key in self._active:
            alert = self._active.pop(dedup_key)
            alert.resolved = True
            alert.resolved_at = time.monotonic()
            self._history.append(alert)
            return True
        return False

    def get_active(
        self, min_severity: AlertSeverity = AlertSeverity.INFO
    ) -> List[Alert]:
        """Return active alerts at or above given severity."""
        severity_order = {
            AlertSeverity.INFO: 0,
            AlertSeverity.WARNING: 1,
            AlertSeverity.ERROR: 2,
            AlertSeverity.CRITICAL: 3,
        }
        min_level = severity_order.get(min_severity, 0)
        return [
            a for a in self._active.values()
            if severity_order.get(a.severity, 0) >= min_level
        ]

    def _notify(self, alert: Alert) -> None:
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception as exc:
                self._log.error("Alert callback error: %s", exc)


# ---------------------------------------------------------------------------
# HealthMonitor — the main health check component
# ---------------------------------------------------------------------------

class HealthMonitor:
    """
    System-wide health monitor. Runs as a component inside ProcessManager
    (registered at RUNTIME priority, 1000ms interval).

    Tracks:
      - Component heartbeats (via report_heartbeat())
      - External dependency reachability (background checks)
      - System resources (CPU, memory)
      - Alerts (dedup + severity management)
    """

    def __init__(
        self,
        heartbeat_timeout_s: float = 5.0,
        resource_check_interval_s: float = 10.0,
        dependency_check_interval_s: float = 30.0,
    ):
        self._log = logging.getLogger("lolbot.runtime.health_monitor")
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._resource_interval_s = resource_check_interval_s
        self._dep_interval_s = dependency_check_interval_s

        self._components: Dict[str, ComponentHealth] = {}
        self._dependencies: Dict[DependencyType, DependencyHealth] = {}
        self._alerts = AlertManager()
        self._resource_snapshot = ResourceSnapshot(timestamp=0.0)
        self._last_resource_check = 0.0
        self._last_dep_check = 0.0

        # Dependency check callables: type → async callable returning (ok, latency_ms, error)
        self._dep_checkers: Dict[
            DependencyType,
            Callable[[], Tuple[bool, float, str]]
        ] = {}

        # Callbacks for evolution layer
        self._health_change_callbacks: List[
            Callable[[str, HealthStatus, HealthStatus], None]
        ] = []

    # ---- ComponentProtocol (so HealthMonitor can be registered in ProcessManager) ----

    @property
    def name(self) -> str:
        return "runtime.health_monitor"

    async def init(self) -> None:
        self._log.info("HealthMonitor initialized")

    async def proc(self) -> None:
        """
        Called every tick by ProcessManager.
        Check heartbeat timeouts, resources, dependencies.
        """
        now = time.monotonic()

        # Check heartbeat timeouts
        for ch in self._components.values():
            if ch.last_heartbeat > 0:
                age = now - ch.last_heartbeat
                if age > self._heartbeat_timeout_s:
                    old_status = ch.status
                    ch.record_miss()
                    if ch.status != old_status:
                        self._on_health_change(ch.name, old_status, ch.status)
                        if ch.status == HealthStatus.UNHEALTHY:
                            self._alerts.fire(
                                AlertSeverity.ERROR,
                                f"component.{ch.name}",
                                f"Component {ch.name} heartbeat timeout "
                                f"({ch.consecutive_misses} consecutive misses)",
                            )

        # Periodic resource check
        if now - self._last_resource_check > self._resource_interval_s:
            self._check_resources()
            self._last_resource_check = now

        # Periodic dependency check (non-blocking)
        if now - self._last_dep_check > self._dep_interval_s:
            asyncio.create_task(self._check_dependencies())
            self._last_dep_check = now

    async def shutdown(self) -> None:
        self._log.info("HealthMonitor shutting down")

    # ---- Public API ----

    def register_component(self, name: str) -> None:
        """Register a component for health tracking."""
        if name not in self._components:
            self._components[name] = ComponentHealth(name=name)
            self._log.debug("Tracking health for component: %s", name)

    def report_heartbeat(
        self,
        component_name: str,
        latency_ms: float,
        healthy: bool = True,
        message: str = "",
    ) -> None:
        """
        Called by components to report their health.
        Should be called at least once per heartbeat_timeout_s.
        """
        if component_name not in self._components:
            self._components[component_name] = ComponentHealth(name=component_name)

        ch = self._components[component_name]
        old_status = ch.status

        hb = Heartbeat(
            component_name=component_name,
            timestamp=time.monotonic(),
            latency_ms=latency_ms,
            healthy=healthy,
            message=message,
        )
        ch.record_heartbeat(hb)

        if ch.status != old_status:
            self._on_health_change(component_name, old_status, ch.status)

            # Auto-resolve alerts when component recovers
            if ch.status == HealthStatus.HEALTHY:
                self._alerts.resolve(
                    f"component.{component_name}", ""
                )

    def register_dependency_checker(
        self,
        dep_type: DependencyType,
        checker: Callable,
    ) -> None:
        """
        Register an async function that checks dependency health.
        Checker signature: async () -> Tuple[bool, float, str]
                          (reachable, latency_ms, error_msg)
        """
        self._dep_checkers[dep_type] = checker
        self._dependencies[dep_type] = DependencyHealth(dep_type=dep_type)

    def on_health_change(
        self,
        callback: Callable[[str, HealthStatus, HealthStatus], None],
    ) -> None:
        """Register callback for component health state transitions."""
        self._health_change_callbacks.append(callback)

    def get_overall_status(self) -> HealthStatus:
        """
        Aggregate system health:
        - UNHEALTHY if any critical component is unhealthy
        - DEGRADED if any component is degraded
        - HEALTHY otherwise
        """
        statuses = [ch.status for ch in self._components.values()]
        dep_statuses = [dh.status for dh in self._dependencies.values()]
        all_statuses = statuses + dep_statuses

        if not all_statuses:
            return HealthStatus.UNKNOWN
        if any(s == HealthStatus.UNHEALTHY for s in all_statuses):
            return HealthStatus.UNHEALTHY
        if any(s == HealthStatus.DEGRADED for s in all_statuses):
            return HealthStatus.DEGRADED
        if all(s == HealthStatus.HEALTHY for s in all_statuses):
            return HealthStatus.HEALTHY
        return HealthStatus.UNKNOWN

    def get_report(self) -> Dict[str, Any]:
        """Full health report for dashboard / API."""
        return {
            "overall": self.get_overall_status().value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {
                name: ch.to_dict()
                for name, ch in self._components.items()
            },
            "dependencies": {
                dt.value: dh.to_dict()
                for dt, dh in self._dependencies.items()
            },
            "resources": self._resource_snapshot.to_dict(),
            "active_alerts": [
                a.to_dict() for a in self._alerts.get_active()
            ],
        }

    def should_trigger_evolution(self) -> bool:
        """
        Returns True if system health warrants an evolution cycle.
        Used by EvolutionController to decide when to propose changes.
        """
        overall = self.get_overall_status()
        if overall == HealthStatus.UNHEALTHY:
            return True
        active_errors = self._alerts.get_active(AlertSeverity.ERROR)
        if len(active_errors) >= 3:
            return True
        degraded = [
            ch for ch in self._components.values()
            if ch.status == HealthStatus.DEGRADED and ch.error_rate > 0.5
        ]
        if len(degraded) >= 2:
            return True
        return False

    @property
    def alert_manager(self) -> AlertManager:
        return self._alerts

    # ---- Internal ----

    def _on_health_change(
        self,
        name: str,
        old: HealthStatus,
        new: HealthStatus,
    ) -> None:
        self._log.info(
            "Health change: %s %s → %s", name, old.value, new.value
        )
        for cb in self._health_change_callbacks:
            try:
                cb(name, old, new)
            except Exception as exc:
                self._log.error("Health change callback error: %s", exc)

    def _check_resources(self) -> None:
        """Gather system resource metrics (non-blocking)."""
        import threading

        try:
            # Memory via /proc/meminfo (Linux) or psutil fallback
            mem_used = 0.0
            mem_total = 0.0
            try:
                with open("/proc/meminfo", "r") as f:
                    lines = f.readlines()
                for line in lines:
                    parts = line.split()
                    if parts[0] == "MemTotal:":
                        mem_total = int(parts[1]) / 1024.0  # kB → MB
                    elif parts[0] == "MemAvailable:":
                        mem_avail = int(parts[1]) / 1024.0
                        mem_used = mem_total - mem_avail
            except (FileNotFoundError, ValueError):
                pass

            # CPU via /proc/stat is complex; use simple load average
            cpu_pct = 0.0
            try:
                load_1, _, _ = os.getloadavg()
                cpu_count = os.cpu_count() or 1
                cpu_pct = (load_1 / cpu_count) * 100.0
            except (OSError, AttributeError):
                pass

            # Disk
            disk_free = 0.0
            try:
                st = os.statvfs("/")
                disk_free = (st.f_bavail * st.f_frsize) / (1024 * 1024)
            except (OSError, AttributeError):
                pass

            # Asyncio tasks
            try:
                loop = asyncio.get_running_loop()
                tasks = len(asyncio.all_tasks(loop))
            except RuntimeError:
                tasks = 0

            self._resource_snapshot = ResourceSnapshot(
                timestamp=time.monotonic(),
                cpu_percent=cpu_pct,
                memory_used_mb=mem_used,
                memory_total_mb=mem_total,
                disk_free_mb=disk_free,
                python_threads=threading.active_count(),
                asyncio_tasks=tasks,
            )

            # Alert on resource thresholds
            if cpu_pct > 90.0:
                self._alerts.fire(
                    AlertSeverity.WARNING,
                    "resources.cpu",
                    f"CPU usage high: {cpu_pct:.0f}%",
                )
            if mem_total > 0 and (mem_used / mem_total) > 0.9:
                self._alerts.fire(
                    AlertSeverity.WARNING,
                    "resources.memory",
                    f"Memory usage high: {mem_used:.0f}/{mem_total:.0f} MB",
                )
            if 0 < disk_free < 500:
                self._alerts.fire(
                    AlertSeverity.ERROR,
                    "resources.disk",
                    f"Low disk space: {disk_free:.0f} MB free",
                )

        except Exception as exc:
            self._log.error("Resource check failed: %s", exc)

    async def _check_dependencies(self) -> None:
        """Run all dependency checks (async, with timeout)."""
        for dep_type, checker in self._dep_checkers.items():
            dh = self._dependencies.get(dep_type)
            if dh is None:
                dh = DependencyHealth(dep_type=dep_type)
                self._dependencies[dep_type] = dh

            try:
                result = await asyncio.wait_for(checker(), timeout=2.0)
                reachable, latency_ms, error_msg = result
                dh.reachable = reachable
                dh.latency_ms = latency_ms
                dh.last_check = time.monotonic()

                if reachable:
                    dh.consecutive_failures = 0
                    dh.last_error = ""
                    self._alerts.resolve(
                        f"dependency.{dep_type.value}", ""
                    )
                else:
                    dh.consecutive_failures += 1
                    dh.last_error = error_msg
                    self._alerts.fire(
                        AlertSeverity.WARNING
                        if dh.consecutive_failures < 3
                        else AlertSeverity.ERROR,
                        f"dependency.{dep_type.value}",
                        f"{dep_type.value} unreachable: {error_msg}",
                    )

            except asyncio.TimeoutError:
                dh.reachable = False
                dh.consecutive_failures += 1
                dh.last_error = "Check timed out (2s)"
                dh.last_check = time.monotonic()
            except Exception as exc:
                dh.consecutive_failures += 1
                dh.last_error = str(exc)
                self._log.error(
                    "Dependency check %s failed: %s", dep_type.value, exc
                )

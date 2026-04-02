"""
MonitorComponent — System health guardian (Apollo modules/monitor).
====================================================================

Periodic TimerComponent that checks all other components' health,
resource usage, channel throughput, and circuit-breaker state. Publishes
health reports to /lol/system_health and exposes an RPC service for
on-demand diagnostics.

Architecture position:
    modules/monitor/monitor_component.py   ← YOU ARE HERE
    ├─ Reads: cyber/statistics (latency, throughput)
    ├─ Reads: modules/monitor/resource_tracker.py (CPU/mem)
    ├─ Publishes: /lol/system_health (HealthReport)
    ├─ Exposes: "system_health" RPC service
    └─ Alerts: on degradation or component failure

Apollo reference:
    modules/monitor/monitor.cc — periodic system health checks
    modules/guardian/guardian.cc — safety guardian
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

from cyber.component.timer_component import (
    ComponentConfig, ComponentState, TimerComponent,
)
from cyber.node.node import CyberNode, Writer
from cyber.statistics.statistics import Statistics

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_MONITOR_INTERVAL_MS: float = 1000.0  # 1Hz health checks
_WARN_LATENCY_US: int = 50000         # 50ms
_ERROR_LATENCY_US: int = 200000       # 200ms
_WARN_FAILURE_RATE: float = 0.05      # 5%
_ERROR_FAILURE_RATE: float = 0.20     # 20%
_MAX_ALERTS_PER_MINUTE: int = 10
_HEALTH_CHANNEL: str = "/lol/system_health"
_ALERT_CHANNEL: str = "/lol/system_alert"


class HealthLevel(Enum):
    """System or component health level."""
    OK = auto()
    DEGRADED = auto()
    ERROR = auto()
    CRITICAL = auto()


@dataclass
class ComponentHealth:
    """Health assessment for a single component."""
    name: str
    level: HealthLevel = HealthLevel.OK
    mean_latency_us: float = 0.0
    p95_latency_us: float = 0.0
    failure_rate: float = 0.0
    overrun_count: int = 0
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "level": self.level.name,
            "mean_latency_us": round(self.mean_latency_us, 1),
            "p95_latency_us": round(self.p95_latency_us, 1),
            "failure_rate": round(self.failure_rate, 4),
            "overrun_count": self.overrun_count,
            "message": self.message,
        }


@dataclass
class HealthReport:
    """Complete system health report."""
    timestamp: float = 0.0
    overall_level: HealthLevel = HealthLevel.OK
    components: List[ComponentHealth] = field(default_factory=list)
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    thread_count: int = 0
    uptime_s: float = 0.0
    channel_count: int = 0
    total_writes_per_second: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "overall_level": self.overall_level.name,
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_mb": round(self.memory_mb, 1),
            "thread_count": self.thread_count,
            "uptime_s": round(self.uptime_s, 1),
            "channel_count": self.channel_count,
            "total_writes_per_second": round(
                self.total_writes_per_second, 1
            ),
            "components": [c.to_dict() for c in self.components],
        }


@dataclass
class AlertRecord:
    """A single alert emitted by the monitor."""
    timestamp: float
    component: str
    level: HealthLevel
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "component": self.component,
            "level": self.level.name,
            "message": self.message,
        }


class MonitorComponent(TimerComponent):
    """System health monitor — the guardian of the pipeline.

    Each ``Proc()`` cycle (1Hz):
    1. Query Statistics singleton for all component metrics
    2. Query ResourceTracker for CPU/memory usage
    3. Evaluate health thresholds for each component
    4. Compute overall system health level
    5. Publish HealthReport to /lol/system_health
    6. Emit alerts for degraded/error components

    Usage::

        monitor = MonitorComponent()
        monitor.initialize()
        monitor.start()
        # ... later ...
        report = monitor.latest_report
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="monitor",
                interval_ms=_MONITOR_INTERVAL_MS,
                warn_threshold_ms=500.0,
            )
        )
        self._node: Optional[CyberNode] = None
        self._health_writer: Optional[Writer] = None
        self._alert_writer: Optional[Writer] = None
        self._latest_report: Optional[HealthReport] = None
        self._alerts: List[AlertRecord] = []
        self._alert_timestamps: List[float] = []
        self._on_alert_callbacks: List[Callable[[AlertRecord], None]] = []
        self._resource_tracker: Optional[Any] = None

    def Init(self) -> bool:
        """Initialize monitor: create node, writers, resource tracker."""
        self._node = CyberNode("monitor")
        self._health_writer = self._node.create_writer(_HEALTH_CHANNEL)
        self._alert_writer = self._node.create_writer(_ALERT_CHANNEL)

        # Try to import resource tracker (optional dependency)
        try:
            from modules.monitor.resource_tracker import ResourceTracker
            self._resource_tracker = ResourceTracker()
            self._resource_tracker.start()
        except ImportError:
            logger.warning("ResourceTracker not available, "
                           "resource metrics disabled")

        logger.info("MonitorComponent initialized")
        return True

    def Proc(self) -> bool:
        """Run one health-check cycle."""
        stats = Statistics.instance()
        snapshot = stats.full_snapshot()

        # Assess each component
        component_healths: List[ComponentHealth] = []
        worst_level = HealthLevel.OK

        for comp_name, comp_data in snapshot.get("components", {}).items():
            health = self._assess_component(comp_name, comp_data)
            component_healths.append(health)
            if health.level.value > worst_level.value:
                worst_level = health.level

        # Resource metrics
        cpu_pct = 0.0
        mem_mb = 0.0
        thread_count = threading.active_count()

        if self._resource_tracker:
            try:
                res = self._resource_tracker.snapshot()
                cpu_pct = res.get("cpu_percent", 0.0)
                mem_mb = res.get("memory_rss_mb", 0.0)
            except Exception:
                pass

        # Resource-based health assessment
        if mem_mb > 1024:
            worst_level = max(worst_level, HealthLevel.DEGRADED,
                              key=lambda x: x.value)
        if cpu_pct > 90:
            worst_level = max(worst_level, HealthLevel.DEGRADED,
                              key=lambda x: x.value)

        # Channel metrics
        ch_data = snapshot.get("channels", {})
        total_wps = sum(
            c.get("writes_per_second", 0) for c in ch_data.values()
        )

        # Build report
        report = HealthReport(
            timestamp=time.time(),
            overall_level=worst_level,
            components=component_healths,
            cpu_percent=cpu_pct,
            memory_mb=mem_mb,
            thread_count=thread_count,
            uptime_s=snapshot.get("uptime_s", 0),
            channel_count=len(ch_data),
            total_writes_per_second=total_wps,
        )
        self._latest_report = report

        # Publish
        if self._health_writer:
            self._health_writer.write(report.to_dict())

        # Generate alerts for non-OK components
        for health in component_healths:
            if health.level != HealthLevel.OK:
                self._emit_alert(health)

        return True

    def _assess_component(
        self, name: str, data: Dict[str, Any]
    ) -> ComponentHealth:
        """Evaluate a single component's health from its statistics."""
        health = ComponentHealth(name=name)

        total_procs = data.get("total_procs", 0)
        total_failures = data.get("total_failures", 0)
        health.mean_latency_us = data.get("mean_us", 0)
        health.p95_latency_us = data.get("p95_us", 0)
        health.overrun_count = data.get("total_overruns", 0)

        if total_procs > 0:
            health.failure_rate = total_failures / total_procs
        else:
            health.failure_rate = 0.0

        # Determine level
        messages = []
        level = HealthLevel.OK

        if health.failure_rate >= _ERROR_FAILURE_RATE:
            level = HealthLevel.ERROR
            messages.append(
                f"failure_rate={health.failure_rate:.1%}"
            )
        elif health.failure_rate >= _WARN_FAILURE_RATE:
            level = HealthLevel.DEGRADED
            messages.append(
                f"failure_rate={health.failure_rate:.1%}"
            )

        if health.p95_latency_us >= _ERROR_LATENCY_US:
            level = max(level, HealthLevel.ERROR, key=lambda x: x.value)
            messages.append(
                f"p95={health.p95_latency_us/1000:.1f}ms"
            )
        elif health.p95_latency_us >= _WARN_LATENCY_US:
            level = max(level, HealthLevel.DEGRADED, key=lambda x: x.value)
            messages.append(
                f"p95={health.p95_latency_us/1000:.1f}ms"
            )

        health.level = level
        health.message = "; ".join(messages) if messages else "ok"
        return health

    def _emit_alert(self, health: ComponentHealth) -> None:
        """Emit an alert, rate-limited."""
        now = time.monotonic()
        # Prune old timestamps
        self._alert_timestamps = [
            t for t in self._alert_timestamps if now - t < 60
        ]
        if len(self._alert_timestamps) >= _MAX_ALERTS_PER_MINUTE:
            return

        alert = AlertRecord(
            timestamp=time.time(),
            component=health.name,
            level=health.level,
            message=health.message,
        )
        self._alerts.append(alert)
        self._alert_timestamps.append(now)

        if self._alert_writer:
            self._alert_writer.write(alert.to_dict())

        for cb in self._on_alert_callbacks:
            try:
                cb(alert)
            except Exception:
                pass

        logger.warning(
            "[Monitor] %s: %s — %s",
            health.level.name, health.name, health.message,
        )

    # ─── Public API ──────────────────────────────────────────────────────

    @property
    def latest_report(self) -> Optional[HealthReport]:
        return self._latest_report

    @property
    def recent_alerts(self) -> List[AlertRecord]:
        return list(self._alerts[-50:])

    def on_alert(self, callback: Callable[[AlertRecord], None]) -> None:
        self._on_alert_callbacks.append(callback)

    def on_shutdown(self) -> None:
        if self._resource_tracker:
            try:
                self._resource_tracker.stop()
            except Exception:
                pass

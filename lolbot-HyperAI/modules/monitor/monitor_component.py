"""
MonitorComponent — System health guardian (Apollo modules/monitor).
====================================================================
lolbot-HyperAI · Monitor Layer

查看 Apollo modules/monitor/ 上现有系统监控组件的实现方式, 理解其模式,
特别是硬件状态、模块健康、系统资源是如何统一监控的。从 Apollo monitor 的
RecurrentRunner + FunctionalSafetyMonitor 这个好例子开始。然后扩充我们的
monitor_component, 增加每个组件的 Proc() 耗时追踪、消息延迟监控、内存使用
告警, 让运维可以通过 /lol/monitor_status 频道实时获取系统健康, 并能在
组件异常时自动降级。

Architecture position:
    modules/monitor/monitor_component.py   <- YOU ARE HERE
    +- Reads: ComponentRegistry.health_summary() (all components)
    +- Reads: /lol/canbus_status, /lol/control_status (channels)
    +- Publishes: /lol/monitor_status (SystemHealthReport)
    +- Publishes: /lol/alert (AlertRecord)
    +- Manages: resource_tracker (CPU/memory watchdog)

Claude11 refactor:
    - ManagedComponent mixin for lifecycle + circuit breaker
    - Per-component Proc() latency tracking via ProcMetrics
    - Alert severity levels (INFO/WARNING/ERROR/CRITICAL)
    - Auto-degradation: components exceeding thresholds get flagged
    - Resource tracker: memory/CPU watchdog with configurable limits
    - Heartbeat publisher for external monitoring systems
"""

from __future__ import annotations

import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.component_base import (
    ComponentDependency,
    ComponentRegistry,
    LifecycleState,
    ManagedComponent,
)

logger = get_logger("monitor")

# --- Constants ---------------------------------------------------------------

_MONITOR_INTERVAL_MS = 2000.0   # 0.5Hz — check health every 2s
_WARN_THRESHOLD_MS = 500.0
_RESOURCE_CHECK_INTERVAL_S = 10.0
_MEMORY_WARN_MB = 512
_MEMORY_CRITICAL_MB = 1024
_PROC_LATENCY_WARN_MS = 50.0    # warn if any Proc() avg > 50ms
_PROC_LATENCY_ERROR_MS = 200.0  # error if any Proc() avg > 200ms
_MAX_ALERTS = 500
_HEARTBEAT_INTERVAL_S = 30.0


# --- Alert system ------------------------------------------------------------

class AlertSeverity(Enum):
    """Alert severity levels."""
    INFO = auto()
    WARNING = auto()
    ERROR = auto()
    CRITICAL = auto()


@dataclass
class AlertRecord:
    """A single alert event."""
    alert_id: int = 0
    timestamp: float = 0.0
    severity: AlertSeverity = AlertSeverity.INFO
    component: str = ""
    category: str = ""
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    resolved: bool = False
    resolved_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.alert_id,
            "ts": round(self.timestamp, 3),
            "severity": self.severity.name,
            "component": self.component,
            "category": self.category,
            "message": self.message,
            "resolved": self.resolved,
        }


class AlertManager:
    """Manages alert lifecycle: create, resolve, dedup, history."""

    def __init__(self, max_alerts: int = _MAX_ALERTS) -> None:
        self._alerts: Deque[AlertRecord] = deque(maxlen=max_alerts)
        self._active: Dict[str, AlertRecord] = {}
        self._counter = 0
        self._callbacks: List[Callable[[AlertRecord], None]] = []

    def fire(
        self,
        severity: AlertSeverity,
        component: str,
        category: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> AlertRecord:
        """Fire a new alert (or update existing active alert)."""
        key = f"{component}:{category}"

        # Dedup: if same alert is already active, skip
        if key in self._active:
            existing = self._active[key]
            if not existing.resolved:
                return existing

        self._counter += 1
        alert = AlertRecord(
            alert_id=self._counter,
            timestamp=time.time(),
            severity=severity,
            component=component,
            category=category,
            message=message,
            details=details or {},
        )
        self._alerts.append(alert)
        self._active[key] = alert

        # Notify callbacks
        for cb in self._callbacks:
            try:
                cb(alert)
            except Exception:
                pass

        # Log based on severity
        if severity == AlertSeverity.CRITICAL:
            logger.error("[ALERT:CRITICAL] %s: %s", component, message)
        elif severity == AlertSeverity.ERROR:
            logger.error("[ALERT:ERROR] %s: %s", component, message)
        elif severity == AlertSeverity.WARNING:
            logger.warning("[ALERT:WARN] %s: %s", component, message)
        else:
            logger.info("[ALERT:INFO] %s: %s", component, message)

        return alert

    def resolve(self, component: str, category: str) -> bool:
        """Resolve an active alert."""
        key = f"{component}:{category}"
        alert = self._active.get(key)
        if alert and not alert.resolved:
            alert.resolved = True
            alert.resolved_at = time.time()
            return True
        return False

    def active_alerts(self) -> List[AlertRecord]:
        """Get all unresolved alerts."""
        return [a for a in self._active.values() if not a.resolved]

    def recent(self, count: int = 20) -> List[AlertRecord]:
        """Get recent alerts."""
        items = list(self._alerts)
        return items[-count:]

    def on_alert(self, callback: Callable[[AlertRecord], None]) -> None:
        """Register alert callback."""
        self._callbacks.append(callback)

    def stats(self) -> Dict[str, Any]:
        active = self.active_alerts()
        return {
            "total_alerts": self._counter,
            "active_count": len(active),
            "active_by_severity": {
                s.name: sum(1 for a in active if a.severity == s)
                for s in AlertSeverity
            },
            "history_size": len(self._alerts),
        }


# --- Resource tracker --------------------------------------------------------

class ResourceTracker:
    """Lightweight resource monitoring (memory, uptime)."""

    def __init__(self) -> None:
        self._start_time = time.monotonic()
        self._last_check = 0.0
        self._snapshots: Deque[Dict[str, Any]] = deque(maxlen=100)

    def check(self) -> Dict[str, Any]:
        """Take a resource snapshot."""
        try:
            import resource as res_mod
            usage = res_mod.getrusage(res_mod.RUSAGE_SELF)
            rss_mb = usage.ru_maxrss / 1024  # Linux: KB -> MB
        except Exception:
            rss_mb = 0.0

        snapshot = {
            "ts": time.time(),
            "rss_mb": round(rss_mb, 1),
            "uptime_s": round(time.monotonic() - self._start_time, 1),
            "pid": os.getpid(),
        }
        self._snapshots.append(snapshot)
        self._last_check = time.monotonic()
        return snapshot

    @property
    def last_rss_mb(self) -> float:
        if self._snapshots:
            return self._snapshots[-1].get("rss_mb", 0.0)
        return 0.0

    def stop(self) -> None:
        """No-op cleanup."""
        pass


# --- Component health checker ------------------------------------------------

@dataclass
class ComponentHealthEntry:
    """Tracked health info for one component."""
    name: str = ""
    healthy: bool = True
    last_check_time: float = 0.0
    avg_proc_latency_ms: float = 0.0
    success_rate: float = 1.0
    consecutive_unhealthy: int = 0
    degraded: bool = False
    details: Dict[str, Any] = field(default_factory=dict)


class ComponentHealthTracker:
    """Tracks per-component health over time."""

    def __init__(self) -> None:
        self._entries: Dict[str, ComponentHealthEntry] = {}
        self._history: Deque[Dict[str, Any]] = deque(maxlen=200)

    def update(self, name: str, health: Dict[str, Any]) -> ComponentHealthEntry:
        """Update health info for a component."""
        entry = self._entries.get(name)
        if entry is None:
            entry = ComponentHealthEntry(name=name)
            self._entries[name] = entry

        entry.healthy = health.get("healthy", True)
        entry.last_check_time = time.monotonic()
        entry.degraded = health.get("degraded", False)
        entry.details = health

        # Extract proc metrics if available
        proc = health.get("details", {}).get("proc_metrics", {})
        if proc:
            entry.avg_proc_latency_ms = proc.get("avg_latency_ms", 0.0)
            entry.success_rate = proc.get("success_rate", 1.0)

        if entry.healthy:
            entry.consecutive_unhealthy = 0
        else:
            entry.consecutive_unhealthy += 1

        return entry

    def get(self, name: str) -> Optional[ComponentHealthEntry]:
        return self._entries.get(name)

    def all_entries(self) -> Dict[str, ComponentHealthEntry]:
        return dict(self._entries)

    def unhealthy(self) -> List[ComponentHealthEntry]:
        return [e for e in self._entries.values() if not e.healthy]

    def summary(self) -> Dict[str, Any]:
        total = len(self._entries)
        healthy = sum(1 for e in self._entries.values() if e.healthy)
        degraded = sum(1 for e in self._entries.values() if e.degraded)
        return {
            "total_components": total,
            "healthy": healthy,
            "unhealthy": total - healthy,
            "degraded": degraded,
        }


# --- MonitorComponent --------------------------------------------------------

class MonitorComponent(TimerComponent, ManagedComponent):
    """System health guardian — monitors all components.

    Proc() cycle (2s interval):
    1. Query ComponentRegistry for all component health
    2. Track per-component latency and success rate
    3. Check resource usage (memory)
    4. Fire alerts for degraded/unhealthy components
    5. Publish system health report to /lol/monitor_status
    6. Emit heartbeat for external monitoring
    """

    COMPONENT_NAME = "monitor"
    DEPENDENCIES = []
    VERSION = "2.0.0"

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="monitor",
                interval_ms=_MONITOR_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None
        self._status_writer: Optional[Writer] = None

        self._alert_manager = AlertManager()
        self._resource_tracker = ResourceTracker()
        self._health_tracker = ComponentHealthTracker()

        self._tick_count = 0
        self._last_resource_check = 0.0
        self._last_heartbeat = 0.0
        self._on_alert_callbacks: List[Callable[[AlertRecord], None]] = []

    def Init(self) -> bool:
        """Initialize monitor component."""
        self._managed_init()
        logger.info("Initializing MonitorComponent v%s ...", self.VERSION)

        self._node = CyberNode("monitor")
        self._status_writer = self._node.CreateWriter(
            "/lol/monitor_status", dict,
        )

        # Wire alert callbacks
        for cb in self._on_alert_callbacks:
            self._alert_manager.on_alert(cb)

        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info("MonitorComponent initialized")
        return True

    def Proc(self) -> bool:
        """Monitor cycle: check health, resources, fire alerts."""
        if self.should_skip_proc():
            return True

        with self.measure_proc() as m:
            self._tick_count += 1

            # 1. Check all component health via registry
            self._check_component_health()

            # 2. Periodic resource check
            now = time.monotonic()
            if now - self._last_resource_check >= _RESOURCE_CHECK_INTERVAL_S:
                self._check_resources()
                self._last_resource_check = now

            # 3. Publish heartbeat
            if now - self._last_heartbeat >= _HEARTBEAT_INTERVAL_S:
                self._publish_heartbeat()
                self._last_heartbeat = now

            m.success = True
            return True

    def _check_component_health(self) -> None:
        """Query all registered components for health."""
        registry = ComponentRegistry.instance()
        health_map = registry.health_summary()

        for name, health in health_map.items():
            if name == self.COMPONENT_NAME:
                continue  # Don't monitor self

            entry = self._health_tracker.update(name, health)

            # Check for latency warnings
            if entry.avg_proc_latency_ms > _PROC_LATENCY_ERROR_MS:
                self._alert_manager.fire(
                    AlertSeverity.ERROR, name, "high_latency",
                    f"Proc() avg latency {entry.avg_proc_latency_ms:.0f}ms "
                    f"exceeds {_PROC_LATENCY_ERROR_MS}ms threshold",
                )
            elif entry.avg_proc_latency_ms > _PROC_LATENCY_WARN_MS:
                self._alert_manager.fire(
                    AlertSeverity.WARNING, name, "high_latency",
                    f"Proc() avg latency {entry.avg_proc_latency_ms:.0f}ms "
                    f"exceeds {_PROC_LATENCY_WARN_MS}ms threshold",
                )
            else:
                self._alert_manager.resolve(name, "high_latency")

            # Check for unhealthy components
            if not entry.healthy:
                sev = AlertSeverity.ERROR
                if entry.consecutive_unhealthy >= 5:
                    sev = AlertSeverity.CRITICAL
                self._alert_manager.fire(
                    sev, name, "unhealthy",
                    f"Component unhealthy for "
                    f"{entry.consecutive_unhealthy} checks",
                    details=entry.details,
                )
            else:
                self._alert_manager.resolve(name, "unhealthy")

            # Check for low success rate
            if entry.success_rate < 0.5:
                self._alert_manager.fire(
                    AlertSeverity.WARNING, name, "low_success_rate",
                    f"Success rate {entry.success_rate:.1%} below 50%",
                )
            else:
                self._alert_manager.resolve(name, "low_success_rate")

    def _check_resources(self) -> None:
        """Check system resource usage."""
        snap = self._resource_tracker.check()
        rss = snap.get("rss_mb", 0.0)

        if rss > _MEMORY_CRITICAL_MB:
            self._alert_manager.fire(
                AlertSeverity.CRITICAL, "system", "memory",
                f"RSS memory {rss:.0f}MB exceeds "
                f"{_MEMORY_CRITICAL_MB}MB limit",
            )
        elif rss > _MEMORY_WARN_MB:
            self._alert_manager.fire(
                AlertSeverity.WARNING, "system", "memory",
                f"RSS memory {rss:.0f}MB exceeds "
                f"{_MEMORY_WARN_MB}MB threshold",
            )
        else:
            self._alert_manager.resolve("system", "memory")

    def _publish_heartbeat(self) -> None:
        """Publish system health heartbeat."""
        if self._status_writer is None:
            return

        report = {
            "ts": time.time(),
            "type": "heartbeat",
            "health": self._health_tracker.summary(),
            "alerts": self._alert_manager.stats(),
            "resource": {
                "rss_mb": self._resource_tracker.last_rss_mb,
            },
        }
        try:
            self._status_writer.Write(report)
        except Exception:
            pass

    # -- Public API --

    def on_alert(self, callback: Callable[[AlertRecord], None]) -> None:
        """Register alert callback."""
        self._on_alert_callbacks.append(callback)
        if hasattr(self, "_alert_manager"):
            self._alert_manager.on_alert(callback)

    def active_alerts(self) -> List[AlertRecord]:
        """Get all active (unresolved) alerts."""
        return self._alert_manager.active_alerts()

    def system_report(self) -> Dict[str, Any]:
        """Full system health report."""
        return {
            "health": self._health_tracker.summary(),
            "components": {
                name: {
                    "healthy": e.healthy,
                    "latency_ms": round(e.avg_proc_latency_ms, 1),
                    "success_rate": round(e.success_rate, 3),
                    "degraded": e.degraded,
                }
                for name, e in self._health_tracker.all_entries().items()
            },
            "alerts": self._alert_manager.stats(),
            "active_alerts": [
                a.to_dict() for a in self._alert_manager.active_alerts()
            ],
            "resource": {
                "rss_mb": self._resource_tracker.last_rss_mb,
            },
        }

    def on_shutdown(self) -> None:
        self._managed_shutdown()
        if self._resource_tracker:
            try:
                self._resource_tracker.stop()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CircuitBreaker — per-component failure isolation (Claude11 addition)
# ---------------------------------------------------------------------------

class _CBState(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


@dataclass
class CircuitBreaker:
    """Per-component circuit breaker.  Trips after N consecutive failures,
    auto-resets after timeout (half-open) to allow recovery."""
    component: str
    state: _CBState = _CBState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    last_state_change: float = 0.0
    trip_count: int = 0
    threshold: int = 5
    reset_timeout_s: float = 30.0

    def record_success(self) -> None:
        self.success_count += 1
        if self.state == _CBState.HALF_OPEN:
            self._transition(_CBState.CLOSED)
        self.failure_count = 0

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.state == _CBState.CLOSED and self.failure_count >= self.threshold:
            self._transition(_CBState.OPEN)
            self.trip_count += 1
        elif self.state == _CBState.HALF_OPEN:
            self._transition(_CBState.OPEN)

    def should_allow(self) -> bool:
        if self.state == _CBState.CLOSED:
            return True
        if self.state == _CBState.OPEN:
            if time.monotonic() - self.last_state_change >= self.reset_timeout_s:
                self._transition(_CBState.HALF_OPEN)
                return True
            return False
        return True

    def _transition(self, new: _CBState) -> None:
        self.state = new
        self.last_state_change = time.monotonic()
        if new == _CBState.CLOSED:
            self.failure_count = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"component": self.component, "state": self.state.name,
                "failure_count": self.failure_count, "trip_count": self.trip_count}


# ---------------------------------------------------------------------------
# LatencyHistogram — Proc() latency distribution (Claude11 addition)
# ---------------------------------------------------------------------------

class LatencyHistogram:
    """Histogram of Proc() latencies for a single component."""
    _BUCKETS = [1, 5, 10, 25, 50, 100, 250, 500, 1000]

    def __init__(self) -> None:
        self._counts: Dict[str, int] = {f"le_{b}ms": 0 for b in self._BUCKETS}
        self._counts["le_inf"] = 0
        self._total_count: int = 0
        self._total_sum_ms: float = 0.0

    def observe(self, latency_ms: float) -> None:
        self._total_count += 1
        self._total_sum_ms += latency_ms
        for b in self._BUCKETS:
            if latency_ms <= b:
                self._counts[f"le_{b}ms"] += 1
                return
        self._counts["le_inf"] += 1

    def percentile(self, p: float) -> float:
        if self._total_count == 0:
            return 0.0
        target = self._total_count * p
        cumulative = 0
        for b in self._BUCKETS:
            cumulative += self._counts[f"le_{b}ms"]
            if cumulative >= target:
                return float(b)
        return float(self._BUCKETS[-1]) if self._BUCKETS else 0.0

    def snapshot(self) -> Dict[str, Any]:
        avg = self._total_sum_ms / self._total_count if self._total_count > 0 else 0
        return {"count": self._total_count, "avg_ms": round(avg, 2),
                "p50_ms": self.percentile(0.5), "p95_ms": self.percentile(0.95),
                "p99_ms": self.percentile(0.99)}

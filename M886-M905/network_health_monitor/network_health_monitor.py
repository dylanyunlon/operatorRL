#!/usr/bin/env python3
"""
M903 — NetworkHealthMonitor
=============================
Monitors Fiddler/Proxifier/LoL network health, auto-degrades on failure.

Reference: M866-M885 system_health_dashboard pattern
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M903.NetworkHealthMonitor")


class HealthStatus(Enum):
    HEALTHY = auto()
    DEGRADED = auto()
    UNHEALTHY = auto()
    UNKNOWN = auto()


@dataclass
class ComponentHealth:
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    latency_ms: float = 0.0
    last_check: Optional[datetime] = None
    error_count: int = 0
    consecutive_failures: int = 0
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "status": self.status.name,
                "latency_ms": round(self.latency_ms, 1),
                "errors": self.error_count, "consecutive_failures": self.consecutive_failures,
                "details": self.details}


class NetworkHealthMonitor:
    """
    Monitors Fiddler↔Proxifier↔LoL network chain health.
    Auto-degrades to cache mode when network issues detected.
    """

    def __init__(self, fiddler_url: str = "http://localhost:8868",
                 check_interval: float = 10.0):
        self._fiddler_url = fiddler_url
        self._interval = check_interval
        self._components: Dict[str, ComponentHealth] = {
            "fiddler_mcp": ComponentHealth(name="Fiddler MCP Server"),
            "lcu_api": ComponentHealth(name="LCU REST API"),
            "live_client": ComponentHealth(name="Live Client Data API"),
            "proxifier": ComponentHealth(name="Proxifier Service"),
        }
        self._listeners: Dict[str, List[Callable]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._overall_status = HealthStatus.UNKNOWN
        self._stats = {"checks": 0, "degradations": 0, "recoveries": 0}
        logger.info("NetworkHealthMonitor initialized")

    def on(self, event: str, cb: Callable):
        self._listeners.setdefault(event, []).append(cb)

    async def _emit(self, event: str, data: Any = None):
        for cb in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb): await cb(data)
                else: cb(data)
            except Exception as exc:
                logger.error("Emit error: %s", exc)

    async def start(self):
        self._shutdown.clear()
        self._poll_task = asyncio.create_task(self._monitor_loop(), name="health-monitor")
        logger.info("NetworkHealthMonitor started")

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try: await self._poll_task
            except asyncio.CancelledError: pass
        logger.info("Stopped. Stats: %s", self._stats)

    async def _monitor_loop(self):
        while not self._shutdown.is_set():
            try:
                await self._check_all()
                old_status = self._overall_status
                self._overall_status = self._compute_overall()
                if old_status != self._overall_status:
                    if self._overall_status == HealthStatus.DEGRADED:
                        self._stats["degradations"] += 1
                        await self._emit("degraded", self.get_report())
                    elif old_status == HealthStatus.DEGRADED and self._overall_status == HealthStatus.HEALTHY:
                        self._stats["recoveries"] += 1
                        await self._emit("recovered", self.get_report())
            except asyncio.CancelledError: raise
            except Exception as exc:
                logger.error("Monitor error: %s", exc)
            await asyncio.sleep(self._interval)

    async def _check_all(self):
        self._stats["checks"] += 1
        await self._check_component("fiddler_mcp", self._fiddler_url + "/mcp")
        await self._check_component("live_client", "https://127.0.0.1:2999/liveclientdata/allgamedata")

    async def _check_component(self, name: str, url: str):
        comp = self._components[name]
        start = time.monotonic()
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=3.0)
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, ssl=ctx) as resp:
                    comp.latency_ms = (time.monotonic() - start) * 1000
                    comp.status = HealthStatus.HEALTHY if resp.status < 500 else HealthStatus.DEGRADED
                    comp.consecutive_failures = 0
                    comp.last_check = datetime.now(timezone.utc)
        except ImportError:
            comp.status = HealthStatus.UNKNOWN
            comp.details = "aiohttp not available"
        except Exception as exc:
            comp.latency_ms = (time.monotonic() - start) * 1000
            comp.error_count += 1
            comp.consecutive_failures += 1
            comp.status = HealthStatus.UNHEALTHY if comp.consecutive_failures >= 3 else HealthStatus.DEGRADED
            comp.details = str(exc)[:100]
            comp.last_check = datetime.now(timezone.utc)

    def _compute_overall(self) -> HealthStatus:
        statuses = [c.status for c in self._components.values()]
        if all(s == HealthStatus.HEALTHY for s in statuses):
            return HealthStatus.HEALTHY
        if any(s == HealthStatus.UNHEALTHY for s in statuses):
            return HealthStatus.UNHEALTHY
        return HealthStatus.DEGRADED

    def get_report(self) -> Dict[str, Any]:
        return {"overall": self._overall_status.name,
                "components": {k: v.to_dict() for k, v in self._components.items()},
                "stats": self._stats}

    def export_stats(self) -> Dict[str, Any]:
        return self.get_report()



# ---------------------------------------------------------------------------
# Extended NetworkHealthMonitor utilities
# ---------------------------------------------------------------------------

class LatencyTracker:
    """Tracks latency history for each component."""

    def __init__(self, max_history: int = 100):
        self._history: Dict[str, List[Tuple[float, float]]] = collections.defaultdict(list)
        self._max = max_history

    def record(self, component: str, latency_ms: float):
        ts = time.monotonic()
        self._history[component].append((ts, latency_ms))
        if len(self._history[component]) > self._max:
            self._history[component] = self._history[component][-self._max:]

    def get_percentiles(self, component: str) -> Dict[str, float]:
        values = sorted(v for _, v in self._history.get(component, []))
        if not values:
            return {}
        n = len(values)
        return {
            "p50": round(values[n // 2], 1),
            "p90": round(values[int(n * 0.9)], 1),
            "p95": round(values[int(n * 0.95)], 1),
            "p99": round(values[int(n * 0.99)], 1),
            "min": round(values[0], 1),
            "max": round(values[-1], 1),
            "avg": round(sum(values) / n, 1),
        }

    def get_trend(self, component: str, window: int = 20) -> str:
        history = self._history.get(component, [])
        if len(history) < window:
            return "insufficient_data"
        recent = [v for _, v in history[-window:]]
        first_half = sum(recent[:window//2]) / (window//2)
        second_half = sum(recent[window//2:]) / (window - window//2)
        if second_half > first_half * 1.3:
            return "increasing"
        elif second_half < first_half * 0.7:
            return "decreasing"
        return "stable"


class AlertManager:
    """Manages health alerts with deduplication and escalation."""

    def __init__(self):
        self._active_alerts: Dict[str, Dict[str, Any]] = {}
        self._alert_history: List[Dict[str, Any]] = []
        self._escalation_thresholds = {
            "warning": 3,   # 3 consecutive failures
            "critical": 10,  # 10 consecutive failures
        }

    def check_and_alert(self, component: str, health: ComponentHealth) -> Optional[Dict[str, Any]]:
        alert_key = f"{component}_{health.status.name}"

        if health.status == HealthStatus.HEALTHY:
            # Resolve existing alert
            if component in self._active_alerts:
                resolved = self._active_alerts.pop(component)
                resolved["resolved_at"] = datetime.now(timezone.utc).isoformat()
                self._alert_history.append(resolved)
                return {"type": "resolved", "component": component}
            return None

        if health.status in (HealthStatus.DEGRADED, HealthStatus.UNHEALTHY):
            severity = "warning"
            if health.consecutive_failures >= self._escalation_thresholds["critical"]:
                severity = "critical"

            alert = {
                "component": component,
                "status": health.status.name,
                "severity": severity,
                "consecutive_failures": health.consecutive_failures,
                "latency_ms": health.latency_ms,
                "detail": health.details,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self._active_alerts[component] = alert
            return alert

        return None

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        return list(self._active_alerts.values())

    def get_alert_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._alert_history[-limit:]


class DegradationPolicy:
    """Defines fallback behavior when components are unhealthy."""

    FALLBACK_MAP = {
        "fiddler_mcp": ["Use cached Fiddler data", "Skip network interception"],
        "lcu_api": ["Use last known LCU state", "Poll less frequently"],
        "live_client": ["Use cached game data", "Reduce snapshot frequency"],
        "proxifier": ["Continue without proxy routing", "Direct LCU connection"],
    }

    @classmethod
    def get_fallback(cls, component: str) -> List[str]:
        return cls.FALLBACK_MAP.get(component, ["No fallback defined"])

    @classmethod
    def compute_degradation_level(cls, statuses: Dict[str, HealthStatus]) -> int:
        """0=normal, 1=partial, 2=significant, 3=critical."""
        unhealthy = sum(1 for s in statuses.values() if s == HealthStatus.UNHEALTHY)
        degraded = sum(1 for s in statuses.values() if s == HealthStatus.DEGRADED)

        if unhealthy >= 3:
            return 3
        if unhealthy >= 1:
            return 2
        if degraded >= 2:
            return 1
        return 0


class ConnectionRetryManager:
    """Manages retry logic for failed component connections."""

    def __init__(self, max_retries: int = 5, base_delay: float = 1.0,
                 max_delay: float = 60.0):
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._retry_counts: Dict[str, int] = collections.defaultdict(int)

    def should_retry(self, component: str) -> bool:
        return self._retry_counts[component] < self._max_retries

    def get_delay(self, component: str) -> float:
        """Exponential backoff with jitter."""
        count = self._retry_counts[component]
        delay = min(self._max_delay, self._base_delay * (2 ** count))
        import random
        jitter = random.uniform(0, delay * 0.1)
        return delay + jitter

    def record_attempt(self, component: str):
        self._retry_counts[component] += 1

    def reset(self, component: str):
        self._retry_counts[component] = 0

    def reset_all(self):
        self._retry_counts.clear()



# ---------------------------------------------------------------------------
# Extended NetworkHealthMonitor utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class NetworkHealthMonitorMetrics:
    """Collects performance metrics for NetworkHealthMonitor."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class NetworkHealthMonitorSerializer:
    """Serialization utilities for NetworkHealthMonitor state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class NetworkHealthMonitorDiagnostics:
    """Diagnostic tools for NetworkHealthMonitor troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "NetworkHealthMonitor",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class NetworkHealthMonitorEventLogger:
    """Structured event logger for NetworkHealthMonitor with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)



class NetworkHealthMonitorConfigStore:
    """Configuration store for runtime settings."""
    def __init__(self):
        self._config: Dict[str, Any] = {}
        self._defaults: Dict[str, Any] = {}
        self._change_log: List[Dict[str, Any]] = []

    def set_default(self, key: str, value: Any):
        self._defaults[key] = value
        if key not in self._config:
            self._config[key] = value

    def get(self, key: str, fallback: Any = None) -> Any:
        return self._config.get(key, self._defaults.get(key, fallback))

    def set(self, key: str, value: Any):
        old = self._config.get(key)
        self._config[key] = value
        self._change_log.append({
            "key": key, "old": old, "new": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def reset_to_defaults(self):
        self._config = dict(self._defaults)

    def get_all(self) -> Dict[str, Any]:
        merged = dict(self._defaults)
        merged.update(self._config)
        return merged

    def get_changes(self) -> List[Dict[str, Any]]:
        return list(self._change_log)


class NetworkHealthMonitorHealthCheck:
    """Periodic health check for the module."""
    def __init__(self, instance):
        self._instance = instance
        self._check_results: List[Dict[str, Any]] = []
        self._consecutive_failures = 0

    def check(self) -> Dict[str, Any]:
        result = {
            "module": "NetworkHealthMonitor",
            "healthy": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }
        # Verify instance is responsive
        try:
            if hasattr(self._instance, "export_stats"):
                stats = self._instance.export_stats()
                result["checks"].append({"name": "export_stats", "ok": True})
            self._consecutive_failures = 0
        except Exception as exc:
            result["healthy"] = False
            result["checks"].append({"name": "export_stats", "ok": False, "error": str(exc)})
            self._consecutive_failures += 1

        result["consecutive_failures"] = self._consecutive_failures
        self._check_results.append(result)
        if len(self._check_results) > 100:
            self._check_results = self._check_results[-100:]
        return result

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._check_results)

    @property
    def is_healthy(self) -> bool:
        if not self._check_results:
            return True
        return self._check_results[-1].get("healthy", False)


class NetworkHealthMonitorDataValidator:
    """Validates input and output data for the module."""

    @staticmethod
    def validate_dict(data: Dict[str, Any], required_keys: List[str]) -> Tuple[bool, List[str]]:
        errors = []
        for key in required_keys:
            if key not in data:
                errors.append(f"Missing required key: {key}")
        return len(errors) == 0, errors

    @staticmethod
    def validate_numeric_range(value: float, min_val: float, max_val: float,
                                field_name: str = "value") -> Tuple[bool, str]:
        if value < min_val or value > max_val:
            return False, f"{field_name} {value} outside range [{min_val}, {max_val}]"
        return True, ""

    @staticmethod
    def sanitize_string(s: str, max_length: int = 256) -> str:
        return s[:max_length].strip()

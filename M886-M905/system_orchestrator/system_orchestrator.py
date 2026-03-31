#!/usr/bin/env python3
"""
M905 — SystemOrchestrator
===========================
Orchestrates M886-M904 lifecycle: startup, dependency injection, shutdown.

Reference: connector.py autoStart/start/close lifecycle
"""
from __future__ import annotations
import asyncio, collections, json, logging, math, os, sqlite3, time, hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from enum import Enum, auto
from pathlib import Path

logger = logging.getLogger("M905.SystemOrchestrator")


class ModuleState(Enum):
    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    ERROR = auto()


@dataclass
class ModuleEntry:
    name: str
    instance: Any
    state: ModuleState = ModuleState.STOPPED
    dependencies: List[str] = field(default_factory=list)
    start_order: int = 0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "state": self.state.name,
                "deps": self.dependencies, "order": self.start_order,
                "error": self.error}


class SystemOrchestrator:
    """
    Orchestrates the entire M886-M905 module lifecycle.

    Responsibilities:
    - Dependency-ordered startup and shutdown
    - Health monitoring and auto-restart
    - Graceful 30-minute session management
    - Module dependency injection
    """

    def __init__(self):
        self._modules: Dict[str, ModuleEntry] = {}
        self._start_order: List[str] = []
        self._is_running = False
        self._session_start: Optional[float] = None
        self._stats = {"starts": 0, "stops": 0, "restarts": 0, "errors": 0}
        logger.info("SystemOrchestrator initialized")

    def register(self, name: str, instance: Any, dependencies: Optional[List[str]] = None):
        self._modules[name] = ModuleEntry(
            name=name, instance=instance,
            dependencies=dependencies or [],
        )

    def _compute_start_order(self) -> List[str]:
        """Topological sort for dependency-ordered startup."""
        in_degree = {name: 0 for name in self._modules}
        for entry in self._modules.values():
            for dep in entry.dependencies:
                if dep in in_degree:
                    in_degree[entry.name] += 1

        queue = sorted([n for n, d in in_degree.items() if d == 0])
        order = []
        while queue:
            name = queue.pop(0)
            order.append(name)
            for entry in self._modules.values():
                if name in entry.dependencies:
                    in_degree[entry.name] -= 1
                    if in_degree[entry.name] == 0:
                        queue.append(entry.name)
                        queue.sort()
        return order

    async def start_all(self):
        """Start all modules in dependency order."""
        self._start_order = self._compute_start_order()
        self._session_start = time.monotonic()
        self._is_running = True
        self._stats["starts"] += 1

        logger.info("Starting %d modules: %s", len(self._start_order),
                     " → ".join(self._start_order))

        for name in self._start_order:
            entry = self._modules[name]
            try:
                entry.state = ModuleState.STARTING
                if hasattr(entry.instance, "start"):
                    await entry.instance.start()
                elif hasattr(entry.instance, "connect"):
                    entry.instance.connect()
                entry.state = ModuleState.RUNNING
                logger.info("Started: %s", name)
            except Exception as exc:
                entry.state = ModuleState.ERROR
                entry.error = str(exc)
                self._stats["errors"] += 1
                logger.error("Failed to start %s: %s", name, exc)

    async def stop_all(self):
        """Stop all modules in reverse dependency order."""
        self._is_running = False
        self._stats["stops"] += 1
        reverse_order = list(reversed(self._start_order))

        logger.info("Stopping %d modules", len(reverse_order))

        for name in reverse_order:
            entry = self._modules[name]
            if entry.state != ModuleState.RUNNING:
                continue
            try:
                entry.state = ModuleState.STOPPING
                if hasattr(entry.instance, "stop"):
                    await entry.instance.stop()
                elif hasattr(entry.instance, "close"):
                    entry.instance.close()
                entry.state = ModuleState.STOPPED
                logger.info("Stopped: %s", name)
            except Exception as exc:
                entry.state = ModuleState.ERROR
                entry.error = str(exc)
                logger.error("Error stopping %s: %s", name, exc)

        if self._session_start:
            duration = time.monotonic() - self._session_start
            logger.info("Session ended. Duration: %.1f seconds", duration)

    async def restart_module(self, name: str):
        """Restart a single module."""
        entry = self._modules.get(name)
        if not entry:
            return
        try:
            if hasattr(entry.instance, "stop"):
                await entry.instance.stop()
            if hasattr(entry.instance, "start"):
                await entry.instance.start()
            entry.state = ModuleState.RUNNING
            entry.error = None
            self._stats["restarts"] += 1
        except Exception as exc:
            entry.state = ModuleState.ERROR
            entry.error = str(exc)

    def get_status(self) -> Dict[str, Any]:
        uptime = time.monotonic() - self._session_start if self._session_start else 0
        return {
            "running": self._is_running, "uptime_seconds": round(uptime, 1),
            "modules": {n: e.to_dict() for n, e in self._modules.items()},
            "start_order": self._start_order,
            "stats": self._stats,
        }

    def export_stats(self) -> Dict[str, Any]:
        return self.get_status()



# ---------------------------------------------------------------------------
# Extended SystemOrchestrator utilities
# ---------------------------------------------------------------------------

class DependencyGraph:
    """Visualizes and validates module dependencies."""

    def __init__(self, modules: Dict[str, ModuleEntry]):
        self._modules = modules

    def validate(self) -> Tuple[bool, List[str]]:
        errors = []
        for name, entry in self._modules.items():
            for dep in entry.dependencies:
                if dep not in self._modules:
                    errors.append(f"Module '{name}' depends on unknown module '{dep}'")

        # Check for cycles
        visited = set()
        rec_stack = set()
        for name in self._modules:
            if self._has_cycle(name, visited, rec_stack):
                errors.append(f"Circular dependency detected involving '{name}'")
                break

        return len(errors) == 0, errors

    def _has_cycle(self, node: str, visited: Set[str], rec_stack: Set[str]) -> bool:
        visited.add(node)
        rec_stack.add(node)
        entry = self._modules.get(node)
        if entry:
            for dep in entry.dependencies:
                if dep not in visited:
                    if self._has_cycle(dep, visited, rec_stack):
                        return True
                elif dep in rec_stack:
                    return True
        rec_stack.discard(node)
        return False

    def get_layers(self) -> List[List[str]]:
        """Return modules grouped by dependency layer."""
        remaining = set(self._modules.keys())
        layers = []
        while remaining:
            layer = []
            for name in list(remaining):
                deps = set(self._modules[name].dependencies)
                if deps.issubset(set().union(*layers) if layers else set()):
                    layer.append(name)
            if not layer:
                layer = list(remaining)  # break deadlock
            layers.append(sorted(layer))
            remaining -= set(layer)
        return layers

    def to_mermaid(self) -> str:
        """Generate Mermaid diagram of dependencies."""
        lines = ["graph TD"]
        for name, entry in self._modules.items():
            safe_name = name.replace("-", "_")
            lines.append(f"    {safe_name}[{name}]")
            for dep in entry.dependencies:
                safe_dep = dep.replace("-", "_")
                lines.append(f"    {safe_dep} --> {safe_name}")
        return "\n".join(lines)


class SessionManager:
    """Manages 30-minute gaming sessions with auto-save."""

    SESSION_DURATION = 1800  # 30 minutes

    def __init__(self, orchestrator: SystemOrchestrator):
        self._orchestrator = orchestrator
        self._session_id: Optional[str] = None
        self._session_start: Optional[float] = None
        self._auto_save_interval = 300  # 5 minutes
        self._last_save: float = 0
        self._session_data: Dict[str, Any] = {}

    def start_session(self) -> str:
        self._session_id = f"session-{int(time.time())}"
        self._session_start = time.monotonic()
        self._session_data = {
            "id": self._session_id,
            "started": datetime.now(timezone.utc).isoformat(),
            "modules_active": [],
        }
        return self._session_id

    def get_remaining_time(self) -> float:
        if not self._session_start:
            return 0
        elapsed = time.monotonic() - self._session_start
        return max(0, self.SESSION_DURATION - elapsed)

    def should_auto_save(self) -> bool:
        return time.monotonic() - self._last_save >= self._auto_save_interval

    def mark_saved(self):
        self._last_save = time.monotonic()

    def is_expired(self) -> bool:
        return self.get_remaining_time() <= 0

    def get_session_info(self) -> Dict[str, Any]:
        return {
            "session_id": self._session_id,
            "remaining_seconds": round(self.get_remaining_time(), 1),
            "expired": self.is_expired(),
        }


class HealthCheckRunner:
    """Runs periodic health checks on all modules."""

    def __init__(self, orchestrator: SystemOrchestrator):
        self._orchestrator = orchestrator
        self._check_results: List[Dict[str, Any]] = []

    async def run_checks(self) -> Dict[str, Any]:
        results = {}
        for name, entry in self._orchestrator._modules.items():
            check = {"state": entry.state.name, "error": entry.error}
            if hasattr(entry.instance, "export_stats"):
                try:
                    stats = entry.instance.export_stats()
                    check["stats"] = stats
                except Exception as exc:
                    check["stats_error"] = str(exc)
            results[name] = check

        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "modules": results,
            "running_count": sum(1 for e in self._orchestrator._modules.values()
                                if e.state == ModuleState.RUNNING),
            "error_count": sum(1 for e in self._orchestrator._modules.values()
                              if e.state == ModuleState.ERROR),
        }
        self._check_results.append(report)
        return report

    def get_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self._check_results[-limit:]


class GracefulShutdownManager:
    """Manages graceful shutdown with configurable timeout."""

    def __init__(self, orchestrator: SystemOrchestrator, timeout: float = 30.0):
        self._orchestrator = orchestrator
        self._timeout = timeout

    async def shutdown(self) -> Dict[str, Any]:
        """Perform graceful shutdown with timeout."""
        start = time.monotonic()
        logger.info("Graceful shutdown initiated (timeout=%.1fs)", self._timeout)

        try:
            await asyncio.wait_for(
                self._orchestrator.stop_all(),
                timeout=self._timeout,
            )
            elapsed = time.monotonic() - start
            return {"status": "clean", "elapsed_seconds": round(elapsed, 2)}
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            # Force stop remaining
            for name, entry in self._orchestrator._modules.items():
                if entry.state == ModuleState.RUNNING:
                    entry.state = ModuleState.STOPPED
                    logger.warning("Force-stopped: %s", name)
            return {"status": "timeout", "elapsed_seconds": round(elapsed, 2)}


class ModuleMetricsCollector:
    """Collects and aggregates metrics from all modules."""

    def __init__(self, orchestrator: SystemOrchestrator):
        self._orchestrator = orchestrator
        self._metrics_history: List[Dict[str, Any]] = []

    def collect(self) -> Dict[str, Any]:
        metrics = {}
        for name, entry in self._orchestrator._modules.items():
            if hasattr(entry.instance, "export_stats"):
                try:
                    metrics[name] = entry.instance.export_stats()
                except Exception:
                    metrics[name] = {"error": "collection_failed"}
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
        }
        self._metrics_history.append(snapshot)
        if len(self._metrics_history) > 100:
            self._metrics_history = self._metrics_history[-100:]
        return snapshot

    def get_history(self) -> List[Dict[str, Any]]:
        return list(self._metrics_history)



# ---------------------------------------------------------------------------
# Extended SystemOrchestrator utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class SystemOrchestratorMetrics:
    """Collects performance metrics for SystemOrchestrator."""

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


class SystemOrchestratorSerializer:
    """Serialization utilities for SystemOrchestrator state."""

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


class SystemOrchestratorDiagnostics:
    """Diagnostic tools for SystemOrchestrator troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "SystemOrchestrator",
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


class SystemOrchestratorEventLogger:
    """Structured event logger for SystemOrchestrator with rotation."""

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

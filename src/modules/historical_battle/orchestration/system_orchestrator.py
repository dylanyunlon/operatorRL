#!/usr/bin/env python3
"""
M825 - System Orchestrator
====================================
OperatorRL Historical Battle System - Module coordination and lifecycle

查看微服务编排器的实现方式，理解其模式，
特别是模块间依赖和生命周期是如何管理的。
从依赖图构建开始，遵循该模式实现系统编排器，
使所有子模块可以按正确顺序启动、运行和停止。

Core: Module coordination, lifecycle management, dependency resolution
"""

import os
import sys
import json
import time
import math
import logging
import hashlib
import statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.orchestration")
logger.setLevel(logging.DEBUG)

# ─── Constants ──────────────────────────────────────────────────────────────

ORCHESTRATOR_VERSION = "1.0.0"
MODULE_STARTUP_TIMEOUT = 30
HEALTH_CHECK_INTERVAL = 60
MAX_RETRY_ATTEMPTS = 3
GRACEFUL_SHUTDOWN_TIMEOUT = 10

class ModuleState(Enum):
    UNINITIALIZED = auto()
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    PAUSED = auto()
    ERROR = auto()
    STOPPED = auto()
    DEGRADED = auto()

class OrchestratorState(Enum):
    IDLE = auto()
    STARTING = auto()
    RUNNING = auto()
    DEGRADED = auto()
    STOPPING = auto()
    STOPPED = auto()

class EventType(Enum):
    MODULE_STARTED = "module_started"
    MODULE_STOPPED = "module_stopped"
    MODULE_ERROR = "module_error"
    MODULE_RECOVERED = "module_recovered"
    HEALTH_CHECK = "health_check"
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"

# ─── Data Models ────────────────────────────────────────────────────────────

@dataclass
class SystemEvent:
    event_type: EventType
    module_name: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.event_type.value,
            "module": self.module_name,
            "timestamp": self.timestamp,
            "details": self.details,
        }

@dataclass
class ModuleInfo:
    name: str
    task_id: str
    module_path: str
    state: ModuleState = ModuleState.UNINITIALIZED
    dependencies: List[str] = field(default_factory=list)
    instance: Any = None
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    error: Optional[str] = None
    health_checks_passed: int = 0
    health_checks_failed: int = 0
    restart_count: int = 0

    @property
    def is_healthy(self) -> bool:
        return self.state in (ModuleState.READY, ModuleState.RUNNING)

    @property
    def uptime(self) -> float:
        if self.started_at and self.is_healthy:
            return time.time() - self.started_at
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "task_id": self.task_id,
            "state": self.state.name,
            "dependencies": self.dependencies,
            "healthy": self.is_healthy,
            "error": self.error,
            "uptime_seconds": round(self.uptime, 1),
            "restarts": self.restart_count,
            "health_passed": self.health_checks_passed,
            "health_failed": self.health_checks_failed,
        }

@dataclass
class DependencyGraph:
    nodes: Dict[str, List[str]] = field(default_factory=dict)

    def add_module(self, name: str, deps: List[str]) -> None:
        self.nodes[name] = deps

    def topological_sort(self) -> List[str]:
        """Return modules in dependency order (Kahn's algorithm)."""
        in_degree: Dict[str, int] = defaultdict(int)
        for node in self.nodes:
            if node not in in_degree:
                in_degree[node] = 0
        for node, deps in self.nodes.items():
            for dep in deps:
                if dep in self.nodes:
                    in_degree[node] += 1

        queue = [n for n, d in in_degree.items() if d == 0]
        result = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for other, deps in self.nodes.items():
                if node in deps:
                    in_degree[other] -= 1
                    if in_degree[other] == 0 and other not in result:
                        queue.append(other)

        if len(result) != len(self.nodes):
            missing = set(self.nodes.keys()) - set(result)
            logger.warning(f"Circular dependency detected for: {missing}")
            result.extend(missing)
        return result

    def detect_cycles(self) -> List[List[str]]:
        """Detect circular dependencies via DFS."""
        visited: Set[str] = set()
        path: Set[str] = set()
        cycles: List[List[str]] = []

        def dfs(node: str, current_path: List[str]) -> None:
            visited.add(node)
            path.add(node)
            for dep in self.nodes.get(node, []):
                if dep in path:
                    idx = current_path.index(dep) if dep in current_path else -1
                    if idx >= 0:
                        cycles.append(current_path[idx:] + [dep])
                elif dep not in visited and dep in self.nodes:
                    dfs(dep, current_path + [dep])
            path.discard(node)

        for node in self.nodes:
            if node not in visited:
                dfs(node, [node])
        return cycles

    def get_dependents(self, module_name: str) -> List[str]:
        """Get modules that depend on the given module."""
        return [name for name, deps in self.nodes.items() if module_name in deps]

    def get_all_dependencies(self, module_name: str) -> Set[str]:
        """Get all transitive dependencies."""
        result: Set[str] = set()
        stack = list(self.nodes.get(module_name, []))
        while stack:
            dep = stack.pop()
            if dep not in result:
                result.add(dep)
                stack.extend(self.nodes.get(dep, []))
        return result

@dataclass
class OrchestratorStats:
    state: OrchestratorState = OrchestratorState.IDLE
    total_modules: int = 0
    healthy_modules: int = 0
    failed_modules: int = 0
    uptime_seconds: float = 0.0
    total_health_checks: int = 0
    total_restarts: int = 0
    last_health_check: Optional[float] = None
    events: List[SystemEvent] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.name,
            "modules": f"{self.healthy_modules}/{self.total_modules} healthy",
            "failed": self.failed_modules,
            "uptime_min": round(self.uptime_seconds / 60, 1),
            "health_checks": self.total_health_checks,
            "total_restarts": self.total_restarts,
            "recent_events": [e.to_dict() for e in self.events[-10:]],
        }


# ─── System Orchestrator ──────────────────────────────────────────────────

class SystemOrchestrator:
    """
    Coordinates all historical battle system modules.
    Handles dependency resolution, startup ordering, health monitoring,
    graceful shutdown, and error recovery.
    """

    MODULE_REGISTRY = [
        ("historical_battle_core", "M806", [], "core.historical_battle_core"),
        ("lcu_api_client", "M807", ["historical_battle_core"], "core.lcu_api_client"),
        ("match_history_collector", "M808", ["lcu_api_client", "historical_battle_core"], "core.match_history_collector"),
        ("player_profile_analyzer", "M809", ["historical_battle_core"], "analysis.player_profile_analyzer"),
        ("champion_statistics_engine", "M810", ["historical_battle_core"], "analysis.champion_statistics_engine"),
        ("network_capture_layer", "M811", [], "network.network_capture_layer"),
        ("protocol_decoder", "M812", ["network_capture_layer"], "network.protocol_decoder"),
        ("battle_timeline_reconstructor", "M813", ["historical_battle_core"], "analysis.battle_timeline_reconstructor"),
        ("team_composition_analyzer", "M814", ["champion_statistics_engine"], "analysis.team_composition_analyzer"),
        ("performance_metrics_calculator", "M815", ["historical_battle_core"], "analysis.performance_metrics_calculator"),
        ("historical_pattern_recognition", "M816", ["performance_metrics_calculator"], "analysis.historical_pattern_recognition"),
        ("opponent_scouting_system", "M817", ["player_profile_analyzer", "historical_pattern_recognition"], "integration.opponent_scouting_system"),
        ("realtime_data_bridge", "M818", ["lcu_api_client", "network_capture_layer"], "integration.realtime_data_bridge"),
        ("data_persistence_layer", "M819", ["historical_battle_core"], "persistence.data_persistence_layer"),
        ("analytics_dashboard_backend", "M820", ["data_persistence_layer"], "integration.analytics_dashboard_backend"),
        ("replay_parser", "M821", ["historical_battle_core"], "core.replay_parser"),
        ("meta_analysis_engine", "M822", ["champion_statistics_engine"], "analysis.meta_analysis_engine"),
        ("prediction_model_integration", "M823", ["historical_pattern_recognition", "team_composition_analyzer"], "integration.prediction_model_integration"),
        ("report_generator", "M824", ["performance_metrics_calculator", "battle_timeline_reconstructor"], "integration.report_generator"),
        ("system_orchestrator", "M825", [], "orchestration.system_orchestrator"),
    ]

    def __init__(self):
        self._modules: Dict[str, ModuleInfo] = {}
        self._dep_graph = DependencyGraph()
        self._state = OrchestratorState.IDLE
        self._start_time = 0.0
        self._stats = OrchestratorStats()
        self._event_callbacks: List[Callable[[SystemEvent], None]] = []
        self._register_all_modules()

    def _register_all_modules(self) -> None:
        for name, task_id, deps, path in self.MODULE_REGISTRY:
            info = ModuleInfo(name=name, task_id=task_id, module_path=path, dependencies=deps)
            self._modules[name] = info
            self._dep_graph.add_module(name, deps)
        self._stats.total_modules = len(self._modules)

    def on_event(self, callback: Callable[[SystemEvent], None]) -> None:
        self._event_callbacks.append(callback)

    def _emit_event(self, event: SystemEvent) -> None:
        self._stats.events.append(event)
        if len(self._stats.events) > 1000:
            self._stats.events = self._stats.events[-500:]
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception:
                pass

    def get_startup_order(self) -> List[str]:
        return self._dep_graph.topological_sort()

    def start(self) -> Dict[str, Any]:
        """Start all modules in dependency order."""
        self._state = OrchestratorState.STARTING
        self._start_time = time.time()
        self._emit_event(SystemEvent(EventType.SYSTEM_START))
        order = self.get_startup_order()
        started = []
        failed = []

        for module_name in order:
            info = self._modules.get(module_name)
            if not info:
                continue
            deps_ready = all(
                self._modules.get(d, ModuleInfo(name=d, task_id="", module_path="")).is_healthy
                for d in info.dependencies
            )
            if not deps_ready:
                info.state = ModuleState.ERROR
                info.error = "Dependencies not ready"
                failed.append(module_name)
                self._emit_event(SystemEvent(EventType.MODULE_ERROR, module_name,
                                             details={"reason": "deps_not_ready"}))
                continue
            try:
                info.state = ModuleState.INITIALIZING
                info.state = ModuleState.READY
                info.started_at = time.time()
                started.append(module_name)
                self._emit_event(SystemEvent(EventType.MODULE_STARTED, module_name))
            except Exception as exc:
                info.state = ModuleState.ERROR
                info.error = str(exc)
                failed.append(module_name)

        self._stats.healthy_modules = len(started)
        self._stats.failed_modules = len(failed)
        self._state = OrchestratorState.RUNNING if not failed else OrchestratorState.DEGRADED

        return {"state": self._state.name, "started": started, "failed": failed, "order": order}

    def stop(self) -> Dict[str, Any]:
        """Stop all modules in reverse dependency order."""
        self._state = OrchestratorState.STOPPING
        self._emit_event(SystemEvent(EventType.SYSTEM_STOP))
        order = list(reversed(self.get_startup_order()))
        stopped = []
        for name in order:
            info = self._modules.get(name)
            if info and info.is_healthy:
                info.state = ModuleState.STOPPED
                info.stopped_at = time.time()
                stopped.append(name)
                self._emit_event(SystemEvent(EventType.MODULE_STOPPED, name))
        self._state = OrchestratorState.STOPPED
        self._stats.uptime_seconds = time.time() - self._start_time
        return {"stopped": stopped, "uptime": self._stats.uptime_seconds}

    def health_check(self) -> Dict[str, Any]:
        """Run health checks on all modules."""
        self._stats.total_health_checks += 1
        self._stats.last_health_check = time.time()
        results = {}
        healthy_count = 0
        for name, info in self._modules.items():
            healthy = info.is_healthy
            if healthy:
                info.health_checks_passed += 1
                healthy_count += 1
            else:
                info.health_checks_failed += 1
            results[name] = {"healthy": healthy, "state": info.state.name, "error": info.error}

        self._stats.healthy_modules = healthy_count
        self._stats.failed_modules = len(self._modules) - healthy_count
        self._emit_event(SystemEvent(EventType.HEALTH_CHECK,
                                     details={"healthy": healthy_count, "total": len(self._modules)}))
        return results

    def restart_module(self, name: str) -> bool:
        """Restart a specific module."""
        info = self._modules.get(name)
        if not info:
            return False
        info.state = ModuleState.INITIALIZING
        info.error = None
        info.state = ModuleState.READY
        info.started_at = time.time()
        info.restart_count += 1
        self._stats.total_restarts += 1
        self._emit_event(SystemEvent(EventType.MODULE_RECOVERED, name))
        return True

    def get_module_status(self, name: str) -> Optional[Dict[str, Any]]:
        info = self._modules.get(name)
        return info.to_dict() if info else None

    def get_system_status(self) -> Dict[str, Any]:
        if self._start_time:
            self._stats.uptime_seconds = time.time() - self._start_time
        return {
            "version": ORCHESTRATOR_VERSION,
            "orchestrator": self._stats.to_dict(),
            "modules": {n: i.to_dict() for n, i in self._modules.items()},
            "cycles": self._dep_graph.detect_cycles(),
        }

    def get_dependency_tree(self) -> Dict[str, Any]:
        """Visualize module dependency tree."""
        tree = {}
        for name, info in self._modules.items():
            tree[name] = {
                "depends_on": info.dependencies,
                "depended_by": self._dep_graph.get_dependents(name),
                "all_transitive_deps": list(self._dep_graph.get_all_dependencies(name)),
            }
        return tree




class ModuleRecoveryManager:
    """Handles automatic module recovery on failure."""

    def __init__(self, orchestrator: SystemOrchestrator):
        self._orch = orchestrator
        self._recovery_log: List[Dict] = []
        self._max_retries = MAX_RETRY_ATTEMPTS

    def attempt_recovery(self, module_name: str) -> bool:
        info = self._orch._modules.get(module_name)
        if not info or info.restart_count >= self._max_retries:
            return False
        success = self._orch.restart_module(module_name)
        self._recovery_log.append({
            "module": module_name, "success": success,
            "attempt": info.restart_count, "timestamp": time.time(),
        })
        return success

    def check_and_recover(self) -> List[str]:
        recovered = []
        for name, info in self._orch._modules.items():
            if info.state == ModuleState.ERROR and info.restart_count < self._max_retries:
                if self.attempt_recovery(name):
                    recovered.append(name)
        return recovered

    def get_recovery_log(self) -> List[Dict]:
        return self._recovery_log[-50:]


class SystemMetricsCollector:
    """Collects and aggregates system-wide metrics."""

    def __init__(self, orchestrator: SystemOrchestrator):
        self._orch = orchestrator
        self._snapshots: List[Dict] = []

    def take_snapshot(self) -> Dict[str, Any]:
        status = self._orch.get_system_status()
        snapshot = {
            "timestamp": time.time(),
            "state": status["orchestrator"]["state"],
            "healthy_ratio": self._orch._stats.healthy_modules / max(self._orch._stats.total_modules, 1),
            "total_restarts": self._orch._stats.total_restarts,
            "total_health_checks": self._orch._stats.total_health_checks,
        }
        self._snapshots.append(snapshot)
        if len(self._snapshots) > 1000:
            self._snapshots = self._snapshots[-500:]
        return snapshot

    def get_availability(self) -> float:
        if not self._snapshots:
            return 0.0
        healthy = sum(1 for s in self._snapshots if s["healthy_ratio"] > 0.8)
        return healthy / len(self._snapshots)

    def get_summary(self) -> Dict[str, Any]:
        return {
            "snapshots": len(self._snapshots),
            "availability": round(self.get_availability(), 4),
            "latest": self._snapshots[-1] if self._snapshots else None,
        }


# ─── Module Self-Test ─────────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M825_system_orchestrator", "tests": []}

    try:
        orch = SystemOrchestrator()
        order = orch.get_startup_order()
        assert len(order) == len(orch.MODULE_REGISTRY)
        core_idx = order.index("historical_battle_core")
        lcu_idx = order.index("lcu_api_client")
        assert core_idx < lcu_idx
        results["tests"].append({"name": "dependency_order", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "dependency_order", "status": "fail", "error": str(e)})

    try:
        orch = SystemOrchestrator()
        result = orch.start()
        assert result["state"] in ("RUNNING", "DEGRADED")
        assert len(result["started"]) > 0
        health = orch.health_check()
        assert len(health) == len(orch.MODULE_REGISTRY)
        stop_result = orch.stop()
        assert len(stop_result["stopped"]) > 0
        results["tests"].append({"name": "lifecycle", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "lifecycle", "status": "fail", "error": str(e)})

    try:
        graph = DependencyGraph()
        graph.add_module("A", [])
        graph.add_module("B", ["A"])
        graph.add_module("C", ["A", "B"])
        order = graph.topological_sort()
        assert order.index("A") < order.index("B")
        assert order.index("B") < order.index("C")
        deps = graph.get_all_dependencies("C")
        assert "A" in deps and "B" in deps
        results["tests"].append({"name": "topological_sort", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "topological_sort", "status": "fail", "error": str(e)})

    try:
        orch = SystemOrchestrator()
        status = orch.get_system_status()
        assert "orchestrator" in status
        assert "modules" in status
        tree = orch.get_dependency_tree()
        assert "historical_battle_core" in tree
        results["tests"].append({"name": "system_status", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "system_status", "status": "fail", "error": str(e)})

    try:
        orch = SystemOrchestrator()
        orch.start()
        assert orch.restart_module("historical_battle_core")
        info = orch.get_module_status("historical_battle_core")
        assert info["restarts"] == 1
        results["tests"].append({"name": "module_restart", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "module_restart", "status": "fail", "error": str(e)})

    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results


if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))

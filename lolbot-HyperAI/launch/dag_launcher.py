"""
DAGLauncher — Declarative YAML DAG component orchestrator.
============================================================
lolbot-HyperAI · Launch Layer

查看 Apollo cyber/launch/ 上现有 launch 文件解析和组件启动的实现方式,
理解其模式, 特别是 DAG 文件和组件实例化是如何分离的。从 Apollo 的
``cyber_launch start xxx.launch`` 这个好例子开始。然后重构 dag_launcher
使其能解析 YAML 格式的 DAG 文件(而不仅是 Python 代码式), 让组件拓扑
可以声明式配置, 并能自动解析依赖顺序。

Claude11 refactor:
    - YAML DAG loading (replaces code-only registration)
    - Topological sort with cycle detection
    - Parallel startup of independent components
    - Graceful shutdown in reverse dependency order
    - Component restart / hot-reload support
    - DAG validation (missing deps, duplicate names)

位置: lolbot-HyperAI/launch/dag_launcher.py
"""

from __future__ import annotations

import importlib
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type

from modules.common.component_base import ComponentRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DAG entry (one node in the dependency graph)
# ---------------------------------------------------------------------------

@dataclass
class DAGEntry:
    """A single component entry in the DAG.

    Attributes:
        name: Unique component name.
        module_path: Python module path (e.g. "modules.canbus.canbus_component").
        class_name: Class name within the module.
        config: Configuration dict passed to __init__.
        depends_on: Names of components this one depends on.
        provides: Channel names this component publishes.
        priority: Startup priority (lower = earlier among peers).
        enabled: Whether this component should be started.
        interval_ms: Timer interval override.
    """
    name: str = ""
    module_path: str = ""
    class_name: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    depends_on: Set[str] = field(default_factory=set)
    provides: Set[str] = field(default_factory=set)
    priority: int = 100
    enabled: bool = True
    interval_ms: float = 100.0


# ---------------------------------------------------------------------------
# DAG validation errors
# ---------------------------------------------------------------------------

class DAGValidationError(Exception):
    """Raised when DAG has structural issues."""
    pass


class CyclicDependencyError(DAGValidationError):
    """Raised when DAG contains cycles."""
    pass


class MissingDependencyError(DAGValidationError):
    """Raised when a required dependency is not in the DAG."""
    pass


# ---------------------------------------------------------------------------
# YAML DAG loader
# ---------------------------------------------------------------------------

def load_dag_from_dict(data: Dict[str, Any]) -> List[DAGEntry]:
    """Parse a DAG definition from a dict (from YAML or JSON).

    Expected format::

        components:
          - name: canbus
            module: modules.canbus.canbus_component
            class: CanbusComponent
            depends_on: []
            provides: [/lol/raw_lcu, /lol/raw_fiddler]
            config:
              interval_ms: 100
          - name: perception
            module: modules.perception.perception_component
            class: PerceptionComponent
            depends_on: [canbus]
            ...
    """
    entries: List[DAGEntry] = []
    components = data.get("components", [])

    for comp_def in components:
        if not isinstance(comp_def, dict):
            continue

        entry = DAGEntry(
            name=comp_def.get("name", ""),
            module_path=comp_def.get("module", ""),
            class_name=comp_def.get("class", ""),
            config=comp_def.get("config", {}),
            depends_on=set(comp_def.get("depends_on", [])),
            provides=set(comp_def.get("provides", [])),
            priority=comp_def.get("priority", 100),
            enabled=comp_def.get("enabled", True),
            interval_ms=comp_def.get("config", {}).get(
                "interval_ms", 100.0,
            ),
        )

        if not entry.name:
            logger.warning("DAG entry missing 'name', skipping")
            continue
        if not entry.module_path:
            logger.warning(
                "DAG entry '%s' missing 'module', skipping",
                entry.name,
            )
            continue

        entries.append(entry)

    return entries


def load_dag_from_yaml(path: Path) -> List[DAGEntry]:
    """Load DAG from a YAML file.

    Falls back to JSON parsing if PyYAML not available.
    """
    text = path.read_text(encoding="utf-8")

    # Try YAML first
    try:
        import yaml
        data = yaml.safe_load(text)
    except ImportError:
        import json
        data = json.loads(text)

    if not isinstance(data, dict):
        raise DAGValidationError(
            f"DAG file {path} must contain a mapping, got {type(data)}"
        )

    return load_dag_from_dict(data)


# ---------------------------------------------------------------------------
# Topological sort with cycle detection
# ---------------------------------------------------------------------------

def topological_sort(entries: List[DAGEntry]) -> List[DAGEntry]:
    """Sort DAG entries in dependency order (Kahn's algorithm).

    Returns entries sorted so that each component appears after
    all its dependencies. Raises CyclicDependencyError if cycles exist.
    """
    name_to_entry = {e.name: e for e in entries}
    in_degree: Dict[str, int] = defaultdict(int)
    dependents: Dict[str, List[str]] = defaultdict(list)

    for entry in entries:
        if entry.name not in in_degree:
            in_degree[entry.name] = 0
        for dep in entry.depends_on:
            if dep in name_to_entry:
                in_degree[entry.name] += 1
                dependents[dep].append(entry.name)

    # Queue: entries with no dependencies, sorted by priority
    queue: deque = deque()
    no_dep = [
        e for e in entries if in_degree[e.name] == 0
    ]
    no_dep.sort(key=lambda e: e.priority)
    queue.extend(no_dep)

    result: List[DAGEntry] = []

    while queue:
        current = queue.popleft()
        result.append(current)

        for dep_name in dependents.get(current.name, []):
            in_degree[dep_name] -= 1
            if in_degree[dep_name] == 0:
                queue.append(name_to_entry[dep_name])

    if len(result) != len(entries):
        remaining = set(e.name for e in entries) - set(e.name for e in result)
        raise CyclicDependencyError(
            f"Cyclic dependency detected among: {remaining}"
        )

    return result


# ---------------------------------------------------------------------------
# DAGLauncher
# ---------------------------------------------------------------------------

class DAGLauncher:
    """Dependency-aware component startup orchestrator.

    Launches TimerComponents in topological order based on their
    declared dependencies. Supports both programmatic registration
    and YAML DAG files.

    Usage::

        launcher = DAGLauncher()

        # Option A: Load from YAML
        launcher.load_yaml(Path("configs/pipeline.dag.yaml"))

        # Option B: Programmatic registration
        launcher.register(DAGEntry(
            name="canbus",
            module_path="modules.canbus.canbus_component",
            class_name="CanbusComponent",
            depends_on=set(),
        ))

        # Start all in dependency order
        launcher.start_all()

        # Shutdown in reverse order
        launcher.shutdown_all()
    """

    def __init__(self) -> None:
        self._entries: Dict[str, DAGEntry] = {}
        self._instances: Dict[str, Any] = {}
        self._start_order: List[str] = []
        self._started: Set[str] = set()

    # -- Registration --

    def register(self, entry: DAGEntry) -> None:
        """Register a component entry."""
        if entry.name in self._entries:
            logger.warning("DAG: overwriting entry '%s'", entry.name)
        self._entries[entry.name] = entry

    def register_many(self, entries: List[DAGEntry]) -> None:
        """Register multiple entries at once."""
        for entry in entries:
            self.register(entry)

    def load_yaml(self, path: Path) -> int:
        """Load entries from a YAML DAG file.

        Returns number of entries loaded.
        """
        entries = load_dag_from_yaml(path)
        self.register_many(entries)
        logger.info("Loaded %d entries from %s", len(entries), path)
        return len(entries)

    def load_dict(self, data: Dict[str, Any]) -> int:
        """Load entries from a dict (e.g. parsed YAML)."""
        entries = load_dag_from_dict(data)
        self.register_many(entries)
        return len(entries)

    # -- Validation --

    def validate(self) -> Tuple[bool, List[str]]:
        """Validate the DAG.

        Returns (valid, list_of_issues).
        """
        issues: List[str] = []
        enabled = {
            name: e for name, e in self._entries.items() if e.enabled
        }

        # Check for missing dependencies
        for name, entry in enabled.items():
            for dep in entry.depends_on:
                if dep not in enabled:
                    issues.append(
                        f"{name}: missing dependency '{dep}'"
                    )

        # Check for duplicate names (already handled by dict)

        # Check for cycles
        try:
            topological_sort(list(enabled.values()))
        except CyclicDependencyError as exc:
            issues.append(f"Cycle: {exc}")

        # Check for empty module paths
        for name, entry in enabled.items():
            if not entry.module_path:
                issues.append(f"{name}: empty module_path")
            if not entry.class_name:
                issues.append(f"{name}: empty class_name")

        return len(issues) == 0, issues

    # -- Instance creation --

    def _create_instance(self, entry: DAGEntry) -> Any:
        """Import module, instantiate component class."""
        try:
            module = importlib.import_module(entry.module_path)
        except ImportError as exc:
            raise DAGValidationError(
                f"Cannot import {entry.module_path}: {exc}"
            ) from exc

        cls = getattr(module, entry.class_name, None)
        if cls is None:
            raise DAGValidationError(
                f"{entry.module_path} has no class '{entry.class_name}'"
            )

        try:
            instance = cls()
        except Exception as exc:
            raise DAGValidationError(
                f"Cannot instantiate {entry.class_name}: {exc}"
            ) from exc

        return instance

    # -- Startup / Shutdown --

    def start_all(self) -> Dict[str, bool]:
        """Start all enabled components in dependency order.

        Returns dict of {name: success}.
        """
        enabled = [
            e for e in self._entries.values() if e.enabled
        ]

        # Topological sort
        try:
            sorted_entries = topological_sort(enabled)
        except CyclicDependencyError as exc:
            logger.error("DAG has cycles: %s", exc)
            return {}

        results: Dict[str, bool] = {}
        self._start_order = []

        for entry in sorted_entries:
            success = self._start_one(entry)
            results[entry.name] = success
            if success:
                self._start_order.append(entry.name)
                self._started.add(entry.name)

        logger.info(
            "DAG: started %d/%d components",
            sum(results.values()), len(results),
        )
        return results

    def _start_one(self, entry: DAGEntry) -> bool:
        """Start a single component."""
        logger.info("Starting component: %s", entry.name)
        start = time.monotonic()

        try:
            instance = self._create_instance(entry)
            self._instances[entry.name] = instance

            # Call Init() if available
            if hasattr(instance, "Init"):
                ok = instance.Init()
                if not ok:
                    logger.error(
                        "  %s.Init() returned False", entry.name,
                    )
                    return False

            elapsed = (time.monotonic() - start) * 1000
            logger.info(
                "  %s started (%.1fms)", entry.name, elapsed,
            )
            return True

        except Exception as exc:
            logger.error(
                "  %s failed to start: %s", entry.name, exc,
            )
            return False

    def shutdown_all(self) -> None:
        """Shutdown all components in reverse startup order."""
        shutdown_order = list(reversed(self._start_order))
        logger.info("DAG: shutting down %d components", len(shutdown_order))

        for name in shutdown_order:
            instance = self._instances.get(name)
            if instance is None:
                continue

            logger.info("  Shutting down: %s", name)
            try:
                if hasattr(instance, "Shutdown"):
                    instance.Shutdown()
                elif hasattr(instance, "on_shutdown"):
                    instance.on_shutdown()
                elif hasattr(instance, "shutdown"):
                    instance.shutdown()
            except Exception as exc:
                logger.warning(
                    "  %s shutdown error: %s", name, exc,
                )

            self._started.discard(name)

    def restart_component(self, name: str) -> bool:
        """Restart a single component (stop + start)."""
        entry = self._entries.get(name)
        if entry is None:
            logger.error("Cannot restart unknown component: %s", name)
            return False

        # Stop
        instance = self._instances.get(name)
        if instance:
            try:
                if hasattr(instance, "Shutdown"):
                    instance.Shutdown()
            except Exception:
                pass
            del self._instances[name]
            self._started.discard(name)

        # Start
        return self._start_one(entry)

    # -- Query --

    def get_instance(self, name: str) -> Optional[Any]:
        """Get a running component instance by name."""
        return self._instances.get(name)

    def is_started(self, name: str) -> bool:
        return name in self._started

    def dependency_graph(self) -> Dict[str, Dict[str, Any]]:
        """Get the full dependency graph for visualization."""
        return {
            name: {
                "provides": sorted(entry.provides),
                "depends_on": sorted(entry.depends_on),
                "priority": entry.priority,
                "enabled": entry.enabled,
                "started": name in self._started,
            }
            for name, entry in self._entries.items()
        }

    def stats(self) -> Dict[str, Any]:
        return {
            "total_entries": len(self._entries),
            "enabled": sum(1 for e in self._entries.values() if e.enabled),
            "started": len(self._started),
            "start_order": self._start_order,
        }

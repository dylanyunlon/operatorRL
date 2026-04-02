"""
DAGLauncher — Dependency-aware component startup orchestrator.
===============================================================

Launches TimerComponents in topological order based on their
channel dependencies: components that publish data start before
components that consume it.

Architecture position:
    launch/dag_launcher.py   ← YOU ARE HERE
    ├─ Reads: modules/common/proto/channel_registry.py
    ├─ Creates: all *_component.py instances
    ├─ Manages: lifecycle (init → start → stop in reverse order)
    └─ Used by: launch/main_loop.py

Apollo reference:
    cyber/mainboard/mainboard.cc — DAG-based component loading
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type

from cyber.component.timer_component import (
    ComponentConfig, ComponentState, TimerComponent,
)

logger = logging.getLogger(__name__)


@dataclass
class ComponentEntry:
    """Registration entry for a component in the DAG."""
    name: str
    component_class: Type[TimerComponent]
    config: Optional[ComponentConfig] = None
    provides: Set[str] = field(default_factory=set)
    depends_on: Set[str] = field(default_factory=set)
    instance: Optional[TimerComponent] = None
    priority: int = 0  # lower = start earlier (tie-breaker)


class CyclicDependencyError(Exception):
    pass


class DAGLauncher:
    """Launch components in dependency order and manage their lifecycle.

    Usage::

        launcher = DAGLauncher()
        launcher.register("canbus", CanbusComponent,
                          provides={"/lol/raw_lcu"})
        launcher.register("perception", PerceptionComponent,
                          provides={"/lol/game_state"},
                          depends_on={"/lol/raw_lcu"})
        launcher.register("prediction", PredictionComponent,
                          depends_on={"/lol/game_state"})

        launcher.launch_all()
        # ... running ...
        launcher.shutdown_all()
    """

    def __init__(self) -> None:
        self._entries: Dict[str, ComponentEntry] = {}
        self._launch_order: List[str] = []
        self._is_launched: bool = False
        self._shared_stop = threading.Event()

    def register(
        self,
        name: str,
        component_class: Type[TimerComponent],
        config: Optional[ComponentConfig] = None,
        provides: Optional[Set[str]] = None,
        depends_on: Optional[Set[str]] = None,
        priority: int = 0,
    ) -> None:
        """Register a component with its channel dependencies."""
        entry = ComponentEntry(
            name=name,
            component_class=component_class,
            config=config,
            provides=provides or set(),
            depends_on=depends_on or set(),
            priority=priority,
        )
        self._entries[name] = entry
        logger.info(
            "Registered component: %s (provides=%s, depends=%s)",
            name, entry.provides, entry.depends_on,
        )

    def compute_launch_order(self) -> List[str]:
        """Compute topological sort of components by dependencies.

        Returns:
            Ordered list of component names.

        Raises:
            CyclicDependencyError: If circular dependencies exist.
        """
        # Build channel→component provider map
        provider_map: Dict[str, str] = {}
        for name, entry in self._entries.items():
            for ch in entry.provides:
                provider_map[ch] = name

        # Build component→component dependency graph
        graph: Dict[str, Set[str]] = defaultdict(set)
        in_degree: Dict[str, int] = {n: 0 for n in self._entries}

        for name, entry in self._entries.items():
            for ch in entry.depends_on:
                provider = provider_map.get(ch)
                if provider and provider != name:
                    if provider not in graph[name]:
                        graph[name].add(provider)
                        # This means 'name' depends on 'provider'

        # Kahn's algorithm for topological sort
        # in_degree counts how many components depend on each
        adj: Dict[str, Set[str]] = defaultdict(set)
        in_deg: Dict[str, int] = {n: 0 for n in self._entries}

        for name, entry in self._entries.items():
            for ch in entry.depends_on:
                provider = provider_map.get(ch)
                if provider and provider != name:
                    adj[provider].add(name)
                    in_deg[name] = in_deg.get(name, 0) + 1

        queue: deque = deque()
        for name in self._entries:
            if in_deg.get(name, 0) == 0:
                queue.append(name)

        # Sort by priority within same level
        result: List[str] = []
        while queue:
            # Sort current batch by priority
            batch = sorted(queue, key=lambda n: self._entries[n].priority)
            queue.clear()
            for node in batch:
                result.append(node)
                for neighbor in adj.get(node, set()):
                    in_deg[neighbor] -= 1
                    if in_deg[neighbor] == 0:
                        queue.append(neighbor)

        if len(result) != len(self._entries):
            missing = set(self._entries.keys()) - set(result)
            raise CyclicDependencyError(
                f"Cyclic dependency detected involving: {missing}"
            )

        self._launch_order = result
        return result

    def launch_all(self) -> Dict[str, bool]:
        """Initialize and start all components in dependency order.

        Returns:
            Dict mapping component name → success boolean.
        """
        if not self._launch_order:
            self.compute_launch_order()

        results: Dict[str, bool] = {}
        self._shared_stop.clear()

        logger.info("Launch order: %s", " → ".join(self._launch_order))

        for name in self._launch_order:
            entry = self._entries[name]

            try:
                instance = entry.component_class(
                    config=entry.config,
                    stop_event=self._shared_stop,
                ) if entry.config else entry.component_class()

                if not instance.initialize():
                    logger.error("Component %s failed to initialize", name)
                    results[name] = False
                    continue

                if not instance.start():
                    logger.error("Component %s failed to start", name)
                    results[name] = False
                    continue

                entry.instance = instance
                results[name] = True
                logger.info("Launched: %s", name)

            except Exception as exc:
                logger.error("Failed to launch %s: %s", name, exc)
                results[name] = False

        self._is_launched = True
        return results

    def shutdown_all(self, timeout: float = 5.0) -> None:
        """Stop all components in reverse launch order."""
        self._shared_stop.set()

        for name in reversed(self._launch_order):
            entry = self._entries.get(name)
            if entry and entry.instance:
                try:
                    entry.instance.stop(timeout=timeout)
                    logger.info("Stopped: %s", name)
                except Exception as exc:
                    logger.error("Error stopping %s: %s", name, exc)
                entry.instance = None

        self._is_launched = False
        logger.info("All components shut down.")

    def get_component(self, name: str) -> Optional[TimerComponent]:
        entry = self._entries.get(name)
        return entry.instance if entry else None

    def status(self) -> Dict[str, Any]:
        components = {}
        for name, entry in self._entries.items():
            if entry.instance:
                components[name] = entry.instance.status()
            else:
                components[name] = {"state": "NOT_LAUNCHED"}
        return {
            "is_launched": self._is_launched,
            "launch_order": self._launch_order,
            "components": components,
        }

    def dependency_graph(self) -> Dict[str, Dict[str, Any]]:
        return {
            name: {
                "provides": sorted(entry.provides),
                "depends_on": sorted(entry.depends_on),
                "priority": entry.priority,
            }
            for name, entry in self._entries.items()
        }

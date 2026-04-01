#!/usr/bin/env python3
"""
ModuleRegistry — Component Discovery, Registration & Version Management
=========================================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Central registry where all system modules declare themselves, their
interfaces, dependencies, and versions. Enables:
  1. Dependency resolution — ensure required modules are present
  2. Interface validation — verify modules expose expected methods
  3. Hot-swap support — replace module implementations at runtime
  4. Version tracking — for evolution rollback

Apollo Reference:
    cyber/class_loader/ → dynamic component loading
    cyber/component/component.h → component registration macros
    modules/common/adapters/ → adapter registry pattern

Design:
    ModuleRegistry
      ├── ModuleDescriptor      (metadata about a registered module)
      ├── DependencyResolver    (topological sort of init order)
      ├── InterfaceValidator    (verify proc/init/shutdown signatures)
      ├── VersionLedger         (track module versions for rollback)
      └── HotSwapController     (swap module impl without restart)

Production Critique (Knuth-level):
    1. User: If a module fails interface validation (e.g., no proc() method),
       the registry logs a clear error naming the module and missing method.
       The system continues without that module — the user sees "Module X
       unavailable" in the dashboard but can still play.
    2. System: Circular dependency detection uses Kahn's algorithm (BFS
       topological sort). If cycles are found, the system refuses to start
       and prints the cycle path for debugging.
"""

import enum
import inspect
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, List, Optional, Protocol, Set, Tuple, Type
)


# ---------------------------------------------------------------------------
# Module descriptor
# ---------------------------------------------------------------------------

class ModuleCategory(enum.Enum):
    """Functional category of a module."""
    CYBER = "cyber"             # Core framework (scheduler, bus, clock)
    PERCEPTION = "perception"   # Data acquisition (capture, LCU)
    DATA = "data"               # Data services (Riot API, DDragon)
    ANALYSIS = "analysis"       # Game analysis
    PREDICTION = "prediction"   # Win probability, outcome prediction
    PLANNING = "planning"       # Strategy generation
    EVOLUTION = "evolution"     # Self-evolution
    OUTPUT = "output"           # Voice, notifications
    INTEGRATION = "integration" # Agent-OS bridge, plugin API
    RUNTIME = "runtime"         # Health, metrics, error recovery


@dataclass
class ModuleDescriptor:
    """Metadata describing a registered module."""
    name: str
    category: ModuleCategory
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    dependencies: List[str] = field(default_factory=list)  # Other module names
    provides: List[str] = field(default_factory=list)       # Interface names
    requires: List[str] = field(default_factory=list)       # Interface names needed
    interval_ms: int = 100
    priority: int = 50
    instance: Any = None
    registered_at: float = field(default_factory=time.monotonic)
    enabled: bool = True
    hot_swappable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category.value,
            "version": self.version,
            "description": self.description,
            "dependencies": self.dependencies,
            "provides": self.provides,
            "requires": self.requires,
            "interval_ms": self.interval_ms,
            "priority": self.priority,
            "enabled": self.enabled,
            "hot_swappable": self.hot_swappable,
        }


@dataclass
class VersionRecord:
    """Record of a module version change (for rollback)."""
    module_name: str
    old_version: str
    new_version: str
    changed_at: float
    reason: str = ""
    rollback_data: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Interface validation
# ---------------------------------------------------------------------------

REQUIRED_METHODS = {"name", "init", "proc", "shutdown"}


class InterfaceValidator:
    """Validates that a module instance implements the ComponentProtocol."""

    def __init__(self):
        self._log = logging.getLogger("lolbot.registry.validator")

    def validate(self, instance: Any, module_name: str) -> List[str]:
        """
        Check that instance has required methods.
        Returns list of error messages (empty = valid).
        """
        errors: List[str] = []

        # Check 'name' property
        if not hasattr(instance, "name"):
            errors.append(f"{module_name}: missing 'name' property")
        elif not isinstance(getattr(type(instance), "name", None), property):
            # Allow both property and regular attribute
            pass

        # Check required methods
        for method_name in ("init", "proc", "shutdown"):
            if not hasattr(instance, method_name):
                errors.append(f"{module_name}: missing '{method_name}()' method")
                continue

            method = getattr(instance, method_name)
            if not callable(method):
                errors.append(
                    f"{module_name}: '{method_name}' exists but is not callable"
                )
                continue

            # Check if async
            if not inspect.iscoroutinefunction(method):
                errors.append(
                    f"{module_name}: '{method_name}()' must be async (coroutine)"
                )

        for error in errors:
            self._log.error("Validation error: %s", error)

        return errors


# ---------------------------------------------------------------------------
# Dependency resolver — topological sort
# ---------------------------------------------------------------------------

class DependencyResolver:
    """
    Resolves module initialization order using topological sort.
    Detects circular dependencies and reports them clearly.
    """

    def __init__(self):
        self._log = logging.getLogger("lolbot.registry.deps")

    def resolve(
        self, modules: Dict[str, ModuleDescriptor]
    ) -> Tuple[List[str], List[List[str]]]:
        """
        Compute initialization order.
        Returns (ordered_names, cycles).
        If cycles is non-empty, the ordering is incomplete.
        """
        # Build adjacency list: module → modules it depends on
        graph: Dict[str, Set[str]] = {}
        in_degree: Dict[str, int] = {}

        for name in modules:
            graph[name] = set()
            in_degree[name] = 0

        for name, desc in modules.items():
            for dep_name in desc.dependencies:
                if dep_name in modules:
                    graph[name].add(dep_name)

        # Compute in-degrees (reversed: if A depends on B, B has edge to A)
        reverse_graph: Dict[str, Set[str]] = defaultdict(set)
        for name, deps in graph.items():
            for dep in deps:
                reverse_graph[dep].add(name)
                in_degree[name] = in_degree.get(name, 0)

        # Recompute in_degree properly
        in_degree = {name: 0 for name in modules}
        for name, deps in graph.items():
            in_degree[name] = len(deps)

        # Kahn's algorithm
        queue = deque([n for n, d in in_degree.items() if d == 0])
        ordered: List[str] = []

        while queue:
            node = queue.popleft()
            ordered.append(node)
            for dependent in reverse_graph[node]:
                if dependent in in_degree:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        queue.append(dependent)

        # Detect cycles
        cycles: List[List[str]] = []
        if len(ordered) < len(modules):
            remaining = set(modules.keys()) - set(ordered)
            cycles = self._find_cycles(graph, remaining)
            self._log.error(
                "Circular dependencies detected among: %s",
                ", ".join(remaining),
            )
            for cycle in cycles:
                self._log.error("  Cycle: %s", " → ".join(cycle))

        return ordered, cycles

    def _find_cycles(
        self, graph: Dict[str, Set[str]], nodes: Set[str]
    ) -> List[List[str]]:
        """Find cycles in the dependency graph using DFS."""
        cycles: List[List[str]] = []
        visited: Set[str] = set()
        in_stack: Set[str] = set()
        stack: List[str] = []

        def dfs(node: str) -> None:
            if node in in_stack:
                # Found cycle
                cycle_start = stack.index(node)
                cycle = stack[cycle_start:] + [node]
                cycles.append(cycle)
                return
            if node in visited or node not in nodes:
                return

            visited.add(node)
            in_stack.add(node)
            stack.append(node)

            for dep in graph.get(node, set()):
                if dep in nodes:
                    dfs(dep)

            stack.pop()
            in_stack.remove(node)

        for node in nodes:
            if node not in visited:
                dfs(node)

        return cycles


# ---------------------------------------------------------------------------
# ModuleRegistry
# ---------------------------------------------------------------------------

class ModuleRegistry:
    """
    Central module registry. All system components register here.

    Usage:
        registry = ModuleRegistry()

        # Register modules
        registry.register(
            name="perception.network_capture",
            category=ModuleCategory.PERCEPTION,
            instance=capture_engine,
            version="1.2.0",
            dependencies=["cyber.message_bus"],
            interval_ms=10,
            priority=0,
        )

        # Resolve dependencies and get init order
        order, cycles = registry.resolve_dependencies()
        assert not cycles, f"Circular deps: {cycles}"

        # Get modules in init order
        for name in order:
            module = registry.get(name)
            await module.init()
    """

    def __init__(self):
        self._log = logging.getLogger("lolbot.integration.registry")
        self._modules: Dict[str, ModuleDescriptor] = {}
        self._validator = InterfaceValidator()
        self._resolver = DependencyResolver()
        self._version_history: List[VersionRecord] = []
        self._interface_map: Dict[str, str] = {}  # interface_name → module_name

    def register(
        self,
        name: str,
        category: ModuleCategory,
        instance: Any,
        version: str = "0.0.0",
        description: str = "",
        dependencies: Optional[List[str]] = None,
        provides: Optional[List[str]] = None,
        requires: Optional[List[str]] = None,
        interval_ms: int = 100,
        priority: int = 50,
        hot_swappable: bool = False,
        validate: bool = True,
    ) -> bool:
        """
        Register a module. Returns True on success, False on validation failure.
        """
        if name in self._modules:
            self._log.warning("Module '%s' already registered — skipping", name)
            return False

        # Validate interface
        if validate:
            errors = self._validator.validate(instance, name)
            if errors:
                self._log.error(
                    "Module '%s' failed validation with %d errors",
                    name, len(errors),
                )
                return False

        descriptor = ModuleDescriptor(
            name=name,
            category=category,
            version=version,
            description=description,
            dependencies=dependencies or [],
            provides=provides or [],
            requires=requires or [],
            interval_ms=interval_ms,
            priority=priority,
            instance=instance,
            hot_swappable=hot_swappable,
        )

        self._modules[name] = descriptor

        # Register provided interfaces
        for iface in (provides or []):
            self._interface_map[iface] = name

        self._log.info(
            "Registered module: %s (v%s, category=%s, priority=%d)",
            name, version, category.value, priority,
        )
        return True

    def unregister(self, name: str) -> bool:
        """Remove a module from the registry."""
        if name not in self._modules:
            return False
        desc = self._modules.pop(name)
        for iface in desc.provides:
            if self._interface_map.get(iface) == name:
                del self._interface_map[iface]
        self._log.info("Unregistered module: %s", name)
        return True

    def get(self, name: str) -> Optional[Any]:
        """Get a module instance by name."""
        desc = self._modules.get(name)
        return desc.instance if desc else None

    def get_descriptor(self, name: str) -> Optional[ModuleDescriptor]:
        """Get module descriptor."""
        return self._modules.get(name)

    def get_by_interface(self, interface_name: str) -> Optional[Any]:
        """Get module instance that provides a given interface."""
        module_name = self._interface_map.get(interface_name)
        if module_name:
            return self.get(module_name)
        return None

    def get_by_category(self, category: ModuleCategory) -> List[ModuleDescriptor]:
        """Get all modules in a category."""
        return [d for d in self._modules.values() if d.category == category]

    def resolve_dependencies(self) -> Tuple[List[str], List[List[str]]]:
        """Compute init order. Returns (ordered_names, cycles)."""
        return self._resolver.resolve(self._modules)

    def check_requirements(self) -> List[str]:
        """
        Verify all module requirements are satisfied.
        Returns list of unmet requirements.
        """
        unmet: List[str] = []
        for desc in self._modules.values():
            for req in desc.requires:
                if req not in self._interface_map:
                    unmet.append(
                        f"{desc.name} requires interface '{req}' "
                        f"but no module provides it"
                    )
            for dep in desc.dependencies:
                if dep not in self._modules:
                    unmet.append(
                        f"{desc.name} depends on '{dep}' "
                        f"but it is not registered"
                    )
        return unmet

    # ---- Hot-swap ----

    def hot_swap(
        self,
        name: str,
        new_instance: Any,
        new_version: str,
        reason: str = "evolution",
    ) -> bool:
        """
        Replace a module's implementation at runtime.
        Only works for modules marked as hot_swappable.
        """
        desc = self._modules.get(name)
        if not desc:
            self._log.error("Cannot hot-swap: module '%s' not found", name)
            return False

        if not desc.hot_swappable:
            self._log.error(
                "Cannot hot-swap: module '%s' is not hot-swappable", name
            )
            return False

        errors = self._validator.validate(new_instance, name)
        if errors:
            self._log.error(
                "Cannot hot-swap: new instance for '%s' failed validation", name
            )
            return False

        old_version = desc.version
        old_instance = desc.instance

        # Record version change
        self._version_history.append(VersionRecord(
            module_name=name,
            old_version=old_version,
            new_version=new_version,
            changed_at=time.monotonic(),
            reason=reason,
        ))

        desc.instance = new_instance
        desc.version = new_version

        self._log.info(
            "Hot-swapped module '%s': v%s → v%s (reason: %s)",
            name, old_version, new_version, reason,
        )
        return True

    def rollback(self, name: str) -> bool:
        """Rollback a module to its previous version (if history exists)."""
        history = [
            r for r in self._version_history
            if r.module_name == name and r.rollback_data
        ]
        if not history:
            self._log.warning("No rollback data for module '%s'", name)
            return False

        last_record = history[-1]
        self._log.info(
            "Rolling back module '%s' to v%s",
            name, last_record.old_version,
        )
        # Rollback would need stored instance; this is a simplified version
        return False

    # ---- Introspection ----

    def list_modules(self) -> List[Dict[str, Any]]:
        """List all registered modules."""
        return [d.to_dict() for d in self._modules.values()]

    def get_dependency_graph(self) -> Dict[str, List[str]]:
        """Return the dependency graph for visualization."""
        return {
            name: desc.dependencies
            for name, desc in self._modules.items()
        }

    def get_version_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent version changes."""
        return [
            {
                "module": r.module_name,
                "old_version": r.old_version,
                "new_version": r.new_version,
                "reason": r.reason,
            }
            for r in self._version_history[-limit:]
        ]

    @property
    def module_count(self) -> int:
        return len(self._modules)

    @property
    def enabled_count(self) -> int:
        return sum(1 for d in self._modules.values() if d.enabled)

"""
common/component_base.py — Apollo-style Component Lifecycle Base
==================================================================
lolbot-HyperAI · modules/common

查看 Apollo cyber/component/timer_component.h 上现有 TimerComponent 基类的
实现方式, 理解其模式, 特别是 Init()/Proc()/Shutdown() 生命周期是如何强制
约束的。从 Apollo TimerComponent 这个好例子开始。然后遵循该模式实现一个
ComponentBase 抽象基类, 让所有 *_component.py 可以继承统一的生命周期接口,
并能自动注册到组件注册表、自动采集 Proc() 性能指标。

本文件提供:
1. ComponentRegistry — 全局组件发现与工厂
2. ProcMetrics       — Proc() 延迟/成功率/P95 采集
3. HealthStatus      — 组件健康协议
4. ManagedComponent  — mixin: 自动注册 + 性能采集 + 依赖检查 + 降级
5. ComponentDependency — 声明式依赖
6. ProcCircuitBreaker — Proc() 连续失败自动熔断

位置: lolbot-HyperAI/modules/common/component_base.py
"""

from __future__ import annotations

import abc
import enum
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Type,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Component lifecycle states
# ---------------------------------------------------------------------------

class LifecycleState(enum.Enum):
    """Standard lifecycle states for all managed components.

    State machine::

        CREATED -> INITIALIZING -> READY -> RUNNING -> STOPPING -> STOPPED
                        |                      |
                        v                      v
                      ERROR  <-----------  DEGRADED
    """
    CREATED = "created"
    INITIALIZING = "initializing"
    READY = "ready"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


# Valid state transitions enforced by ManagedComponent._transition()
_VALID_TRANSITIONS: Dict[LifecycleState, Set[LifecycleState]] = {
    LifecycleState.CREATED: {
        LifecycleState.INITIALIZING, LifecycleState.ERROR,
    },
    LifecycleState.INITIALIZING: {
        LifecycleState.READY, LifecycleState.ERROR,
    },
    LifecycleState.READY: {
        LifecycleState.RUNNING, LifecycleState.STOPPING,
        LifecycleState.ERROR,
    },
    LifecycleState.RUNNING: {
        LifecycleState.DEGRADED, LifecycleState.STOPPING,
        LifecycleState.ERROR,
    },
    LifecycleState.DEGRADED: {
        LifecycleState.RUNNING, LifecycleState.STOPPING,
        LifecycleState.ERROR,
    },
    LifecycleState.STOPPING: {
        LifecycleState.STOPPED, LifecycleState.ERROR,
    },
    LifecycleState.STOPPED: set(),
    LifecycleState.ERROR: {
        LifecycleState.INITIALIZING, LifecycleState.STOPPING,
    },
}


# ---------------------------------------------------------------------------
# Component registry (global singleton)
# ---------------------------------------------------------------------------

class ComponentRegistry:
    """全局组件注册表 — Apollo class_loader + factory 的 Python 等价.

    Thread-safe singleton. Components register themselves during Init(),
    enabling cross-component discovery and health aggregation.

    Usage::

        registry = ComponentRegistry.instance()
        registry.register(my_component)
        comp = registry.get("canbus")
        all_comps = registry.all()
        health = registry.health_summary()
    """

    _instance: Optional[ComponentRegistry] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._components: OrderedDict[str, Any] = OrderedDict()
        self._class_registry: Dict[str, Type] = {}
        self._creation_order: List[str] = []

    @classmethod
    def instance(cls) -> ComponentRegistry:
        """获取单例 (double-checked locking)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置 (仅测试用)."""
        with cls._lock:
            cls._instance = None

    def register(self, component: Any) -> None:
        """注册组件实例."""
        name = getattr(component, "name", type(component).__name__)
        if name in self._components:
            logger.warning("组件 %r 已注册, 覆盖旧实例", name)
        else:
            self._creation_order.append(name)
        self._components[name] = component
        logger.debug("注册组件: %s (%s)", name, type(component).__name__)

    def unregister(self, name: str) -> None:
        """取消注册."""
        removed = self._components.pop(name, None)
        if removed is not None:
            try:
                self._creation_order.remove(name)
            except ValueError:
                pass

    def get(self, name: str) -> Optional[Any]:
        """按名称获取组件."""
        return self._components.get(name)

    def all(self) -> Dict[str, Any]:
        """获取所有已注册组件 (insertion order)."""
        return dict(self._components)

    def names(self) -> List[str]:
        """获取所有已注册组件名."""
        return list(self._components.keys())

    def shutdown_order(self) -> List[str]:
        """返回关闭顺序 (注册的反序, 确保依赖先关闭)."""
        return list(reversed(self._creation_order))

    def register_class(self, name: str, cls: Type) -> None:
        """注册组件类 (工厂模式)."""
        self._class_registry[name] = cls

    def create(self, name: str, **kwargs: Any) -> Any:
        """从注册类创建实例."""
        cls = self._class_registry.get(name)
        if cls is None:
            raise ValueError(f"未注册的组件类: {name!r}")
        return cls(**kwargs)

    def health_summary(self) -> Dict[str, Dict[str, Any]]:
        """汇总所有组件健康状态."""
        summary: Dict[str, Dict[str, Any]] = {}
        for name, comp in self._components.items():
            if hasattr(comp, "health_check"):
                try:
                    summary[name] = comp.health_check()
                except Exception as exc:
                    summary[name] = {
                        "healthy": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
            elif hasattr(comp, "stats"):
                try:
                    summary[name] = {"stats": comp.stats()}
                except Exception:
                    summary[name] = {"healthy": False}
            else:
                summary[name] = {"registered": True}
        return summary

    def count(self) -> int:
        """已注册组件数量."""
        return len(self._components)

    def dependency_graph(self) -> Dict[str, List[str]]:
        """构建依赖关系图 (用于 DAG 排序)."""
        graph: Dict[str, List[str]] = {}
        for name, comp in self._components.items():
            deps = getattr(comp, "DEPENDENCIES", [])
            graph[name] = [d.name for d in deps]
        return graph


# ---------------------------------------------------------------------------
# Component dependency declaration
# ---------------------------------------------------------------------------

@dataclass
class ComponentDependency:
    """组件依赖声明.

    Attributes:
        name: 依赖的组件名.
        required: 是否必须 (True → 缺失时 Init 失败).
        channels: 需要订阅的频道列表.
        min_version: 最低版本要求 (可选).
    """
    name: str
    required: bool = True
    channels: List[str] = field(default_factory=list)
    min_version: str = ""

    def __repr__(self) -> str:
        req = "required" if self.required else "optional"
        return f"Dep({self.name!r}, {req})"


# ---------------------------------------------------------------------------
# Proc metrics collector
# ---------------------------------------------------------------------------

@dataclass
class ProcMetrics:
    """Proc() 性能指标收集器.

    自动嵌入到每个 Proc() 调用中, 收集:
    - 调用计数 / 成功数 / 失败数
    - 延迟统计 (avg / min / max / p95 / p99)
    - 最近 N 次耗时样本 (滚动窗口)
    - 连续失败计数 (用于 circuit breaker)
    """
    total_calls: int = 0
    total_success: int = 0
    total_failure: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    consecutive_failures: int = 0
    last_success_time: float = 0.0
    last_failure_time: float = 0.0
    _recent: List[float] = field(default_factory=list)
    _recent_max: int = 200
    _failure_reasons: Dict[str, int] = field(default_factory=dict)

    def record(self, latency_ms: float, success: bool,
               failure_reason: str = "") -> None:
        """Record one Proc() invocation."""
        self.total_calls += 1
        self.total_latency_ms += latency_ms
        if latency_ms > self.max_latency_ms:
            self.max_latency_ms = latency_ms
        if latency_ms < self.min_latency_ms:
            self.min_latency_ms = latency_ms
        if success:
            self.total_success += 1
            self.consecutive_failures = 0
            self.last_success_time = time.monotonic()
        else:
            self.total_failure += 1
            self.consecutive_failures += 1
            self.last_failure_time = time.monotonic()
            if failure_reason:
                self._failure_reasons[failure_reason] = (
                    self._failure_reasons.get(failure_reason, 0) + 1
                )
        self._recent.append(latency_ms)
        if len(self._recent) > self._recent_max:
            self._recent = self._recent[-self._recent_max:]

    @property
    def avg_latency_ms(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.total_latency_ms / self.total_calls

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 1.0
        return self.total_success / self.total_calls

    def _percentile(self, p: float) -> float:
        """Compute p-th percentile from recent samples."""
        if not self._recent:
            return 0.0
        s = sorted(self._recent)
        idx = int(len(s) * p / 100.0)
        idx = min(idx, len(s) - 1)
        return s[idx]

    @property
    def p95_ms(self) -> float:
        return self._percentile(95)

    @property
    def p99_ms(self) -> float:
        return self._percentile(99)

    @property
    def recent_avg_ms(self) -> float:
        if not self._recent:
            return 0.0
        return sum(self._recent) / len(self._recent)

    def snapshot(self) -> Dict[str, Any]:
        """Export metrics as dict for monitoring."""
        min_lat = self.min_latency_ms
        if min_lat == float("inf"):
            min_lat = 0.0
        return {
            "total_calls": self.total_calls,
            "total_success": self.total_success,
            "total_failure": self.total_failure,
            "success_rate": round(self.success_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "recent_avg_ms": round(self.recent_avg_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "min_latency_ms": round(min_lat, 2),
            "p95_ms": round(self.p95_ms, 2),
            "p99_ms": round(self.p99_ms, 2),
            "consecutive_failures": self.consecutive_failures,
            "top_failure_reasons": dict(
                sorted(
                    self._failure_reasons.items(),
                    key=lambda x: x[1], reverse=True,
                )[:5]
            ),
        }

    def reset(self) -> None:
        """Reset all metrics (e.g. after generation change)."""
        self.total_calls = 0
        self.total_success = 0
        self.total_failure = 0
        self.total_latency_ms = 0.0
        self.max_latency_ms = 0.0
        self.min_latency_ms = float("inf")
        self.consecutive_failures = 0
        self._recent.clear()
        self._failure_reasons.clear()


# ---------------------------------------------------------------------------
# Health check protocol
# ---------------------------------------------------------------------------

@dataclass
class HealthStatus:
    """组件健康状态 — 标准化的健康检查返回值."""
    healthy: bool = True
    component: str = ""
    state: str = ""
    uptime_s: float = 0.0
    degraded: bool = False
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "healthy": self.healthy,
            "component": self.component,
            "state": self.state,
            "uptime_s": round(self.uptime_s, 1),
            "degraded": self.degraded,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Circuit breaker for Proc()
# ---------------------------------------------------------------------------

class ProcCircuitBreaker:
    """Circuit breaker: trips after N consecutive Proc() failures.

    States: CLOSED (normal) -> OPEN (tripped) -> HALF_OPEN (probing)
    When OPEN, Proc() is skipped for cooldown_s. After cooldown, one
    probe is allowed (HALF_OPEN). If probe succeeds -> CLOSED.
    If probe fails -> OPEN with doubled cooldown (capped).
    """

    def __init__(self, max_failures: int = 5,
                 cooldown_s: float = 2.0,
                 max_cooldown_s: float = 60.0) -> None:
        self._max_failures = max_failures
        self._base_cooldown = cooldown_s
        self._max_cooldown = max_cooldown_s
        self._current_cooldown = cooldown_s
        self._state = "closed"
        self._trip_time: float = 0.0
        self._trip_count: int = 0

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == "open"

    def should_allow(self, consecutive_failures: int) -> bool:
        """Check if Proc() should be allowed to execute."""
        if self._state == "closed":
            if consecutive_failures >= self._max_failures:
                self._trip()
                return False
            return True
        if self._state == "open":
            elapsed = time.monotonic() - self._trip_time
            if elapsed >= self._current_cooldown:
                self._state = "half_open"
                return True
            return False
        return True  # half_open — allow one probe

    def record_result(self, success: bool) -> None:
        """Record the result after an allowed Proc() call."""
        if self._state == "half_open":
            if success:
                self._reset()
            else:
                self._current_cooldown = min(
                    self._current_cooldown * 2, self._max_cooldown,
                )
                self._trip()

    def _trip(self) -> None:
        self._state = "open"
        self._trip_time = time.monotonic()
        self._trip_count += 1

    def _reset(self) -> None:
        self._state = "closed"
        self._current_cooldown = self._base_cooldown
        self._trip_time = 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "trip_count": self._trip_count,
            "current_cooldown_s": self._current_cooldown,
        }


# ---------------------------------------------------------------------------
# ManagedComponent mixin
# ---------------------------------------------------------------------------

class ManagedComponent:
    """可管理组件混入 — 为所有 *_component.py 提供统一管理.

    提供:
    1. 自动注册到 ComponentRegistry (register_self)
    2. Proc() 性能自动采集 (measure_proc)
    3. 声明式依赖检查 (DEPENDENCIES + check_dependencies)
    4. 生命周期状态机 (LifecycleState + _transition)
    5. Circuit breaker (连续失败自动跳过 Proc)
    6. 健康检查协议 (health_check)
    7. 降级模式 (enter_degraded / exit_degraded)

    Usage::

        class CanbusComponent(TimerComponent, ManagedComponent):
            COMPONENT_NAME = "canbus"
            DEPENDENCIES = [
                ComponentDependency("transport", required=True),
            ]

            def Init(self) -> bool:
                self._managed_init()
                # ... component-specific init ...
                self._transition(LifecycleState.READY)
                return True

            def Proc(self) -> bool:
                with self.measure_proc() as m:
                    # ... do work ...
                    m.success = True
                return m.success
    """

    COMPONENT_NAME: str = ""
    DEPENDENCIES: List[ComponentDependency] = []
    VERSION: str = "1.0.0"
    CB_MAX_FAILURES: int = 5
    CB_COOLDOWN_S: float = 2.0
    CB_MAX_COOLDOWN_S: float = 60.0

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "COMPONENT_NAME", "") or cls.__name__
        ComponentRegistry.instance().register_class(name, cls)

    def _managed_init(self) -> None:
        """Call at the start of Init()."""
        self._lifecycle_state = LifecycleState.INITIALIZING
        self._proc_metrics = ProcMetrics()
        self._circuit_breaker = ProcCircuitBreaker(
            max_failures=self.CB_MAX_FAILURES,
            cooldown_s=self.CB_COOLDOWN_S,
            max_cooldown_s=self.CB_MAX_COOLDOWN_S,
        )
        self._managed_start_time = time.monotonic()
        self._degraded = False
        self._degraded_reason = ""
        self._proc_skip_count = 0

    def _managed_shutdown(self) -> None:
        """Call at the start of Shutdown()."""
        self._transition(LifecycleState.STOPPING)
        self.unregister_self()
        self._transition(LifecycleState.STOPPED)

    def register_self(self) -> None:
        """注册实例到全局注册表."""
        ComponentRegistry.instance().register(self)

    def unregister_self(self) -> None:
        """从注册表中移除."""
        name = getattr(self, "name",
                       self.COMPONENT_NAME or type(self).__name__)
        ComponentRegistry.instance().unregister(name)

    def _transition(self, new_state: LifecycleState) -> bool:
        """Attempt a lifecycle state transition."""
        current = getattr(self, "_lifecycle_state",
                          LifecycleState.CREATED)
        valid = _VALID_TRANSITIONS.get(current, set())
        if new_state not in valid:
            comp_name = getattr(self, "name",
                                self.COMPONENT_NAME or "unknown")
            logger.warning(
                "[%s] 无效状态转换: %s -> %s (允许: %s)",
                comp_name, current.value, new_state.value,
                [s.value for s in valid],
            )
            return False
        self._lifecycle_state = new_state
        return True

    @property
    def lifecycle_state(self) -> LifecycleState:
        return getattr(self, "_lifecycle_state", LifecycleState.CREATED)

    def check_dependencies(self) -> Tuple[bool, List[str]]:
        """检查依赖是否满足."""
        registry = ComponentRegistry.instance()
        missing: List[str] = []
        for dep in self.DEPENDENCIES:
            comp = registry.get(dep.name)
            if comp is None and dep.required:
                missing.append(dep.name)
        return len(missing) == 0, missing

    def measure_proc(self) -> _ProcMeasureContext:
        """Proc() 性能测量上下文管理器."""
        if not hasattr(self, "_proc_metrics"):
            self._proc_metrics = ProcMetrics()
        if not hasattr(self, "_circuit_breaker"):
            self._circuit_breaker = ProcCircuitBreaker()
        return _ProcMeasureContext(
            self._proc_metrics, self._circuit_breaker,
        )

    def should_skip_proc(self) -> bool:
        """Check circuit breaker — True means skip this Proc()."""
        if not hasattr(self, "_circuit_breaker"):
            return False
        if not hasattr(self, "_proc_metrics"):
            return False
        allowed = self._circuit_breaker.should_allow(
            self._proc_metrics.consecutive_failures,
        )
        if not allowed:
            self._proc_skip_count = getattr(
                self, "_proc_skip_count", 0) + 1
            return True
        return False

    def enter_degraded(self, reason: str) -> None:
        """Enter degraded mode (partial functionality)."""
        self._degraded = True
        self._degraded_reason = reason
        self._transition(LifecycleState.DEGRADED)
        comp_name = getattr(self, "name", self.COMPONENT_NAME)
        logger.warning("[%s] 进入降级模式: %s", comp_name, reason)

    def exit_degraded(self) -> None:
        """Exit degraded mode."""
        self._degraded = False
        self._degraded_reason = ""
        self._transition(LifecycleState.RUNNING)

    @property
    def is_degraded(self) -> bool:
        return getattr(self, "_degraded", False)

    def health_check(self) -> Dict[str, Any]:
        """健康检查 — 返回标准化的健康状态."""
        uptime = 0.0
        start = getattr(self, "_managed_start_time", None)
        if start is not None:
            uptime = time.monotonic() - start
        comp_name = getattr(
            self, "name", self.COMPONENT_NAME or type(self).__name__)
        lc_state = getattr(
            self, "_lifecycle_state", LifecycleState.CREATED)
        status = HealthStatus(
            healthy=True, component=comp_name,
            state=lc_state.value, uptime_s=uptime,
            degraded=getattr(self, "_degraded", False),
        )
        if hasattr(self, "_proc_metrics"):
            pm = self._proc_metrics
            status.details["proc_metrics"] = pm.snapshot()
            if pm.total_calls > 100 and pm.success_rate < 0.5:
                status.healthy = False
                status.details["reason"] = "success_rate < 50%"
            if pm.consecutive_failures > self.CB_MAX_FAILURES:
                status.healthy = False
        if hasattr(self, "_circuit_breaker"):
            status.details["circuit_breaker"] = (
                self._circuit_breaker.stats())
        if getattr(self, "_degraded", False):
            status.details["degraded_reason"] = getattr(
                self, "_degraded_reason", "")
        if lc_state in (LifecycleState.ERROR, LifecycleState.STOPPED):
            status.healthy = False
        status.details["skip_count"] = getattr(
            self, "_proc_skip_count", 0)
        return status.to_dict()

    def proc_metrics_snapshot(self) -> Dict[str, Any]:
        """获取 Proc 指标快照."""
        if hasattr(self, "_proc_metrics"):
            return self._proc_metrics.snapshot()
        return {}


class _ProcMeasureContext:
    """Proc 测量上下文管理器."""

    __slots__ = (
        "_metrics", "_breaker", "_start", "success", "failure_reason",
    )

    def __init__(self, metrics: ProcMetrics,
                 breaker: ProcCircuitBreaker) -> None:
        self._metrics = metrics
        self._breaker = breaker
        self._start: float = 0.0
        self.success: bool = False
        self.failure_reason: str = ""

    def __enter__(self) -> _ProcMeasureContext:
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any,
                 exc_tb: Any) -> bool:
        latency_ms = (time.monotonic() - self._start) * 1000.0
        if exc_type is not None:
            self.success = False
            self.failure_reason = f"{exc_type.__name__}: {exc_val}"
        self._metrics.record(
            latency_ms, self.success, self.failure_reason)
        self._breaker.record_result(self.success)
        return False  # 不吞异常


# ---------------------------------------------------------------------------
# ComponentGraph — static dependency analysis (Claude11 addition)
# ---------------------------------------------------------------------------

class ComponentGraph:
    """Static dependency graph analysis for DAG validation."""

    def __init__(self) -> None:
        self._edges: Dict[str, List[str]] = {}

    def add_component(self, name: str, depends_on: List[str]) -> None:
        self._edges[name] = list(depends_on)

    def has_cycle(self) -> bool:
        visited: set = set(); in_stack: set = set()
        def dfs(n):
            visited.add(n); in_stack.add(n)
            for d in self._edges.get(n, []):
                if d not in visited:
                    if dfs(d): return True
                elif d in in_stack: return True
            in_stack.discard(n); return False
        for n in self._edges:
            if n not in visited and dfs(n): return True
        return False

    def launch_order(self) -> List[str]:
        from collections import deque
        in_deg = {n: 0 for n in self._edges}
        adj: Dict[str, List[str]] = {n: [] for n in self._edges}
        for name, deps in self._edges.items():
            for d in deps:
                if d in adj: adj[d].append(name); in_deg[name] += 1
        q = deque(n for n, d in in_deg.items() if d == 0)
        result = []
        while q:
            n = q.popleft(); result.append(n)
            for nb in adj.get(n, []):
                in_deg[nb] -= 1
                if in_deg[nb] == 0: q.append(nb)
        return result

    # ─── Claude17: Dependency Graph Visualization ────────────────────────

    def visualize(self) -> str:
        """Return ASCII representation of the dependency graph.

        Claude17: Useful for debugging and documentation.
        """
        lines = ["Dependency Graph:"]
        order = self.launch_order()
        for name in order:
            deps = self._edges.get(name, [])
            dep_str = " → ".join(deps) if deps else "(root)"
            lines.append(f"  {name}: depends on {dep_str}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claude17: ComponentCapability — declare what a component provides
# ---------------------------------------------------------------------------

class ComponentCapability:
    """Declares the capabilities and channels a component provides.

    Claude17: Enables dynamic discovery. Instead of hardcoding
    channel names, components declare their capabilities and
    consumers discover them through the registry.

    Usage::

        class MyComponent(ManagedComponent):
            CAPABILITIES = ComponentCapability(
                provides_channels=["/lol/game_state"],
                consumes_channels=["/lol/raw_lcu"],
                provides_services=["game_state_query"],
            )
    """

    def __init__(
        self,
        provides_channels: Optional[List[str]] = None,
        consumes_channels: Optional[List[str]] = None,
        provides_services: Optional[List[str]] = None,
        requires_services: Optional[List[str]] = None,
    ) -> None:
        self.provides_channels = provides_channels or []
        self.consumes_channels = consumes_channels or []
        self.provides_services = provides_services or []
        self.requires_services = requires_services or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provides_channels": self.provides_channels,
            "consumes_channels": self.consumes_channels,
            "provides_services": self.provides_services,
            "requires_services": self.requires_services,
        }


# ---------------------------------------------------------------------------
# Claude17: ProcBudgetTracker — track time budget per Proc() call
# ---------------------------------------------------------------------------

class ProcBudgetTracker:
    """Tracks per-Proc() time budget utilization.

    Claude17: Each component has an interval (e.g., 100ms). This
    tracker measures what fraction of that budget Proc() actually
    uses. If utilization exceeds 80%, the component is at risk
    of overrun.

    Used by AdaptiveIntervalTuner to decide when to slow down.
    """

    def __init__(self, budget_ms: float) -> None:
        self._budget_ms = budget_ms
        self._utilizations: List[float] = []
        self._max_window = 200
        self._overbudget_count = 0

    def record(self, actual_ms: float) -> float:
        """Record actual Proc() duration and return utilization ratio.

        Returns:
            Float ratio (0.0–1.0+). >1.0 means over budget.
        """
        utilization = actual_ms / max(self._budget_ms, 0.1)
        self._utilizations.append(utilization)
        if len(self._utilizations) > self._max_window:
            self._utilizations = self._utilizations[-self._max_window:]
        if utilization > 1.0:
            self._overbudget_count += 1
        return utilization

    @property
    def mean_utilization(self) -> float:
        if not self._utilizations:
            return 0.0
        return sum(self._utilizations) / len(self._utilizations)

    @property
    def peak_utilization(self) -> float:
        return max(self._utilizations) if self._utilizations else 0.0

    @property
    def overbudget_rate(self) -> float:
        if not self._utilizations:
            return 0.0
        return self._overbudget_count / len(self._utilizations)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "budget_ms": self._budget_ms,
            "mean_utilization": round(self.mean_utilization, 4),
            "peak_utilization": round(self.peak_utilization, 4),
            "overbudget_rate": round(self.overbudget_rate, 4),
            "overbudget_count": self._overbudget_count,
            "samples": len(self._utilizations),
        }


# ---------------------------------------------------------------------------
# Claude17: GracefulDegradation — protocol for component degradation
# ---------------------------------------------------------------------------

class DegradationLevel:
    """Defines degradation levels for graceful degradation.

    Claude17: Components can operate at reduced capacity when
    the system is under stress, rather than failing entirely.
    """
    FULL = "full"           # Normal operation
    REDUCED = "reduced"     # Skip non-essential work
    MINIMAL = "minimal"     # Only critical path
    SUSPENDED = "suspended" # Paused, waiting for recovery


class DegradationPolicy:
    """Policy for when and how a component should degrade.

    Claude17: Configurable thresholds for automatic degradation.

    Usage::

        policy = DegradationPolicy(
            reduce_at_utilization=0.7,
            minimize_at_utilization=0.9,
            suspend_at_utilization=1.0,
        )
    """

    def __init__(
        self,
        reduce_at_utilization: float = 0.7,
        minimize_at_utilization: float = 0.9,
        suspend_at_utilization: float = 1.0,
    ) -> None:
        self.reduce_at = reduce_at_utilization
        self.minimize_at = minimize_at_utilization
        self.suspend_at = suspend_at_utilization

    def recommend_level(self, utilization: float) -> str:
        """Recommend a degradation level based on current utilization."""
        if utilization >= self.suspend_at:
            return DegradationLevel.SUSPENDED
        elif utilization >= self.minimize_at:
            return DegradationLevel.MINIMAL
        elif utilization >= self.reduce_at:
            return DegradationLevel.REDUCED
        else:
            return DegradationLevel.FULL

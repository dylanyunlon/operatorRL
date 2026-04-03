"""
common/component_base.py — 组件基类统一接口
=============================================

查看 Apollo cyber/component/timer_component.h 上现有 TimerComponent 基类的
实现方式, 理解其模式, 特别是 Init()/Proc()/Shutdown() 生命周期是如何强制
约束的。从 Apollo TimerComponent 这个好例子开始。然后遵循该模式实现一个
ComponentBase 抽象基类, 让所有 *_component.py 可以继承统一的生命周期接口,
并能自动注册到组件注册表、自动采集 Proc() 性能指标。

本文件是对 cyber/component/timer_component.py 的补充, 提供:
1. 组件注册表 (全局发现)
2. Proc() 性能自动采集中间件
3. 组件间依赖声明
4. 健康检查协议

位置: lolbot-HyperAI/modules/common/component_base.py
"""

from __future__ import annotations

import abc
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Component registry (global singleton)
# ---------------------------------------------------------------------------

class ComponentRegistry:
    """全局组件注册表.

    Apollo 通过 class_loader + factory 管理组件实例.
    我们用简单的注册表模式.

    Usage::

        registry = ComponentRegistry.instance()
        registry.register(my_component)
        comp = registry.get("canbus")
        all_comps = registry.all()
    """

    _instance: Optional[ComponentRegistry] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._components: OrderedDict[str, Any] = OrderedDict()
        self._class_registry: Dict[str, Type] = {}

    @classmethod
    def instance(cls) -> ComponentRegistry:
        """获取单例."""
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
            logger.warning("组件 %r 已注册, 覆盖", name)
        self._components[name] = component
        logger.debug("注册组件: %s (%s)", name, type(component).__name__)

    def unregister(self, name: str) -> None:
        """取消注册."""
        self._components.pop(name, None)

    def get(self, name: str) -> Optional[Any]:
        """按名称获取组件."""
        return self._components.get(name)

    def all(self) -> Dict[str, Any]:
        """获取所有已注册组件."""
        return dict(self._components)

    def names(self) -> List[str]:
        """获取所有已注册组件名."""
        return list(self._components.keys())

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
            elif hasattr(comp, "status"):
                try:
                    summary[name] = comp.status()
                except Exception:
                    summary[name] = {"healthy": False}
            else:
                summary[name] = {"registered": True}
        return summary


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
    """
    name: str
    required: bool = True
    channels: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Proc metrics collector
# ---------------------------------------------------------------------------

@dataclass
class ProcMetrics:
    """Proc() 性能指标收集器.

    自动嵌入到每个 Proc() 调用中, 收集:
    - 调用计数
    - 耗时统计
    - 成功/失败率
    - 最近 N 次耗时样本
    """
    total_calls: int = 0
    total_success: int = 0
    total_failure: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = float("inf")
    _recent: List[float] = field(default_factory=list)
    _recent_max: int = 100

    def record(self, latency_ms: float, success: bool) -> None:
        self.total_calls += 1
        self.total_latency_ms += latency_ms
        if latency_ms > self.max_latency_ms:
            self.max_latency_ms = latency_ms
        if latency_ms < self.min_latency_ms:
            self.min_latency_ms = latency_ms
        if success:
            self.total_success += 1
        else:
            self.total_failure += 1
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
            return 0.0
        return self.total_success / self.total_calls

    @property
    def p95_ms(self) -> float:
        if len(self._recent) < 5:
            return self.max_latency_ms
        s = sorted(self._recent)
        return s[int(len(s) * 0.95)]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_success": self.total_success,
            "total_failure": self.total_failure,
            "success_rate": round(self.success_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "min_latency_ms": round(
                self.min_latency_ms if self.min_latency_ms != float("inf") else 0, 2
            ),
            "p95_ms": round(self.p95_ms, 2),
        }


# ---------------------------------------------------------------------------
# Health check protocol
# ---------------------------------------------------------------------------

@dataclass
class HealthStatus:
    """组件健康状态.

    Attributes:
        healthy: 是否健康.
        component: 组件名.
        state: 组件状态.
        uptime_s: 运行时长.
        details: 附加信息.
    """
    healthy: bool = True
    component: str = ""
    state: str = ""
    uptime_s: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "healthy": self.healthy,
            "component": self.component,
            "state": self.state,
            "uptime_s": round(self.uptime_s, 1),
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# ManagedComponent mixin
# ---------------------------------------------------------------------------

class ManagedComponent:
    """可管理组件混入.

    为 TimerComponent 子类添加:
    1. 自动注册到 ComponentRegistry
    2. Proc() 性能自动采集
    3. 依赖声明
    4. 健康检查

    Usage::

        class CanbusComponent(TimerComponent, ManagedComponent):
            DEPENDENCIES = [
                ComponentDependency("transport", required=True),
            ]

            def Init(self) -> bool:
                self.register_self()
                ...

            def Proc(self) -> bool:
                with self.measure_proc():
                    ...
    """

    DEPENDENCIES: List[ComponentDependency] = []

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # 自动注册类到 class_registry
        name = getattr(cls, "COMPONENT_NAME", cls.__name__)
        ComponentRegistry.instance().register_class(name, cls)

    def register_self(self) -> None:
        """注册实例到全局注册表."""
        name = getattr(self, "name", type(self).__name__)
        ComponentRegistry.instance().register(self)

    def unregister_self(self) -> None:
        """从注册表中移除."""
        name = getattr(self, "name", type(self).__name__)
        ComponentRegistry.instance().unregister(name)

    def check_dependencies(self) -> Tuple[bool, List[str]]:
        """检查依赖是否满足.

        Returns:
            (all_satisfied, list_of_missing_names)
        """
        registry = ComponentRegistry.instance()
        missing: List[str] = []
        for dep in self.DEPENDENCIES:
            comp = registry.get(dep.name)
            if comp is None and dep.required:
                missing.append(dep.name)
        return len(missing) == 0, missing

    def _init_proc_metrics(self) -> None:
        """初始化 Proc 指标收集 (在 Init 中调用)."""
        if not hasattr(self, "_proc_metrics"):
            self._proc_metrics = ProcMetrics()
        if not hasattr(self, "_start_time"):
            self._start_time = time.monotonic()

    def measure_proc(self) -> _ProcMeasureContext:
        """Proc() 性能测量上下文管理器.

        Usage::

            def Proc(self) -> bool:
                with self.measure_proc() as m:
                    ... do work ...
                    m.success = True
                return m.success
        """
        if not hasattr(self, "_proc_metrics"):
            self._init_proc_metrics()
        return _ProcMeasureContext(self._proc_metrics)

    def health_check(self) -> Dict[str, Any]:
        """健康检查."""
        uptime = 0.0
        if hasattr(self, "_start_time"):
            uptime = time.monotonic() - self._start_time

        status = HealthStatus(
            healthy=True,
            component=getattr(self, "name", type(self).__name__),
            state=getattr(self, "state", "unknown"),
            uptime_s=uptime,
        )

        if hasattr(self, "_proc_metrics"):
            pm = self._proc_metrics
            status.details["proc_metrics"] = pm.snapshot()
            if pm.total_calls > 100 and pm.success_rate < 0.5:
                status.healthy = False
                status.details["reason"] = "success_rate < 50%"

        # 检查状态
        state = status.state
        if hasattr(state, "name"):
            state = state.name
        if state in ("ERROR", "SHUTDOWN"):
            status.healthy = False

        return status.to_dict()

    def proc_metrics_snapshot(self) -> Dict[str, Any]:
        """获取 Proc 指标快照."""
        if hasattr(self, "_proc_metrics"):
            return self._proc_metrics.snapshot()
        return {}


class _ProcMeasureContext:
    """Proc 测量上下文管理器."""

    def __init__(self, metrics: ProcMetrics) -> None:
        self._metrics = metrics
        self._start: float = 0.0
        self.success: bool = False

    def __enter__(self) -> _ProcMeasureContext:
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        latency = (time.monotonic() - self._start) * 1000
        if exc_type is not None:
            self.success = False
        self._metrics.record(latency, self.success)
        return False  # 不吞异常

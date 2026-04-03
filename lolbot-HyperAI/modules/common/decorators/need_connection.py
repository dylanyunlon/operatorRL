"""
common/decorators/need_connection.py — 连接前置检查装饰器
============================================================

查看 Seraphine connector.py 上现有 @needLcu 装饰器的实现方式, 理解其模式,
特别是连接前置条件检查和业务逻辑是如何分离的。从 Seraphine 的 @needLcu
装饰器这个好例子开始。然后遵循该模式实现一个 @need_connection 装饰器,
让所有需要 LCU 连接的方法可以自动检查连接状态, 并能在未连接时触发自动
重连或抛出清晰的异常。

位置: lolbot-HyperAI/modules/common/decorators/need_connection.py
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, List, Optional, Protocol, Set,
    Tuple, Type, TypeVar, Union, runtime_checkable,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConnectionRequiredError(Exception):
    """方法需要连接但当前未连接时抛出."""

    def __init__(
        self,
        component: str = "",
        state: str = "",
        message: str = "",
    ) -> None:
        self.component = component
        self.state = state
        parts = [f"Connection required for '{component}'"]
        if state:
            parts.append(f"(current state: {state})")
        if message:
            parts.append(f": {message}")
        super().__init__(" ".join(parts))


class GameNotActiveError(ConnectionRequiredError):
    """需要游戏活跃但当前没有游戏时抛出."""


class ConnectionDegradedWarning(UserWarning):
    """连接处于降级状态 (如 stale) 时发出警告."""


# ---------------------------------------------------------------------------
# Connectable protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Connectable(Protocol):
    """实现此协议的对象可以被 @need_connection 检查.

    任何有 is_connected / state / connect() 的对象都满足.
    """

    @property
    def is_connected(self) -> bool: ...

    @property
    def state(self) -> Any: ...

    def connect(self) -> bool: ...


# ---------------------------------------------------------------------------
# ConnectionGuard — 管理自动重连逻辑
# ---------------------------------------------------------------------------

@dataclass
class ConnectionGuardConfig:
    """ConnectionGuard 配置.

    Attributes:
        auto_reconnect: 断开时是否自动尝试重连.
        max_reconnect_attempts: 自动重连最大尝试次数.
        reconnect_interval_s: 重连间隔.
        required_states: 允许通过检查的状态名集合.
        degraded_states: 降级但可通过的状态名集合 (发出警告).
        game_required: 是否要求游戏活跃.
    """
    auto_reconnect: bool = True
    max_reconnect_attempts: int = 3
    reconnect_interval_s: float = 1.0
    required_states: Set[str] = field(default_factory=lambda: {
        "CONNECTED", "GAME_ACTIVE",
    })
    degraded_states: Set[str] = field(default_factory=lambda: {
        "STALE",
    })
    game_required: bool = False


class ConnectionGuard:
    """连接守卫: 检查连接状态, 必要时自动重连.

    与 Seraphine @needLcu 的对应关系:
    - Seraphine needLcu: 检查 connector.available → 否则抛异常
    - 我们的 Guard: 检查 Connectable.is_connected → 否则重连或抛异常

    Usage::

        guard = ConnectionGuard(connector, config)

        @guard.require
        def fetch_game_data():
            return connector.get_live("/liveclientdata/allgamedata")
    """

    def __init__(
        self,
        connectable: Any,
        config: Optional[ConnectionGuardConfig] = None,
        name: str = "default",
    ) -> None:
        self._connectable = connectable
        self._config = config or ConnectionGuardConfig()
        self._name = name
        self._lock = threading.Lock()
        self._reconnect_count: int = 0
        self._last_reconnect_time: float = 0.0
        self._check_count: int = 0
        self._block_count: int = 0
        self._reconnect_success: int = 0
        self._reconnect_fail: int = 0

    def check(self) -> bool:
        """检查连接状态, 必要时自动重连.

        Returns:
            True 如果可以继续执行.

        Raises:
            ConnectionRequiredError: 无法建立连接.
            GameNotActiveError: 需要游戏但没有游戏.
        """
        self._check_count += 1
        obj = self._connectable

        # 获取当前状态
        state_name = self._get_state_name(obj)

        # 状态在允许列表中 → 通过
        if state_name in self._config.required_states:
            if self._config.game_required and not self._is_game_active(obj):
                raise GameNotActiveError(
                    component=self._name, state=state_name,
                    message="游戏未在进行中",
                )
            return True

        # 状态在降级列表中 → 警告但通过
        if state_name in self._config.degraded_states:
            if self._check_count % 100 == 1:  # 不频繁日志
                logger.warning(
                    "[Guard:%s] 连接降级 (state=%s)", self._name, state_name,
                )
            return True

        # 未连接 → 尝试自动重连
        if self._config.auto_reconnect:
            return self._try_reconnect(obj, state_name)

        # 不自动重连 → 直接失败
        self._block_count += 1
        raise ConnectionRequiredError(
            component=self._name, state=state_name,
            message="auto_reconnect disabled",
        )

    def _try_reconnect(self, obj: Any, state_name: str) -> bool:
        """尝试自动重连."""
        with self._lock:
            now = time.monotonic()
            if now - self._last_reconnect_time < self._config.reconnect_interval_s:
                raise ConnectionRequiredError(
                    component=self._name, state=state_name,
                    message="重连冷却中",
                )

            self._last_reconnect_time = now

            for attempt in range(1, self._config.max_reconnect_attempts + 1):
                self._reconnect_count += 1
                logger.info(
                    "[Guard:%s] 自动重连 attempt %d/%d",
                    self._name, attempt,
                    self._config.max_reconnect_attempts,
                )
                try:
                    if hasattr(obj, "connect") and callable(obj.connect):
                        result = obj.connect()
                        if result:
                            self._reconnect_success += 1
                            logger.info(
                                "[Guard:%s] 重连成功", self._name,
                            )
                            return True
                except Exception as exc:
                    logger.warning(
                        "[Guard:%s] 重连失败: %s", self._name, exc,
                    )

                if attempt < self._config.max_reconnect_attempts:
                    time.sleep(self._config.reconnect_interval_s)

            self._reconnect_fail += 1
            self._block_count += 1
            raise ConnectionRequiredError(
                component=self._name, state=state_name,
                message=f"自动重连 {self._config.max_reconnect_attempts} 次失败",
            )

    def _get_state_name(self, obj: Any) -> str:
        """从对象获取状态名."""
        if hasattr(obj, "state"):
            state = obj.state
            if hasattr(state, "name"):
                return state.name
            return str(state)
        return "UNKNOWN"

    def _is_game_active(self, obj: Any) -> bool:
        """检查游戏是否活跃."""
        if hasattr(obj, "is_game_active"):
            return bool(obj.is_game_active)
        state_name = self._get_state_name(obj)
        return state_name == "GAME_ACTIVE"

    def require(self, func: F) -> F:
        """装饰器: 在函数执行前检查连接状态.

        Seraphine @needLcu 等价物.

        Usage::

            @guard.require
            def fetch():
                ...
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.check()
            return func(*args, **kwargs)
        return wrapper  # type: ignore[return-value]

    def require_game(self, func: F) -> F:
        """装饰器: 要求游戏活跃.

        比 require 更严格: 不仅要连接, 还要有活跃游戏.
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self._check_count += 1
            obj = self._connectable
            state_name = self._get_state_name(obj)

            if not self._is_game_active(obj):
                if state_name in self._config.required_states:
                    raise GameNotActiveError(
                        component=self._name, state=state_name,
                        message="方法需要游戏活跃",
                    )
                self._try_reconnect(obj, state_name)
                if not self._is_game_active(obj):
                    raise GameNotActiveError(
                        component=self._name,
                        state=self._get_state_name(obj),
                        message="重连后仍无活跃游戏",
                    )
            return func(*args, **kwargs)
        return wrapper  # type: ignore[return-value]

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "check_count": self._check_count,
            "block_count": self._block_count,
            "reconnect_attempts": self._reconnect_count,
            "reconnect_success": self._reconnect_success,
            "reconnect_fail": self._reconnect_fail,
        }


# ---------------------------------------------------------------------------
# Convenience: standalone decorators
# ---------------------------------------------------------------------------

def need_connection(
    connectable_attr: str = "_connection_manager",
    auto_reconnect: bool = True,
    game_required: bool = False,
) -> Callable[[F], F]:
    """类方法装饰器: 从 self 上获取 connectable 并检查.

    Usage::

        class MyComponent:
            def __init__(self):
                self._connection_manager = ConnectionManager(...)

            @need_connection("_connection_manager")
            def fetch_data(self):
                ...

            @need_connection("_connection_manager", game_required=True)
            def fetch_game_data(self):
                ...
    """
    config = ConnectionGuardConfig(
        auto_reconnect=auto_reconnect,
        game_required=game_required,
    )

    def decorator(func: F) -> F:
        # 每个 (instance, func) 对应一个 Guard, 用 WeakKeyDictionary 避免泄漏
        guards: Dict[int, ConnectionGuard] = {}

        @functools.wraps(func)
        def wrapper(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
            obj_id = id(self_obj)
            if obj_id not in guards:
                connectable = getattr(self_obj, connectable_attr, None)
                if connectable is None:
                    raise AttributeError(
                        f"{type(self_obj).__name__} 没有属性 "
                        f"'{connectable_attr}'"
                    )
                guards[obj_id] = ConnectionGuard(
                    connectable, config,
                    name=f"{type(self_obj).__name__}.{func.__name__}",
                )
            guards[obj_id].check()
            return func(self_obj, *args, **kwargs)

        return wrapper  # type: ignore[return-value]
    return decorator

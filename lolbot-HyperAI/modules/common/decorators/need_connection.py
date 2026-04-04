"""
common/decorators/need_connection.py — @needLcu / @needConnection guards
=========================================================================
lolbot-HyperAI · modules/common/decorators

查看 Seraphine connector.py 上现有 @needLcu 装饰器的实现方式, 理解其模式,
特别是连接状态检查和 HTTP 调用是如何通过装饰器分离的。从 Seraphine 的
@needLcu + @needSgp 双路径 这个好例子开始。然后遵循该模式实现连接状态
守卫装饰器, 让所有需要 LCU/Fiddler 连接的方法可以自动检查连接状态,
并能在断连时返回默认值或抛出标准异常。

功能清单:
1. @need_connection — 通用连接状态守卫
2. @need_lcu — LCU Live Client API 专用守卫
3. @need_fiddler — Fiddler MCP bridge 专用守卫
4. @need_game_active — 游戏进行中守卫
5. ConnectionNotReady — 连接未就绪异常
6. GameNotActive — 游戏未进行异常
7. connection_status — 连接状态查询协议

位置: lolbot-HyperAI/modules/common/decorators/need_connection.py
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, Optional, Tuple, Type, TypeVar, Union,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConnectionNotReady(Exception):
    """连接未就绪 — 被 @need_connection 拦截时抛出."""

    def __init__(
        self, source: str = "unknown",
        message: str = "Connection not ready",
    ) -> None:
        self.source = source
        super().__init__(f"[{source}] {message}")


class GameNotActive(Exception):
    """游戏未在进行中 — 被 @need_game_active 拦截时抛出."""

    def __init__(self, phase: str = "none") -> None:
        self.phase = phase
        super().__init__(
            f"Game not active (current phase: {phase})"
        )


# ---------------------------------------------------------------------------
# Connection status protocol
# ---------------------------------------------------------------------------

class ConnectionState(Enum):
    """Standard connection states for data sources."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    GAME_NOT_ACTIVE = auto()
    ERROR = auto()
    RECONNECTING = auto()


@dataclass
class ConnectionInfo:
    """Connection status snapshot returned by connection_status()."""
    source: str = "unknown"
    state: ConnectionState = ConnectionState.DISCONNECTED
    connected: bool = False
    game_active: bool = False
    last_success_time: float = 0.0
    last_error: str = ""
    consecutive_errors: int = 0

    @property
    def age_since_success_s(self) -> float:
        """Seconds since last successful connection."""
        if self.last_success_time <= 0:
            return float("inf")
        return time.monotonic() - self.last_success_time

    @property
    def is_stale(self) -> bool:
        """Connection is stale if no success in last 10 seconds."""
        return self.age_since_success_s > 10.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "state": self.state.name,
            "connected": self.connected,
            "game_active": self.game_active,
            "age_since_success_s": round(self.age_since_success_s, 1),
            "last_error": self.last_error,
            "consecutive_errors": self.consecutive_errors,
        }


# ---------------------------------------------------------------------------
# Connection status resolver
# ---------------------------------------------------------------------------

def _resolve_connection_info(
    obj: Any,
    source: str,
) -> Optional[ConnectionInfo]:
    """Try to extract ConnectionInfo from an object.

    Checks in order:
    1. obj.connection_status() method
    2. obj._connection_info attribute
    3. obj._connected / obj.connected bool
    """
    # Method
    if hasattr(obj, "connection_status"):
        try:
            info = obj.connection_status()
            if isinstance(info, ConnectionInfo):
                return info
            if isinstance(info, dict):
                return ConnectionInfo(
                    source=source,
                    connected=info.get("connected", False),
                    game_active=info.get("game_active", False),
                )
        except Exception:
            pass

    # Attribute
    if hasattr(obj, "_connection_info"):
        info = obj._connection_info
        if isinstance(info, ConnectionInfo):
            return info

    # Bool fallback
    connected = getattr(obj, "_connected", None)
    if connected is None:
        connected = getattr(obj, "connected", None)
    if connected is not None:
        return ConnectionInfo(
            source=source,
            connected=bool(connected),
        )

    return None


# ---------------------------------------------------------------------------
# Guard call tracking
# ---------------------------------------------------------------------------

@dataclass
class GuardStats:
    """Statistics for connection guard invocations."""
    total_calls: int = 0
    total_allowed: int = 0
    total_blocked: int = 0
    total_default_returned: int = 0
    last_block_time: float = 0.0
    last_block_reason: str = ""

    def snapshot(self) -> Dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_allowed": self.total_allowed,
            "total_blocked": self.total_blocked,
            "total_default_returned": self.total_default_returned,
            "block_rate": (
                round(self.total_blocked / self.total_calls, 3)
                if self.total_calls > 0 else 0.0
            ),
            "last_block_reason": self.last_block_reason,
        }


# ---------------------------------------------------------------------------
# @need_connection — generic connection guard
# ---------------------------------------------------------------------------

_SENTINEL = object()


def need_connection(
    source: str = "lcu",
    default: Any = _SENTINEL,
    check_game_active: bool = False,
    attr_name: str = "",
    log_block: bool = True,
    stats: Optional[GuardStats] = None,
) -> Callable[[F], F]:
    """Connection state guard decorator.

    Before calling the decorated function, checks that the data source
    connection is alive. If not connected, either returns `default`
    (if provided) or raises ConnectionNotReady.

    The decorator resolves connection state by inspecting `self` (first arg):
    1. self.connection_status() → ConnectionInfo
    2. self._connection_info → ConnectionInfo
    3. self._connected → bool

    Args:
        source: Name of the data source (for error messages).
        default: Value to return when not connected (if not set, raises).
        check_game_active: Also require game to be actively running.
        attr_name: Alternative attribute to check (e.g. "_lcu_connected").
        log_block: Whether to log blocked calls.
        stats: Optional GuardStats collector.

    Usage::

        @need_connection(source="lcu", default=None)
        def get_player_list(self):
            return self._http_get("/liveclientdata/playerlist")

        @need_connection(source="lcu", check_game_active=True)
        def get_active_game_data(self):
            return self._http_get("/liveclientdata/allgamedata")
    """
    _stats = stats

    def decorator(func: F) -> F:
        _local_stats = _stats or GuardStats()
        is_async = asyncio.iscoroutinefunction(func)

        def _check(obj: Any) -> Tuple[bool, str]:
            """Returns (allowed, reason)."""
            # Custom attribute check
            if attr_name:
                val = getattr(obj, attr_name, None)
                if not val:
                    return False, f"{attr_name} is falsy"
                if check_game_active:
                    game = getattr(obj, "_game_active", None)
                    if game is None:
                        game = getattr(obj, "game_active", None)
                    if not game:
                        return False, "game not active"
                return True, ""

            info = _resolve_connection_info(obj, source)
            if info is None:
                return True, ""  # Can't determine — allow

            if not info.connected:
                return False, f"{source} not connected"
            if check_game_active and not info.game_active:
                return False, f"game not active (source={source})"
            if info.is_stale:
                return False, f"{source} connection stale"
            return True, ""

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                _local_stats.total_calls += 1
                obj = args[0] if args else None
                allowed, reason = _check(obj) if obj else (True, "")

                if not allowed:
                    _local_stats.total_blocked += 1
                    _local_stats.last_block_time = time.monotonic()
                    _local_stats.last_block_reason = reason
                    if log_block:
                        logger.debug(
                            "[need_connection] %s blocked: %s",
                            func.__qualname__, reason,
                        )
                    if default is not _SENTINEL:
                        _local_stats.total_default_returned += 1
                        return default
                    raise ConnectionNotReady(source, reason)

                _local_stats.total_allowed += 1
                return await func(*args, **kwargs)

            async_wrapper.guard_stats = _local_stats  # type: ignore
            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            _local_stats.total_calls += 1
            obj = args[0] if args else None
            allowed, reason = _check(obj) if obj else (True, "")

            if not allowed:
                _local_stats.total_blocked += 1
                _local_stats.last_block_time = time.monotonic()
                _local_stats.last_block_reason = reason
                if log_block:
                    logger.debug(
                        "[need_connection] %s blocked: %s",
                        func.__qualname__, reason,
                    )
                if default is not _SENTINEL:
                    _local_stats.total_default_returned += 1
                    return default
                raise ConnectionNotReady(source, reason)

            _local_stats.total_allowed += 1
            return func(*args, **kwargs)

        sync_wrapper.guard_stats = _local_stats  # type: ignore
        return sync_wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Convenience aliases (Seraphine-style)
# ---------------------------------------------------------------------------

def need_lcu(
    default: Any = _SENTINEL,
    check_game_active: bool = False,
    log_block: bool = True,
) -> Callable[[F], F]:
    """LCU Live Client API connection guard.

    Usage::

        @need_lcu(default=None)
        def get_active_player(self):
            return self._http_get("/liveclientdata/activeplayer")
    """
    return need_connection(
        source="lcu",
        default=default,
        check_game_active=check_game_active,
        log_block=log_block,
    )


def need_fiddler(
    default: Any = _SENTINEL,
    log_block: bool = True,
) -> Callable[[F], F]:
    """Fiddler MCP bridge connection guard.

    Usage::

        @need_fiddler(default={})
        def get_network_captures(self):
            return self._fiddler_get("/captures")
    """
    return need_connection(
        source="fiddler",
        default=default,
        check_game_active=False,
        attr_name="_fiddler_connected",
        log_block=log_block,
    )


def need_game_active(
    default: Any = _SENTINEL,
    log_block: bool = False,
) -> Callable[[F], F]:
    """Game-in-progress guard (independent of data source).

    Usage::

        @need_game_active(default=None)
        def compute_win_probability(self):
            return self._model.predict(self._features)
    """
    return need_connection(
        source="game",
        default=default,
        check_game_active=True,
        log_block=log_block,
    )


# ---------------------------------------------------------------------------
# async_need_connection — async version (Claude11 addition)
# ---------------------------------------------------------------------------

def async_need_connection(
    connectable_attr: str = "_connection_manager",
    auto_reconnect: bool = True,
    game_required: bool = False,
) -> Callable:
    """Async version of need_connection for coroutine methods."""
    config = ConnectionGuardConfig(auto_reconnect=auto_reconnect, game_required=game_required)
    def decorator(func: F) -> F:
        guards: Dict[int, ConnectionGuard] = {}
        @functools.wraps(func)
        async def wrapper(self_obj: Any, *args: Any, **kwargs: Any) -> Any:
            oid = id(self_obj)
            if oid not in guards:
                conn = getattr(self_obj, connectable_attr, None)
                if conn is None:
                    raise AttributeError(f"{type(self_obj).__name__} has no '{connectable_attr}'")
                guards[oid] = ConnectionGuard(conn, config,
                    name=f"{type(self_obj).__name__}.{func.__name__}")
            guards[oid].check()
            return await func(self_obj, *args, **kwargs)
        return wrapper  # type: ignore
    return decorator

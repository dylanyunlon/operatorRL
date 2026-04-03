"""
common/decorators/retry.py — 重试装饰器 + 断路器
===================================================

查看 Seraphine connector.py 上现有 @retry 装饰器的实现方式, 理解其模式,
特别是重试逻辑和业务代码是如何通过装饰器分离的。从 Seraphine 的
@retry(count=3, backoff=0.5) 这个好例子开始。然后遵循该模式实现一个通用
的 @retry 装饰器, 让所有 HTTP 调用可以统一添加重试/backoff/jitter, 并能
按异常类型决定是否重试。接着引入断路器模式, 使持续失败的调用能够快速失败
而不浪费资源。

位置: lolbot-HyperAI/modules/common/decorators/retry.py
"""

from __future__ import annotations

import functools
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any, Callable, Dict, Optional, Set, Tuple, Type, TypeVar, Union,
)

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Retryable / non-retryable exception classification
# ---------------------------------------------------------------------------

# 默认可重试异常 (网络/IO 类)
DEFAULT_RETRYABLE: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

# 默认不可重试异常 (编程错误)
DEFAULT_NON_RETRYABLE: Tuple[Type[Exception], ...] = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    NotImplementedError,
)


def _is_retryable(
    exc: Exception,
    retryable: Tuple[Type[Exception], ...],
    non_retryable: Tuple[Type[Exception], ...],
) -> bool:
    """判断异常是否应该重试."""
    if isinstance(exc, non_retryable):
        return False
    if isinstance(exc, retryable):
        return True
    # urllib.error.URLError 需要检查内层 reason
    if hasattr(exc, "reason"):
        reason = str(exc.reason)
        if "Connection refused" in reason or "timed out" in reason:
            return True
    return False


# ---------------------------------------------------------------------------
# Backoff strategies
# ---------------------------------------------------------------------------

class BackoffStrategy(Enum):
    FIXED = auto()
    LINEAR = auto()
    EXPONENTIAL = auto()


def _compute_delay(
    strategy: BackoffStrategy,
    base_s: float,
    attempt: int,
    maximum_s: float,
    jitter: bool,
) -> float:
    """计算第 N 次重试的等待时长."""
    if strategy == BackoffStrategy.FIXED:
        delay = base_s
    elif strategy == BackoffStrategy.LINEAR:
        delay = base_s * attempt
    elif strategy == BackoffStrategy.EXPONENTIAL:
        delay = base_s * (2 ** (attempt - 1))
    else:
        delay = base_s

    delay = min(delay, maximum_s)

    if jitter:
        # Full jitter: [0, delay]
        delay = random.uniform(0, delay)

    return delay


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry(
    max_attempts: int = 3,
    backoff_base_s: float = 0.3,
    backoff_max_s: float = 10.0,
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL,
    jitter: bool = True,
    retryable_exceptions: Tuple[Type[Exception], ...] = DEFAULT_RETRYABLE,
    non_retryable_exceptions: Tuple[Type[Exception], ...] = DEFAULT_NON_RETRYABLE,
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
    reraise: bool = True,
) -> Callable[[F], F]:
    """通用重试装饰器.

    Seraphine 模式: 将重试策略从业务逻辑中分离, 通过装饰器声明.

    Args:
        max_attempts: 最大尝试次数 (含首次).
        backoff_base_s: 基础退避时长.
        backoff_max_s: 最大退避时长.
        strategy: 退避策略 (FIXED/LINEAR/EXPONENTIAL).
        jitter: 是否添加随机抖动.
        retryable_exceptions: 可重试的异常类型.
        non_retryable_exceptions: 不可重试的异常类型.
        on_retry: 重试时的回调 fn(attempt, exc, delay).
        reraise: 耗尽重试后是否重新抛出异常.

    Usage::

        @retry(max_attempts=3, backoff_base_s=0.5)
        def fetch_game_data(url: str) -> dict:
            return http_get(url)

        @retry(max_attempts=5, strategy=BackoffStrategy.LINEAR)
        async def async_fetch(url: str) -> dict:
            return await aiohttp_get(url)
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc

                    if not _is_retryable(
                        exc, retryable_exceptions, non_retryable_exceptions
                    ):
                        logger.debug(
                            "[retry] %s: 不可重试异常 %s, 直接抛出",
                            func.__qualname__, type(exc).__name__,
                        )
                        raise

                    if attempt >= max_attempts:
                        break

                    delay = _compute_delay(
                        strategy, backoff_base_s, attempt,
                        backoff_max_s, jitter,
                    )

                    logger.warning(
                        "[retry] %s: attempt %d/%d failed (%s: %s), "
                        "retrying in %.2fs",
                        func.__qualname__, attempt, max_attempts,
                        type(exc).__name__, exc, delay,
                    )

                    if on_retry is not None:
                        try:
                            on_retry(attempt, exc, delay)
                        except Exception:
                            pass

                    time.sleep(delay)

            # 耗尽所有重试
            logger.error(
                "[retry] %s: 全部 %d 次尝试失败",
                func.__qualname__, max_attempts,
            )
            if reraise and last_exc is not None:
                raise last_exc
            return None

        return wrapper  # type: ignore[return-value]
    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(Enum):
    """断路器状态."""
    CLOSED = auto()      # 正常: 允许请求通过
    OPEN = auto()        # 熔断: 快速失败
    HALF_OPEN = auto()   # 半开: 允许试探请求


@dataclass
class CircuitBreakerConfig:
    """断路器配置.

    Attributes:
        failure_threshold: 连续失败多少次后熔断.
        recovery_timeout_s: 熔断持续时间 (秒), 之后进入半开.
        half_open_max_calls: 半开状态允许的试探请求数.
        success_threshold: 半开状态连续成功多少次后恢复.
    """
    failure_threshold: int = 5
    recovery_timeout_s: float = 30.0
    half_open_max_calls: int = 1
    success_threshold: int = 2


class CircuitBreaker:
    """断路器: 持续失败时快速失败, 避免浪费资源.

    Usage::

        breaker = CircuitBreaker()

        @breaker.protect
        def fetch_data():
            return http_get(url)

        # 或手动:
        if breaker.allow_request():
            try:
                result = fetch_data()
                breaker.record_success()
            except Exception:
                breaker.record_failure()
        else:
            # fast-fail
            pass
    """

    def __init__(
        self,
        config: Optional[CircuitBreakerConfig] = None,
        name: str = "default",
    ) -> None:
        self._config = config or CircuitBreakerConfig()
        self._name = name
        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float = 0.0
        self._half_open_calls: int = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._check_state_transition()
            return self._state

    def allow_request(self) -> bool:
        """检查是否允许请求通过."""
        with self._lock:
            self._check_state_transition()

            if self._state == CircuitState.CLOSED:
                return True

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls < self._config.half_open_max_calls:
                    self._half_open_calls += 1
                    return True
                return False

            # OPEN
            return False

    def record_success(self) -> None:
        """记录成功请求."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._transition(CircuitState.CLOSED)
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info(
                        "[CircuitBreaker:%s] 恢复 → CLOSED", self._name
                    )
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self) -> None:
        """记录失败请求."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                self._transition(CircuitState.OPEN)
                self._success_count = 0
                logger.warning(
                    "[CircuitBreaker:%s] 半开失败 → OPEN", self._name
                )

            elif self._state == CircuitState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    self._transition(CircuitState.OPEN)
                    logger.warning(
                        "[CircuitBreaker:%s] 熔断 → OPEN "
                        "(连续 %d 次失败)",
                        self._name, self._failure_count,
                    )

    def reset(self) -> None:
        """手动重置断路器."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0

    def _check_state_transition(self) -> None:
        """检查是否应该从 OPEN 转换到 HALF_OPEN."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._config.recovery_timeout_s:
                self._transition(CircuitState.HALF_OPEN)
                self._half_open_calls = 0
                self._success_count = 0
                logger.info(
                    "[CircuitBreaker:%s] 恢复超时到期 → HALF_OPEN",
                    self._name,
                )

    def _transition(self, new: CircuitState) -> None:
        self._state = new

    def protect(self, func: F) -> F:
        """装饰器: 将函数包装在断路器保护下.

        Usage::

            @breaker.protect
            def risky_call():
                ...
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not self.allow_request():
                raise CircuitOpenError(
                    f"CircuitBreaker '{self._name}' is OPEN, "
                    f"fast-failing"
                )
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as exc:
                self.record_failure()
                raise

        return wrapper  # type: ignore[return-value]

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "state": self._state.name,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
            }


class CircuitOpenError(Exception):
    """断路器处于 OPEN 状态时抛出."""

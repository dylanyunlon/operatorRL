"""
common/decorators/retry.py — Seraphine-style retry + circuit breaker
=====================================================================
lolbot-HyperAI · modules/common/decorators

查看 Seraphine connector.py 上现有 @retry 装饰器的实现方式, 理解其模式,
特别是重试逻辑和业务代码是如何通过装饰器分离的。从 Seraphine 的
@retry(count=3, backoff=0.5) 这个好例子开始。然后遵循该模式实现一个通用
的 @retry 装饰器, 让所有 HTTP/LCU 调用可以统一添加重试/backoff/jitter,
并能按异常类型决定是否重试。接着引入断路器模式, 使持续失败的调用能够
快速失败而不浪费资源。

功能清单:
1. @retry — 同步重试装饰器 (count, backoff, jitter, retryable exceptions)
2. @async_retry — 异步重试装饰器 (与 @retry 同参, 用于 async def)
3. RetryPolicy — 可复用的重试策略对象
4. CircuitBreaker — 独立断路器 (CLOSED/OPEN/HALF_OPEN)
5. CircuitOpenError — 断路器打开时抛出
6. BackoffStrategy — 指数/线性/常量退避策略
7. RetryStats — 统计重试次数/成功率

位置: lolbot-HyperAI/modules/common/decorators/retry.py
"""

from __future__ import annotations

import asyncio
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
# Retryable exception classification
# ---------------------------------------------------------------------------

DEFAULT_RETRYABLE: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
    IOError,
)

DEFAULT_NON_RETRYABLE: Tuple[Type[Exception], ...] = (
    ValueError,
    TypeError,
    KeyError,
    AttributeError,
    NotImplementedError,
    PermissionError,
)


def _is_retryable(
    exc: Exception,
    retryable: Tuple[Type[Exception], ...],
    non_retryable: Tuple[Type[Exception], ...],
) -> bool:
    """Determine if an exception should trigger a retry."""
    if isinstance(exc, non_retryable):
        return False
    if isinstance(exc, retryable):
        return True
    # urllib.error fallback check (no direct import dependency)
    exc_name = type(exc).__name__
    if exc_name in ("URLError", "HTTPError", "HTTPException"):
        return True
    return False


# ---------------------------------------------------------------------------
# Backoff strategies
# ---------------------------------------------------------------------------

class BackoffKind(Enum):
    """Backoff strategy types."""
    CONSTANT = "constant"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


def compute_backoff(
    attempt: int,
    base_s: float,
    kind: BackoffKind = BackoffKind.EXPONENTIAL,
    max_s: float = 60.0,
    jitter: bool = True,
) -> float:
    """Compute delay before next retry attempt.

    Args:
        attempt: Zero-based attempt index (0 = first retry).
        base_s: Base delay in seconds.
        kind: Backoff strategy.
        max_s: Maximum delay cap.
        jitter: Add random jitter (±25%).

    Returns:
        Delay in seconds.
    """
    if kind == BackoffKind.CONSTANT:
        delay = base_s
    elif kind == BackoffKind.LINEAR:
        delay = base_s * (attempt + 1)
    else:  # EXPONENTIAL
        delay = base_s * (2 ** attempt)

    delay = min(delay, max_s)

    if jitter and delay > 0:
        jitter_range = delay * 0.25
        delay += random.uniform(-jitter_range, jitter_range)
        delay = max(0.0, delay)

    return delay


# ---------------------------------------------------------------------------
# Retry statistics
# ---------------------------------------------------------------------------

@dataclass
class RetryStats:
    """Per-function retry statistics."""
    total_calls: int = 0
    total_retries: int = 0
    total_success: int = 0
    total_exhausted: int = 0
    total_non_retryable: int = 0
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False,
    )

    def record_call(self) -> None:
        with self._lock:
            self.total_calls += 1

    def record_retry(self) -> None:
        with self._lock:
            self.total_retries += 1

    def record_success(self) -> None:
        with self._lock:
            self.total_success += 1

    def record_exhausted(self) -> None:
        with self._lock:
            self.total_exhausted += 1

    def record_non_retryable(self) -> None:
        with self._lock:
            self.total_non_retryable += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_calls": self.total_calls,
                "total_retries": self.total_retries,
                "total_success": self.total_success,
                "total_exhausted": self.total_exhausted,
                "total_non_retryable": self.total_non_retryable,
                "avg_retries_per_call": (
                    round(self.total_retries / self.total_calls, 2)
                    if self.total_calls > 0 else 0.0
                ),
            }


# ---------------------------------------------------------------------------
# Retry policy (reusable config object)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RetryPolicy:
    """Immutable retry configuration.

    Create once, apply to many functions::

        LCU_RETRY = RetryPolicy(count=3, backoff_s=0.5)

        @retry(policy=LCU_RETRY)
        def get_game_data(): ...
    """
    count: int = 3
    backoff_s: float = 0.5
    max_backoff_s: float = 30.0
    backoff_kind: BackoffKind = BackoffKind.EXPONENTIAL
    jitter: bool = True
    retryable: Tuple[Type[Exception], ...] = DEFAULT_RETRYABLE
    non_retryable: Tuple[Type[Exception], ...] = DEFAULT_NON_RETRYABLE
    on_retry: Optional[Callable[[int, Exception], None]] = None


# Default policies for common use cases
LCU_RETRY = RetryPolicy(count=3, backoff_s=0.5, max_backoff_s=5.0)
NETWORK_RETRY = RetryPolicy(count=5, backoff_s=1.0, max_backoff_s=30.0)
FAST_RETRY = RetryPolicy(count=2, backoff_s=0.1, max_backoff_s=1.0)


# ---------------------------------------------------------------------------
# @retry decorator (synchronous)
# ---------------------------------------------------------------------------

def retry(
    count: Optional[int] = None,
    backoff_s: Optional[float] = None,
    max_backoff_s: float = 30.0,
    backoff_kind: BackoffKind = BackoffKind.EXPONENTIAL,
    jitter: bool = True,
    retryable: Optional[Tuple[Type[Exception], ...]] = None,
    non_retryable: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    policy: Optional[RetryPolicy] = None,
    stats: Optional[RetryStats] = None,
) -> Callable[[F], F]:
    """Synchronous retry decorator (Seraphine-style).

    Usage::

        @retry(count=3, backoff_s=0.5)
        def fetch_lcu_data():
            return urllib.request.urlopen(url).read()

        # Or with policy:
        @retry(policy=LCU_RETRY)
        def fetch_lcu_data(): ...

    Args:
        count: Maximum number of retry attempts.
        backoff_s: Base delay between retries.
        max_backoff_s: Maximum delay cap.
        backoff_kind: Backoff strategy (exponential/linear/constant).
        jitter: Add random jitter to delays.
        retryable: Exception types that trigger retry.
        non_retryable: Exception types that never retry.
        on_retry: Callback(attempt, exception) before each retry.
        policy: RetryPolicy object (overrides individual params).
        stats: Optional RetryStats collector.
    """
    # Resolve policy
    p = policy or RetryPolicy()
    _count = count if count is not None else p.count
    _backoff = backoff_s if backoff_s is not None else p.backoff_s
    _max_backoff = max_backoff_s if policy is None else p.max_backoff_s
    _kind = backoff_kind if policy is None else p.backoff_kind
    _jitter = jitter if policy is None else p.jitter
    _retryable = retryable or p.retryable
    _non_retryable = non_retryable or p.non_retryable
    _on_retry = on_retry or p.on_retry
    _stats = stats

    def decorator(func: F) -> F:
        _local_stats = _stats or RetryStats()

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _local_stats.record_call()
            last_exc: Optional[Exception] = None

            for attempt in range(_count + 1):  # attempt 0 = first try
                try:
                    result = func(*args, **kwargs)
                    _local_stats.record_success()
                    return result
                except Exception as exc:
                    last_exc = exc

                    if not _is_retryable(exc, _retryable, _non_retryable):
                        _local_stats.record_non_retryable()
                        raise

                    if attempt >= _count:
                        break  # exhausted

                    _local_stats.record_retry()
                    delay = compute_backoff(
                        attempt, _backoff, _kind, _max_backoff, _jitter,
                    )

                    logger.debug(
                        "[retry] %s attempt %d/%d failed: %s, "
                        "retrying in %.2fs",
                        func.__qualname__, attempt + 1, _count,
                        exc, delay,
                    )

                    if _on_retry:
                        try:
                            _on_retry(attempt, exc)
                        except Exception:
                            pass

                    if delay > 0:
                        time.sleep(delay)

            _local_stats.record_exhausted()
            raise last_exc  # type: ignore[misc]

        wrapper.retry_stats = _local_stats  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# @async_retry decorator (asyncio)
# ---------------------------------------------------------------------------

def async_retry(
    count: Optional[int] = None,
    backoff_s: Optional[float] = None,
    max_backoff_s: float = 30.0,
    backoff_kind: BackoffKind = BackoffKind.EXPONENTIAL,
    jitter: bool = True,
    retryable: Optional[Tuple[Type[Exception], ...]] = None,
    non_retryable: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
    policy: Optional[RetryPolicy] = None,
    stats: Optional[RetryStats] = None,
) -> Callable[[F], F]:
    """Async retry decorator — same API as @retry but for coroutines.

    Usage::

        @async_retry(count=3, backoff_s=0.5)
        async def fetch_lcu_data():
            return await aiohttp_session.get(url)
    """
    p = policy or RetryPolicy()
    _count = count if count is not None else p.count
    _backoff = backoff_s if backoff_s is not None else p.backoff_s
    _max_backoff = max_backoff_s if policy is None else p.max_backoff_s
    _kind = backoff_kind if policy is None else p.backoff_kind
    _jitter = jitter if policy is None else p.jitter
    _retryable = retryable or p.retryable
    _non_retryable = non_retryable or p.non_retryable
    _on_retry = on_retry or p.on_retry
    _stats = stats

    def decorator(func: F) -> F:
        _local_stats = _stats or RetryStats()

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            _local_stats.record_call()
            last_exc: Optional[Exception] = None

            for attempt in range(_count + 1):
                try:
                    result = await func(*args, **kwargs)
                    _local_stats.record_success()
                    return result
                except Exception as exc:
                    last_exc = exc

                    if not _is_retryable(exc, _retryable, _non_retryable):
                        _local_stats.record_non_retryable()
                        raise

                    if attempt >= _count:
                        break

                    _local_stats.record_retry()
                    delay = compute_backoff(
                        attempt, _backoff, _kind, _max_backoff, _jitter,
                    )

                    logger.debug(
                        "[async_retry] %s attempt %d/%d failed: %s, "
                        "retrying in %.2fs",
                        func.__qualname__, attempt + 1, _count,
                        exc, delay,
                    )

                    if _on_retry:
                        try:
                            _on_retry(attempt, exc)
                        except Exception:
                            pass

                    if delay > 0:
                        await asyncio.sleep(delay)

            _local_stats.record_exhausted()
            raise last_exc  # type: ignore[misc]

        wrapper.retry_stats = _local_stats  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Circuit Breaker (standalone, composable with @retry)
# ---------------------------------------------------------------------------

class BreakerState(Enum):
    """Circuit breaker states."""
    CLOSED = auto()    # Normal operation
    OPEN = auto()      # Fast-failing
    HALF_OPEN = auto() # Probing with one request


class CircuitBreaker:
    """Standalone circuit breaker — wrap functions or use as decorator.

    Apollo's canbus uses a similar pattern: after N consecutive failures,
    stop polling and enter backoff to avoid flooding a dead endpoint.

    Usage as decorator::

        breaker = CircuitBreaker("lcu_api", threshold=5, cooldown_s=10)

        @breaker
        def poll_lcu():
            return urllib.request.urlopen(url).read()

    Usage as context manager::

        with breaker.guard():
            result = do_something()

    The breaker is thread-safe.
    """

    def __init__(
        self,
        name: str = "default",
        threshold: int = 5,
        cooldown_s: float = 10.0,
        max_cooldown_s: float = 120.0,
        half_open_max: int = 1,
    ) -> None:
        self._name = name
        self._threshold = threshold
        self._base_cooldown = cooldown_s
        self._max_cooldown = max_cooldown_s
        self._current_cooldown = cooldown_s
        self._half_open_max = half_open_max
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0
        self._trip_count = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state

    @property
    def is_closed(self) -> bool:
        return self.state == BreakerState.CLOSED

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        with self._lock:
            if self._state == BreakerState.CLOSED:
                return True
            if self._state == BreakerState.OPEN:
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self._current_cooldown:
                    self._state = BreakerState.HALF_OPEN
                    self._half_open_calls = 0
                    logger.info(
                        "[CircuitBreaker:%s] OPEN -> HALF_OPEN "
                        "(after %.1fs cooldown)",
                        self._name, elapsed,
                    )
                    return True
                return False
            # HALF_OPEN
            if self._half_open_calls < self._half_open_max:
                self._half_open_calls += 1
                return True
            return False

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            self._success_count += 1
            if self._state == BreakerState.HALF_OPEN:
                self._state = BreakerState.CLOSED
                self._failure_count = 0
                self._current_cooldown = self._base_cooldown
                logger.info(
                    "[CircuitBreaker:%s] HALF_OPEN -> CLOSED",
                    self._name,
                )
            elif self._state == BreakerState.CLOSED:
                self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()

            if self._state == BreakerState.HALF_OPEN:
                self._state = BreakerState.OPEN
                self._current_cooldown = min(
                    self._current_cooldown * 2, self._max_cooldown,
                )
                self._trip_count += 1
                logger.warning(
                    "[CircuitBreaker:%s] HALF_OPEN -> OPEN "
                    "(cooldown=%.1fs)",
                    self._name, self._current_cooldown,
                )
            elif (self._state == BreakerState.CLOSED
                  and self._failure_count >= self._threshold):
                self._state = BreakerState.OPEN
                self._trip_count += 1
                logger.warning(
                    "[CircuitBreaker:%s] CLOSED -> OPEN "
                    "(failures=%d, cooldown=%.1fs)",
                    self._name, self._failure_count,
                    self._current_cooldown,
                )

    def reset(self) -> None:
        """Force reset to CLOSED state."""
        with self._lock:
            self._state = BreakerState.CLOSED
            self._failure_count = 0
            self._current_cooldown = self._base_cooldown

    def __call__(self, func: F) -> F:
        """Use as decorator."""
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not self.allow_request():
                raise CircuitOpenError(
                    f"CircuitBreaker '{self._name}' is OPEN, "
                    f"fast-failing (trip #{self._trip_count})"
                )
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as exc:
                self.record_failure()
                raise

        wrapper.breaker = self  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    def guard(self) -> _BreakerGuard:
        """Use as context manager."""
        return _BreakerGuard(self)

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "state": self._state.name,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "trip_count": self._trip_count,
                "current_cooldown_s": self._current_cooldown,
            }


class _BreakerGuard:
    """Context manager for CircuitBreaker."""

    def __init__(self, breaker: CircuitBreaker) -> None:
        self._breaker = breaker

    def __enter__(self) -> _BreakerGuard:
        if not self._breaker.allow_request():
            raise CircuitOpenError(
                f"CircuitBreaker is OPEN, fast-failing"
            )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any,
                 exc_tb: Any) -> bool:
        if exc_type is None:
            self._breaker.record_success()
        else:
            self._breaker.record_failure()
        return False


class CircuitOpenError(Exception):
    """断路器处于 OPEN 状态时抛出."""

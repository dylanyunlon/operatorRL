"""
Circuit Breaker pattern for backend failure protection.

Prevents cascading failures by tracking backend errors and
short-circuiting calls when a failure threshold is exceeded.
"""

import enum
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")


class CircuitState(enum.Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing — reject calls
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreakerConfig:
    """Configuration for the circuit breaker.

    Args:
        failure_threshold: Number of consecutive failures before opening the circuit.
        reset_timeout_seconds: Seconds to wait before transitioning from OPEN to HALF_OPEN.
        half_open_max_calls: Max calls allowed in HALF_OPEN state before deciding.
        maturity_scaling: M40 - 是否启用成长阶段缩放 (命题7)
            启用后，failure_threshold会根据maturity_level动态调整
        base_maturity_level: M40 - 基准成长阶段级别
            用于计算阈值缩放因子
    """

    failure_threshold: int = 5
    reset_timeout_seconds: float = 30.0
    half_open_max_calls: int = 1
    # === M40: 成长阶段相关配置 (命题7: 小学到大学) ===
    maturity_scaling: bool = True  # 是否启用maturity_level缩放
    base_maturity_level: int = 0  # 基准成长阶段

    def get_effective_threshold(self, maturity_level: int = 0) -> int:
        """根据maturity_level计算有效的failure_threshold。
        
        === M40: 成长阶段缩放逻辑 ===
        - 低maturity (0-2): 严格阈值，少量错误即熔断
        - 中maturity (3-4): 中等阈值
        - 高maturity (5-6): 宽松阈值，允许更多探索失败
        
        公式: effective_threshold = base_threshold * (1 + maturity_level * 0.2)
        例如: base=5, maturity=6 → effective=5*(1+1.2)=11
        
        Args:
            maturity_level: 当前成长阶段级别 (0-6)
            
        Returns:
            有效的failure_threshold
        """
        if not self.maturity_scaling:
            return self.failure_threshold
        
        # 缩放因子: 1.0 (maturity=0) → 2.2 (maturity=6)
        scale_factor = 1.0 + (maturity_level * 0.2)
        effective = int(self.failure_threshold * scale_factor)
        
        return max(1, effective)  # 至少为1


class CircuitBreakerOpen(Exception):
    """Raised when a call is rejected because the circuit is OPEN."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"Circuit breaker is OPEN. Retry after {retry_after:.1f}s."
        )


class CircuitBreaker:
    """Circuit breaker for protecting backend calls.

    Usage::

        cb = CircuitBreaker()
        result = await cb.call(backend.get, "key")
        
    === M40: 成长阶段支持 (命题7) ===
    可以通过set_maturity_level()设置当前成长阶段，
    这会影响failure_threshold的有效值。
    """

    def __init__(self, config: Optional[CircuitBreakerConfig] = None) -> None:
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._half_open_calls: int = 0
        self._last_failure_time: float = 0.0
        # === M40: 当前成长阶段 ===
        self._maturity_level: int = 0

    def set_maturity_level(self, level: int) -> None:
        """设置当前成长阶段级别。
        
        Args:
            level: 成长阶段 (0-6)
        """
        self._maturity_level = max(0, min(6, level))

    def get_effective_threshold(self) -> int:
        """获取当前有效的failure_threshold。"""
        return self._config.get_effective_threshold(self._maturity_level)

    def get_state(self) -> CircuitState:
        """Return the current circuit state, transitioning OPEN→HALF_OPEN if timeout elapsed."""
        if self._state is CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self._config.reset_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
        return self._state

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute *func* through the circuit breaker.

        Args:
            func: An async callable to invoke.
            *args: Positional arguments forwarded to *func*.
            **kwargs: Keyword arguments forwarded to *func*.

        Returns:
            The return value of *func*.

        Raises:
            CircuitBreakerOpen: If the circuit is OPEN and the timeout has not elapsed.
        """
        state = self.get_state()

        if state is CircuitState.OPEN:
            retry_after = (
                self._config.reset_timeout_seconds
                - (time.monotonic() - self._last_failure_time)
            )
            raise CircuitBreakerOpen(max(retry_after, 0.0))

        if state is CircuitState.HALF_OPEN:
            if self._half_open_calls >= self._config.half_open_max_calls:
                raise CircuitBreakerOpen(self._config.reset_timeout_seconds)
            self._half_open_calls += 1

        try:
            result = await func(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise

        self.record_success()
        return result

    def record_success(self) -> None:
        """Record a successful call and reset the breaker if needed."""
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count += 1
        self._half_open_calls = 0

    def record_failure(self) -> None:
        """Record a failed call and open the circuit if threshold is reached.
        
        === M40: 使用有效阈值 ===
        failure_threshold会根据maturity_level动态调整。
        """
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        # M40: 使用有效阈值而非固定阈值
        effective_threshold = self.get_effective_threshold()
        if self._failure_count >= effective_threshold:
            self._state = CircuitState.OPEN
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._half_open_calls = 0

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._last_failure_time = 0.0

    def stats(self) -> dict[str, Any]:
        """获取熔断器统计信息。"""
        return {
            "state": self._state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
            "maturity_level": self._maturity_level,
            "effective_threshold": self.get_effective_threshold(),
            "base_threshold": self._config.failure_threshold,
        }

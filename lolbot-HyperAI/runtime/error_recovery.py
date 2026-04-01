#!/usr/bin/env python3
"""
ErrorRecovery — Circuit Breaker, Auto-Reconnect & Component Self-Healing
==========================================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Implements production-grade error recovery patterns:
  1. Circuit Breaker — prevents cascade failures by cutting off failing
     components after N consecutive errors, then testing recovery with
     half-open probes.
  2. Auto-Reconnect — handles Fiddler MCP / LCU / Riot API disconnections
     with exponential backoff and jitter.
  3. Component Self-Healing — restarts failed components with fresh state
     when the circuit breaker allows it.
  4. Fallback Chain — when primary data source fails, automatically switch
     to secondary (e.g., Fiddler → direct LCU → cached data).

Apollo Reference:
    cyber/transport/shm/condition_notifier.cc → failure detection
    modules/dreamview/backend/sim_control_manager → reconnection logic

Production Critique (Knuth-level):
    1. User: During a ranked game, if Fiddler crashes, the system switches
       to LCU polling within 500ms. The user hears "Switching to direct
       client connection" and predictions continue with slightly less data.
    2. System: The circuit breaker uses a sliding window (not just consecutive
       count) to avoid false trips from transient errors during teamfights
       when packet rates spike 10x. Half-open probes run at most once
       every 30 seconds to avoid hammering a recovering service.
"""

import asyncio
import enum
import logging
import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Coroutine, Deque, Dict, List, Optional, Set, Tuple, TypeVar
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitState(enum.Enum):
    """Martin Fowler's circuit breaker states."""
    CLOSED = "closed"          # Normal operation
    OPEN = "open"              # Failures exceeded threshold — blocking calls
    HALF_OPEN = "half_open"    # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker instance."""
    failure_threshold: int = 5          # Failures to trip the circuit
    success_threshold: int = 2          # Successes in half-open to close
    window_size: int = 20               # Sliding window for error rate
    error_rate_threshold: float = 0.5   # Trip if error rate exceeds this
    open_timeout_s: float = 30.0        # Time in OPEN before trying HALF_OPEN
    half_open_max_concurrent: int = 1   # Max concurrent probes in HALF_OPEN
    max_open_timeout_s: float = 300.0   # Cap on exponential backoff
    backoff_multiplier: float = 1.5     # Exponential backoff factor


class CircuitBreaker:
    """
    Sliding-window circuit breaker with exponential backoff.

    Usage:
        cb = CircuitBreaker("fiddler_mcp", config)
        try:
            result = await cb.call(fiddler_client.get_traffic)
        except CircuitOpenError:
            # Use fallback
            result = await lcu_client.get_state()
    """

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._log = logging.getLogger(f"lolbot.circuit_breaker.{name}")
        self._state = CircuitState.CLOSED
        self._results: Deque[bool] = deque(maxlen=self._config.window_size)
        self._consecutive_failures = 0
        self._consecutive_successes_half_open = 0
        self._last_failure_time = 0.0
        self._last_state_change = time.monotonic()
        self._current_open_timeout = self._config.open_timeout_s
        self._half_open_in_flight = 0
        self._total_calls = 0
        self._total_failures = 0
        self._total_trips = 0
        self._state_change_callbacks: List[
            Callable[[str, CircuitState, CircuitState], None]
        ] = []

    @property
    def state(self) -> CircuitState:
        # Check if OPEN should transition to HALF_OPEN
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_state_change
            if elapsed >= self._current_open_timeout:
                self._transition(CircuitState.HALF_OPEN)
        return self._state

    @property
    def name(self) -> str:
        return self._name

    def on_state_change(
        self, callback: Callable[[str, CircuitState, CircuitState], None]
    ) -> None:
        """Register callback for state transitions."""
        self._state_change_callbacks.append(callback)

    async def call(
        self,
        func: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute func through the circuit breaker.
        Raises CircuitOpenError if the circuit is open.
        """
        current_state = self.state  # Triggers OPEN→HALF_OPEN check

        if current_state == CircuitState.OPEN:
            raise CircuitOpenError(
                f"Circuit '{self._name}' is OPEN "
                f"(will retry in {self._remaining_open_time():.0f}s)"
            )

        if current_state == CircuitState.HALF_OPEN:
            if self._half_open_in_flight >= self._config.half_open_max_concurrent:
                raise CircuitOpenError(
                    f"Circuit '{self._name}' is HALF_OPEN, "
                    f"max concurrent probes reached"
                )
            self._half_open_in_flight += 1

        self._total_calls += 1

        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as exc:
            self._record_failure()
            raise
        finally:
            if current_state == CircuitState.HALF_OPEN:
                self._half_open_in_flight = max(0, self._half_open_in_flight - 1)

    def record_external_success(self) -> None:
        """Manually record a success (for passive health checks)."""
        self._record_success()

    def record_external_failure(self) -> None:
        """Manually record a failure."""
        self._record_failure()

    def force_open(self) -> None:
        """Manually open the circuit."""
        self._transition(CircuitState.OPEN)

    def force_close(self) -> None:
        """Manually close the circuit (reset)."""
        self._reset()

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "state": self.state.value,
            "total_calls": self._total_calls,
            "total_failures": self._total_failures,
            "total_trips": self._total_trips,
            "error_rate": self._error_rate(),
            "consecutive_failures": self._consecutive_failures,
            "remaining_open_s": (
                self._remaining_open_time()
                if self._state == CircuitState.OPEN else 0.0
            ),
        }

    # ---- Internal ----

    def _record_success(self) -> None:
        self._results.append(True)
        self._consecutive_failures = 0

        if self._state == CircuitState.HALF_OPEN:
            self._consecutive_successes_half_open += 1
            if self._consecutive_successes_half_open >= self._config.success_threshold:
                self._reset()
                self._log.info(
                    "Circuit '%s' recovered — closing after %d successes",
                    self._name, self._config.success_threshold,
                )

    def _record_failure(self) -> None:
        self._results.append(False)
        self._consecutive_failures += 1
        self._total_failures += 1
        self._last_failure_time = time.monotonic()

        if self._state == CircuitState.HALF_OPEN:
            # Any failure in half-open → reopen with increased timeout
            self._current_open_timeout = min(
                self._current_open_timeout * self._config.backoff_multiplier,
                self._config.max_open_timeout_s,
            )
            self._transition(CircuitState.OPEN)
            self._log.warning(
                "Circuit '%s' half-open probe failed — reopening for %.0fs",
                self._name, self._current_open_timeout,
            )
            return

        # Check trip conditions (CLOSED state)
        should_trip = False
        if self._consecutive_failures >= self._config.failure_threshold:
            should_trip = True
        elif len(self._results) >= self._config.window_size:
            if self._error_rate() >= self._config.error_rate_threshold:
                should_trip = True

        if should_trip:
            self._total_trips += 1
            self._transition(CircuitState.OPEN)
            self._log.warning(
                "Circuit '%s' TRIPPED — error_rate=%.2f consecutive=%d, "
                "open for %.0fs",
                self._name, self._error_rate(),
                self._consecutive_failures, self._current_open_timeout,
            )

    def _error_rate(self) -> float:
        if not self._results:
            return 0.0
        return sum(1 for r in self._results if not r) / len(self._results)

    def _remaining_open_time(self) -> float:
        if self._state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self._last_state_change
        return max(0.0, self._current_open_timeout - elapsed)

    def _transition(self, new_state: CircuitState) -> None:
        old_state = self._state
        if old_state == new_state:
            return
        self._state = new_state
        self._last_state_change = time.monotonic()
        self._consecutive_successes_half_open = 0

        for cb in self._state_change_callbacks:
            try:
                cb(self._name, old_state, new_state)
            except Exception:
                pass

    def _reset(self) -> None:
        """Full reset to CLOSED state."""
        self._transition(CircuitState.CLOSED)
        self._results.clear()
        self._consecutive_failures = 0
        self._consecutive_successes_half_open = 0
        self._current_open_timeout = self._config.open_timeout_s


class CircuitOpenError(Exception):
    """Raised when calling through an open circuit breaker."""
    pass


# ---------------------------------------------------------------------------
# Auto-Reconnect with exponential backoff + jitter
# ---------------------------------------------------------------------------

@dataclass
class ReconnectConfig:
    """Configuration for auto-reconnect behavior."""
    initial_delay_s: float = 0.5
    max_delay_s: float = 60.0
    backoff_factor: float = 2.0
    jitter_factor: float = 0.25     # ±25% jitter
    max_attempts: int = 0           # 0 = unlimited
    on_reconnect: Optional[Callable[[], Coroutine]] = None
    on_give_up: Optional[Callable[[], None]] = None


class AutoReconnect:
    """
    Manages reconnection attempts with exponential backoff and jitter.
    Used for Fiddler MCP, LCU, Riot API connections.

    Usage:
        reconnect = AutoReconnect("fiddler_mcp", config)
        async for attempt in reconnect.attempts():
            try:
                await fiddler.connect()
                reconnect.mark_connected()
                break
            except ConnectionError:
                pass  # AutoReconnect handles delay
    """

    def __init__(self, name: str, config: Optional[ReconnectConfig] = None):
        self._name = name
        self._config = config or ReconnectConfig()
        self._log = logging.getLogger(f"lolbot.reconnect.{name}")
        self._attempt = 0
        self._connected = False
        self._last_attempt = 0.0
        self._total_reconnects = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def attempt_count(self) -> int:
        return self._attempt

    def mark_connected(self) -> None:
        """Signal that connection was re-established."""
        if not self._connected:
            self._total_reconnects += 1
            self._log.info(
                "'%s' reconnected after %d attempts", self._name, self._attempt
            )
        self._connected = True
        self._attempt = 0

    def mark_disconnected(self) -> None:
        """Signal that connection was lost."""
        if self._connected:
            self._log.warning("'%s' disconnected", self._name)
        self._connected = False

    async def attempts(self):
        """
        Async generator yielding attempt numbers with backoff delays.

        Usage:
            async for attempt_num in reconnect.attempts():
                try:
                    await connect()
                    reconnect.mark_connected()
                    break
                except:
                    pass
        """
        self._attempt = 0
        while True:
            self._attempt += 1

            if 0 < self._config.max_attempts < self._attempt:
                self._log.error(
                    "'%s' giving up after %d attempts",
                    self._name, self._config.max_attempts,
                )
                if self._config.on_give_up:
                    self._config.on_give_up()
                return

            yield self._attempt

            if self._connected:
                return

            delay = self._compute_delay()
            self._log.info(
                "'%s' reconnect attempt %d failed — retrying in %.1fs",
                self._name, self._attempt, delay,
            )
            await asyncio.sleep(delay)

    def _compute_delay(self) -> float:
        """Exponential backoff with jitter."""
        base = self._config.initial_delay_s * (
            self._config.backoff_factor ** (self._attempt - 1)
        )
        capped = min(base, self._config.max_delay_s)
        jitter = capped * self._config.jitter_factor
        return capped + random.uniform(-jitter, jitter)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "connected": self._connected,
            "attempt": self._attempt,
            "total_reconnects": self._total_reconnects,
        }


# ---------------------------------------------------------------------------
# FallbackChain — cascading data source fallbacks
# ---------------------------------------------------------------------------

@dataclass
class FallbackSource:
    """One source in a fallback chain."""
    name: str
    provider: Callable[..., Coroutine]
    circuit_breaker: Optional[CircuitBreaker] = None
    priority: int = 0                   # Lower = preferred
    is_degraded: bool = False           # True for lower-quality sources


class FallbackChain:
    """
    Cascading fallback: try primary, if it fails try secondary, etc.

    Example:
        chain = FallbackChain("game_state")
        chain.add("fiddler_mcp", fiddler.get_state, circuit_breaker=cb_fiddler)
        chain.add("lcu_direct", lcu.get_state, circuit_breaker=cb_lcu, priority=10)
        chain.add("cached", cache.get_state, priority=20, is_degraded=True)

        result, source_name, degraded = await chain.execute()
    """

    def __init__(self, name: str):
        self._name = name
        self._log = logging.getLogger(f"lolbot.fallback.{name}")
        self._sources: List[FallbackSource] = []
        self._last_used_source: str = ""
        self._fallback_count: int = 0

    def add(
        self,
        name: str,
        provider: Callable,
        circuit_breaker: Optional[CircuitBreaker] = None,
        priority: int = 0,
        is_degraded: bool = False,
    ) -> None:
        """Add a source to the fallback chain."""
        self._sources.append(FallbackSource(
            name=name,
            provider=provider,
            circuit_breaker=circuit_breaker,
            priority=priority,
            is_degraded=is_degraded,
        ))
        self._sources.sort(key=lambda s: s.priority)

    async def execute(self, *args: Any, **kwargs: Any) -> Tuple[Any, str, bool]:
        """
        Try sources in priority order. Returns (result, source_name, is_degraded).
        Raises FallbackExhaustedError if all sources fail.
        """
        errors: List[Tuple[str, str]] = []

        for source in self._sources:
            # Check circuit breaker
            if source.circuit_breaker:
                state = source.circuit_breaker.state
                if state == CircuitState.OPEN:
                    errors.append((source.name, "circuit open"))
                    continue

            try:
                if source.circuit_breaker:
                    result = await source.circuit_breaker.call(
                        source.provider, *args, **kwargs
                    )
                else:
                    result = await source.provider(*args, **kwargs)

                if source.name != self._last_used_source:
                    if self._last_used_source:
                        self._fallback_count += 1
                        self._log.info(
                            "Fallback chain '%s': switched %s → %s%s",
                            self._name,
                            self._last_used_source,
                            source.name,
                            " (DEGRADED)" if source.is_degraded else "",
                        )
                    self._last_used_source = source.name

                return result, source.name, source.is_degraded

            except CircuitOpenError:
                errors.append((source.name, "circuit open"))
            except Exception as exc:
                errors.append((source.name, str(exc)))
                self._log.debug(
                    "Fallback source '%s' failed: %s", source.name, exc
                )

        error_summary = "; ".join(f"{n}: {e}" for n, e in errors)
        raise FallbackExhaustedError(
            f"All sources exhausted for '{self._name}': {error_summary}"
        )

    @property
    def current_source(self) -> str:
        return self._last_used_source

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self._name,
            "current_source": self._last_used_source,
            "fallback_count": self._fallback_count,
            "sources": [
                {
                    "name": s.name,
                    "priority": s.priority,
                    "is_degraded": s.is_degraded,
                    "circuit_state": (
                        s.circuit_breaker.state.value
                        if s.circuit_breaker else "none"
                    ),
                }
                for s in self._sources
            ],
        }


class FallbackExhaustedError(Exception):
    """Raised when all sources in a FallbackChain have failed."""
    pass


# ---------------------------------------------------------------------------
# ComponentHealer — restart failed components with backoff
# ---------------------------------------------------------------------------

class ComponentHealer:
    """
    Monitors component states and attempts to restart failed ones.
    Works with ProcessManager — when a component enters ERROR state,
    the healer schedules a restart attempt after a cooldown.

    This is the "self-healing" aspect of the agentic system.
    """

    def __init__(
        self,
        max_restarts_per_component: int = 5,
        restart_cooldown_s: float = 10.0,
        cooldown_multiplier: float = 2.0,
    ):
        self._log = logging.getLogger("lolbot.runtime.healer")
        self._max_restarts = max_restarts_per_component
        self._base_cooldown_s = restart_cooldown_s
        self._cooldown_multiplier = cooldown_multiplier

        self._restart_counts: Dict[str, int] = {}
        self._last_restart: Dict[str, float] = {}
        self._restart_callbacks: Dict[str, Callable[[], Coroutine]] = {}
        self._disabled_components: Set[str] = set()

    def register_restart_callback(
        self, component_name: str, callback: Callable
    ) -> None:
        """Register the async function to call when restarting a component."""
        self._restart_callbacks[component_name] = callback
        self._restart_counts[component_name] = 0

    async def attempt_heal(self, component_name: str) -> bool:
        """
        Attempt to heal (restart) a failed component.
        Returns True if restart was initiated, False if skipped.
        """
        if component_name in self._disabled_components:
            return False

        if component_name not in self._restart_callbacks:
            self._log.warning(
                "No restart callback for component '%s'", component_name
            )
            return False

        count = self._restart_counts.get(component_name, 0)
        if count >= self._max_restarts:
            self._log.error(
                "Component '%s' exceeded max restarts (%d) — disabling",
                component_name, self._max_restarts,
            )
            self._disabled_components.add(component_name)
            return False

        # Check cooldown
        now = time.monotonic()
        last = self._last_restart.get(component_name, 0.0)
        cooldown = self._base_cooldown_s * (self._cooldown_multiplier ** count)
        if now - last < cooldown:
            remaining = cooldown - (now - last)
            self._log.debug(
                "Component '%s' restart cooldown: %.0fs remaining",
                component_name, remaining,
            )
            return False

        # Execute restart
        self._restart_counts[component_name] = count + 1
        self._last_restart[component_name] = now

        self._log.info(
            "Healing component '%s' (attempt %d/%d)",
            component_name, count + 1, self._max_restarts,
        )

        try:
            callback = self._restart_callbacks[component_name]
            coro = callback()
            if asyncio.iscoroutine(coro):
                await asyncio.wait_for(coro, timeout=10.0)
            self._log.info("Component '%s' restarted successfully", component_name)
            return True
        except asyncio.TimeoutError:
            self._log.error(
                "Component '%s' restart timed out (10s)", component_name
            )
            return False
        except Exception as exc:
            self._log.error(
                "Component '%s' restart failed: %s", component_name, exc
            )
            return False

    def reset_component(self, component_name: str) -> None:
        """Reset restart counter for a component (e.g., after manual fix)."""
        self._restart_counts[component_name] = 0
        self._disabled_components.discard(component_name)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "restart_counts": dict(self._restart_counts),
            "disabled_components": list(self._disabled_components),
        }

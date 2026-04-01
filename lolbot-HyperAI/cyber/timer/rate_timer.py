"""
RateTimer — Configurable rate control for dynamic frequency adjustment.
========================================================================

Provides rate-limiting utilities used by components that need to
dynamically adjust their Proc() frequency based on game state.
For example, prediction can run at 5Hz during teamfights but 1Hz
during laning phase.

Architecture position:
    cyber/timer/rate_timer.py   ← YOU ARE HERE
    ├─ Used by: components that adapt their tick rate
    ├─ Used by: scheduler for health-aware throttling
    └─ Provides: RateController, AdaptiveRate, ThrottleGuard

Apollo reference:
    cyber/timer/timer.h          — Timer class
    cyber/timer/timing_wheel.h   — Timing wheel for scheduled callbacks

Design notes:
    - Token-bucket rate limiter for burst tolerance
    - Adaptive rate: speeds up during action, slows during idle
    - ThrottleGuard: prevents component overload
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional


# ─── Token Bucket Rate Limiter ───────────────────────────────────────────────

class RateController:
    """Token-bucket rate limiter.

    Allows burst traffic up to ``burst_size`` while maintaining an
    average rate of ``rate_per_second``.

    Usage::

        rc = RateController(rate_per_second=10, burst_size=3)
        while True:
            if rc.acquire():
                do_work()
            else:
                time.sleep(rc.wait_time)
    """

    def __init__(
        self,
        rate_per_second: float,
        burst_size: int = 1,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst_size < 1:
            raise ValueError("burst_size must be >= 1")

        self._rate = rate_per_second
        self._burst_size = burst_size
        self._tokens: float = float(burst_size)
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()
        self._total_acquired: int = 0
        self._total_denied: int = 0

    def acquire(self) -> bool:
        """Try to acquire one token.

        Returns:
            True if acquired, False if rate-limited.
        """
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._total_acquired += 1
                return True
            self._total_denied += 1
            return False

    @property
    def wait_time(self) -> float:
        """Estimated seconds until the next token is available."""
        with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                return 0.0
            deficit = 1.0 - self._tokens
            return deficit / self._rate

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(
            float(self._burst_size),
            self._tokens + elapsed * self._rate,
        )

    def reset(self) -> None:
        """Reset the token bucket to full."""
        with self._lock:
            self._tokens = float(self._burst_size)
            self._last_refill = time.monotonic()

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            self._refill()
            return {
                "rate_per_second": self._rate,
                "burst_size": self._burst_size,
                "current_tokens": round(self._tokens, 2),
                "total_acquired": self._total_acquired,
                "total_denied": self._total_denied,
            }


# ─── Adaptive Rate Controller ───────────────────────────────────────────────

class ActivityLevel(Enum):
    """Game activity levels for adaptive rate control."""
    IDLE = auto()       # No game / loading screen
    LOW = auto()        # Laning phase, farming
    MEDIUM = auto()     # Skirmishes, roaming
    HIGH = auto()       # Teamfights, baron contests
    CRITICAL = auto()   # Game-deciding moments


@dataclass
class AdaptiveRateConfig:
    """Configuration for adaptive rate control.

    Maps activity levels to target intervals (ms).
    """
    idle_interval_ms: float = 2000.0       # 0.5Hz
    low_interval_ms: float = 1000.0        # 1Hz
    medium_interval_ms: float = 500.0      # 2Hz
    high_interval_ms: float = 200.0        # 5Hz
    critical_interval_ms: float = 100.0    # 10Hz
    ramp_up_speed: float = 0.3             # EMA alpha for speeding up
    ramp_down_speed: float = 0.1           # EMA alpha for slowing down
    min_interval_ms: float = 50.0          # hard floor
    max_interval_ms: float = 5000.0        # hard ceiling


class AdaptiveRate:
    """Dynamically adjusts a component's tick interval based on game activity.

    Usage::

        rate = AdaptiveRate(config)
        # In Proc():
        rate.set_activity(ActivityLevel.HIGH)  # teamfight detected
        new_interval = rate.current_interval_ms
        self.interval_ms = new_interval
    """

    def __init__(self, config: Optional[AdaptiveRateConfig] = None) -> None:
        self._config = config or AdaptiveRateConfig()
        self._activity = ActivityLevel.IDLE
        self._current_interval_ms: float = self._config.idle_interval_ms
        self._target_interval_ms: float = self._config.idle_interval_ms

        self._interval_map = {
            ActivityLevel.IDLE: self._config.idle_interval_ms,
            ActivityLevel.LOW: self._config.low_interval_ms,
            ActivityLevel.MEDIUM: self._config.medium_interval_ms,
            ActivityLevel.HIGH: self._config.high_interval_ms,
            ActivityLevel.CRITICAL: self._config.critical_interval_ms,
        }

    def set_activity(self, level: ActivityLevel) -> None:
        """Update the activity level.

        The interval smoothly transitions toward the new target.
        """
        self._activity = level
        self._target_interval_ms = self._interval_map[level]

    def tick(self) -> float:
        """Advance one tick and return the current interval.

        Uses EMA smoothing: ramps up quickly, ramps down slowly.
        This ensures the system reacts fast to action but doesn't
        thrash when activity fluctuates.
        """
        if self._current_interval_ms > self._target_interval_ms:
            # Ramping up (going faster → lower interval)
            alpha = self._config.ramp_up_speed
        else:
            # Ramping down (going slower → higher interval)
            alpha = self._config.ramp_down_speed

        self._current_interval_ms = (
            alpha * self._target_interval_ms +
            (1.0 - alpha) * self._current_interval_ms
        )

        # Clamp
        self._current_interval_ms = max(
            self._config.min_interval_ms,
            min(self._config.max_interval_ms, self._current_interval_ms),
        )

        return self._current_interval_ms

    @property
    def current_interval_ms(self) -> float:
        return self._current_interval_ms

    @property
    def activity(self) -> ActivityLevel:
        return self._activity

    @property
    def target_interval_ms(self) -> float:
        return self._target_interval_ms

    def status(self) -> Dict[str, Any]:
        return {
            "activity": self._activity.name,
            "current_interval_ms": round(self._current_interval_ms, 1),
            "target_interval_ms": round(self._target_interval_ms, 1),
        }


# ─── Throttle Guard ─────────────────────────────────────────────────────────

class ThrottleGuard:
    """Prevents a component from consuming too much CPU.

    Monitors the duty cycle (time_in_proc / total_time) and triggers
    throttling when it exceeds the threshold.

    Usage::

        guard = ThrottleGuard(max_duty_cycle=0.5)
        # In Proc():
        guard.begin()
        do_work()
        guard.end()
        if guard.is_throttled:
            self.interval_ms *= 2  # slow down
    """

    def __init__(
        self,
        max_duty_cycle: float = 0.5,
        window_size: int = 50,
    ) -> None:
        self._max_duty = max_duty_cycle
        self._window_size = window_size
        self._proc_times: list[float] = []
        self._total_times: list[float] = []
        self._begin_time: float = 0.0
        self._last_end_time: float = 0.0
        self._throttled: bool = False

    def begin(self) -> None:
        """Mark the start of Proc() execution."""
        self._begin_time = time.monotonic()

    def end(self) -> None:
        """Mark the end of Proc() execution and update duty cycle."""
        now = time.monotonic()
        proc_time = now - self._begin_time

        if self._last_end_time > 0:
            total_time = now - self._last_end_time
        else:
            total_time = proc_time

        self._last_end_time = now
        self._proc_times.append(proc_time)
        self._total_times.append(total_time)

        # Trim window
        if len(self._proc_times) > self._window_size:
            self._proc_times = self._proc_times[-self._window_size:]
            self._total_times = self._total_times[-self._window_size:]

        # Check duty cycle
        total_proc = sum(self._proc_times)
        total_elapsed = sum(self._total_times)
        if total_elapsed > 0:
            duty = total_proc / total_elapsed
            self._throttled = duty > self._max_duty

    @property
    def is_throttled(self) -> bool:
        return self._throttled

    @property
    def duty_cycle(self) -> float:
        total_proc = sum(self._proc_times)
        total_elapsed = sum(self._total_times)
        if total_elapsed <= 0:
            return 0.0
        return total_proc / total_elapsed

    def status(self) -> Dict[str, Any]:
        return {
            "throttled": self._throttled,
            "duty_cycle": round(self.duty_cycle, 3),
            "max_duty_cycle": self._max_duty,
            "samples": len(self._proc_times),
        }

"""
Online statistics — EMA, Welford, quantile estimation for streaming data.
==========================================================================
lolbot-HyperAI · Common Math

Provides O(1) memory streaming statistics for use in Proc() loops:
    - EMA (Exponential Moving Average) with configurable alpha
    - Welford's online variance algorithm
    - P2 quantile estimation for p50/p95/p99

Architecture position:
    modules/common/math/statistics.py   ← YOU ARE HERE
    ├─ Used by: prediction/ (trend tracking)
    ├─ Used by: cyber/component/timer_component.py (latency)
    └─ Used by: evolution/fitness_evaluator.py (metric summarization)

Design notes:
    - All estimators are single-pass, O(1) memory
    - Thread-safe for single-writer (Proc() thread)
    - Serializable state for checkpoint/restore
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple


class EMA:
    """Exponential Moving Average with configurable smoothing factor.

    Useful for smoothing noisy signals (win probability, gold diffs)
    without keeping a large history buffer.
    """

    def __init__(self, alpha: float = 0.3, initial: float = 0.0) -> None:
        self._alpha = max(0.001, min(1.0, alpha))
        self._value = initial
        self._count = 0

    def update(self, value: float) -> float:
        """Add a new observation and return the updated EMA."""
        if self._count == 0:
            self._value = value
        else:
            self._value = self._alpha * value + (1.0 - self._alpha) * self._value
        self._count += 1
        return self._value

    @property
    def value(self) -> float:
        return self._value

    @property
    def count(self) -> int:
        return self._count

    def reset(self, initial: float = 0.0) -> None:
        self._value = initial
        self._count = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"alpha": self._alpha, "value": self._value, "count": self._count}


class WelfordVariance:
    """Welford's online algorithm for mean and variance.

    Single-pass, numerically stable computation of running mean,
    variance, and standard deviation.
    """

    def __init__(self) -> None:
        self._count = 0
        self._mean = 0.0
        self._m2 = 0.0
        self._min = float("inf")
        self._max = float("-inf")

    def update(self, value: float) -> None:
        """Add a new observation."""
        self._count += 1
        delta = value - self._mean
        self._mean += delta / self._count
        delta2 = value - self._mean
        self._m2 += delta * delta2
        self._min = min(self._min, value)
        self._max = max(self._max, value)

    @property
    def count(self) -> int:
        return self._count

    @property
    def mean(self) -> float:
        return self._mean if self._count > 0 else 0.0

    @property
    def variance(self) -> float:
        if self._count < 2:
            return 0.0
        return self._m2 / (self._count - 1)

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)

    @property
    def min_val(self) -> float:
        return self._min if self._count > 0 else 0.0

    @property
    def max_val(self) -> float:
        return self._max if self._count > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self._count,
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "min": round(self.min_val, 4),
            "max": round(self.max_val, 4),
        }


class RollingWindow:
    """Fixed-size rolling window with basic stats.

    For when you need windowed stats (e.g. last 60 seconds of data)
    rather than all-time statistics.
    """

    def __init__(self, max_size: int = 100) -> None:
        self._data: Deque[float] = deque(maxlen=max_size)

    def add(self, value: float) -> None:
        self._data.append(value)

    @property
    def count(self) -> int:
        return len(self._data)

    @property
    def mean(self) -> float:
        if not self._data:
            return 0.0
        return sum(self._data) / len(self._data)

    @property
    def last(self) -> float:
        return self._data[-1] if self._data else 0.0

    @property
    def first(self) -> float:
        return self._data[0] if self._data else 0.0

    @property
    def delta(self) -> float:
        """Difference between last and first values in window."""
        if len(self._data) < 2:
            return 0.0
        return self._data[-1] - self._data[0]

    def percentile(self, p: float) -> float:
        """Compute percentile (0-100) from current window."""
        if not self._data:
            return 0.0
        sorted_data = sorted(self._data)
        idx = int(len(sorted_data) * p / 100.0)
        idx = min(idx, len(sorted_data) - 1)
        return sorted_data[idx]

    @property
    def p50(self) -> float:
        return self.percentile(50)

    @property
    def p95(self) -> float:
        return self.percentile(95)

    @property
    def p99(self) -> float:
        return self.percentile(99)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "mean": round(self.mean, 4),
            "p50": round(self.p50, 4),
            "p95": round(self.p95, 4),
            "p99": round(self.p99, 4),
            "delta": round(self.delta, 4),
        }

    def clear(self) -> None:
        self._data.clear()


class TrendDetector:
    """Detects rising/falling/stable trends from a rolling window.

    Used by prediction and planning to assess momentum direction.
    """

    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"

    def __init__(
        self,
        window_size: int = 30,
        threshold: float = 0.05,
    ) -> None:
        self._window = RollingWindow(window_size)
        self._threshold = threshold

    def update(self, value: float) -> str:
        """Add observation and return current trend."""
        self._window.add(value)
        return self.direction

    @property
    def direction(self) -> str:
        if self._window.count < 5:
            return self.STABLE
        delta = self._window.delta
        if delta > self._threshold:
            return self.RISING
        elif delta < -self._threshold:
            return self.FALLING
        return self.STABLE

    @property
    def value(self) -> float:
        return self._window.last

    @property
    def delta(self) -> float:
        return self._window.delta

    def to_dict(self) -> Dict[str, Any]:
        return {
            "direction": self.direction,
            "value": round(self.value, 4),
            "delta": round(self.delta, 4),
            "window": self._window.to_dict(),
        }

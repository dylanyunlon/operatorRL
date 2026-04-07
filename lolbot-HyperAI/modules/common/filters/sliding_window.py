"""
modules/common/filters/sliding_window.py — Time-windowed Rolling Statistics
=============================================================================
lolbot-HyperAI · modules/common/filters

查看 modules/common/math/statistics.py 上现有统计工具的实现方式, 理解其模式。
从 statistics.py 这个好例子开始。然后, 遵循该模式实现一个新的 SlidingWindow,
让 GoldTrendAnalyzer / MomentumTracker 可以高效计算滑动窗口内的 mean/std/trend,
并能在 O(1) 时间内添加和过期数据点。接着在 PerceptionComponent.Proc() 引入
SlidingWindowAggregator, 使 game_state 能够携带滚动统计 (30s/60s/120s 窗口),
同时优化内存使用 (固定大小 deque 替代 list append)。

Architecture position:
    modules/common/filters/sliding_window.py   ← YOU ARE HERE
    ├─ Used by: modules/perception/fusion/gold_trend_analyzer.py
    ├─ Used by: modules/perception/fusion/momentum_tracker.py
    ├─ Used by: modules/prediction/evaluator/confidence_calibrator.py
    ├─ Used by: modules/prediction/team_fight/teamfight_predictor.py
    └─ Used by: evolution/fitness_evaluator.py

Apollo reference:
    modules/planning/math/smoothing_spline.h — time-windowed smoothing
    modules/perception/lib/filter/digital_filter.h — rolling filter

Design notes:
    - Two implementations: SlidingWindowCounter (count-based) and
      TimeSlidingWindow (time-based with expiry)
    - O(1) amortized add/remove for counter-based
    - O(k) for time-based where k = expired entries per add
    - Welford's online algorithm for numerically stable mean/variance
    - Linear regression via incremental sum-of-products
    - Thread-safe via lock (windows may be shared across components)
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Deque, Dict, List, Optional, Tuple,
)

from cyber.logger.cyber_logger import get_logger

logger = get_logger("common.sliding_window")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_WINDOW_SIZE = 100
_DEFAULT_WINDOW_DURATION_S = 60.0
_MIN_SAMPLES_FOR_TREND = 3
_MIN_SAMPLES_FOR_STD = 2


# ─── Rolling Statistics (Welford's algorithm) ────────────────────────────────

class WelfordAccumulator:
    """Numerically stable online mean/variance via Welford's algorithm.

    This avoids catastrophic cancellation that occurs with the naive
    sum-of-squares approach when values are large and close together.

    Usage::

        acc = WelfordAccumulator()
        for x in data:
            acc.add(x)
        print(acc.mean, acc.std)
    """

    __slots__ = ("_count", "_mean", "_m2", "_min", "_max", "_sum")

    def __init__(self) -> None:
        self._count: int = 0
        self._mean: float = 0.0
        self._m2: float = 0.0
        self._min: float = float("inf")
        self._max: float = float("-inf")
        self._sum: float = 0.0

    def add(self, value: float) -> None:
        """Add a new sample."""
        self._count += 1
        self._sum += value
        delta = value - self._mean
        self._mean += delta / self._count
        delta2 = value - self._mean
        self._m2 += delta * delta2
        self._min = min(self._min, value)
        self._max = max(self._max, value)

    def remove(self, value: float) -> None:
        """Remove a sample (for sliding window eviction).

        Note: this is the reverse Welford update. Only valid if the
        value was previously added. Numerical stability degrades
        slightly with many add/remove cycles.
        """
        if self._count <= 1:
            self.reset()
            return
        self._count -= 1
        self._sum -= value
        delta = value - self._mean
        self._mean = (self._mean * (self._count + 1) - value) / self._count
        delta2 = value - self._mean
        self._m2 -= delta * delta2
        # Clamp M2 to prevent negative variance from floating-point drift
        if self._m2 < 0:
            self._m2 = 0.0

    @property
    def count(self) -> int:
        return self._count

    @property
    def mean(self) -> float:
        return self._mean if self._count > 0 else 0.0

    @property
    def variance(self) -> float:
        if self._count < _MIN_SAMPLES_FOR_STD:
            return 0.0
        return self._m2 / self._count

    @property
    def sample_variance(self) -> float:
        if self._count < _MIN_SAMPLES_FOR_STD:
            return 0.0
        return self._m2 / (self._count - 1)

    @property
    def std(self) -> float:
        return math.sqrt(max(0, self.variance))

    @property
    def sample_std(self) -> float:
        return math.sqrt(max(0, self.sample_variance))

    @property
    def min_val(self) -> float:
        return self._min if self._count > 0 else 0.0

    @property
    def max_val(self) -> float:
        return self._max if self._count > 0 else 0.0

    @property
    def total(self) -> float:
        return self._sum

    def reset(self) -> None:
        self._count = 0
        self._mean = 0.0
        self._m2 = 0.0
        self._min = float("inf")
        self._max = float("-inf")
        self._sum = 0.0


# ─── Count-based Sliding Window ──────────────────────────────────────────────

@dataclass
class WindowStats:
    """Snapshot of window statistics."""
    count: int = 0
    mean: float = 0.0
    std: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    total: float = 0.0
    trend_slope: float = 0.0
    trend_r_squared: float = 0.0
    latest: float = 0.0
    oldest: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "mean": round(self.mean, 4),
            "std": round(self.std, 4),
            "min": round(self.min_val, 4),
            "max": round(self.max_val, 4),
            "total": round(self.total, 4),
            "trend_slope": round(self.trend_slope, 6),
            "trend_r2": round(self.trend_r_squared, 4),
        }


class SlidingWindowCounter:
    """Fixed-size sliding window with O(1) amortized statistics.

    Maintains a deque of the last N values with running mean/std
    via Welford's algorithm, and linear regression for trend.

    Usage::

        win = SlidingWindowCounter(window_size=30)
        for gold_diff in snapshots:
            win.add(gold_diff)
        stats = win.stats()
        print(f"Gold trend: {stats.trend_slope:+.1f}/tick")
    """

    def __init__(self, window_size: int = _DEFAULT_WINDOW_SIZE) -> None:
        self._max_size = max(2, window_size)
        self._values: Deque[float] = deque(maxlen=self._max_size)
        self._acc = WelfordAccumulator()
        self._lock = threading.Lock()
        # For linear regression
        self._indices: Deque[int] = deque(maxlen=self._max_size)
        self._next_index: int = 0

    def add(self, value: float) -> None:
        """Add a value to the window, evicting the oldest if full."""
        with self._lock:
            if len(self._values) >= self._max_size:
                evicted = self._values[0]
                self._acc.remove(evicted)
            self._values.append(value)
            self._acc.add(value)
            self._indices.append(self._next_index)
            self._next_index += 1

    def stats(self) -> WindowStats:
        """Compute current window statistics."""
        with self._lock:
            if not self._values:
                return WindowStats()

            slope, r2 = self._compute_trend()

            return WindowStats(
                count=self._acc.count,
                mean=self._acc.mean,
                std=self._acc.std,
                min_val=self._acc.min_val,
                max_val=self._acc.max_val,
                total=self._acc.total,
                trend_slope=slope,
                trend_r_squared=r2,
                latest=self._values[-1],
                oldest=self._values[0],
            )

    @property
    def count(self) -> int:
        return len(self._values)

    @property
    def mean(self) -> float:
        return self._acc.mean

    @property
    def is_full(self) -> bool:
        return len(self._values) >= self._max_size

    def values(self) -> List[float]:
        """Return a copy of current window values."""
        with self._lock:
            return list(self._values)

    def reset(self) -> None:
        with self._lock:
            self._values.clear()
            self._indices.clear()
            self._acc.reset()
            self._next_index = 0

    def _compute_trend(self) -> Tuple[float, float]:
        """Simple linear regression: y = slope * x + intercept.

        Returns (slope, r_squared).
        """
        n = len(self._values)
        if n < _MIN_SAMPLES_FOR_TREND:
            return (0.0, 0.0)

        # Use normalized indices (0, 1, 2, ...) for x
        sum_x = 0.0
        sum_y = 0.0
        sum_xy = 0.0
        sum_x2 = 0.0
        sum_y2 = 0.0

        for i, val in enumerate(self._values):
            x = float(i)
            sum_x += x
            sum_y += val
            sum_xy += x * val
            sum_x2 += x * x
            sum_y2 += val * val

        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-12:
            return (0.0, 0.0)

        slope = (n * sum_xy - sum_x * sum_y) / denom

        # R-squared
        ss_tot = sum_y2 - (sum_y * sum_y) / n
        if abs(ss_tot) < 1e-12:
            r2 = 0.0
        else:
            y_mean = sum_y / n
            ss_res = 0.0
            for i, val in enumerate(self._values):
                predicted = slope * i + (y_mean - slope * sum_x / n)
                ss_res += (val - predicted) ** 2
            r2 = max(0.0, 1.0 - ss_res / ss_tot)

        return (slope, r2)


# ─── Time-based Sliding Window ───────────────────────────────────────────────

@dataclass(order=True)
class _TimedSample:
    """A value with a timestamp for time-based expiry."""
    timestamp: float
    value: float = field(compare=False)


class TimeSlidingWindow:
    """Time-based sliding window that expires entries by age.

    Unlike SlidingWindowCounter which keeps a fixed number of
    entries, this window keeps all entries within a time duration
    and expires old entries on each add() or query.

    Usage::

        win = TimeSlidingWindow(duration_s=120.0)  # 2-minute window
        win.add(gold_diff, game_time=current_time)
        stats = win.stats()
    """

    def __init__(self, duration_s: float = _DEFAULT_WINDOW_DURATION_S) -> None:
        self._duration_s = max(1.0, duration_s)
        self._samples: Deque[_TimedSample] = deque()
        self._acc = WelfordAccumulator()
        self._lock = threading.Lock()

    def add(self, value: float, timestamp: Optional[float] = None) -> None:
        """Add a timestamped value and expire old entries."""
        ts = timestamp if timestamp is not None else time.monotonic()

        with self._lock:
            # Expire old entries
            cutoff = ts - self._duration_s
            while self._samples and self._samples[0].timestamp < cutoff:
                evicted = self._samples.popleft()
                self._acc.remove(evicted.value)

            self._samples.append(_TimedSample(timestamp=ts, value=value))
            self._acc.add(value)

    def stats(self, as_of: Optional[float] = None) -> WindowStats:
        """Compute current statistics, expiring old entries first."""
        with self._lock:
            if as_of is not None:
                cutoff = as_of - self._duration_s
                while (self._samples
                       and self._samples[0].timestamp < cutoff):
                    evicted = self._samples.popleft()
                    self._acc.remove(evicted.value)

            if not self._samples:
                return WindowStats()

            slope, r2 = self._compute_time_trend()

            return WindowStats(
                count=self._acc.count,
                mean=self._acc.mean,
                std=self._acc.std,
                min_val=self._acc.min_val,
                max_val=self._acc.max_val,
                total=self._acc.total,
                trend_slope=slope,
                trend_r_squared=r2,
                latest=self._samples[-1].value,
                oldest=self._samples[0].value,
            )

    @property
    def count(self) -> int:
        return len(self._samples)

    @property
    def duration_s(self) -> float:
        return self._duration_s

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._acc.reset()

    def _compute_time_trend(self) -> Tuple[float, float]:
        """Linear regression using actual timestamps as x-axis."""
        n = len(self._samples)
        if n < _MIN_SAMPLES_FOR_TREND:
            return (0.0, 0.0)

        t0 = self._samples[0].timestamp
        sum_x = sum_y = sum_xy = sum_x2 = sum_y2 = 0.0

        for s in self._samples:
            x = s.timestamp - t0
            y = s.value
            sum_x += x
            sum_y += y
            sum_xy += x * y
            sum_x2 += x * x
            sum_y2 += y * y

        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-12:
            return (0.0, 0.0)

        slope = (n * sum_xy - sum_x * sum_y) / denom

        ss_tot = sum_y2 - (sum_y * sum_y) / n
        if abs(ss_tot) < 1e-12:
            return (slope, 0.0)

        y_mean = sum_y / n
        x_mean = sum_x / n
        intercept = y_mean - slope * x_mean
        ss_res = 0.0
        for s in self._samples:
            x = s.timestamp - t0
            predicted = slope * x + intercept
            ss_res += (s.value - predicted) ** 2
        r2 = max(0.0, 1.0 - ss_res / ss_tot)

        return (slope, r2)


# ─── Multi-window Aggregator ─────────────────────────────────────────────────

class MultiWindowAggregator:
    """Manages multiple time windows (30s, 60s, 120s) for one metric.

    Used by PerceptionComponent to track gold_diff at different
    time scales simultaneously, enabling both short-term spike
    detection and longer-term trend analysis.

    Usage::

        agg = MultiWindowAggregator(
            windows={"30s": 30.0, "60s": 60.0, "120s": 120.0}
        )
        agg.add(gold_diff, game_time)
        all_stats = agg.all_stats()
        # {"30s": WindowStats(...), "60s": WindowStats(...), "120s": ...}
    """

    def __init__(self, windows: Optional[Dict[str, float]] = None) -> None:
        if windows is None:
            windows = {"30s": 30.0, "60s": 60.0, "120s": 120.0}
        self._windows: Dict[str, TimeSlidingWindow] = {
            name: TimeSlidingWindow(duration_s=dur)
            for name, dur in windows.items()
        }

    def add(self, value: float, timestamp: Optional[float] = None) -> None:
        """Add a value to all windows simultaneously."""
        for win in self._windows.values():
            win.add(value, timestamp)

    def stats(self, name: str) -> WindowStats:
        """Get stats for a specific window."""
        win = self._windows.get(name)
        if win is None:
            return WindowStats()
        return win.stats()

    def all_stats(self) -> Dict[str, WindowStats]:
        """Get stats for all windows."""
        return {name: win.stats() for name, win in self._windows.items()}

    def trend_summary(self) -> Dict[str, float]:
        """Get slope from each window for quick comparison."""
        return {
            name: win.stats().trend_slope
            for name, win in self._windows.items()
        }

    def reset(self) -> None:
        for win in self._windows.values():
            win.reset()

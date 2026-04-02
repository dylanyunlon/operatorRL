"""
GameStatistics — Sliding-window statistical aggregation toolkit.
=================================================================
lolbot-HyperAI · Common Layer

Provides reusable rolling-window statistics (min, max, mean, median,
p95, p99, trend, variance) for any numeric time series.  Used by
prediction (gold diff trend), perception (event rate), planning
(advice frequency), and runtime (latency monitoring).

Architecture position:
    modules/common/math/statistics.py   ← YOU ARE HERE
    ├─ Used by: prediction/prediction_component.py (win prob smoothing)
    ├─ Used by: cyber/component/timer_component.py (latency stats)
    ├─ Used by: runtime/metrics_collector.py (system metrics)
    └─ Used by: modules/planning/ (cooldown tracking)

Apollo reference:
    modules/common/math/vec2d.cc, angle.cc — shared math utilities
    cyber/timer/timing_wheel.cc — timing infrastructure

Design notes:
    - Zero external dependencies (no numpy/scipy)
    - Thread-safe via copy-on-read semantics
    - Supports multiple named series tracked in parallel
    - Trend detection via simple linear regression over window
    - Configurable window size per series
"""

from __future__ import annotations

import bisect
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("common.math.statistics")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_WINDOW_SIZE = 200
_MIN_SAMPLES_FOR_STATS = 2
_MIN_SAMPLES_FOR_TREND = 5
_MIN_SAMPLES_FOR_PERCENTILE = 10


# ─── Single-Series Rolling Window ───────────────────────────────────────────

@dataclass
class WindowStats:
    """Computed statistics for a rolling window."""
    count: int = 0
    mean: float = 0.0
    median: float = 0.0
    min_val: float = 0.0
    max_val: float = 0.0
    std_dev: float = 0.0
    variance: float = 0.0
    p95: float = 0.0
    p99: float = 0.0
    sum_val: float = 0.0
    trend_slope: float = 0.0    # Positive = increasing
    trend_direction: str = "flat"  # "up", "down", "flat"
    last_value: float = 0.0
    window_duration_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "mean": round(self.mean, 4),
            "median": round(self.median, 4),
            "min": round(self.min_val, 4),
            "max": round(self.max_val, 4),
            "std_dev": round(self.std_dev, 4),
            "p95": round(self.p95, 4),
            "p99": round(self.p99, 4),
            "trend_slope": round(self.trend_slope, 6),
            "trend_direction": self.trend_direction,
            "last_value": round(self.last_value, 4),
        }


class RollingWindow:
    """Rolling window over a single numeric time series.

    Stores (timestamp, value) pairs in a bounded deque and computes
    statistics on demand.  All operations are O(n) in window size
    at worst, O(1) amortized for recording.

    Example::

        window = RollingWindow("gold_diff", max_size=200)
        window.record(1500.0)
        window.record(1800.0)
        stats = window.compute()
        print(stats.mean, stats.trend_direction)
    """

    def __init__(
        self,
        name: str = "unnamed",
        max_size: int = _DEFAULT_WINDOW_SIZE,
    ) -> None:
        self._name = name
        self._max_size = max_size
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=max_size)
        self._total_recorded: int = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    @property
    def size(self) -> int:
        return len(self._samples)

    @property
    def total_recorded(self) -> int:
        return self._total_recorded

    def record(self, value: float, timestamp: Optional[float] = None) -> None:
        """Record a new sample.

        Args:
            value: The numeric value to record.
            timestamp: Optional monotonic timestamp. Defaults to now.
        """
        ts = timestamp if timestamp is not None else time.monotonic()
        with self._lock:
            self._samples.append((ts, value))
            self._total_recorded += 1

    def compute(self) -> WindowStats:
        """Compute statistics over the current window.

        Returns:
            WindowStats with all computed metrics.
        """
        with self._lock:
            samples = list(self._samples)

        if not samples:
            return WindowStats()

        values = [v for _, v in samples]
        n = len(values)

        stats = WindowStats()
        stats.count = n
        stats.sum_val = sum(values)
        stats.mean = stats.sum_val / n
        stats.min_val = min(values)
        stats.max_val = max(values)
        stats.last_value = values[-1]

        # Duration
        if n >= 2:
            stats.window_duration_s = samples[-1][0] - samples[0][0]

        # Variance and standard deviation
        if n >= _MIN_SAMPLES_FOR_STATS:
            variance_sum = sum((v - stats.mean) ** 2 for v in values)
            stats.variance = variance_sum / (n - 1)  # Bessel's correction
            stats.std_dev = math.sqrt(stats.variance)

        # Median and percentiles (requires sorted copy)
        if n >= _MIN_SAMPLES_FOR_STATS:
            sorted_vals = sorted(values)
            stats.median = self._percentile(sorted_vals, 0.50)

            if n >= _MIN_SAMPLES_FOR_PERCENTILE:
                stats.p95 = self._percentile(sorted_vals, 0.95)
                stats.p99 = self._percentile(sorted_vals, 0.99)
            else:
                stats.p95 = stats.max_val
                stats.p99 = stats.max_val

        # Trend (linear regression slope)
        if n >= _MIN_SAMPLES_FOR_TREND:
            stats.trend_slope = self._compute_trend(samples)
            if stats.trend_slope > 0.001 * stats.std_dev and stats.std_dev > 0:
                stats.trend_direction = "up"
            elif stats.trend_slope < -0.001 * stats.std_dev and stats.std_dev > 0:
                stats.trend_direction = "down"
            else:
                stats.trend_direction = "flat"

        return stats

    def latest(self, n: int = 1) -> List[float]:
        """Return the last n values."""
        with self._lock:
            if n >= len(self._samples):
                return [v for _, v in self._samples]
            return [v for _, v in list(self._samples)[-n:]]

    def clear(self) -> None:
        """Clear the window."""
        with self._lock:
            self._samples.clear()
            self._total_recorded = 0

    @staticmethod
    def _percentile(sorted_vals: List[float], p: float) -> float:
        """Compute the p-th percentile from sorted values.

        Uses linear interpolation between closest ranks.
        """
        n = len(sorted_vals)
        if n == 0:
            return 0.0
        if n == 1:
            return sorted_vals[0]

        k = (n - 1) * p
        f = math.floor(k)
        c = math.ceil(k)

        if f == c:
            return sorted_vals[int(k)]

        d0 = sorted_vals[int(f)] * (c - k)
        d1 = sorted_vals[int(c)] * (k - f)
        return d0 + d1

    @staticmethod
    def _compute_trend(samples: List[Tuple[float, float]]) -> float:
        """Compute linear regression slope over (timestamp, value) pairs.

        Uses the least-squares formula:
            slope = (n * sum(xy) - sum(x) * sum(y)) / (n * sum(x²) - sum(x)²)

        Returns:
            Slope (units/second). Positive = increasing.
        """
        n = len(samples)
        if n < 2:
            return 0.0

        # Normalize timestamps to start from 0
        t0 = samples[0][0]
        xs = [t - t0 for t, _ in samples]
        ys = [v for _, v in samples]

        sum_x = sum(xs)
        sum_y = sum(ys)
        sum_xy = sum(x * y for x, y in zip(xs, ys))
        sum_x2 = sum(x * x for x in xs)

        denom = n * sum_x2 - sum_x * sum_x
        if abs(denom) < 1e-12:
            return 0.0

        return (n * sum_xy - sum_x * sum_y) / denom


# ─── Multi-Series Tracker ───────────────────────────────────────────────────

class GameStatistics:
    """Manages multiple named rolling windows for parallel time series.

    Provides a unified interface to record and query statistics across
    all tracked series (gold_diff, kill_diff, xp_diff, etc.).

    Example::

        tracker = GameStatistics()
        tracker.record("gold_diff", 1500.0)
        tracker.record("kill_diff", 2)
        tracker.record("gold_diff", 1800.0)

        gold_stats = tracker.get("gold_diff")
        print(gold_stats.trend_direction)  # "up"

        all_stats = tracker.compute_all()
        for name, stats in all_stats.items():
            print(f"{name}: mean={stats.mean:.1f}, trend={stats.trend_direction}")
    """

    def __init__(
        self,
        default_window_size: int = _DEFAULT_WINDOW_SIZE,
    ) -> None:
        self._default_window_size = default_window_size
        self._series: Dict[str, RollingWindow] = {}
        self._lock = threading.Lock()

    def record(
        self,
        series_name: str,
        value: float,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record a value to a named series (auto-creates if new).

        Args:
            series_name: Name of the series (e.g. "gold_diff").
            value: Numeric value to record.
            timestamp: Optional monotonic timestamp.
        """
        window = self._get_or_create(series_name)
        window.record(value, timestamp)

    def get(self, series_name: str) -> WindowStats:
        """Compute statistics for a single series.

        Args:
            series_name: Name of the series.

        Returns:
            WindowStats. Returns empty stats if series doesn't exist.
        """
        with self._lock:
            window = self._series.get(series_name)
        if window is None:
            return WindowStats()
        return window.compute()

    def compute_all(self) -> Dict[str, WindowStats]:
        """Compute statistics for all tracked series.

        Returns:
            Dict of series_name → WindowStats.
        """
        with self._lock:
            names = list(self._series.keys())

        result = {}
        for name in names:
            result[name] = self.get(name)
        return result

    def latest(self, series_name: str, n: int = 1) -> List[float]:
        """Get the latest n values from a series."""
        with self._lock:
            window = self._series.get(series_name)
        if window is None:
            return []
        return window.latest(n)

    def series_names(self) -> List[str]:
        """Return all tracked series names."""
        with self._lock:
            return list(self._series.keys())

    def has_series(self, name: str) -> bool:
        with self._lock:
            return name in self._series

    def remove_series(self, name: str) -> bool:
        """Remove a series."""
        with self._lock:
            return self._series.pop(name, None) is not None

    def _get_or_create(self, name: str) -> RollingWindow:
        """Get or auto-create a rolling window for a series."""
        with self._lock:
            if name not in self._series:
                self._series[name] = RollingWindow(
                    name=name,
                    max_size=self._default_window_size,
                )
            return self._series[name]

    # ── Convenience: record snapshot features ────────────────────────────

    def record_snapshot_features(
        self,
        features: Dict[str, float],
        timestamp: Optional[float] = None,
    ) -> None:
        """Record multiple features at once from a game snapshot.

        Args:
            features: Dict of feature_name → value.
            timestamp: Shared timestamp for all features.
        """
        ts = timestamp or time.monotonic()
        for name, value in features.items():
            self.record(name, value, ts)

    # ── Stats & Reset ────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return summary statistics."""
        with self._lock:
            series_info = {
                name: {
                    "size": w.size,
                    "total_recorded": w.total_recorded,
                }
                for name, w in self._series.items()
            }
        return {
            "series_count": len(series_info),
            "series": series_info,
        }

    def reset(self) -> None:
        """Clear all series (e.g. between games)."""
        with self._lock:
            self._series.clear()

    def to_dict(self) -> Dict[str, Any]:
        """Export all computed stats as a dict."""
        all_stats = self.compute_all()
        return {name: s.to_dict() for name, s in all_stats.items()}


# ─── Utility: Exponential Moving Average ─────────────────────────────────────

class ExponentialMovingAverage:
    """Lightweight EMA for real-time smoothing.

    Simpler than RollingWindow for cases where only the smoothed
    current value matters (e.g. win probability display).

    Example::

        ema = ExponentialMovingAverage(alpha=0.3)
        ema.update(0.52)
        ema.update(0.58)
        print(ema.value)  # ~0.558
    """

    def __init__(self, alpha: float = 0.3, initial: Optional[float] = None) -> None:
        self._alpha = max(0.01, min(1.0, alpha))
        self._value: Optional[float] = initial
        self._count: int = 0

    def update(self, new_value: float) -> float:
        """Update with a new observation and return smoothed value."""
        if self._value is None:
            self._value = new_value
        else:
            self._value = self._alpha * new_value + (1.0 - self._alpha) * self._value
        self._count += 1
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    @property
    def count(self) -> int:
        return self._count

    def reset(self, initial: Optional[float] = None) -> None:
        self._value = initial
        self._count = 0


# ─── Utility: Rate Counter ──────────────────────────────────────────────────

class RateCounter:
    """Counts events per second over a rolling window.

    Useful for monitoring: events/sec, messages/sec, errors/sec.

    Example::

        counter = RateCounter(window_s=10.0)
        counter.tick()
        counter.tick()
        counter.tick()
        print(counter.rate)  # ~3.0/s (depends on actual timing)
    """

    def __init__(self, window_s: float = 10.0) -> None:
        self._window_s = max(1.0, window_s)
        self._timestamps: Deque[float] = deque()
        self._total: int = 0

    def tick(self, count: int = 1) -> None:
        """Record one or more events at the current time."""
        now = time.monotonic()
        for _ in range(count):
            self._timestamps.append(now)
            self._total += 1
        self._prune(now)

    @property
    def rate(self) -> float:
        """Current events per second."""
        now = time.monotonic()
        self._prune(now)
        if not self._timestamps:
            return 0.0
        duration = now - self._timestamps[0]
        if duration < 0.001:
            return 0.0
        return len(self._timestamps) / duration

    @property
    def count_in_window(self) -> int:
        self._prune(time.monotonic())
        return len(self._timestamps)

    @property
    def total(self) -> int:
        return self._total

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def reset(self) -> None:
        self._timestamps.clear()
        self._total = 0

"""
cyber/diagnostics/proc_histogram.py — Histogram-based Proc() latency profiler.
=================================================================================
Claude18 · Extends Claude11's proc_profiler.py with histogram + overrun detection

Adds to existing ProcProfiler (kept intact):
    - Histogram bucketing for latency distribution
    - Overrun rate tracking per component
    - Budget-aware alerting (warn/error thresholds)
    - Periodic log output compatible with MonitorComponent

Apollo reference: cyber/timer/timing_wheel.cc timing instrumentation.

File location: lolbot-HyperAI/cyber/diagnostics/proc_histogram.py
"""

from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

_HISTOGRAM_BUCKETS_MS = [
    0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0,
    100.0, 200.0, 500.0, 1000.0,
]
_SAMPLE_WINDOW = 1000
_REPORT_INTERVAL_S = 30.0


@dataclass
class HistogramProfile:
    """Per-component latency histogram with budget tracking."""
    component: str
    budget_ms: float
    total_calls: int = 0
    total_overruns: int = 0
    total_failures: int = 0
    samples: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_SAMPLE_WINDOW)
    )
    max_duration_ms: float = 0.0
    last_duration_ms: float = 0.0
    _bucket_counts: Dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        for b in _HISTOGRAM_BUCKETS_MS:
            self._bucket_counts[f"<={b}ms"] = 0
        self._bucket_counts[">1000ms"] = 0

    def record(self, duration_ms: float, success: bool = True) -> bool:
        """Record one sample. Returns True if overrun detected."""
        with self._lock:
            self.total_calls += 1
            if not success:
                self.total_failures += 1
            self.samples.append(duration_ms)
            self.last_duration_ms = duration_ms
            if duration_ms > self.max_duration_ms:
                self.max_duration_ms = duration_ms

            overrun = duration_ms > self.budget_ms
            if overrun:
                self.total_overruns += 1

            placed = False
            for b in _HISTOGRAM_BUCKETS_MS:
                if duration_ms <= b:
                    self._bucket_counts[f"<={b}ms"] += 1
                    placed = True
                    break
            if not placed:
                self._bucket_counts[">1000ms"] += 1

            return overrun

    def percentile(self, p: float) -> float:
        with self._lock:
            if not self.samples:
                return 0.0
            s = sorted(self.samples)
            idx = min(int(len(s) * p / 100.0), len(s) - 1)
            return s[idx]

    def mean(self) -> float:
        with self._lock:
            return statistics.mean(self.samples) if self.samples else 0.0

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "component": self.component,
                "budget_ms": self.budget_ms,
                "total_calls": self.total_calls,
                "total_failures": self.total_failures,
                "total_overruns": self.total_overruns,
                "overrun_pct": round(
                    self.total_overruns / max(1, self.total_calls) * 100, 2
                ),
                "last_ms": round(self.last_duration_ms, 3),
                "max_ms": round(self.max_duration_ms, 3),
                "mean_ms": round(self.mean(), 3),
                "p50_ms": round(self.percentile(50), 3),
                "p95_ms": round(self.percentile(95), 3),
                "p99_ms": round(self.percentile(99), 3),
                "histogram": dict(self._bucket_counts),
            }


class ProcHistogramProfiler:
    """System-wide histogram profiler for all component Proc() calls.

    Usage::
        profiler = ProcHistogramProfiler()
        profiler.register("canbus", budget_ms=100.0)
        profiler.record("canbus", 12.5, success=True)
        report = profiler.report()
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, HistogramProfile] = {}
        self._lock = threading.Lock()
        self._last_log_time: float = 0.0

    def register(self, component: str, budget_ms: float = 100.0) -> None:
        with self._lock:
            if component not in self._profiles:
                self._profiles[component] = HistogramProfile(
                    component=component, budget_ms=budget_ms,
                )

    def record(
        self, component: str, duration_ms: float, success: bool = True,
    ) -> bool:
        """Record a sample. Returns True if overrun."""
        with self._lock:
            profile = self._profiles.get(component)
        if profile is None:
            return False
        return profile.record(duration_ms, success)

    def report(self) -> Dict[str, Any]:
        with self._lock:
            profiles = list(self._profiles.values())
        result = {}
        for p in profiles:
            result[p.component] = p.snapshot()
        return result

    def log_if_due(self) -> bool:
        """Log periodic report. Returns True if logged."""
        now = time.monotonic()
        if now - self._last_log_time < _REPORT_INTERVAL_S:
            return False
        self._last_log_time = now

        for comp, snap in self.report().items():
            if snap["total_calls"] == 0:
                continue
            level = logging.INFO
            if snap["overrun_pct"] > 10:
                level = logging.WARNING
            if snap["overrun_pct"] > 30:
                level = logging.ERROR
            logger.log(
                level,
                "[ProcHistogram] %s: calls=%d mean=%.1fms p95=%.1fms "
                "p99=%.1fms overrun=%.1f%%",
                comp, snap["total_calls"], snap["mean_ms"],
                snap["p95_ms"], snap["p99_ms"], snap["overrun_pct"],
            )
        return True

    def reset(self) -> None:
        with self._lock:
            for p in self._profiles.values():
                with p._lock:
                    p.total_calls = 0
                    p.total_failures = 0
                    p.total_overruns = 0
                    p.samples.clear()
                    p.max_duration_ms = 0.0


# Singleton
_INSTANCE: Optional[ProcHistogramProfiler] = None
_LOCK = threading.Lock()


def get_histogram_profiler() -> ProcHistogramProfiler:
    global _INSTANCE
    with _LOCK:
        if _INSTANCE is None:
            _INSTANCE = ProcHistogramProfiler()
        return _INSTANCE

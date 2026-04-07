"""
CyberRT Proc Profiler — Per-component CPU/wall-time profiling.
================================================================
cyber/diagnostics/proc_profiler.py

Claude17: Apollo's cyber framework includes built-in profiling for
each component's Proc() execution. We add a lightweight profiler
that can be attached to any TimerComponent to collect detailed
timing breakdowns without impacting normal performance.

Architecture position:
    cyber/diagnostics/proc_profiler.py   ← YOU ARE HERE (Claude17 new file)
    ├─ Attached to: any TimerComponent via attach()
    ├─ Collects: wall time, user CPU, sys CPU per Proc() call
    ├─ Reports: hotspot analysis, timing distributions
    └─ Consumed by: MonitorComponent, StructuredLogger

Apollo reference:
    cyber/croutine/routine.h — coroutine timing
    cyber/statistics/statistics.h — channel/component stats

Design notes:
    - Uses os.times() for CPU measurement (no external deps)
    - Minimal overhead: only timestamps + subtraction per call
    - Configurable sampling rate to reduce overhead further
    - Thread-safe: each profiler instance bound to one component
"""

from __future__ import annotations

import os
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple


_DEFAULT_WINDOW = 500
_DEFAULT_SAMPLE_RATE = 1.0  # 1.0 = profile every call


@dataclass
class ProcProfile:
    """Single Proc() execution profile."""
    sequence: int
    wall_ms: float
    user_cpu_ms: float = 0.0
    sys_cpu_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)

    @property
    def total_cpu_ms(self) -> float:
        return self.user_cpu_ms + self.sys_cpu_ms

    @property
    def cpu_efficiency(self) -> float:
        """Ratio of CPU time to wall time. <1.0 means I/O waiting."""
        if self.wall_ms <= 0:
            return 0.0
        return min(self.total_cpu_ms / self.wall_ms, 2.0)


class ProcProfiler:
    """Lightweight profiler for TimerComponent.Proc() calls.

    Claude17: Collects timing data with minimal overhead and
    provides analysis methods for hotspot detection.

    Usage::

        profiler = ProcProfiler(component_name="canbus")
        profiler.begin()
        # ... Proc() runs ...
        profiler.end(sequence=42)

        report = profiler.report()
    """

    def __init__(
        self,
        component_name: str,
        window: int = _DEFAULT_WINDOW,
        sample_rate: float = _DEFAULT_SAMPLE_RATE,
    ) -> None:
        self._name = component_name
        self._window = window
        self._sample_rate = sample_rate
        self._profiles: Deque[ProcProfile] = deque(maxlen=window)
        self._lock = threading.Lock()

        # In-flight measurement state
        self._t0_wall: float = 0.0
        self._t0_user: float = 0.0
        self._t0_sys: float = 0.0
        self._sampling: bool = False
        self._call_count: int = 0
        self._sampled_count: int = 0

    def begin(self) -> None:
        """Mark the start of a Proc() call.

        Should be called immediately before Proc() executes.
        Respects sample_rate to skip profiling on some calls.
        """
        self._call_count += 1

        # Sampling decision
        if self._sample_rate < 1.0:
            import random
            if random.random() > self._sample_rate:
                self._sampling = False
                return

        self._sampling = True
        self._sampled_count += 1
        self._t0_wall = time.monotonic()
        try:
            times = os.times()
            self._t0_user = times.user
            self._t0_sys = times.system
        except Exception:
            self._t0_user = 0.0
            self._t0_sys = 0.0

    def end(self, sequence: int = 0) -> Optional[ProcProfile]:
        """Mark the end of a Proc() call and record the profile.

        Args:
            sequence: The component's sequence counter.

        Returns:
            ProcProfile if this call was sampled, None otherwise.
        """
        if not self._sampling:
            return None

        wall_ms = (time.monotonic() - self._t0_wall) * 1000.0

        user_cpu_ms = 0.0
        sys_cpu_ms = 0.0
        try:
            times = os.times()
            user_cpu_ms = (times.user - self._t0_user) * 1000.0
            sys_cpu_ms = (times.system - self._t0_sys) * 1000.0
        except Exception:
            pass

        profile = ProcProfile(
            sequence=sequence,
            wall_ms=round(wall_ms, 3),
            user_cpu_ms=round(user_cpu_ms, 3),
            sys_cpu_ms=round(sys_cpu_ms, 3),
        )

        with self._lock:
            self._profiles.append(profile)

        self._sampling = False
        return profile

    def report(self) -> Dict[str, Any]:
        """Generate a profiling report from collected data.

        Returns comprehensive timing statistics including:
        - Wall time distribution (mean, p50, p95, p99, max)
        - CPU time distribution
        - CPU efficiency (ratio of CPU to wall time)
        - Hotspot identification (slowest calls)
        """
        with self._lock:
            profiles = list(self._profiles)

        if not profiles:
            return {
                "component": self._name,
                "call_count": self._call_count,
                "sampled_count": self._sampled_count,
                "profile_count": 0,
            }

        wall_times = [p.wall_ms for p in profiles]
        cpu_times = [p.total_cpu_ms for p in profiles]
        efficiencies = [p.cpu_efficiency for p in profiles]

        def _percentile(data: List[float], pct: float) -> float:
            if not data:
                return 0.0
            s = sorted(data)
            idx = int(len(s) * pct)
            return s[min(idx, len(s) - 1)]

        # Find top 5 slowest calls
        slowest = sorted(profiles, key=lambda p: p.wall_ms, reverse=True)[:5]

        return {
            "component": self._name,
            "call_count": self._call_count,
            "sampled_count": self._sampled_count,
            "profile_count": len(profiles),
            "sample_rate": self._sample_rate,
            "wall_time": {
                "mean_ms": round(statistics.mean(wall_times), 3),
                "median_ms": round(statistics.median(wall_times), 3),
                "p95_ms": round(_percentile(wall_times, 0.95), 3),
                "p99_ms": round(_percentile(wall_times, 0.99), 3),
                "max_ms": round(max(wall_times), 3),
                "min_ms": round(min(wall_times), 3),
                "stddev_ms": round(
                    statistics.stdev(wall_times), 3
                ) if len(wall_times) > 1 else 0.0,
            },
            "cpu_time": {
                "mean_ms": round(statistics.mean(cpu_times), 3),
                "max_ms": round(max(cpu_times), 3),
            },
            "cpu_efficiency": {
                "mean": round(statistics.mean(efficiencies), 4),
                "min": round(min(efficiencies), 4),
            },
            "slowest_calls": [
                {
                    "seq": p.sequence,
                    "wall_ms": p.wall_ms,
                    "cpu_ms": round(p.total_cpu_ms, 3),
                }
                for p in slowest
            ],
        }

    def reset(self) -> None:
        """Clear all collected profiles."""
        with self._lock:
            self._profiles.clear()
        self._call_count = 0
        self._sampled_count = 0

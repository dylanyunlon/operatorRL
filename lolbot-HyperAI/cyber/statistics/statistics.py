"""
CyberStatistics — Runtime performance statistics collector.
=============================================================

Maps Apollo's ``cyber::statistics::Statistics`` to Python: singleton
that collects Proc() latencies, channel throughput, and component
health metrics from all TimerComponents in the system.

Architecture position:
    cyber/statistics/statistics.py   ← YOU ARE HERE
    ├─ Fed by: cyber/component/timer_component.py (per-tick latency)
    ├─ Fed by: cyber/node/node.py (channel write counts)
    ├─ Queried by: modules/monitor/monitor_component.py
    └─ Queried by: modules/dreamview/api/dreamview_api.py

Apollo reference:
    cyber/statistics/statistics.h — Statistics::SamplingProcLatency()
"""

from __future__ import annotations

import logging
import statistics as pystats
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_WINDOW_SIZE: int = 1000
_CHANNEL_WINDOW: int = 500
_INSTANCE_LOCK = threading.Lock()
_INSTANCE: Optional["Statistics"] = None


@dataclass
class ProcLatencySample:
    """Single Proc() latency measurement."""
    component_name: str
    latency_us: int
    timestamp_ns: int
    success: bool


@dataclass
class ChannelThroughput:
    """Per-channel write throughput tracker."""
    write_count: int = 0
    byte_count: int = 0
    _timestamps: Deque[float] = field(
        default_factory=lambda: deque(maxlen=_CHANNEL_WINDOW)
    )

    def record_write(self, byte_size: int = 0) -> None:
        self.write_count += 1
        self.byte_count += byte_size
        self._timestamps.append(time.monotonic())

    @property
    def writes_per_second(self) -> float:
        if len(self._timestamps) < 2:
            return 0.0
        span = self._timestamps[-1] - self._timestamps[0]
        if span <= 0:
            return 0.0
        return (len(self._timestamps) - 1) / span


class ComponentStats:
    """Aggregated statistics for a single component."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._latencies: Deque[int] = deque(maxlen=_WINDOW_SIZE)
        self.total_procs: int = 0
        self.total_failures: int = 0
        self.total_overruns: int = 0
        self._lock = threading.Lock()

    def sample_latency(self, latency_us: int, success: bool) -> None:
        with self._lock:
            self._latencies.append(latency_us)
            self.total_procs += 1
            if not success:
                self.total_failures += 1

    def record_overrun(self) -> None:
        with self._lock:
            self.total_overruns += 1

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            lats = list(self._latencies)

        if not lats:
            return {
                "name": self.name, "total_procs": self.total_procs,
                "total_failures": self.total_failures,
                "total_overruns": self.total_overruns,
                "mean_us": 0, "p50_us": 0, "p95_us": 0,
                "p99_us": 0, "max_us": 0,
            }

        sorted_lats = sorted(lats)
        n = len(sorted_lats)
        return {
            "name": self.name,
            "total_procs": self.total_procs,
            "total_failures": self.total_failures,
            "total_overruns": self.total_overruns,
            "window_size": n,
            "mean_us": round(pystats.mean(lats), 1),
            "p50_us": sorted_lats[n // 2],
            "p95_us": sorted_lats[min(int(n * 0.95), n - 1)],
            "p99_us": sorted_lats[min(int(n * 0.99), n - 1)],
            "max_us": sorted_lats[-1],
        }


class Statistics:
    """Singleton runtime statistics collector.

    All TimerComponents and CyberNode channels report metrics here.
    The monitor component and dreamview dashboard query this for
    system health visualization.

    Usage::

        stats = Statistics.instance()
        stats.sample_proc_latency("perception", 1200, True)
        stats.record_channel_write("/lol/game_state", 4096)
        snapshot = stats.full_snapshot()
    """

    def __init__(self) -> None:
        self._components: Dict[str, ComponentStats] = {}
        self._channels: Dict[str, ChannelThroughput] = defaultdict(
            ChannelThroughput
        )
        self._lock = threading.Lock()
        self._start_time = time.monotonic()
        self._custom_counters: Dict[str, int] = defaultdict(int)
        self._custom_gauges: Dict[str, float] = {}

    @classmethod
    def instance(cls) -> "Statistics":
        """Get or create the singleton instance."""
        global _INSTANCE
        if _INSTANCE is None:
            with _INSTANCE_LOCK:
                if _INSTANCE is None:
                    _INSTANCE = cls()
        return _INSTANCE

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        global _INSTANCE
        with _INSTANCE_LOCK:
            _INSTANCE = None

    # ─── Component metrics ───────────────────────────────────────────────

    def sample_proc_latency(
        self,
        component_name: str,
        latency_us: int,
        success: bool = True,
    ) -> None:
        """Record a single Proc() latency sample.

        Args:
            component_name: The component that produced the sample.
            latency_us: Latency in microseconds.
            success: Whether Proc() returned True.
        """
        with self._lock:
            if component_name not in self._components:
                self._components[component_name] = ComponentStats(
                    component_name
                )
            self._components[component_name].sample_latency(
                latency_us, success
            )

    def record_overrun(self, component_name: str) -> None:
        with self._lock:
            if component_name not in self._components:
                self._components[component_name] = ComponentStats(
                    component_name
                )
            self._components[component_name].record_overrun()

    # ─── Channel metrics ─────────────────────────────────────────────────

    def record_channel_write(
        self, channel: str, byte_size: int = 0
    ) -> None:
        with self._lock:
            self._channels[channel].record_write(byte_size)

    # ─── Custom metrics ──────────────────────────────────────────────────

    def increment_counter(self, name: str, delta: int = 1) -> None:
        with self._lock:
            self._custom_counters[name] += delta

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._custom_gauges[name] = value

    def get_counter(self, name: str) -> int:
        with self._lock:
            return self._custom_counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        with self._lock:
            return self._custom_gauges.get(name, 0.0)

    # ─── Snapshots ───────────────────────────────────────────────────────

    def component_snapshot(self, name: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            comp = self._components.get(name)
        return comp.snapshot() if comp else None

    def channel_snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                ch: {
                    "write_count": info.write_count,
                    "byte_count": info.byte_count,
                    "writes_per_second": round(info.writes_per_second, 1),
                }
                for ch, info in self._channels.items()
            }

    def full_snapshot(self) -> Dict[str, Any]:
        """Return complete statistics snapshot for all components."""
        with self._lock:
            comp_snapshots = {
                name: comp.snapshot()
                for name, comp in self._components.items()
            }
            channel_data = {
                ch: {
                    "write_count": info.write_count,
                    "writes_per_second": round(info.writes_per_second, 1),
                }
                for ch, info in self._channels.items()
            }
            counters = dict(self._custom_counters)
            gauges = dict(self._custom_gauges)

        uptime = time.monotonic() - self._start_time
        return {
            "uptime_s": round(uptime, 1),
            "component_count": len(comp_snapshots),
            "channel_count": len(channel_data),
            "components": comp_snapshots,
            "channels": channel_data,
            "counters": counters,
            "gauges": gauges,
        }

    def health_summary(self) -> Dict[str, str]:
        """Quick health check: component_name → 'ok'|'degraded'|'error'."""
        result = {}
        with self._lock:
            for name, comp in self._components.items():
                snap = comp.snapshot()
                failure_rate = (
                    snap["total_failures"] / max(snap["total_procs"], 1)
                )
                if failure_rate > 0.5:
                    result[name] = "error"
                elif failure_rate > 0.1 or snap["total_overruns"] > 10:
                    result[name] = "degraded"
                else:
                    result[name] = "ok"
        return result

    def __repr__(self) -> str:
        return (
            f"<Statistics components={len(self._components)} "
            f"channels={len(self._channels)}>"
        )

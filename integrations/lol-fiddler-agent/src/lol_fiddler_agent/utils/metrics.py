"""
Metrics Collector - Application-level metrics for monitoring.

Provides counters, gauges, and histograms for tracking system health
and performance. Exports to Prometheus format when available.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class MetricValue:
    """A single metric measurement."""
    name: str
    value: float
    timestamp: float = field(default_factory=time.time)
    labels: dict[str, str] = field(default_factory=dict)
    metric_type: str = "gauge"  # gauge, counter, histogram


class Counter:
    """Monotonically increasing counter."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._value: float = 0.0

    def inc(self, amount: float = 1.0) -> None:
        self._value += amount

    @property
    def value(self) -> float:
        return self._value

    def reset(self) -> None:
        self._value = 0.0


class Gauge:
    """Value that can go up and down."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._value: float = 0.0

    def set(self, value: float) -> None:
        self._value = value

    def inc(self, amount: float = 1.0) -> None:
        self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        self._value -= amount

    @property
    def value(self) -> float:
        return self._value


class Histogram:
    """Tracks value distribution with configurable buckets."""

    def __init__(
        self, name: str, description: str = "",
        buckets: Optional[list[float]] = None,
    ) -> None:
        self.name = name
        self.description = description
        self._buckets = buckets or [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        self._counts: dict[float, int] = {b: 0 for b in self._buckets}
        self._counts[float("inf")] = 0
        self._sum: float = 0.0
        self._count: int = 0

    def observe(self, value: float) -> None:
        self._sum += value
        self._count += 1
        for bucket in self._buckets:
            if value <= bucket:
                self._counts[bucket] += 1
        self._counts[float("inf")] += 1

    @property
    def count(self) -> int:
        return self._count

    @property
    def sum(self) -> float:
        return self._sum

    @property
    def avg(self) -> float:
        if self._count == 0:
            return 0.0
        return self._sum / self._count


class MetricsCollector:
    """Central metrics registry.

    Example::

        metrics = MetricsCollector()
        advice_count = metrics.counter("advice_total", "Total advice given")
        game_time = metrics.gauge("game_time_seconds", "Current game time")
        latency = metrics.histogram("inference_latency", "ML inference time")

        advice_count.inc()
        game_time.set(300.5)
        latency.observe(0.023)
    """

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}

    def counter(self, name: str, description: str = "") -> Counter:
        if name not in self._counters:
            self._counters[name] = Counter(name, description)
        return self._counters[name]

    def gauge(self, name: str, description: str = "") -> Gauge:
        if name not in self._gauges:
            self._gauges[name] = Gauge(name, description)
        return self._gauges[name]

    def histogram(self, name: str, description: str = "", buckets: Optional[list[float]] = None) -> Histogram:
        if name not in self._histograms:
            self._histograms[name] = Histogram(name, description, buckets)
        return self._histograms[name]

    def get_all(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for name, c in self._counters.items():
            result[name] = {"type": "counter", "value": c.value}
        for name, g in self._gauges.items():
            result[name] = {"type": "gauge", "value": g.value}
        for name, h in self._histograms.items():
            result[name] = {"type": "histogram", "count": h.count, "sum": h.sum, "avg": h.avg}
        return result

    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines: list[str] = []
        for name, c in self._counters.items():
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {c.value}")
        for name, g in self._gauges.items():
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {g.value}")
        for name, h in self._histograms.items():
            lines.append(f"# TYPE {name} histogram")
            lines.append(f"{name}_count {h.count}")
            lines.append(f"{name}_sum {h.sum}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        for c in self._counters.values():
            c.reset()


# Global metrics instance
_global_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    global _global_metrics
    if _global_metrics is None:
        _global_metrics = MetricsCollector()
    return _global_metrics

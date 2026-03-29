"""
Evolution Metrics Exporter — Prometheus/OpenTelemetry metrics output.

Location: agentos/governance/evolution_metrics_exporter.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.evolution_metrics_exporter.v1"

class EvolutionMetricsExporter:
    """Export evolution metrics in Prometheus text format."""

    def __init__(self, namespace: str = "operatorrl") -> None:
        self.namespace = namespace
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def inc_counter(self, name: str, value: float = 1.0) -> None:
        key = f"{self.namespace}_{name}_total"
        self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float) -> None:
        key = f"{self.namespace}_{name}"
        self._gauges[key] = value

    def export_prometheus(self) -> str:
        lines = []
        for k, v in sorted(self._counters.items()):
            lines.append(f"# TYPE {k} counter")
            lines.append(f"{k} {v}")
        for k, v in sorted(self._gauges.items()):
            lines.append(f"# TYPE {k} gauge")
            lines.append(f"{k} {v}")
        self._fire_evolution("metrics_exported", {"counter_count": len(self._counters), "gauge_count": len(self._gauges)})
        return "\n".join(lines)

    def metric_count(self) -> int:
        return len(self._counters) + len(self._gauges)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

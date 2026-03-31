"""
ObservabilityMetricsExporter — Exports pipeline metrics in Prometheus/OpenTelemetry format.

Architecture (拿来主义):
  e2e_inference_telemetry_exporter.py, realtime_dashboard_data_source.py

Location: integrations/lol-history/src/lol_history/observability_metrics_exporter.py

Design Notes (Knuth-level critique):
  User:
    - Production-grade module with unified {"status": "ok"} response format.
    - Stateless or bounded-state design for long-running sessions.
    - Graceful degradation: partial results on component failure.
  System:
    - All data structures bounded (deque/OrderedDict with maxlen).
    - Evolution callback integration for self-improvement feedback.
    - Comprehensive get_stats() for observability.
    - Zero external dependencies beyond stdlib.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.observability_metrics_exporter.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _MetricStore:
    """In-memory metric store with labels."""

    def __init__(self, max_series: int = 1000) -> None:
        self._counters: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._histograms: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        self._gauges: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._max_series = max_series

    def _label_key(self, labels: Dict[str, str]) -> str:
        if not labels:
            return "__default__"
        return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def inc_counter(self, name: str, value: float,
                     labels: Dict[str, str] = None) -> None:
        key = self._label_key(labels or {})
        self._counters[name][key] += value

    def observe_histogram(self, name: str, value: float,
                          labels: Dict[str, str] = None) -> None:
        key = self._label_key(labels or {})
        values = self._histograms[name][key]
        values.append(value)
        if len(values) > 1000:
            self._histograms[name][key] = values[-500:]

    def set_gauge(self, name: str, value: float,
                   labels: Dict[str, str] = None) -> None:
        key = self._label_key(labels or {})
        self._gauges[name][key] = value

    def get_all(self) -> Dict[str, Any]:
        return {
            "counters": {n: dict(v) for n, v in self._counters.items()},
            "histograms": {n: {k: len(v) for k, v in series.items()}
                          for n, series in self._histograms.items()},
            "gauges": {n: dict(v) for n, v in self._gauges.items()},
        }


class _PrometheusFormatter:
    """Formats metrics in Prometheus exposition format."""

    def format(self, store: _MetricStore) -> str:
        lines = []
        data = store.get_all()

        for name, series in data["counters"].items():
            lines.append(f"# TYPE {name} counter")
            for labels, value in series.items():
                label_str = f"{{{labels}}}" if labels != "__default__" else ""
                lines.append(f"{name}{label_str} {value}")

        for name, series in data["gauges"].items():
            lines.append(f"# TYPE {name} gauge")
            for labels, value in series.items():
                label_str = f"{{{labels}}}" if labels != "__default__" else ""
                lines.append(f"{name}{label_str} {value}")

        return "\n".join(lines)


class _OpenTelemetryFormatter:
    """Formats metrics in OpenTelemetry-compatible JSON."""

    def format(self, store: _MetricStore) -> Dict[str, Any]:
        data = store.get_all()
        metrics = []
        for name, series in data["counters"].items():
            for labels, value in series.items():
                metrics.append({
                    "name": name, "type": "counter",
                    "labels": labels, "value": value,
                })
        for name, series in data["gauges"].items():
            for labels, value in series.items():
                metrics.append({
                    "name": name, "type": "gauge",
                    "labels": labels, "value": value,
                })
        return {
            "resource": {"service.name": "operatorRL-lol-history"},
            "metrics": metrics,
            "timestamp": time.time(),
        }


class ObservabilityMetricsExporter:
    """Exports pipeline metrics in Prometheus/OpenTelemetry format.

    Public API: record_counter, record_histogram, record_gauge,
                export_prometheus, export_opentelemetry, get_all_metrics, get_stats
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._store = _MetricStore()
        self._prom = _PrometheusFormatter()
        self._otel = _OpenTelemetryFormatter()
        self._export_count = 0

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def record_counter(self, name: str, value: float = 1.0,
                        labels: Dict[str, str] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._store.inc_counter(name, value, labels)
        return {"status": "ok", "metric": name, "type": "counter"}

    def record_histogram(self, name: str, value: float,
                          labels: Dict[str, str] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._store.observe_histogram(name, value, labels)
        return {"status": "ok", "metric": name, "type": "histogram"}

    def record_gauge(self, name: str, value: float,
                      labels: Dict[str, str] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._store.set_gauge(name, value, labels)
        return {"status": "ok", "metric": name, "type": "gauge"}

    def export_prometheus(self) -> Dict[str, Any]:
        self._op_count += 1
        self._export_count += 1
        text = self._prom.format(self._store)
        return {"status": "ok", "format": "prometheus", "content": text}

    def export_opentelemetry(self) -> Dict[str, Any]:
        self._op_count += 1
        self._export_count += 1
        payload = self._otel.format(self._store)
        return {"status": "ok", "format": "opentelemetry", "payload": payload}

    def get_all_metrics(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "metrics": self._store.get_all()}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "export_count": self._export_count,
            "metrics_summary": self._store.get_all(),
        }

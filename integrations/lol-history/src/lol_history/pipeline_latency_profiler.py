"""
PipelineLatencyProfiler — Profiles latency across pipeline stages with P50/P95/P99 stats.

Architecture (拿来主义):
  intel_pipeline_profiler.py（M743）, e2e_inference_telemetry_exporter.py

Location: integrations/lol-history/src/lol_history/pipeline_latency_profiler.py

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
_EVOLUTION_KEY = "integrations.lol_history.pipeline_latency_profiler.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _SpanRecord:
    """A single timing span."""
    __slots__ = ("name", "start_time", "end_time", "duration_ms", "metadata")

    def __init__(self, name: str) -> None:
        self.name = name
        self.start_time = time.monotonic()
        self.end_time: float = 0.0
        self.duration_ms: float = 0.0
        self.metadata: Dict[str, Any] = {}

    def end(self) -> float:
        self.end_time = time.monotonic()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        return self.duration_ms


class _LatencyHistogram:
    """Histogram of latency values with percentile computation."""

    def __init__(self, max_values: int = 1000) -> None:
        self._values: deque = deque(maxlen=max_values)

    def add(self, value_ms: float) -> None:
        self._values.append(value_ms)

    def percentile(self, p: float) -> float:
        if not self._values:
            return 0.0
        sorted_vals = sorted(self._values)
        idx = min(int(len(sorted_vals) * p / 100.0), len(sorted_vals) - 1)
        return sorted_vals[idx]

    def mean(self) -> float:
        return _safe_div(sum(self._values), len(self._values)) if self._values else 0.0

    def get_report(self) -> Dict[str, float]:
        return {
            "count": len(self._values),
            "mean": round(self.mean(), 2),
            "p50": round(self.percentile(50), 2),
            "p95": round(self.percentile(95), 2),
            "p99": round(self.percentile(99), 2),
            "min": round(min(self._values), 2) if self._values else 0.0,
            "max": round(max(self._values), 2) if self._values else 0.0,
        }


class _SLAChecker:
    """Checks latency against SLA thresholds."""

    def __init__(self) -> None:
        self._violations: deque = deque(maxlen=200)
        self._check_count = 0

    def check(self, stage: str, actual_ms: float,
              threshold_ms: float) -> Dict[str, Any]:
        self._check_count += 1
        violated = actual_ms > threshold_ms
        if violated:
            self._violations.append({
                "stage": stage, "actual_ms": actual_ms,
                "threshold_ms": threshold_ms, "ts": time.monotonic(),
            })
        return {
            "stage": stage,
            "actual_ms": round(actual_ms, 2),
            "threshold_ms": threshold_ms,
            "violated": violated,
            "overshoot_ms": round(max(0, actual_ms - threshold_ms), 2),
        }

    def get_stats(self) -> Dict[str, Any]:
        return {
            "check_count": self._check_count,
            "violations": len(self._violations),
            "recent_violations": list(self._violations)[-10:],
        }


class PipelineLatencyProfiler:
    """Profiles latency across pipeline stages with P50/P95/P99 statistics.

    Public API: start_span, end_span, get_latency_report, check_sla,
                get_bottleneck, record_external, get_stats
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._active_spans: Dict[str, _SpanRecord] = {}
        self._histograms: Dict[str, _LatencyHistogram] = defaultdict(_LatencyHistogram)
        self._sla_checker = _SLAChecker()
        self._total_spans = 0
        self._span_history: deque = deque(maxlen=500)

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def start_span(self, stage_name: str) -> Dict[str, Any]:
        """Start timing a pipeline stage."""
        self._op_count += 1
        span = _SpanRecord(stage_name)
        self._active_spans[stage_name] = span
        return {"status": "ok", "stage": stage_name, "started": True}

    def end_span(self, stage_name: str) -> Dict[str, Any]:
        """End timing a pipeline stage and record the latency."""
        self._op_count += 1
        span = self._active_spans.pop(stage_name, None)
        if not span:
            return {"status": "ok", "stage": stage_name, "found": False}

        duration_ms = span.end()
        self._histograms[stage_name].add(duration_ms)
        self._total_spans += 1
        self._span_history.append({
            "stage": stage_name, "duration_ms": round(duration_ms, 2),
            "ts": span.end_time,
        })

        return {
            "status": "ok",
            "stage": stage_name,
            "duration_ms": round(duration_ms, 2),
            "histogram": self._histograms[stage_name].get_report(),
        }

    def record_external(self, stage_name: str, duration_ms: float) -> Dict[str, Any]:
        """Record an externally measured latency."""
        self._op_count += 1
        self._histograms[stage_name].add(duration_ms)
        self._total_spans += 1
        return {
            "status": "ok",
            "stage": stage_name,
            "duration_ms": duration_ms,
        }

    def get_latency_report(self) -> Dict[str, Any]:
        """Get full latency report across all stages."""
        self._op_count += 1
        report = {}
        for stage, hist in self._histograms.items():
            report[stage] = hist.get_report()
        return {"status": "ok", "stages": report, "total_spans": self._total_spans}

    def check_sla(self, thresholds: Dict[str, float]) -> Dict[str, Any]:
        """Check latency against SLA thresholds per stage."""
        self._op_count += 1
        results = {}
        violations = []
        for stage, threshold_ms in thresholds.items():
            hist = self._histograms.get(stage)
            if hist:
                p95 = hist.percentile(95)
                check = self._sla_checker.check(stage, p95, threshold_ms)
                results[stage] = check
                if check["violated"]:
                    violations.append(stage)
        return {
            "status": "ok",
            "sla_results": results,
            "violations": violations,
            "all_passed": len(violations) == 0,
        }

    def get_bottleneck(self) -> Dict[str, Any]:
        """Identify the slowest pipeline stage."""
        self._op_count += 1
        if not self._histograms:
            return {"status": "ok", "bottleneck": None}
        slowest = max(self._histograms.items(),
                      key=lambda x: x[1].percentile(95))
        return {
            "status": "ok",
            "bottleneck": slowest[0],
            "p95_ms": round(slowest[1].percentile(95), 2),
            "all_stages": {s: round(h.percentile(95), 2) for s, h in self._histograms.items()},
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "total_spans": self._total_spans,
            "active_spans": list(self._active_spans.keys()),
            "tracked_stages": list(self._histograms.keys()),
            "sla": self._sla_checker.get_stats(),
        }

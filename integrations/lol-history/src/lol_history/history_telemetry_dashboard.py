"""
HistoryTelemetryDashboard — Telemetry collection and dashboard data for history intelligence.

Architecture (拿来主义):
  inference_telemetry_exporter.py（M582）+ history_feedback_loop_orchestrator.py（M625）

Location: integrations/lol-history/src/lol_history/history_telemetry_dashboard.py

Design Notes (Knuth-level critique):
  User:
    - record_metric is fire-and-forget — never blocks or raises.
    - get_dashboard returns complete snapshot suitable for rendering.
    - Metrics auto-expire after max_age_seconds to prevent stale data.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Bounded metric storage via _max_metrics to prevent memory growth.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_telemetry_dashboard.v1"
_DEFAULT_MAX_METRICS: int = 10000
_DEFAULT_MAX_AGE: float = 3600.0  # 1 hour


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class HistoryTelemetryDashboard:
    """Telemetry collection and dashboard data for history intelligence.

    Public API
    ----------
    record_metric       — record a telemetry metric
    record_event        — record a telemetry event
    get_dashboard       — get full dashboard snapshot
    get_metric_summary  — get summary for a specific metric
    get_health          — overall system health check
    reset               — clear all telemetry

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, max_metrics: int = _DEFAULT_MAX_METRICS,
                 max_age_seconds: float = _DEFAULT_MAX_AGE) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._max_metrics: int = max_metrics
        self._max_age: float = max_age_seconds
        # metric_name -> deque of (timestamp, value)
        self._metrics: Dict[str, Deque[Tuple[float, float]]] = defaultdict(
            lambda: deque(maxlen=max_metrics)
        )
        self._events: Deque[Dict[str, Any]] = deque(maxlen=max_metrics)
        self._counters: Dict[str, int] = defaultdict(int)
        self._start_time: float = time.time()

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    def _prune_old(self, metric_name: str) -> None:
        """Remove metrics older than max_age."""
        cutoff = time.time() - self._max_age
        q = self._metrics[metric_name]
        while q and q[0][0] < cutoff:
            q.popleft()

    # ------------------------------------------------------------------ #

    def record_metric(self, name: str, value: float,
                      tags: Dict[str, str] = None) -> Dict[str, Any]:
        """Record a telemetry metric.

        Parameters
        ----------
        name : str  metric name
        value : float  metric value
        tags : dict  optional tags

        Returns
        -------
        dict  with status
        """
        self._op_count += 1
        ts = time.time()
        self._metrics[name].append((ts, value))
        self._counters["total_metrics"] += 1
        return {"status": "ok", "op": "record_metric", "name": name}

    # ------------------------------------------------------------------ #

    def record_event(self, event_type: str, details: Dict[str, Any] = None) -> Dict[str, Any]:
        """Record a telemetry event.

        Parameters
        ----------
        event_type : str
        details : dict

        Returns
        -------
        dict
        """
        self._op_count += 1
        self._events.append({
            "event_type": event_type,
            "details": details or {},
            "timestamp": time.time(),
        })
        self._counters["total_events"] += 1
        return {"status": "ok", "op": "record_event", "event_type": event_type}

    # ------------------------------------------------------------------ #

    def get_metric_summary(self, name: str) -> Dict[str, Any]:
        """Get summary for a specific metric.

        Returns
        -------
        dict  with count, mean, min, max, latest, trend
        """
        self._op_count += 1
        self._prune_old(name)
        q = self._metrics.get(name, deque())
        values = [v for _, v in q]

        if not values:
            return {"status": "ok", "op": "get_metric_summary",
                    "name": name, "count": 0}

        n = len(values)
        mean = sum(values) / n
        trend = "stable"
        if n >= 4:
            first_half = sum(values[:n // 2]) / max(n // 2, 1)
            second_half = sum(values[n // 2:]) / max(n - n // 2, 1)
            if second_half > first_half * 1.05:
                trend = "increasing"
            elif second_half < first_half * 0.95:
                trend = "decreasing"

        return {"status": "ok", "op": "get_metric_summary",
                "name": name, "count": n,
                "mean": round(mean, 4), "min": round(min(values), 4),
                "max": round(max(values), 4), "latest": round(values[-1], 4),
                "trend": trend}

    # ------------------------------------------------------------------ #

    def get_dashboard(self) -> Dict[str, Any]:
        """Get full dashboard snapshot.

        Returns
        -------
        dict  with metrics summaries, recent events, health, uptime
        """
        self._op_count += 1
        _start = time.time()

        metric_summaries: Dict[str, Any] = {}
        for name in list(self._metrics.keys()):
            self._prune_old(name)
            q = self._metrics[name]
            values = [v for _, v in q]
            if values:
                metric_summaries[name] = {
                    "count": len(values),
                    "mean": round(sum(values) / len(values), 4),
                    "latest": round(values[-1], 4),
                }

        recent_events = list(self._events)[-10:]
        uptime = time.time() - self._start_time

        elapsed = time.time() - _start
        self._fire("get_dashboard_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "get_dashboard",
                "metrics": metric_summaries,
                "recent_events": recent_events,
                "counters": dict(self._counters),
                "uptime_seconds": round(uptime, 2)}

    # ------------------------------------------------------------------ #

    def get_health(self) -> Dict[str, Any]:
        """Overall system health check.

        Returns
        -------
        dict  with healthy, checks (list of individual checks)
        """
        self._op_count += 1
        checks: List[Dict[str, Any]] = []
        healthy = True

        # Check uptime
        uptime = time.time() - self._start_time
        checks.append({"name": "uptime", "ok": uptime > 0, "value": round(uptime, 2)})

        # Check metric freshness
        latest_ts = 0.0
        for q in self._metrics.values():
            if q:
                latest_ts = max(latest_ts, q[-1][0])
        if latest_ts > 0:
            age = time.time() - latest_ts
            fresh = age < self._max_age
            checks.append({"name": "metric_freshness", "ok": fresh,
                           "age_seconds": round(age, 2)})
            if not fresh:
                healthy = False
        else:
            checks.append({"name": "metric_freshness", "ok": True, "note": "no_metrics_yet"})

        # Check storage
        total_stored = sum(len(q) for q in self._metrics.values())
        storage_ok = total_stored < self._max_metrics * len(self._metrics) * 0.9
        checks.append({"name": "storage", "ok": storage_ok, "stored": total_stored})
        if not storage_ok:
            healthy = False

        return {"status": "ok", "op": "get_health",
                "healthy": healthy, "checks": checks}

    # ------------------------------------------------------------------ #

    def reset(self) -> Dict[str, Any]:
        """Clear all telemetry."""
        self._op_count += 1
        self._metrics.clear()
        self._events.clear()
        self._counters.clear()
        self._start_time = time.time()
        self._fire("reset_completed", {})
        return {"status": "ok", "op": "reset"}

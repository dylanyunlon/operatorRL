"""
CrossGameTelemetryAggregator — Aggregate telemetry from different game adapters.

Collects and unifies telemetry data (latency, throughput, error rates) from
all game protocol adapters into a single dashboard-ready format.

Location: extensions/protocol_decoder/src/cross_game_telemetry_aggregator.py

Reference (拿来主義):
  - integrations/lol-history/src/lol_history/e2e_inference_telemetry_exporter.py（M664）:
    telemetry export
  - integrations/lol-history/src/lol_history/history_telemetry_dashboard.py（M643）:
    telemetry aggregation

Design Notes (Knuth-level critique):
  User:
    - record() accepts any game_type + metric_name + value — fully flexible.
    - get_summary() provides per-game and cross-game aggregates.
    - export_json/csv for external consumption.
  System:
    - Sliding window per metric for memory bounding.
    - O(1) record, O(W) summary where W = window size.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.cross_game_telemetry_aggregator.v1"

_DEFAULT_WINDOW_SIZE: int = 1000


class CrossGameTelemetryAggregator:
    """Cross-game telemetry aggregator.

    Public API:
        record(game_type, metric, value)
        get_summary(game_type=None) -> dict
        get_metric(game_type, metric) -> dict
        export_json() -> str
        export_csv_rows() -> list[dict]
        clear(game_type=None)
        get_stats() -> dict
    """

    def __init__(self, window_size: int = _DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = window_size
        # game_type → metric_name → list of (ts, value)
        self._data: Dict[str, Dict[str, List[tuple]]] = defaultdict(lambda: defaultdict(list))
        self._record_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def record(self, game_type: str, metric: str, value: float) -> None:
        """Record a telemetry data point."""
        self._record_count += 1
        buf = self._data[game_type][metric]
        buf.append((time.time(), value))
        # Trim to window
        if len(buf) > self._window_size:
            self._data[game_type][metric] = buf[-self._window_size:]

    def get_metric(self, game_type: str, metric: str) -> Dict[str, Any]:
        """Get aggregated stats for a specific metric."""
        buf = self._data.get(game_type, {}).get(metric, [])
        if not buf:
            return {"count": 0}
        values = [v for _, v in buf]
        return {
            "count": len(values),
            "mean": sum(values) / len(values),
            "min": min(values),
            "max": max(values),
            "latest": values[-1],
            "latest_ts": buf[-1][0],
        }

    def get_summary(self, game_type: Optional[str] = None) -> Dict[str, Any]:
        """Get summary across all games or a specific game."""
        result: Dict[str, Any] = {}

        games = [game_type] if game_type else list(self._data.keys())
        for gt in games:
            metrics = self._data.get(gt, {})
            gt_summary = {}
            for metric_name in metrics:
                gt_summary[metric_name] = self.get_metric(gt, metric_name)
            result[gt] = gt_summary

        return {
            "games": result,
            "total_records": self._record_count,
            "game_count": len(self._data),
        }

    def export_json(self, indent: int = 2) -> str:
        return json.dumps(self.get_summary(), indent=indent, default=str)

    def export_csv_rows(self) -> List[Dict[str, Any]]:
        """Export as flat rows for CSV."""
        rows = []
        for gt, metrics in self._data.items():
            for metric_name, buf in metrics.items():
                for ts, val in buf:
                    rows.append({
                        "game_type": gt,
                        "metric": metric_name,
                        "value": val,
                        "timestamp": ts,
                    })
        return rows

    def clear(self, game_type: Optional[str] = None) -> None:
        if game_type:
            self._data.pop(game_type, None)
        else:
            self._data.clear()

    def get_stats(self) -> Dict[str, Any]:
        return {
            "record_count": self._record_count,
            "game_count": len(self._data),
            "window_size": self._window_size,
            "metrics_per_game": {
                gt: list(m.keys()) for gt, m in self._data.items()
            },
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")

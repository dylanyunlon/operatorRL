"""
RealtimeDashboardDataSource — Real-time data source for the decision monitoring dashboard.

Architecture (拿来主义):
  history_telemetry_dashboard.py（M643）— telemetry aggregation
  cross_game_telemetry_aggregator.py（M681）— ingest→export

Location: integrations/lol-history/src/lol_history/realtime_dashboard_data_source.py
"""
from __future__ import annotations
import json, logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.realtime_dashboard_data_source.v1"

class RealtimeDashboardDataSource:
    """Real-time data source for decision dashboard.

    Public API: push, get_snapshot, get_history, export_json, export_sse, get_stats
    """
    def __init__(self, buffer_size: int = 500) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._buffer: deque = deque(maxlen=buffer_size)
        self._latest: Dict[str, Any] = {}
        self._push_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def push(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        self._push_count += 1
        entry = {"timestamp": time.time(), **metrics}
        self._buffer.append(entry)
        self._latest.update(metrics)
        self._latest["_last_update"] = entry["timestamp"]
        return {"status": "ok", "buffered": len(self._buffer)}

    def get_snapshot(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "snapshot": dict(self._latest)}

    def get_history(self, n: int = 50) -> List[Dict]:
        self._op_count += 1
        return list(self._buffer)[-n:]

    def export_json(self) -> str:
        self._op_count += 1
        return json.dumps({"snapshot": self._latest, "history_size": len(self._buffer)}, default=str)

    def export_sse(self) -> str:
        self._op_count += 1
        data = json.dumps(self._latest, default=str)
        return f"event: dashboard_update\ndata: {data}\n\n"

    def get_stats(self) -> Dict[str, Any]:
        return {"pushes": self._push_count, "buffer_size": len(self._buffer),
                "metrics_tracked": len(self._latest), "total_ops": self._op_count}


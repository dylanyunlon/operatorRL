"""
Fiddler Snapshot Exporter — Export Fiddler captures to evolution training data.
Location: extensions/fiddler-bridge/src/fiddler_snapshot_exporter.py
Reference: Akagi MITM replay export, DI-star training data pipeline
"""
from __future__ import annotations
import json, logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_snapshot_exporter.v1"

class FiddlerSnapshotExporter:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def export(self, captures: list[dict[str, Any]], *, exclude_errors: bool = False) -> dict[str, Any]:
        if not captures:
            self._fire("export_empty", {})
            return {"spans": [], "export_timestamp": time.time(), "total_captures": 0}

        filtered = captures
        if exclude_errors:
            filtered = [c for c in captures if c.get("status", 200) < 400]

        # Sort by timestamp
        filtered.sort(key=lambda c: c.get("timestamp", 0))

        spans = []
        for c in filtered:
            body_str = c.get("body", "{}")
            try:
                state = json.loads(body_str) if isinstance(body_str, str) else body_str
            except (json.JSONDecodeError, TypeError):
                state = {"raw": body_str}

            spans.append({
                "state": state,
                "action": {"method": c.get("method", "GET"), "url": c.get("url", "")},
                "timestamp": c.get("timestamp", 0),
            })

        result = {"spans": spans, "export_timestamp": time.time(), "total_captures": len(captures)}
        self._fire("export_complete", {"spans": len(spans)})
        return result

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})

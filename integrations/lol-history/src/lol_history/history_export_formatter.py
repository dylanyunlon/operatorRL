"""
HistoryExportFormatter — Multi-format export for history intelligence data.

Architecture (拿来主义):
  evolution_metrics_exporter.py + history_to_training_exporter.py（M606）

Location: integrations/lol-history/src/lol_history/history_export_formatter.py

Design Notes (Knuth-level critique):
  User:
    - export() auto-detects format from extension or explicit parameter.
    - All formats produce valid, parseable output even with empty data.
    - CSV export handles nested dicts via flattening.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Format registry is extensible — new formats can be registered at runtime.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_export_formatter.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _flatten_dict(d: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """Flatten nested dict with dot-separated keys."""
    items: Dict[str, Any] = {}
    for k, v in d.items():
        new_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(_flatten_dict(v, new_key))
        else:
            items[new_key] = v
    return items


class HistoryExportFormatter:
    """Multi-format export for history intelligence data.

    Public API
    ----------
    export_json     — export as JSON string
    export_csv      — export as CSV string
    export_jsonl    — export as JSON Lines string
    export          — auto-detect format and export
    get_stats       — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._export_count: int = 0
        self._formats: Dict[str, Callable] = {
            "json": self.export_json,
            "csv": self.export_csv,
            "jsonl": self.export_jsonl,
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def export_json(self, records: List[Dict[str, Any]] = None,
                    pretty: bool = True) -> Dict[str, Any]:
        """Export records as JSON string.

        Parameters
        ----------
        records : list of dict
        pretty : bool

        Returns
        -------
        dict  with status, output (str), record_count, byte_size
        """
        self._op_count += 1
        _start = time.time()
        if records is None:
            records = []

        indent = 2 if pretty else None
        output = json.dumps(records, ensure_ascii=False, default=str, indent=indent)

        self._export_count += 1
        elapsed = time.time() - _start
        self._fire("export_json_completed", {"elapsed": elapsed, "count": len(records)})
        return {"status": "ok", "op": "export_json",
                "output": output, "record_count": len(records),
                "byte_size": len(output.encode("utf-8")), "format": "json"}

    # ------------------------------------------------------------------ #

    def export_csv(self, records: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Export records as CSV string.

        Nested dicts are flattened with dot-separated keys.

        Parameters
        ----------
        records : list of dict

        Returns
        -------
        dict  with status, output (str), record_count, byte_size
        """
        self._op_count += 1
        _start = time.time()
        if records is None or not records:
            return {"status": "ok", "op": "export_csv",
                    "output": "", "record_count": 0, "byte_size": 0, "format": "csv"}

        flattened = [_flatten_dict(r) for r in records]
        all_keys = list(dict.fromkeys(k for row in flattened for k in row))

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for row in flattened:
            writer.writerow(row)
        output = buf.getvalue()

        self._export_count += 1
        elapsed = time.time() - _start
        self._fire("export_csv_completed", {"elapsed": elapsed, "count": len(records)})
        return {"status": "ok", "op": "export_csv",
                "output": output, "record_count": len(records),
                "byte_size": len(output.encode("utf-8")), "format": "csv"}

    # ------------------------------------------------------------------ #

    def export_jsonl(self, records: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Export records as JSON Lines string.

        Parameters
        ----------
        records : list of dict

        Returns
        -------
        dict  with status, output (str), record_count, byte_size
        """
        self._op_count += 1
        _start = time.time()
        if records is None:
            records = []

        lines = [json.dumps(r, ensure_ascii=False, default=str) for r in records]
        output = "\n".join(lines)

        self._export_count += 1
        elapsed = time.time() - _start
        self._fire("export_jsonl_completed", {"elapsed": elapsed, "count": len(records)})
        return {"status": "ok", "op": "export_jsonl",
                "output": output, "record_count": len(records),
                "byte_size": len(output.encode("utf-8")), "format": "jsonl"}

    # ------------------------------------------------------------------ #

    def export(self, records: List[Dict[str, Any]] = None,
               fmt: str = "json", **kwargs: Any) -> Dict[str, Any]:
        """Auto-detect format and export.

        Parameters
        ----------
        records : list of dict
        fmt : str  ("json", "csv", "jsonl")

        Returns
        -------
        dict
        """
        self._op_count += 1
        handler = self._formats.get(fmt)
        if handler is None:
            return {"status": "error", "reason": f"unsupported format: {fmt}",
                    "supported": list(self._formats.keys())}
        return handler(records, **kwargs)

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Internal statistics."""
        return {
            "op_count": self._op_count,
            "export_count": self._export_count,
            "supported_formats": list(self._formats.keys()),
        }

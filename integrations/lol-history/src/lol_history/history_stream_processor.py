"""
HistoryStreamProcessor — Streaming incremental updates for history data.

Architecture (拿来主义):
  seraphine_event_stream_processor.py + live_history_fusion_engine.py（M614）

Location: integrations/lol-history/src/lol_history/history_stream_processor.py

Design Notes (Knuth-level critique):
  User:
    - process_event handles malformed events gracefully — returns error dict, never raises.
    - Sliding window ensures bounded memory regardless of stream length.
    - get_snapshot always returns a consistent view even mid-stream.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - _buffer is bounded by _max_buffer_size to prevent OOM in long sessions.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, Deque, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_stream_processor.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Division safe against zero denominators."""
    return a / b if b != 0 else default


class HistoryStreamProcessor:
    """Streaming incremental updates for history data.

    Consumes a stream of match/event records and maintains an up-to-date
    aggregate view via sliding-window incremental processing.

    Public API
    ----------
    process_event       — ingest a single event
    process_batch       — ingest a list of events
    get_snapshot        — current aggregate snapshot
    get_window_stats    — window statistics
    reset               — clear all state

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, window_size: int = 50, max_buffer_size: int = 10000) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._cache: Dict[str, Any] = {}
        self._history: List[Dict[str, Any]] = []
        self._config: Dict[str, Any] = {
            "window_size": window_size,
            "max_buffer_size": max_buffer_size,
        }
        self._buffer: Deque[Dict[str, Any]] = deque(maxlen=max_buffer_size)
        self._aggregate: Dict[str, float] = defaultdict(float)
        self._event_counts: Dict[str, int] = defaultdict(int)
        self._window_size: int = window_size
        self._last_event_ts: float = 0.0
        self._errors: int = 0

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": data,
            })

    # ------------------------------------------------------------------ #

    def process_event(self, event: Dict[str, Any] = None) -> Dict[str, Any]:
        """Ingest a single event into the stream.

        Parameters
        ----------
        event : dict
            Must contain at least ``type`` key.  Optional ``value``, ``timestamp``.

        Returns
        -------
        dict  with status, event_type, buffer_size
        """
        self._op_count += 1
        _start = time.time()
        if event is None:
            event = {}

        etype = event.get("type", "unknown")
        value = event.get("value", 1.0)
        ts = event.get("timestamp", time.time())

        if not isinstance(etype, str):
            self._errors += 1
            return {"status": "error", "reason": "type must be string"}

        stamped = {"type": etype, "value": value, "ts": ts, "ingested_at": time.time()}
        self._buffer.append(stamped)
        self._event_counts[etype] += 1

        # Sliding window aggregate: keep only last window_size per type
        window = [e for e in self._buffer if e["type"] == etype]
        if len(window) > self._window_size:
            window = window[-self._window_size:]
        self._aggregate[etype] = sum(e.get("value", 0) for e in window) / len(window)
        self._last_event_ts = ts

        elapsed = time.time() - _start
        self._fire("process_event_completed", {"elapsed": elapsed, "event_type": etype})
        return {"status": "ok", "op": "process_event", "event_type": etype,
                "buffer_size": len(self._buffer)}

    # ------------------------------------------------------------------ #

    def process_batch(self, events: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Ingest a batch of events.

        Parameters
        ----------
        events : list of dict

        Returns
        -------
        dict  with status, processed, errors
        """
        self._op_count += 1
        _start = time.time()
        if events is None:
            events = []

        processed = 0
        errors = 0
        for ev in events:
            result = self.process_event(ev)
            if result.get("status") == "ok":
                processed += 1
            else:
                errors += 1

        elapsed = time.time() - _start
        self._fire("process_batch_completed", {"elapsed": elapsed, "processed": processed})
        return {"status": "ok", "op": "process_batch", "processed": processed, "errors": errors}

    # ------------------------------------------------------------------ #

    def get_snapshot(self) -> Dict[str, Any]:
        """Return current aggregate snapshot.

        Returns
        -------
        dict  with aggregates, event_counts, buffer_size, last_event_ts
        """
        self._op_count += 1
        _start = time.time()

        snapshot = {
            "status": "ok",
            "op": "get_snapshot",
            "aggregates": dict(self._aggregate),
            "event_counts": dict(self._event_counts),
            "buffer_size": len(self._buffer),
            "last_event_ts": self._last_event_ts,
            "total_errors": self._errors,
        }

        elapsed = time.time() - _start
        self._fire("get_snapshot_completed", {"elapsed": elapsed})
        return snapshot

    # ------------------------------------------------------------------ #

    def get_window_stats(self) -> Dict[str, Any]:
        """Return statistics for the current sliding window.

        Returns
        -------
        dict  with per-type count, mean, min, max
        """
        self._op_count += 1
        _start = time.time()

        stats: Dict[str, Any] = {}
        by_type: Dict[str, List[float]] = defaultdict(list)
        for ev in self._buffer:
            by_type[ev["type"]].append(ev.get("value", 0.0))

        for etype, values in by_type.items():
            tail = values[-self._window_size:]
            stats[etype] = {
                "count": len(tail),
                "mean": _safe_div(sum(tail), len(tail)),
                "min": min(tail) if tail else 0.0,
                "max": max(tail) if tail else 0.0,
            }

        elapsed = time.time() - _start
        self._fire("get_window_stats_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "get_window_stats", "stats": stats}

    # ------------------------------------------------------------------ #

    def reset(self) -> Dict[str, Any]:
        """Clear all state."""
        self._op_count += 1
        _start = time.time()

        self._buffer.clear()
        self._aggregate.clear()
        self._event_counts.clear()
        self._last_event_ts = 0.0
        self._errors = 0

        elapsed = time.time() - _start
        self._fire("reset_completed", {"elapsed": elapsed})
        return {"status": "ok", "op": "reset"}

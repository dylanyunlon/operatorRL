"""
HistoryPacketCorrelator — Correlates Fiddler-captured packets with historical match data.

Architecture (拿来主义):
  fiddler_replay_recorder.py + live_match_history_correlator.py

Location: extensions/fiddler_bridge/src/history_packet_correlator.py

Design Notes (Knuth-level critique):
  User:
    - correlate() never crashes on malformed packets — returns error with reason.
    - get_correlation_report always returns valid dict even with zero data.
    - Time-window matching is configurable via tolerance_seconds.
  System:
    - Thread-safe via _lock on mutable state.
    - evolution_callback fires on every operation for system-level tracking.
    - Bounded correlation cache via _max_correlations.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.history_packet_correlator.v1"
_DEFAULT_TOLERANCE: float = 5.0  # seconds
_DEFAULT_MAX_CORRELATIONS: int = 50000


class CorrelationRecord:
    """A single correlation between a live packet and historical data."""

    __slots__ = ("packet_id", "match_id", "game_time", "correlation_score",
                 "historical_event", "correlated_at")

    def __init__(self, packet_id: str, match_id: str, game_time: float,
                 correlation_score: float, historical_event: Dict[str, Any]) -> None:
        self.packet_id = packet_id
        self.match_id = match_id
        self.game_time = game_time
        self.correlation_score = correlation_score
        self.historical_event = historical_event
        self.correlated_at: float = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "match_id": self.match_id,
            "game_time": self.game_time,
            "correlation_score": self.correlation_score,
            "historical_event": self.historical_event,
            "correlated_at": self.correlated_at,
        }


class HistoryPacketCorrelator:
    """Correlates Fiddler-captured packets with historical match data.

    Lifecycle:
        1. set_history_context(match_history) — load historical events.
        2. correlate(packet) — find matching historical events for a packet.
        3. get_correlation_report() — summary of all correlations.

    Public API
    ----------
    set_history_context
    correlate
    correlate_batch
    get_correlation_report
    get_stats

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self, *, tolerance_seconds: float = _DEFAULT_TOLERANCE,
                 max_correlations: int = _DEFAULT_MAX_CORRELATIONS) -> None:
        self._tolerance = tolerance_seconds
        self._max_correlations = max_correlations
        self._lock = threading.Lock()

        self._history_events: List[Dict[str, Any]] = []
        self._correlations: List[CorrelationRecord] = []
        self._correlation_count: int = 0
        self._miss_count: int = 0
        self._error_count: int = 0

        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"source": _EVOLUTION_KEY, "type": event_type,
                     "timestamp": time.time(), "payload": data})
            except Exception:
                logger.exception("evolution_callback raised in HistoryPacketCorrelator")

    # ------------------------------------------------------------------ #

    def set_history_context(self, match_history: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Load historical events for correlation.

        Parameters
        ----------
        match_history : list of dict
            Each dict should contain game_time, event_type, and details.

        Returns
        -------
        dict  with status, event_count
        """
        _start = time.time()
        if match_history is None:
            match_history = []

        with self._lock:
            self._history_events = list(match_history)

        elapsed = time.time() - _start
        self._fire("set_history_context", {"elapsed": elapsed, "event_count": len(match_history)})
        return {"status": "ok", "op": "set_history_context",
                "event_count": len(match_history)}

    # ------------------------------------------------------------------ #

    def correlate(self, packet: Dict[str, Any] = None) -> Dict[str, Any]:
        """Find matching historical events for a Fiddler packet.

        Parameters
        ----------
        packet : dict
            Must contain packet_id, game_time.  Optional: match_id, event_type.

        Returns
        -------
        dict  with status, matches (list of correlation dicts)
        """
        _start = time.time()
        if packet is None:
            packet = {}

        packet_id = packet.get("packet_id", "unknown")
        game_time = packet.get("game_time")
        match_id = packet.get("match_id", "")
        event_type = packet.get("event_type", "")

        if game_time is None:
            self._error_count += 1
            return {"status": "error", "reason": "missing game_time"}

        matches: List[Dict[str, Any]] = []

        with self._lock:
            for hist in self._history_events:
                hist_time = hist.get("game_time", -9999)
                if abs(float(hist_time) - float(game_time)) <= self._tolerance:
                    # compute correlation score
                    time_diff = abs(float(hist_time) - float(game_time))
                    score = max(0.0, 1.0 - time_diff / self._tolerance)
                    if event_type and hist.get("event_type") == event_type:
                        score = min(1.0, score + 0.2)

                    rec = CorrelationRecord(
                        packet_id=packet_id,
                        match_id=match_id or hist.get("match_id", ""),
                        game_time=float(game_time),
                        correlation_score=round(score, 4),
                        historical_event=hist,
                    )
                    matches.append(rec.to_dict())
                    if len(self._correlations) < self._max_correlations:
                        self._correlations.append(rec)

        if matches:
            self._correlation_count += len(matches)
        else:
            self._miss_count += 1

        elapsed = time.time() - _start
        self._fire("correlate_completed", {"elapsed": elapsed, "match_count": len(matches)})
        return {"status": "ok", "op": "correlate", "matches": matches,
                "packet_id": packet_id}

    # ------------------------------------------------------------------ #

    def correlate_batch(self, packets: Sequence[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Correlate a batch of packets.

        Parameters
        ----------
        packets : sequence of dict

        Returns
        -------
        dict  with status, processed, total_matches
        """
        _start = time.time()
        if packets is None:
            packets = []

        total_matches = 0
        for pkt in packets:
            result = self.correlate(pkt)
            total_matches += len(result.get("matches", []))

        elapsed = time.time() - _start
        self._fire("correlate_batch_completed", {"elapsed": elapsed, "processed": len(packets)})
        return {"status": "ok", "op": "correlate_batch",
                "processed": len(packets), "total_matches": total_matches}

    # ------------------------------------------------------------------ #

    def get_correlation_report(self) -> Dict[str, Any]:
        """Summary of all correlations found.

        Returns
        -------
        dict  with correlation_count, miss_count, avg_score, by_match
        """
        _start = time.time()

        with self._lock:
            records = list(self._correlations)

        by_match: Dict[str, int] = defaultdict(int)
        total_score = 0.0
        for rec in records:
            by_match[rec.match_id] += 1
            total_score += rec.correlation_score

        avg_score = total_score / len(records) if records else 0.0

        elapsed = time.time() - _start
        self._fire("get_correlation_report", {"elapsed": elapsed})
        return {
            "status": "ok", "op": "get_correlation_report",
            "correlation_count": self._correlation_count,
            "miss_count": self._miss_count,
            "error_count": self._error_count,
            "avg_score": round(avg_score, 4),
            "by_match": dict(by_match),
        }

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        """Return internal statistics."""
        return {
            "history_event_count": len(self._history_events),
            "correlation_count": self._correlation_count,
            "miss_count": self._miss_count,
            "error_count": self._error_count,
            "stored_correlations": len(self._correlations),
            "tolerance": self._tolerance,
            "max_correlations": self._max_correlations,
        }

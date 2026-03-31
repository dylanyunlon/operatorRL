"""
LiveHistoryEventCorrelator — Correlates live game events with historical patterns.

Architecture (拿来主义):
  live_match_history_correlator.py — live-history correlation patterns
  game_event_pattern_library.py（M615）— pattern library store→query

Location: integrations/lol-history/src/lol_history/live_history_event_correlator.py

Design Notes (Knuth-level critique):
  User:
    - correlate() maps a live event to the most similar historical pattern.
    - Returns what happened next historically → predictive power for coaching.
  System:
    - Pattern matching uses event_type + game_time + context features.
    - Sliding window of recent events for sequence matching.
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.live_history_event_correlator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class LiveHistoryEventCorrelator:
    """Correlates live events with historical pattern library.

    Public API: ingest_historical_patterns, correlate, correlate_sequence, get_stats
    """
    def __init__(self, window_size: int = 50) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        # Patterns indexed by event_type
        self._patterns: Dict[str, List[Dict[str, Any]]] = {}
        self._live_window: deque = deque(maxlen=window_size)
        self._correlate_count = 0
        self._match_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def ingest_historical_patterns(self, patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ingest historical event patterns.

        Each pattern: {event_type, time_bucket, context: {...}, outcome: {...},
                       next_events: [...], win_correlation: float}
        """
        self._op_count += 1
        ingested = 0
        for p in patterns:
            et = p.get("event_type", "")
            if not et:
                continue
            self._patterns.setdefault(et, []).append(p)
            ingested += 1
        return {"status": "ok", "ingested": ingested,
                "event_types": len(self._patterns),
                "total_patterns": sum(len(v) for v in self._patterns.values())}

    def _similarity(self, live_event: Dict, pattern: Dict) -> float:
        """Compute similarity between a live event and a historical pattern."""
        score = 0.0
        weights = 0.0

        # Event type match (already filtered, so base match)
        score += 1.0
        weights += 1.0

        # Time proximity
        live_time = live_event.get("game_time", 0)
        pattern_time = pattern.get("game_time", pattern.get("time_bucket_center", 0))
        if pattern_time > 0:
            time_diff = abs(live_time - pattern_time)
            time_sim = max(1.0 - time_diff / 300.0, 0.0)  # 5 min window
            score += time_sim * 0.8
            weights += 0.8

        # Context feature overlap
        live_ctx = live_event.get("context", {})
        pattern_ctx = pattern.get("context", {})
        if live_ctx and pattern_ctx:
            common_keys = set(live_ctx.keys()) & set(pattern_ctx.keys())
            if common_keys:
                matches = sum(1 for k in common_keys if live_ctx[k] == pattern_ctx[k])
                ctx_sim = _safe_div(matches, len(common_keys))
                score += ctx_sim * 0.6
                weights += 0.6

        return _safe_div(score, weights)

    def correlate(self, live_event: Dict[str, Any], top_n: int = 3) -> Dict[str, Any]:
        """Correlate a single live event with historical patterns.

        Args:
            live_event: {event_type, game_time, context: {...}, ...}

        Returns:
            Dict with top matching patterns and their predicted next events.
        """
        self._op_count += 1
        self._correlate_count += 1
        self._live_window.append(live_event)

        et = live_event.get("event_type", "")
        candidates = self._patterns.get(et, [])

        if not candidates:
            return {"status": "ok", "matches": [], "note": "no_patterns_for_event_type"}

        scored = []
        for p in candidates:
            sim = self._similarity(live_event, p)
            scored.append({"pattern": p, "similarity": round(sim, 4)})

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        top_matches = scored[:top_n]
        self._match_count += len(top_matches)

        # Aggregate predicted next events
        next_event_votes: Dict[str, float] = {}
        for m in top_matches:
            for ne in m["pattern"].get("next_events", []):
                ne_type = ne if isinstance(ne, str) else ne.get("event_type", "")
                next_event_votes[ne_type] = next_event_votes.get(ne_type, 0) + m["similarity"]

        predicted_next = sorted(next_event_votes.items(), key=lambda x: x[1], reverse=True)[:5]

        result = {
            "status": "ok",
            "event_type": et,
            "matches": [{"similarity": m["similarity"],
                          "outcome": m["pattern"].get("outcome"),
                          "next_events": m["pattern"].get("next_events", []),
                          "win_correlation": m["pattern"].get("win_correlation")}
                         for m in top_matches],
            "predicted_next": [{"event_type": e, "weight": round(w, 3)} for e, w in predicted_next],
        }
        if top_matches and top_matches[0]["similarity"] > 0.7:
            self._fire("strong_match", {"event_type": et,
                                         "similarity": top_matches[0]["similarity"]})
        return result

    def correlate_sequence(self, min_sequence_len: int = 3) -> Dict[str, Any]:
        """Correlate the recent event sequence with historical sequences."""
        self._op_count += 1
        recent = list(self._live_window)[-min_sequence_len:]
        if len(recent) < min_sequence_len:
            return {"status": "ok", "sequence_matches": [], "note": "insufficient_events"}

        event_types = [e.get("event_type", "") for e in recent]
        # Simple sequence matching: find patterns whose next_events predicted this sequence
        matches = []
        for et, patterns in self._patterns.items():
            for p in patterns:
                predicted = [ne if isinstance(ne, str) else ne.get("event_type", "")
                             for ne in p.get("next_events", [])]
                if len(predicted) >= 2:
                    overlap = sum(1 for a, b in zip(event_types, predicted) if a == b)
                    if overlap >= 2:
                        matches.append({"trigger_event": et,
                                         "overlap": overlap,
                                         "outcome": p.get("outcome"),
                                         "win_correlation": p.get("win_correlation")})

        matches.sort(key=lambda x: x.get("overlap", 0), reverse=True)
        return {"status": "ok", "sequence_matches": matches[:5],
                "recent_events": event_types}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "correlate_count": self._correlate_count,
                "total_patterns": sum(len(v) for v in self._patterns.values()),
                "event_types": len(self._patterns),
                "live_window_size": len(self._live_window),
                "total_matches": self._match_count}

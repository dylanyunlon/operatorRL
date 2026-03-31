"""
LiveGameHistoryCorrelatorEngine — Correlates live game events with historical patterns.

Architecture (拿来主义):
  live_history_event_correlator.py（M596）— event-level correlation
  live_match_history_correlator.py（M558）— match-level correlation
  Seraphine/app/lol/tools.py — getTeammates, separateTeams team extraction

Location: integrations/lol-history/src/lol_history/live_game_history_correlator_engine.py

Design Notes (Knuth-level critique):
  User:
    - Real-time pattern matching: "at 15min, when enemy jungler has this KDA pattern,
      they historically attempt Baron 68% of the time."
    - Confidence degrades gracefully when historical data is sparse.
  System:
    - Correlation is streaming: each live event updates correlation scores incrementally.
    - Historical patterns are pre-indexed by game_time_bucket for O(1) lookup.
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.live_game_history_correlator_engine.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class LiveGameHistoryCorrelatorEngine:
    """Correlates live game events with historical patterns in real-time.

    Public API: index_historical_pattern, correlate_live_event,
                get_active_correlations, predict_next_event, reset_game, get_stats
    """
    def __init__(self, time_bucket_seconds: int = 60) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._correlate_count = 0
        self._time_bucket = time_bucket_seconds
        # time_bucket → list of historical patterns
        self._pattern_index: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        # Active correlations for current game
        self._active_correlations: List[Dict[str, Any]] = []
        self._live_events: List[Dict[str, Any]] = []
        self._game_start_time: float = 0.0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _to_bucket(self, game_time_seconds: float) -> int:
        return int(game_time_seconds // self._time_bucket)

    def index_historical_pattern(self, pattern: Dict[str, Any]) -> Dict[str, Any]:
        """Index a historical pattern for future correlation lookups."""
        self._op_count += 1
        game_time = pattern.get("game_time", 0)
        bucket = self._to_bucket(game_time)
        pattern["_bucket"] = bucket
        pattern["_indexed_at"] = time.time()
        self._pattern_index[bucket].append(pattern)
        return {"status": "ok", "bucket": bucket,
                "patterns_in_bucket": len(self._pattern_index[bucket])}

    def index_from_timeline(self, timeline_events: List[Dict[str, Any]],
                             match_id: str = "") -> Dict[str, Any]:
        """Bulk index patterns from a match timeline."""
        self._op_count += 1
        indexed = 0
        for event in timeline_events:
            event["match_id"] = match_id
            self.index_historical_pattern(event)
            indexed += 1
        return {"status": "ok", "indexed": indexed, "match_id": match_id}

    def correlate_live_event(self, live_event: Dict[str, Any]) -> Dict[str, Any]:
        """Correlate a live game event with indexed historical patterns."""
        self._op_count += 1
        self._correlate_count += 1
        game_time = live_event.get("game_time", 0)
        bucket = self._to_bucket(game_time)
        event_type = live_event.get("event_type", live_event.get("type", ""))
        self._live_events.append(live_event)
        # Find matching patterns in same time bucket (+/- 1 bucket)
        matches = []
        for b in [bucket - 1, bucket, bucket + 1]:
            for pattern in self._pattern_index.get(b, []):
                similarity = self._compute_similarity(live_event, pattern)
                if similarity > 0.3:
                    matches.append({
                        "pattern": pattern, "similarity": round(similarity, 3),
                        "bucket": b,
                    })
        matches.sort(key=lambda m: m["similarity"], reverse=True)
        top_matches = matches[:5]
        # Update active correlations
        if top_matches:
            correlation = {
                "live_event": live_event, "game_time": game_time,
                "matches": top_matches, "timestamp": time.time(),
                "best_similarity": top_matches[0]["similarity"],
            }
            self._active_correlations.append(correlation)
            if len(self._active_correlations) > 500:
                self._active_correlations = self._active_correlations[-250:]
        self._fire("correlated", {"game_time": game_time,
                                   "matches_found": len(top_matches)})
        return {"status": "ok", "event_type": event_type,
                "correlations": len(top_matches),
                "best_similarity": top_matches[0]["similarity"] if top_matches else 0.0,
                "top_matches": top_matches}

    def _compute_similarity(self, live: Dict[str, Any],
                             historical: Dict[str, Any]) -> float:
        """Compute similarity between live event and historical pattern."""
        score = 0.0
        checks = 0
        # Event type match
        if live.get("event_type") == historical.get("event_type"):
            score += 0.4
        checks += 1
        # Champion match
        if live.get("champion_id") and live.get("champion_id") == historical.get("champion_id"):
            score += 0.3
        checks += 1
        # Position/area match
        if live.get("position") and historical.get("position"):
            live_pos = live["position"]
            hist_pos = historical["position"]
            if isinstance(live_pos, dict) and isinstance(hist_pos, dict):
                dx = abs(live_pos.get("x", 0) - hist_pos.get("x", 0))
                dy = abs(live_pos.get("y", 0) - hist_pos.get("y", 0))
                dist = (dx ** 2 + dy ** 2) ** 0.5
                if dist < 2000:
                    score += 0.3 * (1 - dist / 2000)
        checks += 1
        return min(1.0, score)

    def predict_next_event(self, current_game_time: float) -> Dict[str, Any]:
        """Predict likely next events based on historical correlations."""
        self._op_count += 1
        next_bucket = self._to_bucket(current_game_time) + 1
        upcoming = self._pattern_index.get(next_bucket, [])
        if not upcoming:
            return {"status": "ok", "predictions": [], "game_time": current_game_time}
        # Count event types in next bucket
        type_counts = defaultdict(int)
        for p in upcoming:
            et = p.get("event_type", "unknown")
            type_counts[et] += 1
        total = sum(type_counts.values())
        predictions = [
            {"event_type": et, "probability": round(_safe_div(c, total), 3), "count": c}
            for et, c in sorted(type_counts.items(), key=lambda x: x[1], reverse=True)
        ][:5]
        return {"status": "ok", "predictions": predictions,
                "game_time": current_game_time, "next_bucket": next_bucket}

    def reset_game(self) -> Dict[str, Any]:
        """Reset live game state for a new game (keeps historical patterns)."""
        self._op_count += 1
        self._active_correlations.clear()
        self._live_events.clear()
        self._game_start_time = time.time()
        return {"status": "ok", "patterns_retained": sum(
            len(v) for v in self._pattern_index.values())}

    def get_active_correlations(self, n: int = 10) -> Dict[str, Any]:
        """Get most recent active correlations."""
        self._op_count += 1
        return {"status": "ok",
                "correlations": self._active_correlations[-n:],
                "total": len(self._active_correlations)}

    def get_stats(self) -> Dict[str, Any]:
        total_patterns = sum(len(v) for v in self._pattern_index.values())
        return {"correlate_count": self._correlate_count,
                "total_patterns": total_patterns,
                "buckets": len(self._pattern_index),
                "active_correlations": len(self._active_correlations),
                "live_events": len(self._live_events),
                "total_ops": self._op_count}

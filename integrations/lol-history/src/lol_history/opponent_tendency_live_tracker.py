"""
OpponentTendencyLiveTracker — Tracks opponent tendencies live vs historical baseline.

Architecture (拿来主义):
  opponent_behavior_modeler.py — behavior modeling patterns
  champion_tendency_analyzer.py — tendency analysis from match data

Location: integrations/lol-history/src/lol_history/opponent_tendency_live_tracker.py

Design Notes (Knuth-level critique):
  User:
    - record_event() builds live behavior profile; compare() shows deviations from history.
    - Deviation alerts enable real-time tactical adjustments.
  System:
    - Rolling window prevents memory bloat in 30+ minute sessions.
    - Deviation score uses normalized z-score against historical baseline.
"""
from __future__ import annotations
import logging, time, math
from collections import deque, defaultdict
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.opponent_tendency_live_tracker.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class OpponentTendencyLiveTracker:
    """Tracks live opponent behavior and compares to historical baselines.

    Public API: set_baseline, record_event, compare, get_deviations, get_live_profile, get_stats
    """
    def __init__(self, window_size: int = 300) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._baselines: Dict[str, Dict[str, Any]] = {}
        self._live_events: Dict[str, deque] = {}
        self._live_counts: Dict[str, Dict[str, int]] = {}
        self._window_size = window_size
        self._deviation_history: List[Dict] = []
        self._event_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_baseline(self, puuid: str, baseline: Dict[str, Any]) -> Dict[str, Any]:
        """Set historical behavior baseline for an opponent.

        Args:
            puuid: Player unique ID.
            baseline: Dict with keys like aggression_rate, ward_freq, roam_freq,
                      cs_per_min, deaths_per_game, avg_first_blood_time, etc.
                      Each key maps to {"mean": float, "std": float, "samples": int}.
        """
        self._op_count += 1
        self._baselines[puuid] = baseline
        self._live_events.setdefault(puuid, deque(maxlen=self._window_size))
        self._live_counts.setdefault(puuid, defaultdict(int))
        return {"status": "ok", "puuid": puuid, "baseline_metrics": len(baseline)}

    def record_event(self, puuid: str, event_type: str, value: float = 1.0,
                     game_time: float = 0.0, meta: Dict[str, Any] = None) -> Dict[str, Any]:
        """Record a live game event for an opponent.

        Args:
            puuid: Player unique ID.
            event_type: e.g. "aggression", "ward_placed", "roam", "death", "cs_tick".
            value: Numeric value of the event.
            game_time: Current game time in seconds.
        """
        self._op_count += 1
        self._event_count += 1
        self._live_events.setdefault(puuid, deque(maxlen=self._window_size))
        self._live_counts.setdefault(puuid, defaultdict(int))

        entry = {"type": event_type, "value": value, "game_time": game_time,
                 "meta": meta or {}, "ts": time.time()}
        self._live_events[puuid].append(entry)
        self._live_counts[puuid][event_type] += 1
        return {"status": "ok", "total_events": len(self._live_events[puuid])}

    def compare(self, puuid: str) -> Dict[str, Any]:
        """Compare live behavior against historical baseline.

        Returns:
            Dict with per-metric z-scores, deviation flags, and summary.
        """
        self._op_count += 1
        baseline = self._baselines.get(puuid)
        if not baseline:
            return {"status": "ok", "puuid": puuid, "deviations": {}, "note": "no_baseline"}

        events = self._live_events.get(puuid, deque())
        if not events:
            return {"status": "ok", "puuid": puuid, "deviations": {}, "note": "no_live_data"}

        # Compute live rates
        total_time_s = max((events[-1].get("game_time", 0) - events[0].get("game_time", 0)), 60)
        live_counts = self._live_counts.get(puuid, {})

        deviations = {}
        for metric, hist in baseline.items():
            if not isinstance(hist, dict) or "mean" not in hist:
                continue
            hist_mean = hist["mean"]
            hist_std = hist.get("std", hist_mean * 0.2)
            if hist_std <= 0:
                hist_std = 0.1

            # Map metric to event type
            live_count = live_counts.get(metric, 0)
            live_rate = _safe_div(live_count, total_time_s / 60.0)  # per minute

            z_score = _safe_div(live_rate - hist_mean, hist_std)
            is_deviant = abs(z_score) > 1.5

            deviations[metric] = {
                "live_rate": round(live_rate, 4),
                "historical_mean": round(hist_mean, 4),
                "historical_std": round(hist_std, 4),
                "z_score": round(z_score, 2),
                "is_deviant": is_deviant,
                "direction": "higher" if z_score > 0 else "lower" if z_score < 0 else "same",
            }

        deviant_metrics = [k for k, v in deviations.items() if v.get("is_deviant")]
        result = {
            "status": "ok", "puuid": puuid,
            "deviations": deviations,
            "deviant_count": len(deviant_metrics),
            "deviant_metrics": deviant_metrics,
            "total_events": len(events),
        }

        if deviant_metrics:
            self._deviation_history.append({"puuid": puuid, "metrics": deviant_metrics,
                                             "ts": time.time()})
            self._fire("deviation_detected", {"puuid": puuid, "metrics": deviant_metrics})

        return result

    def get_deviations(self, puuid: str = None) -> List[Dict]:
        """Get deviation history, optionally filtered by puuid."""
        self._op_count += 1
        if puuid:
            return [d for d in self._deviation_history if d.get("puuid") == puuid]
        return list(self._deviation_history)

    def get_live_profile(self, puuid: str) -> Dict[str, Any]:
        """Get raw live event counts for an opponent."""
        self._op_count += 1
        return {"puuid": puuid, "counts": dict(self._live_counts.get(puuid, {})),
                "total_events": len(self._live_events.get(puuid, []))}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "tracked_opponents": len(self._live_events),
                "baselines_set": len(self._baselines), "total_events": self._event_count,
                "total_deviations": len(self._deviation_history)}

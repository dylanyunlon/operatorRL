"""
LaneMatchupHistoryEnricher — Enriches live lane state with historical lane data.

Architecture (拿来主义):
  lane_state_tracker.py — live lane state tracking
  historical_matchup_predictor.py — matchup data lookups

Location: integrations/lol-history/src/lol_history/lane_matchup_history_enricher.py

Design Notes (Knuth-level critique):
  User:
    - enrich() merges live lane data with historical averages for context.
    - Shows "you're ahead/behind compared to historical average" at any game time.
  System:
    - Historical data is bucketed by game_time intervals (0-5m, 5-10m, 10-15m, 15+m).
    - Enrichment is O(1) lookup per metric per time bucket.
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.lane_matchup_history_enricher.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_TIME_BUCKETS = [(0, 300, "early"), (300, 600, "mid_early"), (600, 900, "mid"),
                 (900, 1200, "mid_late"), (1200, float("inf"), "late")]


def _bucket_for_time(game_time_s: float) -> str:
    for lo, hi, label in _TIME_BUCKETS:
        if lo <= game_time_s < hi:
            return label
    return "late"


class LaneMatchupHistoryEnricher:
    """Enriches live lane state with historical context.

    Public API: ingest_lane_history, enrich, get_historical_curve, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        # key: (our_champ, enemy_champ, role, time_bucket) -> {metric: [values]}
        self._history: Dict[Tuple, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        self._enrich_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def ingest_lane_history(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ingest historical lane phase snapshots.

        Each record: {our_champ, enemy_champ, role, game_time, cs, kills, deaths,
                      gold, xp, tower_plates, ward_count, ...}
        """
        self._op_count += 1
        ingested = 0
        for rec in records:
            our = rec.get("our_champ", 0)
            enemy = rec.get("enemy_champ", 0)
            role = rec.get("role", "any")
            gt = rec.get("game_time", 0)
            bucket = _bucket_for_time(gt)
            key = (our, enemy, role, bucket)

            for metric in ["cs", "kills", "deaths", "gold", "xp", "tower_plates", "ward_count"]:
                val = rec.get(metric)
                if val is not None:
                    self._history[key][metric].append(float(val))
            ingested += 1

        return {"status": "ok", "ingested": ingested, "history_keys": len(self._history)}

    def enrich(self, our_champ: int, enemy_champ: int, role: str,
               game_time: float, live_state: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a live lane state snapshot with historical context.

        Args:
            live_state: Dict with cs, kills, deaths, gold, etc.

        Returns:
            Dict with original live_state + historical averages + delta annotations.
        """
        self._op_count += 1
        self._enrich_count += 1
        bucket = _bucket_for_time(game_time)
        key = (our_champ, enemy_champ, role, bucket)
        hist = self._history.get(key, {})

        enriched = dict(live_state)
        enriched["_game_time"] = game_time
        enriched["_time_bucket"] = bucket
        enriched["_historical"] = {}
        enriched["_deltas"] = {}

        for metric in ["cs", "kills", "deaths", "gold", "xp", "tower_plates", "ward_count"]:
            live_val = live_state.get(metric)
            hist_vals = hist.get(metric, [])
            if hist_vals:
                hist_mean = sum(hist_vals) / len(hist_vals)
                enriched["_historical"][metric] = {
                    "mean": round(hist_mean, 2),
                    "samples": len(hist_vals),
                    "min": round(min(hist_vals), 2),
                    "max": round(max(hist_vals), 2),
                }
                if live_val is not None:
                    delta = live_val - hist_mean
                    pct = _safe_div(delta, hist_mean) * 100 if hist_mean else 0
                    enriched["_deltas"][metric] = {
                        "absolute": round(delta, 2),
                        "percentage": round(pct, 1),
                        "assessment": "ahead" if delta > 0 else "behind" if delta < 0 else "even",
                    }

        # Overall lane assessment
        positive_deltas = sum(1 for d in enriched["_deltas"].values()
                              if d.get("assessment") == "ahead")
        negative_deltas = sum(1 for d in enriched["_deltas"].values()
                              if d.get("assessment") == "behind")
        total = positive_deltas + negative_deltas
        if total > 0:
            if positive_deltas > negative_deltas:
                enriched["_lane_assessment"] = "winning"
            elif negative_deltas > positive_deltas:
                enriched["_lane_assessment"] = "losing"
            else:
                enriched["_lane_assessment"] = "even"
        else:
            enriched["_lane_assessment"] = "unknown"

        self._fire("enriched", {"matchup": f"{our_champ}v{enemy_champ}", "bucket": bucket})
        return {"status": "ok", "enriched": enriched}

    def get_historical_curve(self, our_champ: int, enemy_champ: int, role: str,
                             metric: str) -> Dict[str, Any]:
        """Get the historical curve for a metric across all time buckets."""
        self._op_count += 1
        curve = []
        for lo, hi, label in _TIME_BUCKETS:
            key = (our_champ, enemy_champ, role, label)
            vals = self._history.get(key, {}).get(metric, [])
            if vals:
                curve.append({"bucket": label, "mean": round(sum(vals) / len(vals), 2),
                              "samples": len(vals)})
            else:
                curve.append({"bucket": label, "mean": None, "samples": 0})
        return {"status": "ok", "metric": metric, "curve": curve}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        total_records = sum(len(next(iter(v.values()), [])) for v in self._history.values())
        return {"op_count": self._op_count, "enrich_count": self._enrich_count,
                "history_keys": len(self._history), "approx_records": total_records}

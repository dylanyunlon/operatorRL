"""
HistoricalObjectivePriorityAdvisor — Advises objective priority from historical outcomes.

Architecture (拿来主义):
  objective_priority_engine.py — objective prioritization logic
  objective_control_analyzer.py — objective control analysis

Location: integrations/lol-history/src/lol_history/historical_objective_priority_advisor.py

Design Notes (Knuth-level critique):
  User:
    - advise() ranks objectives by historical win-rate impact at current game time.
    - Context-aware: different priorities for different game phases and team comps.
  System:
    - Objective value is time-dependent (dragon value changes late game).
    - Conditional probabilities: P(win | took dragon at 15min with this comp).
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.historical_objective_priority_advisor.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_OBJECTIVES = ["dragon", "rift_herald", "baron", "tower", "inhibitor", "elder_dragon"]


class HistoricalObjectivePriorityAdvisor:
    """Advises objective priority using historical outcome data.

    Public API: ingest_objective_data, advise, get_objective_value, get_stats
    """
    def __init__(self, min_samples: int = 5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._min_samples = min_samples
        # key: (objective_type, time_bucket) -> {wins_with, total_with, wins_without, total_without}
        self._objective_stats: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(
            lambda: {"wins_with": 0, "total_with": 0, "wins_without": 0, "total_without": 0})
        self._advise_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    @staticmethod
    def _time_bucket(game_time_s: float) -> str:
        if game_time_s < 600: return "early"
        if game_time_s < 1200: return "mid"
        if game_time_s < 1800: return "late"
        return "very_late"

    def ingest_objective_data(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ingest historical objective outcome records.

        Each record: {objective_type, game_time, team_took: bool, team_won: bool}
        """
        self._op_count += 1
        ingested = 0
        for rec in records:
            obj_type = rec.get("objective_type", "")
            if obj_type not in _OBJECTIVES:
                continue
            gt = rec.get("game_time", 0)
            bucket = self._time_bucket(gt)
            key = (obj_type, bucket)
            took = rec.get("team_took", False)
            won = rec.get("team_won", False)
            if took:
                self._objective_stats[key]["total_with"] += 1
                if won:
                    self._objective_stats[key]["wins_with"] += 1
            else:
                self._objective_stats[key]["total_without"] += 1
                if won:
                    self._objective_stats[key]["wins_without"] += 1
            ingested += 1
        return {"status": "ok", "ingested": ingested, "stat_keys": len(self._objective_stats)}

    def get_objective_value(self, objective_type: str, game_time: float) -> Dict[str, Any]:
        """Get the historical value (win rate impact) of an objective at a given time."""
        self._op_count += 1
        bucket = self._time_bucket(game_time)
        key = (objective_type, bucket)
        stats = self._objective_stats.get(key)
        if not stats or (stats["total_with"] + stats["total_without"]) < self._min_samples:
            return {"objective": objective_type, "time_bucket": bucket,
                    "value": 0.0, "note": "insufficient_data"}

        wr_with = _safe_div(stats["wins_with"], stats["total_with"], 0.5)
        wr_without = _safe_div(stats["wins_without"], stats["total_without"], 0.5)
        value = wr_with - wr_without  # Win rate differential

        return {
            "objective": objective_type, "time_bucket": bucket,
            "value": round(value, 4),
            "win_rate_with": round(wr_with, 4),
            "win_rate_without": round(wr_without, 4),
            "samples_with": stats["total_with"],
            "samples_without": stats["total_without"],
        }

    def advise(self, game_time: float, available_objectives: List[str] = None,
               team_state: Dict[str, Any] = None) -> Dict[str, Any]:
        """Rank available objectives by priority.

        Args:
            game_time: Current game time in seconds.
            available_objectives: Objectives currently contestable (default: all).
            team_state: Optional context (gold_lead, alive_count, etc.).
        """
        self._op_count += 1
        self._advise_count += 1
        if available_objectives is None:
            available_objectives = list(_OBJECTIVES)

        scored = []
        for obj in available_objectives:
            val_info = self.get_objective_value(obj, game_time)
            value = val_info.get("value", 0.0)

            # Adjust by team state if provided
            if team_state:
                gold_lead = team_state.get("gold_lead", 0)
                alive = team_state.get("alive_count", 5)
                # When behind, high-value objectives matter more
                if gold_lead < -2000:
                    value *= 1.2
                # When fewer alive, risky objectives down-weighted
                if alive < 3 and obj in ("baron", "elder_dragon"):
                    value *= 0.5

            scored.append({
                "objective": obj, "priority_score": round(value, 4),
                "win_rate_impact": val_info.get("value", 0.0),
                "samples": val_info.get("samples_with", 0) + val_info.get("samples_without", 0),
            })

        scored.sort(key=lambda x: x["priority_score"], reverse=True)
        result = {
            "status": "ok", "ranked_objectives": scored,
            "top_priority": scored[0]["objective"] if scored else None,
            "game_time": game_time, "time_bucket": self._time_bucket(game_time),
        }
        self._fire("advised", {"top": result["top_priority"], "game_time": game_time})
        return result

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "advise_count": self._advise_count,
                "stat_keys": len(self._objective_stats)}

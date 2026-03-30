"""
GoldEfficiencyTracker — Track gold income efficiency across games.

Architecture (拿来主义):
  查看 **history_match_aggregator.py** 的GPM计算方式。

Location: integrations/lol-history/src/lol_history/gold_efficiency_tracker.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.gold_efficiency_tracker.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class GoldEfficiencyTracker:
    """Track gold-per-minute, CS efficiency, and economy ratings."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._games: List[Dict[str, Any]] = []

    def record_game(self, gold: int, cs: int, kills: int, assists: int,
                    duration_minutes: float) -> None:
        self._games.append({
            "gold": gold, "cs": cs, "kills": kills, "assists": assists,
            "duration_minutes": duration_minutes,
        })

    def get_summary(self) -> Dict[str, Any]:
        n = len(self._games)
        if n == 0:
            return {"total_games": 0, "avg_gold_per_min": 0.0, "avg_cs_per_min": 0.0,
                    "avg_gold_per_cs": 0.0, "avg_kills": 0.0}
        gpm = [_safe_div(g["gold"], g["duration_minutes"])
               for g in self._games if g["duration_minutes"] > 0]
        cspm = [_safe_div(g["cs"], g["duration_minutes"])
                for g in self._games if g["duration_minutes"] > 0]
        gpc = [_safe_div(g["gold"], g["cs"])
               for g in self._games if g["cs"] > 0]
        return {
            "total_games": n,
            "avg_gold_per_min": sum(gpm) / len(gpm) if gpm else 0.0,
            "avg_cs_per_min": sum(cspm) / len(cspm) if cspm else 0.0,
            "avg_gold_per_cs": sum(gpc) / len(gpc) if gpc else 0.0,
            "avg_kills": sum(g["kills"] for g in self._games) / n,
        }

    def compute_efficiency_rating(self) -> str:
        s = self.get_summary()
        gpm = s["avg_gold_per_min"]
        if gpm >= 500:
            return "excellent"
        elif gpm >= 400:
            return "good"
        elif gpm >= 300:
            return "average"
        return "poor"

    def to_dict(self) -> Dict[str, Any]:
        return self.get_summary()

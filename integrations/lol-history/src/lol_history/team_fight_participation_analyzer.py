"""
TeamFightParticipationAnalyzer — Kill participation and teamfight contribution analysis.

Architecture (拿来主义):
  查看 **combat_outcome_predictor.py** 的团战建模方式。
  实现 **TeamFightParticipationAnalyzer**。

Location: integrations/lol-history/src/lol_history/team_fight_participation_analyzer.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.team_fight_participation_analyzer.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class TeamFightParticipationAnalyzer:
    """Analyze kill participation and teamfight contribution."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._games: List[Dict[str, Any]] = []

    def record_game(self, kills: int, assists: int, team_kills: int, deaths: int) -> None:
        kp = _safe_div(kills + assists, team_kills)
        self._games.append({
            "kills": kills, "assists": assists, "team_kills": team_kills,
            "deaths": deaths, "kill_participation": kp,
        })

    def get_summary(self) -> Dict[str, Any]:
        n = len(self._games)
        if n == 0:
            return {"total_games": 0, "avg_kill_participation": 0.0, "avg_deaths": 0.0}
        kps = [g["kill_participation"] for g in self._games]
        deaths = [g["deaths"] for g in self._games]
        return {
            "total_games": n,
            "avg_kill_participation": sum(kps) / n,
            "avg_deaths": sum(deaths) / n,
            "avg_kills": sum(g["kills"] for g in self._games) / n,
            "avg_assists": sum(g["assists"] for g in self._games) / n,
        }

    def compute_teamfight_rating(self) -> str:
        s = self.get_summary()
        kp = s["avg_kill_participation"]
        if kp >= 0.7:
            return "excellent"
        elif kp >= 0.55:
            return "good"
        elif kp >= 0.4:
            return "average"
        return "poor"

    def to_dict(self) -> Dict[str, Any]:
        return self.get_summary()

"""
Team Comp Evaluator — Analyzes team composition strength and synergy.

Evaluates ally vs enemy champion compositions for teamfight rating,
split push potential, and overall advantage.

Location: integrations/lol/src/lol_agent/team_comp_evaluator.py

Reference (拿来主义):
  - dota2bot-OpenHyperAI: hero_selection.lua draft evaluation
  - leagueoflegends-optimizer: champion statistics aggregation
  - integrations/lol/src/lol_agent/champion_performance_analyzer.py: scoring pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.team_comp_evaluator.v1"

# Default champion data (base score 5.0 for unknown champions)
_DEFAULT_CHAMPION: dict[str, Any] = {"role": "unknown", "teamfight": 5, "split": 5}


class TeamCompEvaluator:
    """Evaluates team compositions for strategic advantage.

    Scores each team based on champion data, computes teamfight
    rating, and provides recommendations.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._champion_db: dict[str, dict[str, Any]] = {}

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_champion(
        self, name: str, data: dict[str, Any]
    ) -> None:
        """Register champion data.

        Args:
            name: Champion name.
            data: Dict with role, teamfight score, split score.
        """
        self._champion_db[name] = data

    def evaluate(
        self,
        ally_champions: list[str],
        enemy_champions: list[str],
    ) -> dict[str, Any]:
        """Evaluate two team compositions.

        Args:
            ally_champions: List of ally champion names.
            enemy_champions: List of enemy champion names.

        Returns:
            Dict with ally_score, enemy_score, teamfight_rating,
            advantage, recommendation, ally_champions.
        """
        ally_score = self._score_team(ally_champions)
        enemy_score = self._score_team(enemy_champions)
        advantage = ally_score - enemy_score

        teamfight_rating = self._teamfight_rating(ally_champions)

        if advantage > 2.0:
            recommendation = "Strong composition, force teamfights"
        elif advantage < -2.0:
            recommendation = "Weak composition, avoid 5v5, split push"
        else:
            recommendation = "Even compositions, play to individual strengths"

        result = {
            "ally_score": ally_score,
            "enemy_score": enemy_score,
            "advantage": advantage,
            "teamfight_rating": teamfight_rating,
            "recommendation": recommendation,
            "ally_champions": list(ally_champions),
            "enemy_champions": list(enemy_champions),
        }

        self._fire_evolution("comp_evaluated", {
            "ally_score": ally_score,
            "enemy_score": enemy_score,
            "advantage": advantage,
        })

        return result

    def _score_team(self, champions: list[str]) -> float:
        """Compute overall team score."""
        if not champions:
            return 0.0
        total = 0.0
        for champ in champions:
            data = self._champion_db.get(champ, _DEFAULT_CHAMPION)
            total += data.get("teamfight", 5) * 0.6 + data.get("split", 5) * 0.4
        return total

    def _teamfight_rating(self, champions: list[str]) -> float:
        """Compute teamfight-specific rating."""
        if not champions:
            return 0.0
        total = 0.0
        for champ in champions:
            data = self._champion_db.get(champ, _DEFAULT_CHAMPION)
            total += data.get("teamfight", 5)
        return total / len(champions)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

"""
Post-Game Analyzer — Game review with key moment annotation and improvements.

Analyzes completed games for performance scoring, identifies key moments
(multikills, baron kills), and generates improvement suggestions.

Location: integrations/lol/src/lol_agent/post_game_analyzer.py

Reference (拿来主义):
  - DI-star/distar/ctools/worker/league/cum_stat.py: cumulative stat tracking
  - integrations/lol/src/lol_agent/lol_evolution_loop.py: fitness computation from episodes
  - leagueoflegends-optimizer: post-match analysis pipeline
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.post_game_analyzer.v1"

_KEY_EVENT_TYPES: set[str] = {"multikill", "baron_kill", "elder_kill", "ace", "pentakill", "tower_push"}


class PostGameAnalyzer:
    """Analyzes completed games for performance and improvement.

    Computes performance scores, identifies key moments,
    generates improvement suggestions, tracks game history.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._history: list[dict[str, Any]] = []

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def analyze(self, game_data: dict[str, Any]) -> dict[str, Any]:
        """Analyze a completed game.

        Args:
            game_data: Dict with outcome, duration, events, stats.

        Returns:
            Dict with outcome, performance_score, key_moments,
            improvements, stats.
        """
        outcome = game_data.get("outcome", "unknown")
        duration = game_data.get("duration", 0)
        events = game_data.get("events", [])
        stats = game_data.get("stats", {})

        performance_score = self._compute_performance(stats, outcome)
        key_moments = self._find_key_moments(events)
        improvements = self._generate_improvements(stats, outcome)

        result = {
            "outcome": outcome,
            "duration": duration,
            "performance_score": performance_score,
            "key_moments": key_moments,
            "improvements": improvements,
            "stats": stats,
        }

        self._history.append(result)

        self._fire_evolution("game_analyzed", {
            "outcome": outcome,
            "performance_score": performance_score,
        })

        return result

    def _compute_performance(
        self, stats: dict[str, Any], outcome: str
    ) -> float:
        """Compute performance score [0.0, 10.0].

        Formula: KDA_score * 0.4 + outcome_bonus + death_penalty.
        """
        kills = stats.get("kills", 0)
        deaths = max(stats.get("deaths", 0), 1)
        assists = stats.get("assists", 0)

        kda = (kills + assists) / deaths
        kda_score = min(5.0, kda)

        outcome_bonus = 3.0 if outcome == "win" else 0.0
        death_penalty = min(2.0, stats.get("deaths", 0) * 0.2)

        score = kda_score * 0.4 + outcome_bonus + 2.0 - death_penalty
        return max(0.0, min(10.0, score))

    def _find_key_moments(
        self, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Identify key moments from event list."""
        return [e for e in events if e.get("type", "") in _KEY_EVENT_TYPES]

    def _generate_improvements(
        self, stats: dict[str, Any], outcome: str
    ) -> list[str]:
        """Generate improvement suggestions based on stats."""
        improvements: list[str] = []

        deaths = stats.get("deaths", 0)
        kills = stats.get("kills", 0)
        assists = stats.get("assists", 0)

        if deaths > 6:
            improvements.append("Reduce deaths — focus on positioning and map awareness")
        if kills + assists < 5 and outcome == "loss":
            improvements.append("Increase kill participation — look for teamfight opportunities")
        if outcome == "loss" and deaths > kills:
            improvements.append("Avoid extended trades when behind — farm safely")

        return improvements

    def get_history(self) -> list[dict[str, Any]]:
        """Return analysis history."""
        return list(self._history)

    def get_trend(self) -> dict[str, Any]:
        """Compute performance trend over game history.

        Returns:
            Dict with win_rate, avg_performance, game_count.
        """
        if not self._history:
            return {"win_rate": 0.0, "avg_performance": 0.0, "game_count": 0}

        wins = sum(1 for g in self._history if g["outcome"] == "win")
        avg_perf = sum(g["performance_score"] for g in self._history) / len(self._history)

        return {
            "win_rate": wins / len(self._history),
            "avg_performance": avg_perf,
            "game_count": len(self._history),
        }

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

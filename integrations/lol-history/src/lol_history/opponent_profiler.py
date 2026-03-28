"""
Opponent Profiler — History-based opponent habit and weakness analysis.

Generates opponent profiles from historical match data including
champion pool detection, weakness identification, aggression scoring,
and vision habit analysis.

Location: integrations/lol-history/src/lol_history/opponent_profiler.py
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.opponent_profiler.v1"


class OpponentProfiler:
    """Opponent profiling engine based on historical match data.

    Analyzes match history to produce profiles with champion pool,
    weakness detection, aggression score, and vision habits.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def profile_from_matches(
        self, puuid: str, matches: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Generate a full opponent profile from match history.

        Args:
            puuid: Player unique ID.
            matches: List of match dicts.

        Returns:
            Profile dict with habits, weaknesses, champion pool.
        """
        if not matches:
            return {
                "puuid": puuid,
                "win_rate": 0.0,
                "kda": 0.0,
                "main_champions": [],
                "habits": {},
                "weaknesses": [],
                "games_analyzed": 0,
            }

        total = len(matches)
        wins = sum(1 for m in matches if m.get("win"))
        kills = sum(m.get("kills", 0) for m in matches)
        deaths = sum(m.get("deaths", 0) for m in matches)
        assists = sum(m.get("assists", 0) for m in matches)
        kda = (kills + assists) / max(deaths, 1)
        win_rate = wins / total

        return {
            "puuid": puuid,
            "win_rate": win_rate,
            "kda": kda,
            "main_champions": self.detect_champion_pool(matches),
            "habits": {
                "aggression": self.compute_aggression_score(matches),
                "vision": self.compute_vision_habit(matches),
            },
            "weaknesses": self.detect_weaknesses(matches),
            "games_analyzed": total,
        }

    def detect_champion_pool(
        self, matches: list[dict[str, Any]]
    ) -> list[str]:
        """Detect most played champions.

        Args:
            matches: List of match dicts with 'champion' key.

        Returns:
            Champion names ordered by frequency.
        """
        counter = Counter(m.get("champion", "") for m in matches)
        return [champ for champ, _ in counter.most_common() if champ]

    def detect_weaknesses(
        self, matches: list[dict[str, Any]]
    ) -> list[str]:
        """Detect player weaknesses from match statistics.

        Args:
            matches: List of match dicts.

        Returns:
            List of weakness identifiers.
        """
        weaknesses: list[str] = []
        if not matches:
            return weaknesses

        early_deaths = [m.get("deaths_before_15", 0) for m in matches]
        cs_diffs = [m.get("cs_diff_at_15", 0) for m in matches]
        gold_diffs = [m.get("gold_diff_at_15", 0) for m in matches]

        avg_early_deaths = sum(early_deaths) / len(matches)
        avg_cs_diff = sum(cs_diffs) / len(matches)
        avg_gold_diff = sum(gold_diffs) / len(matches)

        if avg_early_deaths > 1.5 or avg_cs_diff < -10 or avg_gold_diff < -200:
            weaknesses.append("early_game")

        avg_deaths = sum(m.get("deaths", 0) for m in matches) / len(matches)
        if avg_deaths > 6:
            weaknesses.append("positioning")

        return weaknesses

    def compute_aggression_score(
        self, matches: list[dict[str, Any]]
    ) -> float:
        """Compute aggression score from 0 to 1.

        Args:
            matches: List of match dicts.

        Returns:
            Aggression score [0.0, 1.0].
        """
        if not matches:
            return 0.0
        total_kills = sum(m.get("kills", 0) for m in matches)
        total_solo = sum(m.get("solo_kills", 0) for m in matches)
        total_deaths = sum(m.get("deaths", 0) for m in matches)
        n = len(matches)
        raw = (total_kills + total_solo * 2) / max(n, 1)
        death_penalty = total_deaths / max(n, 1) * 0.5
        score = min(1.0, max(0.0, (raw - death_penalty) / 20.0))
        return score

    def compute_vision_habit(
        self, matches: list[dict[str, Any]]
    ) -> dict[str, float]:
        """Compute vision habits.

        Args:
            matches: List of match dicts.

        Returns:
            Dict with wards_per_min and vision_score_per_min.
        """
        if not matches:
            return {"wards_per_min": 0.0, "vision_score_per_min": 0.0}

        total_wards = sum(m.get("wards_placed", 0) for m in matches)
        total_vision = sum(m.get("vision_score", 0) for m in matches)
        total_seconds = sum(m.get("game_duration", 1800) for m in matches)
        total_minutes = total_seconds / 60.0

        return {
            "wards_per_min": total_wards / max(total_minutes, 1.0),
            "vision_score_per_min": total_vision / max(total_minutes, 1.0),
        }

    def assess_threat(self, profile: dict[str, Any]) -> str:
        """Assess threat level from profile.

        Args:
            profile: Profile dict with win_rate, kda, games_analyzed.

        Returns:
            Threat level: "unknown", "low", "medium", "high", "extreme".
        """
        games = profile.get("games_analyzed", 0)
        if games < 5:
            return "unknown"

        wr = profile.get("win_rate", 0.0)
        kda = profile.get("kda", 0.0)
        score = wr * 0.6 + min(kda / 10.0, 1.0) * 0.4

        if score >= 0.75:
            return "extreme"
        elif score >= 0.55:
            return "high"
        elif score >= 0.4:
            return "medium"
        else:
            return "low"

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "opponent_profiler",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

"""
Matchup Database — Historical matchup winrate with confidence scoring.

Stores champion-vs-champion matchup results, computes winrates with
confidence intervals, and provides counter/worst matchup queries.

Location: integrations/lol-history/src/lol_history/matchup_database.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.matchup_database.v1"


class MatchupDatabase:
    """Champion matchup database with confidence-weighted winrates.

    Records matchup results and provides queries for counter picks,
    worst matchups, and confidence-weighted winrate lookups.
    """

    def __init__(self) -> None:
        # key: (champion, opponent) -> {"wins": int, "games": int}
        self._data: dict[tuple[str, str], dict[str, int]] = defaultdict(
            lambda: {"wins": 0, "games": 0}
        )
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_matchup(
        self, champion: str, opponent: str, won: bool
    ) -> None:
        """Record a matchup result.

        Args:
            champion: Champion played.
            opponent: Opponent champion.
            won: Whether champion won.
        """
        self._data[(champion, opponent)]["games"] += 1
        if won:
            self._data[(champion, opponent)]["wins"] += 1
        # Also record the inverse
        self._data[(opponent, champion)]["games"] += 1
        if not won:
            self._data[(opponent, champion)]["wins"] += 1

    def query_matchup(
        self, champion: str, opponent: str
    ) -> dict[str, Any]:
        """Query matchup statistics.

        Args:
            champion: Champion name.
            opponent: Opponent champion name.

        Returns:
            Dict with games, wins, win_rate, confidence.
        """
        entry = self._data.get((champion, opponent), {"wins": 0, "games": 0})
        games = entry["games"]
        wins = entry["wins"]
        wr = wins / games if games > 0 else 0.0
        confidence = self._compute_confidence(games)
        return {
            "champion": champion,
            "against": opponent,
            "games": games,
            "wins": wins,
            "win_rate": wr,
            "confidence": confidence,
        }

    def _compute_confidence(self, games: int) -> float:
        """Compute confidence score based on sample size.

        Uses sigmoid-like scaling: confidence approaches 1.0
        as games increase.

        Args:
            games: Number of games.

        Returns:
            Confidence [0.0, 1.0].
        """
        if games == 0:
            return 0.0
        return 1.0 - math.exp(-games / 20.0)

    def best_counter_for(self, champion: str) -> dict[str, Any]:
        """Find best counter pick against a champion.

        Args:
            champion: Champion to counter.

        Returns:
            Matchup dict with the opponent having lowest winrate for champion.
        """
        matchups = self.all_matchups_for(champion)
        if not matchups:
            return {"against": "", "win_rate": 0.0, "confidence": 0.0}
        matchups.sort(key=lambda m: m["win_rate"])
        return matchups[0]

    def worst_matchup_for(self, champion: str) -> dict[str, Any]:
        """Find worst matchup for a champion.

        Args:
            champion: Champion name.

        Returns:
            Matchup dict with the opponent having lowest winrate for champion.
        """
        return self.best_counter_for(champion)

    def all_matchups_for(self, champion: str) -> list[dict[str, Any]]:
        """Get all matchup data for a champion.

        Args:
            champion: Champion name.

        Returns:
            List of matchup dicts.
        """
        results: list[dict[str, Any]] = []
        for (c, o), entry in self._data.items():
            if c == champion:
                games = entry["games"]
                wins = entry["wins"]
                results.append({
                    "against": o,
                    "games": games,
                    "wins": wins,
                    "win_rate": wins / games if games > 0 else 0.0,
                    "confidence": self._compute_confidence(games),
                })
        return results

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "matchup_database",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

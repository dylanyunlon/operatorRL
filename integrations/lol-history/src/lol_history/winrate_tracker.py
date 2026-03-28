"""
Winrate Tracker — Personal hero pool winrate drift and trend detection.

Tracks per-champion winrate over time, detects improving/declining trends,
and provides recent form analysis.

Location: integrations/lol-history/src/lol_history/winrate_tracker.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.winrate_tracker.v1"


class WinrateTracker:
    """Track personal winrate drift and trends per champion.

    Records game results, computes per-champion and overall winrates,
    detects trends using sliding window analysis.
    """

    def __init__(self) -> None:
        self._records: dict[str, list[bool]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_game(self, champion: str, win: bool) -> None:
        """Record a game result.

        Args:
            champion: Champion played.
            win: Whether the game was won.
        """
        if champion not in self._records:
            self._records[champion] = []
        self._records[champion].append(win)

    def get_champion_stats(self, champion: str) -> dict[str, Any]:
        """Get stats for a specific champion.

        Args:
            champion: Champion name.

        Returns:
            Dict with games, wins, losses, win_rate.
        """
        results = self._records.get(champion, [])
        games = len(results)
        wins = sum(results)
        return {
            "champion": champion,
            "games": games,
            "wins": wins,
            "losses": games - wins,
            "win_rate": wins / games if games > 0 else 0.0,
        }

    def detect_trend(self, champion: str, window: int = 10) -> str:
        """Detect trend for a champion using sliding window.

        Args:
            champion: Champion name.
            window: Window size for trend detection.

        Returns:
            "improving", "declining", or "stable".
        """
        results = self._records.get(champion, [])
        if len(results) < window * 2:
            return "stable"
        first_half = results[-window * 2 : -window]
        second_half = results[-window:]
        wr_first = sum(first_half) / len(first_half)
        wr_second = sum(second_half) / len(second_half)
        diff = wr_second - wr_first
        if diff > 0.15:
            return "improving"
        elif diff < -0.15:
            return "declining"
        return "stable"

    def overall_winrate(self) -> float:
        """Get overall winrate across all champions.

        Returns:
            Overall win rate [0.0, 1.0].
        """
        all_results: list[bool] = []
        for results in self._records.values():
            all_results.extend(results)
        if not all_results:
            return 0.0
        return sum(all_results) / len(all_results)

    def top_champions(self, n: int = 5) -> list[dict[str, Any]]:
        """Get top N champions by games played.

        Args:
            n: Number of champions to return.

        Returns:
            List of champion stat dicts, sorted by games played desc.
        """
        stats = [self.get_champion_stats(c) for c in self._records]
        stats.sort(key=lambda s: s["games"], reverse=True)
        return stats[:n]

    def recent_form(
        self, champion: str, last_n: int = 5
    ) -> dict[str, Any]:
        """Get recent form for a champion.

        Args:
            champion: Champion name.
            last_n: Number of recent games.

        Returns:
            Dict with wins, losses, win_rate.
        """
        results = self._records.get(champion, [])
        recent = results[-last_n:]
        wins = sum(recent)
        return {
            "wins": wins,
            "losses": len(recent) - wins,
            "win_rate": wins / len(recent) if recent else 0.0,
            "games": len(recent),
        }

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "winrate_tracker",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

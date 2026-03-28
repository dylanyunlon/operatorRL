"""
Match Analyzer — statistical analysis of historical match data.

Provides winrate computation, champion stats, KDA analysis, role detection,
streak detection, and game duration statistics from match history data.

Used by PlayerProfiler to build opponent intelligence reports.

Location: integrations/lol-history/src/lol_history/match_analyzer.py
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


class MatchAnalyzer:
    """Analyzes historical match data for tactical intelligence."""

    def compute_winrate(self, matches: list[dict[str, Any]]) -> float:
        """Compute overall winrate from match list.

        Args:
            matches: List of match dicts with 'win' boolean field.

        Returns:
            Winrate as float [0.0, 1.0]. Returns 0.0 for empty input.
        """
        if not matches:
            return 0.0
        wins = sum(1 for m in matches if m.get("win", False))
        return wins / len(matches)

    def champion_stats(
        self, matches: list[dict[str, Any]]
    ) -> dict[int, dict[str, Any]]:
        """Aggregate stats per champion.

        Args:
            matches: List of match dicts with champion_id, win, kills, deaths, assists.

        Returns:
            Dict mapping champion_id → {games, wins, kills, deaths, assists, winrate}.
        """
        stats: dict[int, dict[str, Any]] = {}

        for m in matches:
            cid = m.get("champion_id")
            if cid is None:
                continue

            if cid not in stats:
                stats[cid] = {
                    "games": 0, "wins": 0,
                    "kills": 0, "deaths": 0, "assists": 0,
                }

            s = stats[cid]
            s["games"] += 1
            if m.get("win", False):
                s["wins"] += 1
            s["kills"] += m.get("kills", 0)
            s["deaths"] += m.get("deaths", 0)
            s["assists"] += m.get("assists", 0)

        # Compute derived stats
        for s in stats.values():
            s["winrate"] = s["wins"] / s["games"] if s["games"] > 0 else 0.0
            deaths = s["deaths"] or 1
            s["avg_kda"] = (s["kills"] + s["assists"]) / deaths

        return stats

    def recent_form(
        self, matches: list[dict[str, Any]], last_n: int = 5
    ) -> dict[str, int]:
        """Analyze recent form (last N games).

        Args:
            matches: List of match dicts (most recent first).
            last_n: Number of recent games to consider.

        Returns:
            Dict with wins, losses counts.
        """
        recent = matches[:last_n]
        wins = sum(1 for m in recent if m.get("win", False))
        losses = len(recent) - wins
        return {"wins": wins, "losses": losses}

    def compute_kda(self, matches: list[dict[str, Any]]) -> float:
        """Compute aggregate KDA ratio.

        Args:
            matches: List of match dicts with kills, deaths, assists.

        Returns:
            KDA ratio. Returns inf equivalent (kills+assists) if zero deaths.
        """
        total_k = sum(m.get("kills", 0) for m in matches)
        total_d = sum(m.get("deaths", 0) for m in matches)
        total_a = sum(m.get("assists", 0) for m in matches)

        if total_d == 0:
            return float(total_k + total_a) if (total_k + total_a) > 0 else 0.0
        return (total_k + total_a) / total_d

    def preferred_role(self, matches: list[dict[str, Any]]) -> str:
        """Detect preferred role/lane.

        Args:
            matches: List of match dicts with 'role' and/or 'lane' fields.

        Returns:
            Most common lane string (e.g., 'TOP', 'MID', 'BOTTOM', 'JUNGLE').
            Empty string if no data.
        """
        lanes = [m.get("lane", "") for m in matches if m.get("lane")]
        if not lanes:
            return ""
        counter = Counter(lanes)
        return counter.most_common(1)[0][0]

    def duration_stats(
        self, matches: list[dict[str, Any]]
    ) -> dict[str, float]:
        """Compute game duration statistics.

        Args:
            matches: List of match dicts with 'duration_seconds' field.

        Returns:
            Dict with avg, min, max duration in seconds.
        """
        durations = [
            m.get("duration_seconds", 0)
            for m in matches
            if m.get("duration_seconds", 0) > 0
        ]
        if not durations:
            return {"avg": 0.0, "min": 0, "max": 0}
        return {
            "avg": sum(durations) / len(durations),
            "min": min(durations),
            "max": max(durations),
        }

    def current_streak(
        self, matches: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Detect current win/loss streak.

        Args:
            matches: List of match dicts (most recent first) with 'win' field.

        Returns:
            Dict with type ('win' or 'loss') and count.
        """
        if not matches:
            return {"type": "none", "count": 0}

        first_result = matches[0].get("win", False)
        streak_type = "win" if first_result else "loss"
        count = 0

        for m in matches:
            if m.get("win", False) == first_result:
                count += 1
            else:
                break

        return {"type": streak_type, "count": count}

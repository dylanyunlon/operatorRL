"""
Pregame Scout — Automatic opponent history scouting.

Retrieves and analyzes all opponents' match history to generate
pre-game intelligence reports with threat rankings, champion pools,
and weakness detection.

Location: integrations/lol-history/src/lol_history/pregame_scout.py
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.pregame_scout.v1"


class PregameScout:
    """Pre-game opponent scouting engine.

    Analyzes match history data to produce player profiles with:
    - Win rate and KDA
    - Main champion pool
    - Threat level assessment
    - Weakness detection
    """

    def scout_player(
        self,
        puuid: str,
        match_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Scout a single player from their match history.

        Args:
            puuid: Player unique ID.
            match_data: List of match dicts with champion, win, kills, deaths, assists.

        Returns:
            Player profile dict.
        """
        if not match_data:
            return {
                "puuid": puuid,
                "win_rate": 0.0,
                "kda": 0.0,
                "main_champions": [],
                "threat_level": "unknown",
                "games_analyzed": 0,
            }

        total = len(match_data)
        wins = sum(1 for m in match_data if m.get("win"))
        kills = sum(m.get("kills", 0) for m in match_data)
        deaths = sum(m.get("deaths", 0) for m in match_data)
        assists = sum(m.get("assists", 0) for m in match_data)

        win_rate = wins / max(total, 1)
        kda = (kills + assists) / max(deaths, 1)

        # Champion pool analysis
        champ_counter = Counter(m.get("champion", "Unknown") for m in match_data)
        main_champions = [champ for champ, _ in champ_counter.most_common(3)]

        # Threat level
        if win_rate >= 0.6 and kda >= 3.0:
            threat_level = "high"
        elif win_rate >= 0.45:
            threat_level = "medium"
        else:
            threat_level = "low"

        return {
            "puuid": puuid,
            "win_rate": win_rate,
            "kda": kda,
            "main_champions": main_champions,
            "threat_level": threat_level,
            "games_analyzed": total,
            "total_kills": kills,
            "total_deaths": deaths,
            "total_assists": assists,
        }

    def scout_all(
        self,
        opponents: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        """Scout all opponents.

        Args:
            opponents: Dict mapping puuid → match_data list.

        Returns:
            Dict mapping puuid → profile.
        """
        return {
            puuid: self.scout_player(puuid, matches)
            for puuid, matches in opponents.items()
        }

    def generate_report(
        self,
        opponents: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Generate a full pre-game scouting report.

        Args:
            opponents: Dict mapping puuid → match_data list.

        Returns:
            Report dict with profiles and threat ranking.
        """
        profiles = self.scout_all(opponents)

        # Rank by threat: high KDA + high win rate = higher threat
        threat_scores = {}
        for puuid, profile in profiles.items():
            score = profile["win_rate"] * 2 + profile["kda"] * 0.5
            threat_scores[puuid] = score

        threat_ranking = sorted(
            threat_scores.keys(),
            key=lambda p: threat_scores[p],
            reverse=True,
        )

        return {
            "profiles": profiles,
            "threat_ranking": threat_ranking,
            "total_opponents": len(profiles),
        }

"""
Player Profiler — builds opponent intelligence profiles from historical match data.

Combines MatchAnalyzer statistics into actionable pre-game intelligence:
- Threat level classification
- Champion comfort scoring
- Weakness detection
- Playstyle classification
- Pre-game report generation

This is the key module that makes historical battle data *actionable* for
the agentic decision loop during live games.

Location: integrations/lol-history/src/lol_history/player_profiler.py
"""

from __future__ import annotations

import logging
from typing import Any

from lol_history.match_analyzer import MatchAnalyzer

logger = logging.getLogger(__name__)


class PlayerProfiler:
    """Builds opponent intelligence profiles from match history.

    Usage:
        profiler = PlayerProfiler()
        matches = client.parse_match_list(raw_response)
        profile = profiler.build_profile(puuid="abc-123", matches=matches)
        threat = profiler.classify_threat(profile)
        report = profiler.to_pre_game_report(profile)
    """

    def __init__(self) -> None:
        self._analyzer = MatchAnalyzer()

    def build_profile(
        self, puuid: str, matches: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build a comprehensive player profile from match history.

        Args:
            puuid: Player's unique identifier.
            matches: List of normalized match dicts.

        Returns:
            Profile dict with winrate, kda, preferred_role, champion_pool,
            threat_level, recent_form, etc.
        """
        if not matches:
            return {
                "puuid": puuid,
                "games_played": 0,
                "winrate": 0.0,
                "kda": 0.0,
                "preferred_role": "",
                "champion_pool": [],
                "threat_level": "unknown",
                "recent_form": {"wins": 0, "losses": 0},
            }

        winrate = self._analyzer.compute_winrate(matches)
        kda = self._analyzer.compute_kda(matches)
        role = self._analyzer.preferred_role(matches)
        champ_stats = self._analyzer.champion_stats(matches)
        form = self._analyzer.recent_form(matches)

        # Build champion pool (sorted by games played)
        champion_pool = sorted(
            [
                {
                    "champion_id": cid,
                    "games": s["games"],
                    "winrate": s["winrate"],
                    "avg_kda": s["avg_kda"],
                }
                for cid, s in champ_stats.items()
            ],
            key=lambda x: x["games"],
            reverse=True,
        )

        profile = {
            "puuid": puuid,
            "games_played": len(matches),
            "winrate": winrate,
            "kda": kda,
            "preferred_role": role,
            "champion_pool": champion_pool,
            "recent_form": form,
        }
        profile["threat_level"] = self.classify_threat(profile)
        return profile

    def classify_threat(self, profile: dict[str, Any]) -> str:
        """Classify opponent threat level.

        Thresholds:
            extreme: winrate >= 0.65 AND kda >= 4.0 AND games >= 50
            high:    winrate >= 0.55 AND kda >= 3.0 AND games >= 30
            medium:  winrate >= 0.45
            low:     everything else

        Args:
            profile: Player profile dict with winrate, kda, games_played.

        Returns:
            One of: 'extreme', 'high', 'medium', 'low'.
        """
        wr = profile.get("winrate", 0.0)
        kda = profile.get("kda", 0.0)
        games = profile.get("games_played", 0)

        if wr >= 0.65 and kda >= 4.0 and games >= 50:
            return "extreme"
        if wr >= 0.55 and kda >= 3.0 and games >= 30:
            return "high"
        if wr >= 0.45:
            return "medium"
        return "low"

    def champion_comfort_score(
        self, champion_stats: dict[str, Any]
    ) -> float:
        """Compute comfort score for a specific champion.

        Score = normalized(games * winrate * kda_factor).
        Range: [0.0, 1.0].

        Args:
            champion_stats: Dict with games, winrate, avg_kda.

        Returns:
            Comfort score between 0.0 and 1.0.
        """
        games = champion_stats.get("games", 0)
        winrate = champion_stats.get("winrate", 0.0)
        avg_kda = champion_stats.get("avg_kda", 0.0)

        if games == 0:
            return 0.0

        # Normalize: games caps at 100, KDA caps at 10
        games_factor = min(games / 100.0, 1.0)
        kda_factor = min(avg_kda / 10.0, 1.0)

        raw = games_factor * 0.3 + winrate * 0.4 + kda_factor * 0.3
        return max(0.0, min(1.0, raw))

    def detect_weaknesses(
        self, matches: list[dict[str, Any]]
    ) -> list[str]:
        """Detect player weaknesses from match history.

        Checks for:
        - High death rate (avg deaths > 6)
        - Low KDA (< 2.0)
        - Short game losses (dying early)
        - Narrow champion pool

        Args:
            matches: List of normalized match dicts.

        Returns:
            List of weakness description strings.
        """
        if not matches:
            return []

        weaknesses = []

        # High death rate
        avg_deaths = sum(m.get("deaths", 0) for m in matches) / len(matches)
        if avg_deaths > 6:
            weaknesses.append(f"high_death_rate:avg={avg_deaths:.1f}")

        # Low KDA
        kda = self._analyzer.compute_kda(matches)
        if kda < 2.0:
            weaknesses.append(f"low_kda:{kda:.2f}")

        # Short game losses
        short_losses = [
            m for m in matches
            if not m.get("win", False) and m.get("duration_seconds", 9999) < 1500
        ]
        if len(short_losses) > len(matches) * 0.3:
            weaknesses.append("early_game_collapses")

        # Narrow champion pool
        champs = set(m.get("champion_id") for m in matches if m.get("champion_id"))
        if len(champs) <= 2 and len(matches) >= 5:
            weaknesses.append(f"narrow_champion_pool:{len(champs)}")

        return weaknesses

    def classify_playstyle(
        self, matches: list[dict[str, Any]]
    ) -> str:
        """Classify overall playstyle from match statistics.

        Categories:
        - aggressive: high kills, high damage share
        - passive: low kills, low deaths, high CS
        - supportive: high assists relative to kills
        - balanced: none of the above

        Args:
            matches: List of match dicts with kills, deaths, assists,
                     cs_per_min, damage_share.

        Returns:
            One of: 'aggressive', 'passive', 'supportive', 'balanced'.
        """
        if not matches:
            return "balanced"

        avg_kills = sum(m.get("kills", 0) for m in matches) / len(matches)
        avg_deaths = sum(m.get("deaths", 0) for m in matches) / len(matches)
        avg_assists = sum(m.get("assists", 0) for m in matches) / len(matches)
        avg_dmg_share = sum(m.get("damage_share", 0.2) for m in matches) / len(matches)

        # Aggressive: high kills + high damage
        if avg_kills > 8 and avg_dmg_share > 0.28:
            return "aggressive"

        # Supportive: assists > kills * 2
        if avg_assists > avg_kills * 2:
            return "supportive"

        # Passive: low kills, low deaths
        if avg_kills < 4 and avg_deaths < 3:
            return "passive"

        return "balanced"

    def to_pre_game_report(self, profile: dict[str, Any]) -> str:
        """Generate a human-readable pre-game intelligence report.

        Args:
            profile: Complete player profile dict.

        Returns:
            Formatted report string.
        """
        lines = [
            f"=== Player Profile: {profile.get('puuid', 'unknown')} ===",
            f"Games: {profile.get('games_played', 0)}",
            f"Winrate: {profile.get('winrate', 0.0):.1%}",
            f"KDA: {profile.get('kda', 0.0):.2f}",
            f"Preferred Role: {profile.get('preferred_role', 'unknown')}",
            f"Threat Level: {profile.get('threat_level', 'unknown')}",
        ]

        pool = profile.get("champion_pool", [])
        if pool:
            top3 = pool[:3]
            pool_str = ", ".join(
                f"#{c['champion_id']}({c['games']}g/{c['winrate']:.0%})"
                for c in top3
            )
            lines.append(f"Top Champions: {pool_str}")

        form = profile.get("recent_form", {})
        if form:
            lines.append(f"Recent Form: {form.get('wins', 0)}W-{form.get('losses', 0)}L")

        return "\n".join(lines)

    def merge_match_sources(
        self,
        lcu_matches: list[dict[str, Any]],
        sgp_matches: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge match data from LCU and SGP sources, deduplicating by game_id.

        SGP may have more recent data; LCU may have more detailed stats.
        When both have the same game_id, prefer LCU (more detail).

        Args:
            lcu_matches: Matches from LCU API.
            sgp_matches: Matches from SGP API.

        Returns:
            Merged and deduplicated list.
        """
        seen: dict[int, dict[str, Any]] = {}

        # LCU first (preferred)
        for m in lcu_matches:
            gid = m.get("game_id")
            if gid is not None:
                seen[gid] = m

        # SGP fills gaps
        for m in sgp_matches:
            gid = m.get("game_id")
            if gid is not None and gid not in seen:
                seen[gid] = m

        return list(seen.values())

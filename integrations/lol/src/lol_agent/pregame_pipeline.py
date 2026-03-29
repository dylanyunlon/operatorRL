"""
Pregame Pipeline — End-to-end pregame: Seraphine history → opponent profile → BP → matchup.

Orchestrates the full pregame flow: resolve opponents, fetch history,
build profiles, compute threat, recommend bans/picks, predict lanes.

Location: integrations/lol/src/lol_agent/pregame_pipeline.py

Reference (拿来主义):
  - Seraphine/app/lol/tools.py: parseSummonerGameInfo full flow
  - Seraphine/app/lol/tools.py: parseAllyGameInfo team scanning
  - integrations/lol/src/lol_agent/pregame_scout_engine.py: scout pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.pregame_pipeline.v1"


class PregamePipeline:
    """End-to-end pregame intelligence pipeline.

    Flow: opponents → resolve PUUIDs → fetch history → build profiles
    → compute threats → recommend bans → predict matchups.
    """

    def __init__(self) -> None:
        self._last_report: dict[str, Any] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def run(
        self,
        opponents: list[dict[str, Any]],
        my_champion: str = "",
    ) -> dict[str, Any]:
        """Execute full pregame pipeline.

        Args:
            opponents: List of opponent dicts with puuid, summonerName,
                       history (list of match dicts), rank (tier/division).
            my_champion: Current player's selected champion.

        Returns:
            Comprehensive pregame report.
        """
        profiles = []
        for opp in opponents:
            profile = self._build_profile(opp)
            profiles.append(profile)

        profiles.sort(key=lambda p: p.get("threat_level", 0.0), reverse=True)

        ban_suggestions = self._suggest_bans(profiles)
        matchup_notes = self._predict_matchups(profiles, my_champion)

        report = {
            "opponent_profiles": profiles,
            "ban_suggestions": ban_suggestions,
            "matchup_notes": matchup_notes,
            "timestamp": time.time(),
        }
        self._last_report = report
        self._fire_evolution("pregame_pipeline_complete", {
            "opponent_count": len(profiles),
        })
        return report

    def _build_profile(self, opp: dict[str, Any]) -> dict[str, Any]:
        """Build a single opponent profile from raw data."""
        history = opp.get("history", [])
        rank = opp.get("rank", {})

        total = len(history)
        wins = sum(1 for g in history if g.get("win", False))
        kills = sum(g.get("kills", 0) for g in history)
        deaths = sum(g.get("deaths", 0) for g in history)
        assists = sum(g.get("assists", 0) for g in history)
        kda = (kills + assists) / max(deaths, 1)
        winrate = wins / max(total, 1)

        # Champion pool (Seraphine getRecentChampions pattern)
        champ_counts: dict[int, int] = {}
        for g in history:
            cid = g.get("championId", 0)
            if cid and g.get("queueId", 0) != 0:
                champ_counts[cid] = champ_counts.get(cid, 0) + 1
        top_champs = sorted(champ_counts.items(), key=lambda x: x[1], reverse=True)[:3]

        tier = rank.get("tier", "UNRANKED")
        tier_score = _TIER_SCORES.get(tier.upper(), 0.3)
        threat = 0.40 * tier_score + 0.35 * min(winrate, 1.0) + 0.25 * min(kda / 10, 1.0)

        return {
            "summoner_name": opp.get("summonerName", ""),
            "puuid": opp.get("puuid", ""),
            "tier": tier,
            "winrate": winrate,
            "kda": kda,
            "games_played": total,
            "top_champions": [{"championId": c, "games": n} for c, n in top_champs],
            "threat_level": max(0.0, min(1.0, threat)),
        }

    def _suggest_bans(self, profiles: list[dict[str, Any]]) -> list[int]:
        """Suggest bans based on opponent champion pools."""
        all_champs: dict[int, float] = {}
        for p in profiles:
            threat = p.get("threat_level", 0.5)
            for c in p.get("top_champions", []):
                cid = c["championId"]
                score = c["games"] * threat
                all_champs[cid] = all_champs.get(cid, 0.0) + score
        ranked = sorted(all_champs.items(), key=lambda x: x[1], reverse=True)
        return [cid for cid, _ in ranked[:5]]

    def _predict_matchups(
        self, profiles: list[dict[str, Any]], my_champ: str
    ) -> list[dict[str, Any]]:
        """Generate matchup prediction notes."""
        notes = []
        for p in profiles:
            threat = p.get("threat_level", 0.5)
            if threat >= 0.6:
                verdict = "dangerous"
            elif threat >= 0.4:
                verdict = "moderate"
            else:
                verdict = "manageable"
            notes.append({
                "summoner": p["summoner_name"],
                "threat": threat,
                "verdict": verdict,
            })
        return notes

    def get_last_report(self) -> dict[str, Any]:
        return self._last_report

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })


_TIER_SCORES: dict[str, float] = {
    "IRON": 0.1, "BRONZE": 0.2, "SILVER": 0.3, "GOLD": 0.4,
    "PLATINUM": 0.5, "EMERALD": 0.55, "DIAMOND": 0.65,
    "MASTER": 0.8, "GRANDMASTER": 0.9, "CHALLENGER": 1.0,
    "UNRANKED": 0.3,
}

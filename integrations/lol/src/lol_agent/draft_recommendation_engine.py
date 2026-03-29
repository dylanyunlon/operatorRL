"""
Draft Recommendation Engine — Pre-game pick/ban recommendation from history.
Location: integrations/lol/src/lol_agent/draft_recommendation_engine.py
Reference: leagueoflegends-optimizer draft, Seraphine champion data
"""
from __future__ import annotations
import logging, math, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.draft_recommendation_engine.v1"

class DraftRecommendationEngine:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def recommend(self, player_history: list[dict[str, Any]], team_comp: list[str],
                  enemy_picks: list[str], *, enemy_history: list[dict[str, Any]] | None = None,
                  matchup_data: dict[str, dict[str, float]] | None = None,
                  banned: list[str] | None = None) -> dict[str, Any]:
        banned_set = set(banned or []) | set(team_comp) | set(enemy_picks)
        picks: list[dict[str, Any]] = []
        bans: list[str] = []

        # Ban recommendations from enemy history
        if enemy_history:
            sorted_enemy = sorted(enemy_history, key=lambda h: h.get("winrate", 0) * h.get("games", 0), reverse=True)
            for eh in sorted_enemy[:3]:
                if eh.get("winrate", 0) > 0.6 and eh.get("games", 0) >= 5:
                    bans.append(eh["champion"])

        # Pick recommendations from player pool
        available = [h for h in player_history if h.get("champion") not in banned_set]
        if matchup_data and enemy_picks:
            for h in available:
                champ = h["champion"]
                if champ in (matchup_data or {}):
                    bonus = sum(matchup_data[champ].get(f"vs_{e}", 0.5) - 0.5 for e in enemy_picks)
                    h = dict(h)
                    h["_matchup_bonus"] = bonus
                    available = [x if x.get("champion") != champ else h for x in available]

        # Sort by composite score
        def _score(h: dict) -> float:
            wr = h.get("winrate", 0.5)
            games = h.get("games", 0)
            conf = min(1.0, games / 20.0)
            bonus = h.get("_matchup_bonus", 0)
            return wr * conf + bonus * 0.3

        available.sort(key=_score, reverse=True)

        for h in available[:5]:
            games = h.get("games", 0)
            conf = min(1.0, games / 20.0)
            reason_parts = [f"{h['champion']}: {h.get('winrate', 0):.0%} WR over {games} games"]
            if h.get("role"):
                reason_parts.append(f"role={h['role']}")
            picks.append({"champion": h["champion"], "confidence": conf,
                          "score": _score(h), "reason": ", ".join(reason_parts)})

        self._fire("recommend_complete", {"picks": len(picks), "bans": len(bans)})
        return {"picks": picks, "bans": bans}

    def _fire(self, t: str, d: dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": t, "key": _EVOLUTION_KEY, "timestamp": time.time(), **d})

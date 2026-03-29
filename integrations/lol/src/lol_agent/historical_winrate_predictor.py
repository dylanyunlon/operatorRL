"""
Historical Winrate Predictor — Predict match win probability from historical profiles.
Location: integrations/lol/src/lol_agent/historical_winrate_predictor.py
Reference: Seraphine/app/lol/tools.py: parseSummonerGameInfo winrate aggregation
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.historical_winrate_predictor.v1"

class HistoricalWinratePredictor:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def predict(self, ally_profiles: list[dict[str, Any]], enemy_profiles: list[dict[str, Any]]) -> dict[str, Any]:
        if not ally_profiles and not enemy_profiles:
            return {"win_probability": 0.5, "confidence": 0.0, "factors": {}}
        ally_score = self._team_score(ally_profiles)
        enemy_score = self._team_score(enemy_profiles)
        total = ally_score + enemy_score
        raw_prob = ally_score / total if total > 0 else 0.5
        win_prob = max(0.05, min(0.95, raw_prob))
        ally_games = sum(p.get("games", 1) for p in ally_profiles)
        enemy_games = sum(p.get("games", 1) for p in enemy_profiles)
        confidence = min(1.0, (ally_games + enemy_games) / 200.0)
        return {"win_probability": round(win_prob, 4), "confidence": round(confidence, 4),
                "factors": {"ally_score": round(ally_score, 4), "enemy_score": round(enemy_score, 4),
                            "ally_games": ally_games, "enemy_games": enemy_games}}

    def _team_score(self, profiles: list[dict[str, Any]]) -> float:
        if not profiles:
            return 0.5
        total = 0.0
        for p in profiles:
            wr = p.get("winrate", 0.5)
            cwr = p.get("champion_winrate", wr)
            rank = p.get("rank_numeric", 0)
            player_score = 0.4 * wr + 0.3 * cwr
            if rank > 0:
                player_score += 0.3 * min(rank / 3000.0, 1.0)
            else:
                player_score += 0.3 * 0.5
            total += player_score
        return total / len(profiles)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "historical_winrate_predictor", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

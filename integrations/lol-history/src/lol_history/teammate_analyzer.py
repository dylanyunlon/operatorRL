"""
Teammate Analyzer — Detect recent teammates and duo-queue patterns.

Location: integrations/lol-history/src/lol_history/teammate_analyzer.py
Reference: Seraphine/app/lol/tools.py: getRecentTeammates, getTeammates
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.teammate_analyzer.v1"

class TeammateAnalyzer:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def find_recent_teammates(self, games: list[dict[str, Any]], target_puuid: str) -> dict[str, dict[str, Any]]:
        mate_stats: dict[str, dict] = defaultdict(lambda: {"games": 0, "wins": 0})
        for game in games:
            participants = game.get("participants", [])
            target_team = None
            for p in participants:
                if p.get("puuid") == target_puuid:
                    target_team = p.get("teamId")
                    break
            if target_team is None:
                continue
            for p in participants:
                puuid = p.get("puuid", "")
                if puuid == target_puuid or puuid == "":
                    continue
                if p.get("teamId") != target_team:
                    continue
                mate_stats[puuid]["games"] += 1
                if p.get("stats", {}).get("win", False):
                    mate_stats[puuid]["wins"] += 1
        result = {}
        for puuid, stats in mate_stats.items():
            g = stats["games"]
            result[puuid] = {"games": g, "wins": stats["wins"], "winrate": stats["wins"] / g if g > 0 else 0.0}
        return result

    def detect_duo_queue(self, games: list[dict[str, Any]], target_puuid: str, min_games: int = 3) -> dict[str, dict[str, Any]]:
        teammates = self.find_recent_teammates(games, target_puuid)
        return {puuid: stats for puuid, stats in teammates.items() if stats["games"] >= min_games}

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "teammate_analyzer", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

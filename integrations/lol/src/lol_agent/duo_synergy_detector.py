"""
Duo Synergy Detector — Detect duo/premade queue patterns from match history.
Location: integrations/lol/src/lol_agent/duo_synergy_detector.py
Reference: Seraphine/app/lol/tools.py: getTeammates, getRecentTeammates
"""
from __future__ import annotations
import logging, time
from collections import defaultdict
from itertools import combinations
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.duo_synergy_detector.v1"

class DuoSynergyDetector:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def detect_duos(self, history: list[dict[str, Any]], target_puuid: str, min_games: int = 3) -> dict[str, dict[str, Any]]:
        mate_stats: dict[str, dict] = defaultdict(lambda: {"games": 0, "wins": 0})
        for game in history:
            participants = game.get("participants", [])
            target_team, target_win = None, False
            for p in participants:
                if p.get("puuid") == target_puuid:
                    target_team = p.get("teamId")
                    target_win = p.get("stats", {}).get("win", False)
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
                if target_win:
                    mate_stats[puuid]["wins"] += 1
        result = {}
        for puuid, stats in mate_stats.items():
            g = stats["games"]
            if g >= min_games:
                result[puuid] = {"games": g, "wins": stats["wins"], "winrate": stats["wins"] / g if g > 0 else 0.0}
        return result

    def detect_premade_group(self, history: list[dict[str, Any]], target_puuid: str, min_games: int = 3) -> list[list[str]]:
        duos = self.detect_duos(history, target_puuid, min_games)
        if not duos:
            return []
        partner_puuids = list(duos.keys())
        groups = [[target_puuid, p] for p in partner_puuids]
        for p1, p2 in combinations(partner_puuids, 2):
            shared = 0
            for game in history:
                participants = game.get("participants", [])
                game_puuids = {p.get("puuid") for p in participants}
                team_map = {p.get("puuid"): p.get("teamId") for p in participants}
                if (target_puuid in game_puuids and p1 in game_puuids and p2 in game_puuids
                        and team_map.get(target_puuid) == team_map.get(p1) == team_map.get(p2)):
                    shared += 1
            if shared >= min_games:
                groups.append([target_puuid, p1, p2])
        return groups

    def duo_threat_score(self, duo_info: dict[str, Any]) -> float:
        games = duo_info.get("games", 0)
        wr = duo_info.get("winrate", 0.5)
        game_factor = min(games / 30.0, 1.0)
        wr_factor = min(max((wr - 0.4) / 0.3, 0.0), 1.0)
        return 0.5 * game_factor + 0.5 * wr_factor

    def synergy_label(self, winrate: float, games: int) -> str:
        if games < 5:
            return "Occasional"
        if winrate >= 0.65:
            return "Strong Synergy"
        if winrate >= 0.55:
            return "Good Synergy"
        if winrate >= 0.45:
            return "Average Synergy"
        return "Weak Synergy"

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "duo_synergy_detector", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

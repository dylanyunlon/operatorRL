"""
Ally Game Info Parser — Extract ally and enemy team info from game sessions.
Location: integrations/lol-history/src/lol_history/ally_game_info_parser.py
Reference: Seraphine/app/lol/tools.py: parseAllyGameInfo, parseSummonerGameInfo
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.ally_game_info_parser.v1"

class AllyGameInfoParser:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_ally_info(self, session: dict[str, Any], current_puuid: str) -> dict[str, Any]:
        my_team = session.get("myTeam", [])
        allies = []
        current_player = None
        for m in my_team:
            entry = {"puuid": m.get("puuid", ""), "champion_id": m.get("championId", 0),
                     "summoner_name": m.get("summonerName", ""),
                     "spell1": m.get("spell1Id", 0), "spell2": m.get("spell2Id", 0)}
            allies.append(entry)
            if m.get("puuid") == current_puuid:
                current_player = entry
        return {"allies": allies, "current_player": current_player, "team_size": len(allies)}

    def parse_enemy_info(self, session: dict[str, Any]) -> dict[str, Any]:
        their_team = session.get("theirTeam", [])
        enemies = [{"puuid": m.get("puuid", ""), "champion_id": m.get("championId", 0),
                     "summoner_name": m.get("summonerName", "")} for m in their_team]
        return {"enemies": enemies}

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "ally_game_info_parser", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

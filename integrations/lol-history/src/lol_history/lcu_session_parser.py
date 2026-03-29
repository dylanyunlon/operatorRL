"""
LCU Session Parser — Parse LCU gameflow session data.

Extracts game ID, queue type, team rosters, and champ select state
from raw LCU WebSocket / REST session payloads.

Location: integrations/lol-history/src/lol_history/lcu_session_parser.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: GameflowSession handling
  - Seraphine/app/lol/tools.py: parseGameInfoByGameflowSession
  - Seraphine/app/lol/listener.py: onGameFlowPhaseChanged event shape
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.lcu_session_parser.v1"


class LcuSessionParser:
    """Parse LCU gameflow session payloads into structured data.

    Mirrors Seraphine connector.py session handling patterns.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_session(self, session: dict[str, Any]) -> dict[str, Any]:
        """Parse a raw gameflow session into structured data.

        Args:
            session: Raw LCU gameflow session dict.

        Returns:
            Structured session with game_id, queue_id, teams, valid flag.
        """
        game_data = session.get("gameData")
        if not game_data or not isinstance(game_data, dict):
            return {"valid": False, "game_id": None, "queue_id": None,
                    "team_one": [], "team_two": []}

        game_id = game_data.get("gameId")
        queue = game_data.get("queue", {})
        queue_id = queue.get("id", 0) if isinstance(queue, dict) else 0

        team_one_raw = game_data.get("teamOne", [])
        team_two_raw = game_data.get("teamTwo", [])

        team_one = self._parse_team(team_one_raw)
        team_two = self._parse_team(team_two_raw)

        result = {
            "valid": game_id is not None,
            "game_id": game_id,
            "queue_id": queue_id,
            "team_one": team_one,
            "team_two": team_two,
        }

        self._fire_evolution({"action": "parse_session", "game_id": game_id})
        return result

    def parse_champ_select(self, cs: dict[str, Any]) -> dict[str, Any]:
        """Parse champ select session data.

        Args:
            cs: Raw champ select session dict.

        Returns:
            Structured champ select with my_team, their_team, bans.
        """
        my_team_raw = cs.get("myTeam", [])
        their_team_raw = cs.get("theirTeam", [])
        bans = cs.get("bans", {})

        my_team = [
            {
                "cell_id": m.get("cellId", 0),
                "champion_id": m.get("championId", 0),
                "puuid": m.get("puuid", ""),
            }
            for m in my_team_raw
        ]

        their_team = [
            {
                "cell_id": m.get("cellId", 0),
                "champion_id": m.get("championId", 0),
                "puuid": m.get("puuid", ""),
            }
            for m in their_team_raw
        ]

        my_bans = bans.get("myTeamBans", [])
        their_bans = bans.get("theirTeamBans", [])

        return {
            "my_team": my_team,
            "their_team": their_team,
            "my_bans": my_bans,
            "their_bans": their_bans,
        }

    def _parse_team(self, raw: list[dict]) -> list[dict[str, Any]]:
        return [
            {
                "puuid": p.get("puuid", ""),
                "champion_id": p.get("championId", 0),
                "summoner_name": p.get("summonerName", ""),
            }
            for p in raw
        ]

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "lcu_session_parser", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

"""
Game Detail Parser — Deep parse single game data into structured metrics.

Location: integrations/lol-history/src/lol_history/game_detail_parser.py

Reference (拿来主义):
  - Seraphine/app/lol/tools.py: parseGameDetailData, parseGameData
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.game_detail_parser.v1"


class GameDetailParser:
    """Parse raw game detail JSON into structured metrics."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse(self, game: dict[str, Any]) -> dict[str, Any]:
        game_id = game.get("gameId")
        if game_id is None:
            return {"valid": False, "game_id": None, "participants": [],
                    "teams": {}, "winning_team": None, "duration_seconds": 0,
                    "game_mode": ""}

        duration = game.get("gameDuration", 0)
        mode = game.get("gameMode", "CLASSIC")
        participants_raw = game.get("participants", [])

        participants = []
        teams: dict[int, list] = {}
        winning_team = None

        for p in participants_raw:
            stats = p.get("stats", {})
            team_id = p.get("teamId", 0)
            win = stats.get("win", False)

            parsed = {
                "puuid": p.get("puuid", ""),
                "champion_id": p.get("championId", 0),
                "team_id": team_id,
                "kills": stats.get("kills", 0),
                "deaths": stats.get("deaths", 0),
                "assists": stats.get("assists", 0),
                "cs": stats.get("totalMinionsKilled", 0),
                "win": win,
            }
            participants.append(parsed)

            if team_id not in teams:
                teams[team_id] = []
            teams[team_id].append(parsed)

            if win:
                winning_team = team_id

        return {
            "valid": True,
            "game_id": game_id,
            "duration_seconds": duration,
            "game_mode": mode,
            "participants": participants,
            "teams": teams,
            "winning_team": winning_team,
        }

    def compute_cspm(self, cs: int, duration_seconds: int) -> float:
        if duration_seconds <= 0:
            return 0.0
        return cs / (duration_seconds / 60.0)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "game_detail_parser", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

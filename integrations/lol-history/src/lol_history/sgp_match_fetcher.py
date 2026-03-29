"""
SGP Match Fetcher — Build URLs and parse responses for SGP match history.

Location: integrations/lol-history/src/lol_history/sgp_match_fetcher.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: getSummonerGamesByPuuid, getGameDetailByGameId
  - Seraphine/app/lol/tools.py: parseGamesDataFromSGP
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.sgp_match_fetcher.v1"


class SgpMatchFetcher:
    """URL builder and response parser for SGP match history API."""

    def __init__(self, base_url: str = "https://127.0.0.1:2999") -> None:
        self.base_url = base_url.rstrip("/")
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def build_sgp_url(self, puuid: str, count: int = 20, start: int = 0) -> str:
        return (
            f"{self.base_url}/lol-match-history/v1/products/lol/{puuid}/matches"
            f"?begIndex={start}&endIndex={start + count}"
        )

    def build_detail_url(self, game_id: int) -> str:
        return f"{self.base_url}/lol-match-history/v1/games/{game_id}"

    def parse_sgp_response(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        games = response.get("games", [])
        if isinstance(games, dict):
            games = games.get("games", [])
        if not isinstance(games, list):
            return []
        return list(games)

    def parse_detail_response(self, response: dict[str, Any]) -> dict[str, Any]:
        return {
            "game_id": response.get("gameId"),
            "duration_seconds": response.get("gameDuration", 0),
            "participants": response.get("participants", []),
            "game_mode": response.get("gameMode", ""),
            "game_type": response.get("gameType", ""),
        }

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "sgp_match_fetcher", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

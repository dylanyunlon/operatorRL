"""
Seraphine History Client — Historical match data via LCU/WeGame API.

Provides URL building and response parsing for historical match data
using Seraphine-compatible LCU API endpoints.

Location: integrations/lol/src/lol_agent/seraphine_history_client.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: getGameDetailByGameId, getRankedStatsByPuuid
  - integrations/lol-history/src/lol_history/seraphine_bridge.py: URL building
  - Seraphine/app/lol/connector.py: LCU endpoint patterns (/lol-match-history/v1/)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.seraphine_history_client.v1"


class SeraphineHistoryClient:
    """HTTP client for Seraphine-style LCU match history access.

    Mirrors Seraphine connector.py patterns: URL building for
    match history, summoner lookup, ranked stats, and game detail.

    Attributes:
        base_url: LCU or proxy base URL.
        timeout: Request timeout.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        base_url: str = "https://127.0.0.1:2999",
        timeout: float = 10.0,
    ) -> None:
        # --- Seraphine connector.py fields (拿来主义) ---
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._request_count: int = 0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def build_summoner_url(self, summoner_name: str) -> str:
        """Build URL for summoner lookup (Seraphine connector pattern).

        Args:
            summoner_name: Summoner display name.

        Returns:
            Full URL string.
        """
        return f"{self.base_url}/lol-summoner/v1/summoners?name={summoner_name}"

    def build_match_history_url(
        self, puuid: str, count: int = 20, start: int = 0
    ) -> str:
        """Build URL for match history (Seraphine connector pattern).

        Args:
            puuid: Player UUID.
            count: Number of matches to fetch.
            start: Start index for pagination.

        Returns:
            Full URL string.
        """
        return (
            f"{self.base_url}/lol-match-history/v1/products/lol/"
            f"{puuid}/matches?begIndex={start}&endIndex={start + count}"
        )

    def build_game_detail_url(self, game_id: int) -> str:
        """Build URL for single game detail (mirrors getGameDetailByGameId).

        Args:
            game_id: Game ID.

        Returns:
            Full URL string.
        """
        return f"{self.base_url}/lol-match-history/v1/games/{game_id}"

    def parse_match_list(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse match list response.

        Args:
            response: Response dict with 'games' key.

        Returns:
            List of match dicts.
        """
        games = response.get("games", [])
        self._request_count += 1

        self._fire_evolution("match_list_parsed", {
            "count": len(games),
        })
        return games

    def parse_ranked_stats(self, response: dict[str, Any]) -> dict[str, Any]:
        """Parse ranked stats response (mirrors getRankedStatsByPuuid).

        Extracts RANKED_SOLO_5x5 queue data.

        Args:
            response: Response dict with 'queues' key.

        Returns:
            Dict with tier, division, wins, losses.
        """
        queues = response.get("queues", [])
        for q in queues:
            if q.get("queueType") == "RANKED_SOLO_5x5":
                return {
                    "tier": q.get("tier", "UNRANKED"),
                    "division": q.get("division", ""),
                    "wins": q.get("wins", 0),
                    "losses": q.get("losses", 0),
                }
        return {"tier": "UNRANKED", "division": "", "wins": 0, "losses": 0}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

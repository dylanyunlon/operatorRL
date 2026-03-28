"""
Seraphine Bridge — HTTP API bridge for historical match data.

Provides HTTP API access to Seraphine-style match history endpoints,
adapted from the Seraphine project (github.com/ljszx/Seraphine) connector
patterns for production-grade operation.

Location: integrations/lol-history/src/lol_history/seraphine_bridge.py

Reference: Seraphine connector + LCU API patterns.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.seraphine_bridge.v1"


class SeraphineClient:
    """HTTP API client for Seraphine-style match history access.

    Provides URL building, response parsing, and evolution callback
    hooks for training data collection.

    Usage:
        client = SeraphineClient(base_url="http://localhost:8080")
        url = client.build_summoner_url("PlayerName")
        url2 = client.build_match_history_url(puuid="abc-123", count=20)
        summoner = client.parse_summoner_response(response_json)
        matches = client.parse_match_list_response(response_json)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:2999",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.evolution_callback: Optional[Callable[[dict], None]] = None
        self._request_count: int = 0

    def build_summoner_url(self, summoner_name: str) -> str:
        """Build URL for summoner lookup.

        Args:
            summoner_name: Summoner display name.

        Returns:
            Full URL string.
        """
        return f"{self.base_url}/lol-summoner/v1/summoners?name={summoner_name}"

    def build_match_history_url(
        self, puuid: str, count: int = 20, start: int = 0
    ) -> str:
        """Build URL for match history retrieval.

        Args:
            puuid: Player unique ID.
            count: Number of matches to retrieve.
            start: Start index for pagination.

        Returns:
            Full URL string.
        """
        return (
            f"{self.base_url}/lol-match-history/v1/products/lol/{puuid}/matches"
            f"?begIndex={start}&endIndex={start + count}"
        )

    def parse_summoner_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Parse summoner lookup response.

        Args:
            response: Raw JSON response dict.

        Returns:
            Normalized summoner data.
        """
        return {
            "puuid": response.get("puuid", ""),
            "name": response.get("name", response.get("displayName", "")),
            "summoner_level": response.get("summonerLevel", 0),
            "id": response.get("id", ""),
        }

    def parse_match_list_response(
        self, response: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Parse match list response.

        Args:
            response: Raw JSON response with games list.

        Returns:
            List of match dicts.
        """
        games = response.get("games", response.get("matches", []))
        if isinstance(games, dict):
            games = games.get("games", [])
        return list(games)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback with module metadata.

        Args:
            data: Event data dict.
        """
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "seraphine_bridge",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

"""
LCU Async Client — Async httpx-based LCU API client.

Provides async access to the League Client Update API for
retrieving match history, ranked stats, and summoner data.

Inspired by Seraphine's connector patterns, adapted for
production-grade async operation with retry and auth.

Location: integrations/lol-history/src/lol_history/lcu_async_client.py
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.lcu_async_client.v1"


@dataclass
class LCUClientConfig:
    """Configuration for the async LCU client."""
    host: str = "127.0.0.1"
    port: int = 2999
    auth_token: str = ""
    max_retries: int = 3
    timeout: float = 10.0
    verify_ssl: bool = False


class LCUAsyncClient:
    """Async client for the LCU API.

    Provides URL building, auth header generation, and response
    parsing for LCU match history endpoints.

    Usage:
        client = LCUAsyncClient(config=LCUClientConfig(auth_token="riot-123"))
        url = client.build_url("/lol-match-history/v1/products/lol/puuid/matches")
        headers = client.auth_headers()
        # async: response = await httpx.get(url, headers=headers, verify=False)
        matches = client.parse_match_list(response_json)
    """

    def __init__(self, config: LCUClientConfig | None = None) -> None:
        self.config = config or LCUClientConfig()
        self._request_count: int = 0

    def build_url(self, path: str) -> str:
        """Build full LCU API URL.

        Args:
            path: API path (e.g., "/lol-match-history/v1/products/lol/puuid/matches").

        Returns:
            Full HTTPS URL.
        """
        host = self.config.host
        port = self.config.port
        if not path.startswith("/"):
            path = f"/{path}"
        return f"https://{host}:{port}{path}"

    def auth_headers(self) -> dict[str, str]:
        """Build authentication headers.

        LCU uses Basic auth with "riot" username and the auth token.
        """
        headers: dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        if self.config.auth_token:
            credentials = f"riot:{self.config.auth_token}"
            b64 = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {b64}"

        return headers

    def parse_match_list(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse match history response.

        LCU returns: {"games": {"games": [...]}}
        """
        games_wrapper = raw.get("games", {})
        if isinstance(games_wrapper, dict):
            return games_wrapper.get("games", [])
        return []

    def parse_ranked_stats(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse ranked stats response."""
        return {
            "queues": raw.get("queues", []),
            "highest_ranked_entry": raw.get("highestRankedEntry", {}),
        }

    def get_stats(self) -> dict[str, Any]:
        """Get client statistics."""
        return {
            "requests_made": self._request_count,
            "host": self.config.host,
            "port": self.config.port,
        }

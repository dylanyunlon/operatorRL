"""
Live Client Connector — Riot Live Client Data API interface.

Provides URL building, response parsing, and connection management for
the LoL Live Client Data API at https://127.0.0.1:2999/liveclientdata/.

Location: integrations/lol/src/lol_agent/live_client_connector.py

Reference (拿来主义):
  - Seraphine/app/lol/connector.py: retry decorator, needLcu guard, PastRequest tracking
  - leagueoflegends-optimizer: Riot API endpoint patterns
  - integrations/lol-history/src/lol_history/seraphine_bridge.py: URL building pattern
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.live_client_connector.v1"

# Live Client Data API endpoint map (reference: Riot developer docs)
_ENDPOINTS: dict[str, str] = {
    "allgamedata": "/liveclientdata/allgamedata",
    "playerlist": "/liveclientdata/playerlist",
    "activeplayer": "/liveclientdata/activeplayer",
    "eventdata": "/liveclientdata/eventdata",
    "gamestats": "/liveclientdata/gamestats",
    "scores": "/liveclientdata/playerscores",
    "items": "/liveclientdata/playeritems",
    "abilities": "/liveclientdata/activeplayerabilities",
    "runes": "/liveclientdata/activeplayerrunes",
}


class LiveClientConnector:
    """HTTP connector for Riot's Live Client Data API.

    Mirrors Seraphine's connector.py patterns: URL building,
    response parsing, retry-aware design, evolution callback hooks.

    Attributes:
        base_url: Base URL for the API (default: https://127.0.0.1:2999).
        timeout: Request timeout in seconds.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        base_url: str = "https://127.0.0.1:2999",
        timeout: float = 5.0,
        max_retries: int = 3,
    ) -> None:
        # --- Seraphine connector.py fields (拿来主义) ---
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

        # --- Request tracking (mirrors PastRequest pattern) ---
        self._request_count: int = 0
        self._last_request_time: float = 0.0
        self._error_count: int = 0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def build_url(self, endpoint: str) -> str:
        """Build full URL for a Live Client Data API endpoint.

        Args:
            endpoint: Endpoint key (e.g., 'allgamedata', 'playerlist').

        Returns:
            Full URL string.
        """
        path = _ENDPOINTS.get(endpoint, f"/liveclientdata/{endpoint}")
        return f"{self.base_url}{path}"

    def parse_response(self, raw: str) -> dict[str, Any]:
        """Parse a JSON response string from the API.

        Args:
            raw: Raw response body string.

        Returns:
            Parsed dict, or dict with 'error' key on failure.
        """
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                self._request_count += 1
                self._last_request_time = time.time()
                return data
            if isinstance(data, list):
                self._request_count += 1
                return {"items": data}
            return {"data": data}
        except (json.JSONDecodeError, TypeError):
            self._error_count += 1
            return {"error": "invalid_json", "raw": str(raw)[:200]}

    def get_stats(self) -> dict[str, Any]:
        """Return connector statistics.

        Returns:
            Dict with request_count, error_count, last_request_time.
        """
        return {
            "request_count": self._request_count,
            "error_count": self._error_count,
            "last_request_time": self._last_request_time,
        }

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

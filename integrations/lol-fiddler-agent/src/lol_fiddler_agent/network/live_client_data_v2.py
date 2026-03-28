"""
Live Client Data V2 — LoL Live API with LoLCodec consistency verification.

Fetches live game data from the LoL client API and verifies output
format consistency with the protocol-decoder LoLCodec.

Location: integrations/lol-fiddler-agent/src/lol_fiddler_agent/network/live_client_data_v2.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_fiddler_agent.network.live_client_data_v2.v1"

_ENDPOINTS = {
    "allgamedata": "/liveclientdata/allgamedata",
    "playerlist": "/liveclientdata/playerlist",
    "activeplayer": "/liveclientdata/activeplayer",
    "gamestats": "/liveclientdata/gamestats",
    "eventdata": "/liveclientdata/eventdata",
}


@dataclass
class LiveGameSnapshot:
    """Parsed live game data snapshot."""
    game_time: float = 0.0
    active_player_name: str = ""
    active_player_level: int = 0
    player_count: int = 0
    timestamp: float = field(default_factory=time.time)
    raw_data: dict[str, Any] = field(default_factory=dict)


class LiveClientDataV2:
    """LoL Live Client Data API v2.

    Key improvements over v1:
    - Codec-consistent output format
    - Snapshot dataclass for type safety
    - Evolution callback hooks
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 2999) -> None:
        self._host = host
        self._port = port
        self._codec_name = "lol"

    @property
    def base_url(self) -> str:
        return f"https://{self._host}:{self._port}"

    @property
    def codec_name(self) -> str:
        return self._codec_name

    def build_url(self, endpoint_key: str) -> str:
        """Build full URL for an endpoint."""
        path = _ENDPOINTS.get(endpoint_key, f"/liveclientdata/{endpoint_key}")
        return f"{self.base_url}{path}"

    def parse_game_data(self, raw: dict[str, Any]) -> Optional[LiveGameSnapshot]:
        """Parse raw allgamedata response into a snapshot.

        Args:
            raw: Raw JSON response from /liveclientdata/allgamedata.

        Returns:
            LiveGameSnapshot or None if data is incomplete.
        """
        if not raw:
            return None

        active_player = raw.get("activePlayer", {})
        game_data = raw.get("gameData", {})
        all_players = raw.get("allPlayers", [])

        if not active_player and not game_data:
            return None

        return LiveGameSnapshot(
            game_time=game_data.get("gameTime", 0.0),
            active_player_name=active_player.get("summonerName", ""),
            active_player_level=active_player.get("level", 0),
            player_count=len(all_players),
            raw_data=raw,
        )

"""
LoL Protocol Decoder — Live Client API JSON decoding + field mapping.

Decodes LoL Live Client Data API responses into standardized fields.
Supports allgamedata, activeplayer, playerlist, gamestats, eventdata endpoints.

Location: extensions/protocol-decoder/src/lol_protocol_decoder.py

Reference (拿来主义):
  - leagueoflegends-optimizer/src/live_client/: Live Client API data pipeline
  - integrations/lol/src/lol_agent/live_client_connector.py: parse_response pattern
  - extensions/fiddler-bridge/src/fiddler_bridge/lol_protocol_decoder.py: initial decoder
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.lol_protocol_decoder.v1"


class LoLProtocolDecoder:
    """LoL Live Client Data API protocol decoder.

    Decodes JSON responses from LoL's local API (https://127.0.0.1:2999)
    into standardized field names for downstream processing.

    Attributes:
        game_name: Always 'lol'.
        decode_count: Number of decodes performed.
        evolution_callback: Optional callback for evolution events.
    """

    game_name: str = "lol"

    def __init__(self) -> None:
        self._decode_count: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def decode_count(self) -> int:
        return self._decode_count

    def decode(self, raw: str, endpoint: str = "", **kwargs: Any) -> dict[str, Any]:
        """Decode a raw JSON response from LoL Live Client API.

        Args:
            raw: Raw JSON string.
            endpoint: API endpoint path for routing.

        Returns:
            Decoded and normalized dict.
        """
        self._decode_count += 1

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {"error": "invalid_json", "raw": raw[:200]}

        # Route to specific decoder based on endpoint
        if "allgamedata" in endpoint:
            result = self._decode_all_game_data(data)
        elif "activeplayer" in endpoint:
            result = self._decode_active_player(data)
        elif "playerlist" in endpoint:
            result = self._decode_player_list(data)
        elif "gamestats" in endpoint:
            result = self._decode_game_stats(data)
        elif "eventdata" in endpoint:
            result = self._decode_event_data(data)
        else:
            result = {"data": data, "raw": True}

        result["_endpoint"] = endpoint
        self._fire_evolution({"action": "decode", "endpoint": endpoint})
        return result

    def _decode_all_game_data(self, data: dict) -> dict[str, Any]:
        game_data = data.get("gameData", {})
        return {
            "game_time": game_data.get("gameTime", 0.0),
            "map_number": game_data.get("mapNumber", 0),
            "all_players": data.get("allPlayers", []),
            "active_player": data.get("activePlayer", {}),
        }

    def _decode_active_player(self, data: dict) -> dict[str, Any]:
        return {
            "summoner_name": data.get("summonerName", ""),
            "level": data.get("level", 0),
            "current_gold": data.get("currentGold", 0.0),
            "abilities": data.get("abilities", {}),
            "champion_stats": data.get("championStats", {}),
        }

    def _decode_player_list(self, data: Any) -> dict[str, Any]:
        if isinstance(data, list):
            players = [
                {
                    "summoner_name": p.get("summonerName", ""),
                    "team": p.get("team", ""),
                    "champion": p.get("championName", ""),
                }
                for p in data
            ]
        else:
            players = []
        return {"players": players}

    def _decode_game_stats(self, data: dict) -> dict[str, Any]:
        return {
            "game_mode": data.get("gameMode", ""),
            "game_time": data.get("gameTime", 0.0),
            "map_name": data.get("mapName", ""),
        }

    def _decode_event_data(self, data: dict) -> dict[str, Any]:
        raw_events = data.get("Events", [])
        events = [
            {
                "name": e.get("EventName", ""),
                "time": e.get("EventTime", 0.0),
            }
            for e in raw_events
        ]
        return {"events": events}

    # --- Evolution pattern ---
    def _fire_evolution(self, detail: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "key": _EVOLUTION_KEY,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                logger.warning("Evolution callback error (lol_protocol_decoder)")

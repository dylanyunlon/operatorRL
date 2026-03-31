"""
LolProtocolAdapter — LoL protocol adapter via unified GameProtocolAdapterBase interface.

Adapts LoL's Live Client Data API (https://127.0.0.1:2999/liveclientdata/) + Fiddler
capture to the universal game state schema defined by GameProtocolAdapterBase.

Location: extensions/protocol_decoder/src/lol_protocol_adapter.py

Reference (拿来主义):
  - extensions/fiddler_bridge/src/fiddler_lol_decoder.py: endpoint dispatch + decode
  - game_protocol_adapter_base.py（M666）: unified adapter interface
  - integrations/lol/src/lol_agent/live_client_connector.py: _ENDPOINTS map
  - Seraphine/app/lol/connector.py: endpoint→handler pattern

Design Notes (Knuth-level critique):
  User:
    - connect() validates config has valid host/port but never crashes on bad input.
    - decode() handles both raw JSON strings and pre-parsed dicts.
    - normalize() maps LoL-specific fields to universal schema (players/map/resources/time).
  System:
    - Endpoint dispatch is O(1) dict lookup mirroring fiddler_lol_decoder.py pattern.
    - Normalization preserves original keys under '_raw' for debugging.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.lol_protocol_adapter.v1"

# Lazy import to avoid circular; base is in same package
try:
    from .game_protocol_adapter_base import GameProtocolAdapterBase
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from game_protocol_adapter_base import GameProtocolAdapterBase


_LOL_ENDPOINTS = {
    "allgamedata", "playerlist", "activeplayer", "eventdata",
    "gamestats", "playerscores", "playeritems",
    "activeplayerabilities", "activeplayerrunes",
}


class LolProtocolAdapter(GameProtocolAdapterBase):
    """LoL protocol adapter — Live Client Data + Fiddler capture.

    Config keys:
        host: str (default '127.0.0.1')
        port: int (default 2999)
        use_fiddler: bool (default False)
    """

    def __init__(self) -> None:
        super().__init__()
        self._host: str = "127.0.0.1"
        self._port: int = 2999
        self._use_fiddler: bool = False
        self._endpoint_stats: Dict[str, int] = {}

    @property
    def game_type(self) -> str:
        return "lol"

    def _connect_impl(self, config: Dict[str, Any]) -> bool:
        self._host = config.get("host", "127.0.0.1")
        self._port = config.get("port", 2999)
        self._use_fiddler = config.get("use_fiddler", False)
        # Validate port range
        if not isinstance(self._port, int) or self._port < 1 or self._port > 65535:
            return False
        return True

    def _disconnect_impl(self) -> None:
        self._endpoint_stats.clear()

    def _decode_impl(self, raw_data: Any) -> Dict[str, Any]:
        """Decode raw LoL data (JSON string or dict)."""
        if isinstance(raw_data, str):
            parsed = json.loads(raw_data)
        elif isinstance(raw_data, dict):
            parsed = dict(raw_data)
        else:
            raise ValueError(f"Unsupported raw_data type: {type(raw_data)}")

        # Detect endpoint from data structure
        endpoint = parsed.get("_endpoint", "unknown")
        if "allPlayers" in parsed and "gameData" in parsed:
            endpoint = "allgamedata"
        elif "activePlayer" in parsed and "allPlayers" not in parsed:
            endpoint = "activeplayer"
        elif isinstance(parsed.get("Events"), list):
            endpoint = "eventdata"

        self._endpoint_stats[endpoint] = self._endpoint_stats.get(endpoint, 0) + 1

        return {
            "endpoint": endpoint,
            "data": parsed,
            "source": "fiddler" if self._use_fiddler else "liveclient",
        }

    def _normalize_impl(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize LoL decoded data to universal schema."""
        data = decoded.get("data", {})
        endpoint = decoded.get("endpoint", "unknown")

        # Universal schema fields
        result: Dict[str, Any] = {
            "game_type": "lol",
            "source_endpoint": endpoint,
            "game_time": 0.0,
            "players": [],
            "map_state": {},
            "resources": {},
            "events": [],
            "_raw": data,
        }

        # Extract game time
        gd = data.get("gameData", data)
        result["game_time"] = gd.get("gameTime", gd.get("game_time", 0.0))

        # Normalize players
        all_players = data.get("allPlayers", [])
        for p in all_players:
            result["players"].append({
                "name": p.get("summonerName", p.get("riotIdGameName", "")),
                "champion": p.get("championName", ""),
                "level": p.get("level", 0),
                "team": p.get("team", ""),
                "is_dead": p.get("isDead", False),
                "position": p.get("position", ""),
                "kills": p.get("scores", {}).get("kills", 0),
                "deaths": p.get("scores", {}).get("deaths", 0),
                "assists": p.get("scores", {}).get("assists", 0),
                "cs": p.get("scores", {}).get("creepScore", 0),
            })

        # Events
        raw_events = data.get("events", data.get("Events", []))
        if isinstance(raw_events, dict):
            raw_events = raw_events.get("Events", [])
        for ev in raw_events:
            result["events"].append({
                "type": ev.get("EventName", ev.get("type", "unknown")),
                "time": ev.get("EventTime", ev.get("time", 0.0)),
                "data": ev,
            })

        # Map state stub
        map_info = data.get("mapNumber", data.get("mapName", ""))
        result["map_state"] = {"map_id": map_info}

        return result

    def get_endpoint_stats(self) -> Dict[str, int]:
        """Return per-endpoint decode counts."""
        return dict(self._endpoint_stats)

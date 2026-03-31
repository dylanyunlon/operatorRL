"""
Dota2ProtocolAdapter — Dota2 Game State Integration (GSI) protocol adapter.

Adapts Dota2's GSI protocol (HTTP POST from game client) to the universal
game state schema defined by GameProtocolAdapterBase.

Location: extensions/protocol_decoder/src/dota2_protocol_adapter.py

Reference (拿来主义):
  - dota2bot-OpenHyperAI/: Dota2 bot architecture and game state handling
  - game_protocol_adapter_base.py（M666）: unified adapter interface
  - DI-star: StarCraft observation normalization pattern

Design Notes (Knuth-level critique):
  User:
    - decode() accepts both GSI JSON payloads and pre-parsed dicts.
    - normalize() maps Dota2 hero/item/ability data to universal schema.
    - Unknown GSI sections are preserved under '_raw' — forward compatible.
  System:
    - GSI section dispatch is O(1) dict-based — mirrors fiddler_lol_decoder pattern.
    - Player normalization handles both 'player' (spectator) and 'hero' (player) views.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.dota2_protocol_adapter.v1"

try:
    from .game_protocol_adapter_base import GameProtocolAdapterBase
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from game_protocol_adapter_base import GameProtocolAdapterBase


_GSI_SECTIONS = {
    "provider", "map", "player", "hero", "abilities",
    "items", "draft", "wearables", "minimap", "roshan",
    "buildings", "league", "previously", "added",
}


class Dota2ProtocolAdapter(GameProtocolAdapterBase):
    """Dota2 GSI protocol adapter.

    Config keys:
        gsi_port: int (default 3001)
        auth_token: str (optional)
    """

    def __init__(self) -> None:
        super().__init__()
        self._gsi_port: int = 3001
        self._auth_token: str = ""
        self._section_stats: Dict[str, int] = {}

    @property
    def game_type(self) -> str:
        return "dota2"

    def _connect_impl(self, config: Dict[str, Any]) -> bool:
        self._gsi_port = config.get("gsi_port", 3001)
        self._auth_token = config.get("auth_token", "")
        if not isinstance(self._gsi_port, int) or self._gsi_port < 1 or self._gsi_port > 65535:
            return False
        return True

    def _disconnect_impl(self) -> None:
        self._section_stats.clear()

    def _decode_impl(self, raw_data: Any) -> Dict[str, Any]:
        """Decode Dota2 GSI payload."""
        if isinstance(raw_data, str):
            parsed = json.loads(raw_data)
        elif isinstance(raw_data, dict):
            parsed = dict(raw_data)
        else:
            raise ValueError(f"Unsupported raw_data type: {type(raw_data)}")

        # Track which GSI sections are present
        sections_found = [s for s in _GSI_SECTIONS if s in parsed]
        for s in sections_found:
            self._section_stats[s] = self._section_stats.get(s, 0) + 1

        return {
            "sections": sections_found,
            "data": parsed,
            "source": "gsi",
        }

    def _normalize_impl(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Dota2 GSI data to universal schema."""
        data = decoded.get("data", {})

        result: Dict[str, Any] = {
            "game_type": "dota2",
            "game_time": 0.0,
            "players": [],
            "map_state": {},
            "resources": {},
            "events": [],
            "_raw": data,
        }

        # Map section
        map_data = data.get("map", {})
        result["game_time"] = map_data.get("clock_time", map_data.get("game_time", 0.0))
        result["map_state"] = {
            "map_id": map_data.get("name", ""),
            "game_state": map_data.get("game_state", ""),
            "daytime": map_data.get("daytime", True),
            "radiant_score": map_data.get("radiant_score", 0),
            "dire_score": map_data.get("dire_score", 0),
        }

        # Player / Hero section
        hero_data = data.get("hero", {})
        player_data = data.get("player", {})

        if hero_data:
            result["players"].append({
                "name": player_data.get("name", player_data.get("steamid", "")),
                "champion": hero_data.get("name", ""),
                "level": hero_data.get("level", 0),
                "team": player_data.get("team_name", ""),
                "is_dead": not hero_data.get("alive", True),
                "position": "",
                "kills": player_data.get("kills", 0),
                "deaths": player_data.get("deaths", 0),
                "assists": player_data.get("assists", 0),
                "cs": player_data.get("last_hits", 0),
            })

        # Abilities as events (if present)
        abilities = data.get("abilities", {})
        for key, ab in abilities.items():
            if isinstance(ab, dict) and ab.get("name"):
                result["events"].append({
                    "type": "ability_state",
                    "time": result["game_time"],
                    "data": {"slot": key, **ab},
                })

        # Resources: gold, items
        items_data = data.get("items", {})
        result["resources"] = {
            "gold": player_data.get("gold", 0),
            "gold_reliable": player_data.get("gold_reliable", 0),
            "gold_unreliable": player_data.get("gold_unreliable", 0),
            "item_count": sum(1 for k, v in items_data.items() if isinstance(v, dict) and v.get("name", "empty") != "empty"),
        }

        return result

    def get_section_stats(self) -> Dict[str, int]:
        """Return per-section decode counts."""
        return dict(self._section_stats)

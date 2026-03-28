"""
Dota2 Codec — Game State Integration (GSI) JSON protocol parser.

Parses the JSON payload from Dota 2's built-in Game State Integration system
(HTTP POST to a local endpoint) into a normalized structure.

Reference: https://developer.valvesoftware.com/wiki/Counter-Strike:_Global_Offensive_Game_State_Integration

Location: extensions/protocol-decoder/src/protocol_decoder/codecs/dota2.py
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from protocol_decoder.codec import GameCodec

logger = logging.getLogger(__name__)

_DOTA2_EVOLUTION_KEY: str = "protocol_decoder.codecs.dota2.v1"


class Dota2Codec(GameCodec):
    """Dota 2 Game State Integration codec.

    Parses the JSON from Dota 2's GSI HTTP POST into a normalized dict
    with sections: map, player, hero, abilities, items.
    """

    def __init__(self) -> None:
        self._version = "0.1.0"

    @property
    def name(self) -> str:
        return "dota2"

    @property
    def version(self) -> str:
        return self._version

    def parse(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Parse GSI JSON into normalized dict."""
        if not raw:
            return None

        try:
            text = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return None

        if not text.startswith("{"):
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None

        return self._normalize(data)

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Normalize Dota 2 GSI data."""
        result: dict[str, Any] = {}

        # Map state
        map_data = data.get("map", {})
        result["map"] = {
            "name": map_data.get("name", ""),
            "match_id": map_data.get("matchid", ""),
            "game_time": map_data.get("game_time", 0),
            "clock_time": map_data.get("clock_time", 0),
            "daytime": map_data.get("daytime", True),
            "game_state": map_data.get("game_state", ""),
            "paused": map_data.get("paused", False),
            "win_team": map_data.get("win_team", "none"),
        }

        # Player stats
        player_data = data.get("player", {})
        result["player"] = {
            "name": player_data.get("name", ""),
            "kills": player_data.get("kills", 0),
            "deaths": player_data.get("deaths", 0),
            "assists": player_data.get("assists", 0),
            "last_hits": player_data.get("last_hits", 0),
            "denies": player_data.get("denies", 0),
            "gold": player_data.get("gold", 0),
            "gpm": player_data.get("gpm", 0),
            "xpm": player_data.get("xpm", 0),
        }

        # Hero data
        hero_data = data.get("hero", {})
        result["hero"] = {
            "id": hero_data.get("id", 0),
            "name": hero_data.get("name", ""),
            "level": hero_data.get("level", 0),
            "alive": hero_data.get("alive", True),
            "health": hero_data.get("health", 0),
            "max_health": hero_data.get("max_health", 0),
            "mana": hero_data.get("mana", 0),
            "max_mana": hero_data.get("max_mana", 0),
        }

        # Abilities
        abilities_raw = data.get("abilities", {})
        abilities: list[dict[str, Any]] = []
        for key in sorted(abilities_raw.keys()):
            ab = abilities_raw[key]
            abilities.append({
                "slot": key,
                "name": ab.get("name", ""),
                "level": ab.get("level", 0),
                "can_cast": ab.get("can_cast", False),
                "passive": ab.get("passive", False),
                "cooldown": ab.get("cooldown", 0),
                "ultimate": ab.get("ultimate", False),
            })
        result["abilities"] = abilities

        # Items
        items_raw = data.get("items", {})
        items: list[dict[str, Any]] = []
        for key in sorted(items_raw.keys()):
            item = items_raw[key]
            items.append({
                "slot": key,
                "name": item.get("name", ""),
                "charges": item.get("charges", 0),
                "can_cast": item.get("can_cast", False),
                "cooldown": item.get("cooldown", 0),
            })
        result["items"] = items

        return result

    def encode(self, data: dict[str, Any]) -> Optional[bytes]:
        """Encode back to JSON."""
        try:
            return json.dumps(data).encode("utf-8")
        except Exception:
            return None

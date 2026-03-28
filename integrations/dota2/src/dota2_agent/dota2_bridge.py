"""
Dota2 Bridge — GameState Integration API adapter.

Provides HTTP API access to Dota 2 Game State Integration (GSI),
adapted from dota2bot-OpenHyperAI game state patterns for
production-grade operation in operatorRL.

Location: integrations/dota2/src/dota2_agent/dota2_bridge.py

Reference: dota2bot-OpenHyperAI (GSI + bot mode patterns).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "dota2_agent.dota2_bridge.v1"

# Hero pool sourced from dota2bot-OpenHyperAI/bots/BotLib/hero_*.lua
_DOTA2_HERO_POOL = [
    "axe", "crystal_maiden", "juggernaut", "invoker", "pudge",
    "lion", "sniper", "earthshaker", "slardar", "phantom_assassin",
    "rubick", "witch_doctor", "bane", "venomancer", "techies",
    "dragon_knight", "ursa", "dazzle", "elder_titan", "gyrocopter",
    "batrider", "brewmaster", "dark_seer", "death_prophet", "lich",
    "enchantress", "vengefulspirit", "faceless_void", "visage",
    "rattletrap", "necrolyte", "earth_spirit", "centaur", "mars",
    "dawnbreaker", "meepo", "enigma", "omniknight", "abaddon",
    "spectre", "ogre_magi", "ringmaster",
]


class Dota2Bridge:
    """Bridge to Dota 2 via Game State Integration (GSI) HTTP API.

    Adapted from dota2bot-OpenHyperAI's game state access patterns.
    The bot framework uses `GetBot()`, `GetTeamPlayers()`, etc. — here
    we translate those into HTTP GSI endpoint calls for external tooling.

    Usage:
        bridge = Dota2Bridge(api_host="127.0.0.1", api_port=27015)
        bridge.connect()
        url = bridge.build_game_state_url()
        state = bridge.parse_game_state(raw_json)
        bridge.disconnect()
    """

    def __init__(
        self,
        api_host: str = "127.0.0.1",
        api_port: int = 27015,
    ) -> None:
        self.api_host = api_host
        self.api_port = api_port
        self.connected: bool = False
        self.evolution_callback: Optional[Callable[[dict], None]] = None
        self._state_count: int = 0

    def connect(self) -> None:
        """Establish connection marker to GSI endpoint."""
        self.connected = True
        logger.info("Dota2Bridge connected to %s:%d", self.api_host, self.api_port)

    def disconnect(self) -> None:
        """Disconnect from GSI endpoint."""
        self.connected = False
        logger.info("Dota2Bridge disconnected")

    def build_game_state_url(self) -> str:
        """Build GSI HTTP endpoint URL.

        Returns:
            Full URL string for the GSI listener.
        """
        return f"http://{self.api_host}:{self.api_port}/gamestate"

    def parse_game_state(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse raw GSI JSON into a normalized game state.

        Mirrors dota2bot-OpenHyperAI's GetBot() → bot:GetHealth() etc.
        pattern but via HTTP JSON instead of Lua API.

        Args:
            raw: Raw GSI JSON payload.

        Returns:
            Normalized game state dict.
        """
        map_data = raw.get("map", {})
        hero_data = raw.get("hero", {})
        abilities = raw.get("abilities", [])

        state = {
            "game_time": map_data.get("game_time", 0),
            "daytime": map_data.get("daytime", True),
            "hero_health": hero_data.get("health", 0),
            "hero_max_health": hero_data.get("max_health", 0),
            "hero_level": hero_data.get("level", 0),
            "abilities": [
                {"name": a.get("name", ""), "cooldown": a.get("cooldown", 0.0)}
                for a in abilities
            ],
        }

        self._state_count += 1
        self._fire_evolution({
            "event": "game_state_parsed",
            "game_time": state["game_time"],
            "parse_count": self._state_count,
        })

        return state

    def get_hero_list(self) -> list[str]:
        """Return known Dota 2 hero pool.

        Sourced from dota2bot-OpenHyperAI/bots/BotLib/hero_*.lua files.

        Returns:
            List of hero internal names.
        """
        return list(_DOTA2_HERO_POOL)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

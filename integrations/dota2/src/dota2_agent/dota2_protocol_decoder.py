"""
Dota2 Protocol Decoder — Game protocol parsing.

Provides JSON and Protobuf protocol decoding for Dota 2,
adapted from Akagi's MITM proxy + Protobuf decoder patterns
and dota2bot-OpenHyperAI's GSI event structure.

Location: integrations/dota2/src/dota2_agent/dota2_protocol_decoder.py

Reference: Akagi liqi.json decoder + dota2bot GSI patterns.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "dota2_agent.dota2_protocol_decoder.v1"


class Dota2ProtocolDecoder:
    """Multi-protocol decoder for Dota 2 game data.

    Supports JSON (GSI), Protobuf (stub), and custom registered decoders.
    Architecture mirrors Akagi's mitmproxy addon decoder chain.
    """

    def __init__(self) -> None:
        self.registered_decoders: dict[str, Callable] = {
            "json": self._decode_json,
            "protobuf": self._decode_protobuf_stub,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_decoder(self, protocol: str, handler: Callable) -> None:
        """Register a custom protocol decoder.

        Args:
            protocol: Protocol name string.
            handler: Callable that takes raw bytes and returns dict.
        """
        self.registered_decoders[protocol] = handler
        logger.info("Registered decoder for protocol: %s", protocol)

    def decode(self, raw: bytes, protocol: str = "json") -> Optional[dict[str, Any]]:
        """Decode raw bytes using the specified protocol.

        Args:
            raw: Raw bytes from network capture.
            protocol: Protocol name (json, protobuf, or custom).

        Returns:
            Decoded dict, empty dict for empty input, or None on error.
        """
        if not raw:
            return {}

        handler = self.registered_decoders.get(protocol)
        if handler is None:
            logger.warning("Unknown protocol: %s", protocol)
            return {}

        result = handler(raw)
        self._fire_evolution({"protocol": protocol, "decoded": result is not None})
        return result

    def decode_gsi(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Decode a Game State Integration JSON payload.

        Normalizes the Valve GSI format into operatorRL's internal format.

        Args:
            payload: GSI JSON dict.

        Returns:
            Normalized game state dict.
        """
        map_data = payload.get("map", {})
        hero_data = payload.get("hero", {})

        hero_name = hero_data.get("name", "")
        # Strip npc_dota_hero_ prefix (Valve convention)
        if hero_name.startswith("npc_dota_hero_"):
            hero_name = hero_name[len("npc_dota_hero_"):]

        return {
            "game_time": map_data.get("game_time", 0),
            "hero_name": hero_name,
            "hero_health": hero_data.get("health", 0),
            "hero_level": hero_data.get("level", 0),
            "provider": payload.get("provider", {}).get("name", ""),
        }

    def supported_protocols(self) -> list[str]:
        """List all supported protocol names.

        Returns:
            List of protocol name strings.
        """
        return list(self.registered_decoders.keys())

    def _decode_json(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Decode JSON bytes."""
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Failed to decode JSON")
            return {}

    def _decode_protobuf_stub(self, raw: bytes) -> dict[str, Any]:
        """Stub Protobuf decoder — returns raw bytes info.

        In production, this would use generated Protobuf classes
        similar to Akagi's liqi.json-based decoder.
        """
        return {
            "raw_bytes": raw.hex(),
            "fields": {},
            "byte_length": len(raw),
        }

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

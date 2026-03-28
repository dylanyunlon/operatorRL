"""
LoL Protocol Decoder — Game protocol packet parsing.

Decodes LoL game protocol packets including JSON payloads, binary headers,
and Live Client Data API responses. Inspired by Akagi MITM architecture.

Reference: Akagi/mitm/mitm_abc.py, Akagi/mitm/common.py
Location: extensions/fiddler-bridge/src/fiddler_bridge/lol_protocol_decoder.py
"""

from __future__ import annotations

import json
import logging
import struct
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.lol_protocol_decoder.v1"

# Binary packet type IDs (simplified mapping)
_PACKET_TYPES: dict[int, str] = {
    0x0001: "game_event",
    0x0002: "player_action",
    0x0003: "state_update",
}


class LoLProtocolDecoder:
    """LoL game protocol decoder.

    Handles JSON packet decoding, binary header parsing,
    Live Client Data interpretation, and custom decoder registration.
    """

    def __init__(self) -> None:
        self.decoders: dict[str, Callable[[bytes], dict]] = {}
        self._stats: dict[str, int] = {"total_decoded": 0, "errors": 0}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def decode_packet(self, raw: bytes) -> dict[str, Any]:
        """Decode a raw packet (JSON or binary).

        Attempts JSON decoding first, falls back to binary header parse.

        Args:
            raw: Raw packet bytes.

        Returns:
            Decoded packet dict.
        """
        self._stats["total_decoded"] += 1

        # Try JSON first
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        # Try binary header
        try:
            header = self.parse_packet_header(raw)
            ptype = _PACKET_TYPES.get(header.get("type_id", 0), "unknown")

            # Check custom decoders
            if ptype in self.decoders:
                return self.decoders[ptype](raw[6:])

            return {
                "type": ptype,
                "header": header,
                "raw": raw[6:].hex() if len(raw) > 6 else raw.hex(),
            }
        except Exception:
            self._stats["errors"] += 1
            return {"type": "unknown", "raw": raw.hex()}

    def parse_packet_header(self, packet: bytes) -> dict[str, Any]:
        """Parse binary packet header (4-byte length + 2-byte type).

        Args:
            packet: Raw packet bytes (at least 6 bytes).

        Returns:
            Dict with length and type_id.
        """
        if len(packet) < 6:
            return {"length": len(packet), "type_id": 0}
        length = struct.unpack(">I", packet[:4])[0]
        type_id = struct.unpack(">H", packet[4:6])[0]
        return {"length": length, "type_id": type_id}

    def decode_live_client_data(
        self, lcd_json: dict[str, Any]
    ) -> dict[str, Any]:
        """Decode Live Client Data API response.

        Args:
            lcd_json: JSON from https://127.0.0.1:2999/liveclientdata/

        Returns:
            Normalized game state dict.
        """
        active = lcd_json.get("activePlayer", {})
        all_players = lcd_json.get("allPlayers", [])
        game_data = lcd_json.get("gameData", {})

        return {
            "active_champion": active.get("championName", ""),
            "active_level": active.get("level", 0),
            "all_players": [
                {"champion": p.get("championName", "")}
                for p in all_players
            ],
            "game_time": game_data.get("gameTime", 0.0),
        }

    def identify_packet_type(self, type_bytes: bytes) -> str:
        """Identify packet type from type bytes.

        Args:
            type_bytes: 2-byte type identifier.

        Returns:
            Packet type string.
        """
        if len(type_bytes) < 2:
            return "unknown"
        type_id = struct.unpack(">H", type_bytes[:2])[0]
        return _PACKET_TYPES.get(type_id, "unknown")

    def register_decoder(
        self, event_type: str, decoder_fn: Callable[[bytes], dict]
    ) -> None:
        """Register a custom decoder for a specific event type.

        Args:
            event_type: Event type string.
            decoder_fn: Callable that takes raw bytes and returns dict.
        """
        self.decoders[event_type] = decoder_fn

    def get_stats(self) -> dict[str, int]:
        """Get decoder statistics.

        Returns:
            Stats dict with total_decoded and errors.
        """
        return dict(self._stats)

    def reset(self) -> None:
        """Reset decoder state and statistics."""
        self._stats = {"total_decoded": 0, "errors": 0}

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "lol_protocol_decoder",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

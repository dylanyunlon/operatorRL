"""
Riichi City Codec — binary WebSocket protocol parser for 一番街 (Riichi City).

Reference: Akagi/mitm/bridge/riichi_city/ architecture.

Location: extensions/protocol-decoder/src/protocol_decoder/codecs/riichi_city.py
"""

from __future__ import annotations

import json
import logging
import struct
from typing import Any, Optional

from protocol_decoder.codec import GameCodec

logger = logging.getLogger(__name__)

_RIICHI_CITY_EVOLUTION_KEY: str = "protocol_decoder.codecs.riichi_city.v1"

# Riichi City protocol constants (from Akagi riichi_city/consts.py pattern)
RC_HEADER_SIZE: int = 8  # 4 bytes msg_type + 4 bytes payload_length
RC_MSG_TYPE_HEARTBEAT: int = 0x01
RC_MSG_TYPE_AUTH: int = 0x02
RC_MSG_TYPE_GAME_ACTION: int = 0x10
RC_MSG_TYPE_GAME_STATE: int = 0x11
RC_MSG_TYPE_GAME_RESULT: int = 0x12

_MSG_TYPE_NAMES: dict[int, str] = {
    RC_MSG_TYPE_HEARTBEAT: "heartbeat",
    RC_MSG_TYPE_AUTH: "auth",
    RC_MSG_TYPE_GAME_ACTION: "game_action",
    RC_MSG_TYPE_GAME_STATE: "game_state",
    RC_MSG_TYPE_GAME_RESULT: "game_result",
}


class RiichiCityCodec(GameCodec):
    """Riichi City binary WebSocket protocol codec.

    Riichi City uses a binary protocol with:
    - 4-byte message type (little-endian uint32)
    - 4-byte payload length (little-endian uint32)
    - JSON payload bytes
    """

    def __init__(self) -> None:
        self._version = "0.1.0"

    @property
    def name(self) -> str:
        return "riichi_city"

    @property
    def version(self) -> str:
        return self._version

    def parse(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Parse a Riichi City binary message."""
        if not raw or len(raw) < RC_HEADER_SIZE:
            return None

        try:
            msg_type = struct.unpack("<I", raw[0:4])[0]
            payload_len = struct.unpack("<I", raw[4:8])[0]
        except struct.error:
            return None

        if len(raw) < RC_HEADER_SIZE + payload_len:
            logger.warning("Incomplete message: expected %d bytes, got %d",
                           RC_HEADER_SIZE + payload_len, len(raw))
            return None

        payload_bytes = raw[RC_HEADER_SIZE:RC_HEADER_SIZE + payload_len]

        # Try parsing payload as JSON
        payload: dict[str, Any] = {}
        if payload_bytes:
            try:
                payload = json.loads(payload_bytes.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {"raw_hex": payload_bytes.hex()}

        type_name = _MSG_TYPE_NAMES.get(msg_type, f"unknown_0x{msg_type:04X}")

        return {
            "msg_type": msg_type,
            "type_name": type_name,
            "payload_length": payload_len,
            "payload": payload,
        }

    def encode(self, data: dict[str, Any]) -> Optional[bytes]:
        """Encode a structured dict back to Riichi City binary format."""
        msg_type = data.get("msg_type")
        payload = data.get("payload", {})
        if msg_type is None:
            return None

        try:
            payload_bytes = json.dumps(payload).encode("utf-8")
            header = struct.pack("<I", msg_type) + struct.pack("<I", len(payload_bytes))
            return header + payload_bytes
        except Exception:
            return None

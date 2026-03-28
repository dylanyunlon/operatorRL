"""
WebSocket Frame Parser — parses raw WebSocket frames for game protocols.

Handles text and binary frames, continuation frames, and close/ping/pong control frames.
Used by the Fiddler Bridge to extract game protocol messages from WebSocket traffic.

Location: extensions/protocol-decoder/src/protocol_decoder/websocket.py
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional

logger = logging.getLogger(__name__)

_WS_EVOLUTION_KEY: str = "protocol_decoder.websocket.v1"


class WSOpcode(IntEnum):
    """WebSocket frame opcodes (RFC 6455)."""
    CONTINUATION = 0x0
    TEXT = 0x1
    BINARY = 0x2
    CLOSE = 0x8
    PING = 0x9
    PONG = 0xA


@dataclass
class WSFrame:
    """Parsed WebSocket frame."""
    opcode: WSOpcode
    payload: bytes
    fin: bool = True
    masked: bool = False
    mask_key: bytes = b""

    @property
    def is_control(self) -> bool:
        return self.opcode >= 0x8

    @property
    def is_text(self) -> bool:
        return self.opcode == WSOpcode.TEXT

    @property
    def is_binary(self) -> bool:
        return self.opcode == WSOpcode.BINARY

    def text(self) -> str:
        """Decode payload as UTF-8 text."""
        return self.payload.decode("utf-8", errors="replace")

    def to_dict(self) -> dict[str, Any]:
        return {
            "opcode": self.opcode.name,
            "fin": self.fin,
            "masked": self.masked,
            "payload_length": len(self.payload),
        }


class WSFrameParser:
    """Stateless WebSocket frame parser.

    Parses raw bytes into WSFrame objects. Handles multi-frame messages
    via continuation frame assembly.
    """

    def __init__(self) -> None:
        self._fragments: list[bytes] = []
        self._fragment_opcode: Optional[WSOpcode] = None

    def parse_frame(self, data: bytes) -> Optional[WSFrame]:
        """Parse a single WebSocket frame from raw bytes.

        Returns None if data is insufficient or malformed.
        """
        if len(data) < 2:
            return None

        byte0 = data[0]
        byte1 = data[1]

        fin = bool(byte0 & 0x80)
        opcode_val = byte0 & 0x0F

        try:
            opcode = WSOpcode(opcode_val)
        except ValueError:
            logger.warning("Unknown WebSocket opcode: 0x%02X", opcode_val)
            return None

        masked = bool(byte1 & 0x80)
        payload_len = byte1 & 0x7F
        offset = 2

        if payload_len == 126:
            if len(data) < 4:
                return None
            payload_len = struct.unpack(">H", data[2:4])[0]
            offset = 4
        elif payload_len == 127:
            if len(data) < 10:
                return None
            payload_len = struct.unpack(">Q", data[2:10])[0]
            offset = 10

        mask_key = b""
        if masked:
            if len(data) < offset + 4:
                return None
            mask_key = data[offset:offset + 4]
            offset += 4

        if len(data) < offset + payload_len:
            return None

        payload = bytearray(data[offset:offset + payload_len])
        if masked:
            for i in range(len(payload)):
                payload[i] ^= mask_key[i % 4]

        return WSFrame(
            opcode=opcode,
            payload=bytes(payload),
            fin=fin,
            masked=masked,
            mask_key=mask_key,
        )

    def assemble(self, frame: WSFrame) -> Optional[bytes]:
        """Assemble fragmented messages. Returns complete payload when FIN is set."""
        if frame.is_control:
            return frame.payload

        if frame.opcode != WSOpcode.CONTINUATION:
            # Start of new message
            self._fragment_opcode = frame.opcode
            self._fragments = [frame.payload]
        else:
            # Continuation
            self._fragments.append(frame.payload)

        if frame.fin:
            complete = b"".join(self._fragments)
            self._fragments.clear()
            self._fragment_opcode = None
            return complete

        return None  # More fragments expected

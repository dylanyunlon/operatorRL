"""
Game Protocol Codec — ABC, registry, and implementations.

Provides:
- GameCodec: abstract base class (parse/encode interface)
- LiqiCodec: Majsoul/liqi protobuf WebSocket protocol
- LoLCodec: League of Legends Live Client Data API (JSON over HTTP)
- CodecRegistry: name-based codec lookup
- Low-level helpers: varint, protobuf block, XOR cipher

Ported from Akagi mitm/bridge/majsoul/liqi.py with clean abstraction.
Reference: Akagi's BridgeBase → our GameCodec.

Location: extensions/protocol-decoder/src/protocol_decoder/codec.py
"""

from __future__ import annotations

import json
import logging
import os
import struct
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ──────────────────────── Exceptions ────────────────────────


class CodecError(Exception):
    """Error during protocol encoding/decoding."""
    pass


# ──────────────────────── Enums ─────────────────────────────


class MsgType(Enum):
    """Majsoul/liqi message types."""
    NOTIFY = 1
    REQ = 2
    RES = 3


# ──────────────────────── Low-level helpers ─────────────────
# Ported from Akagi liqi.py with identical semantics.

_XOR_KEYS = [0x84, 0x5E, 0x4E, 0x42, 0x39, 0xA2, 0x1F, 0x60, 0x1C]


def xor_decode(data: bytes) -> bytes:
    """XOR-decode Majsoul action data."""
    if not data:
        return b""
    buf = bytearray(data)
    for i in range(len(buf)):
        u = (23 ^ len(buf)) + 5 * i + _XOR_KEYS[i % len(_XOR_KEYS)] & 255
        buf[i] ^= u
    return bytes(buf)


def xor_encode(data: bytes) -> bytes:
    """XOR-encode Majsoul action data (same operation as decode — XOR is self-inverse)."""
    return xor_decode(data)


def to_varint(x: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    if x == 0:
        return b"\x00"
    result = bytearray()
    while x > 0:
        byte = x & 0x7F
        x >>= 7
        if x > 0:
            byte |= 0x80
        result.append(byte)
    return bytes(result)


def parse_varint(buf: bytes | bytearray, p: int) -> tuple[int, int]:
    """Parse a varint from protobuf bytes at position p. Returns (value, new_pos)."""
    data = 0
    base = 0
    while p < len(buf):
        data += (buf[p] & 0x7F) << base
        base += 7
        p += 1
        if buf[p - 1] >> 7 == 0:
            break
    return (data, p)


def from_protobuf(buf: bytes | bytearray) -> list[dict[str, Any]]:
    """Parse raw protobuf bytes into a list of field blocks."""
    p = 0
    result: list[dict[str, Any]] = []
    while p < len(buf):
        block_begin = p
        block_type_raw = buf[p] & 7
        block_id = buf[p] >> 3
        p += 1
        if block_type_raw == 0:
            # varint
            data, p = parse_varint(buf, p)
            result.append({"id": block_id, "type": "varint", "data": data, "begin": block_begin})
        elif block_type_raw == 2:
            # length-delimited (string/bytes)
            s_len, p = parse_varint(buf, p)
            data_bytes = buf[p:p + s_len]
            p += s_len
            result.append({"id": block_id, "type": "string", "data": bytes(data_bytes), "begin": block_begin})
        else:
            raise CodecError(f"Unknown protobuf wire type {block_type_raw} at position {block_begin}")
    return result


def to_protobuf(data: list[dict[str, Any]]) -> bytes:
    """Serialize a list of field blocks back to protobuf bytes."""
    result = b""
    for d in data:
        if d["type"] == "varint":
            result += ((d["id"] << 3) + 0).to_bytes(length=1, byteorder="little")
            result += to_varint(d["data"])
        elif d["type"] == "string":
            result += ((d["id"] << 3) + 2).to_bytes(length=1, byteorder="little")
            raw = d["data"] if isinstance(d["data"], bytes) else d["data"].encode()
            result += to_varint(len(raw))
            result += raw
        else:
            raise CodecError(f"Unsupported protobuf type: {d['type']}")
    return result


# ──────────────────────── GameCodec ABC ─────────────────────


class GameCodec(ABC):
    """Abstract base class for game protocol codecs.

    Subclasses must implement:
        - parse(raw_bytes) → structured dict or None
        - encode(data_dict) → raw bytes or None
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique codec identifier (e.g., 'liqi', 'lol', 'dota2')."""
        ...

    @abstractmethod
    def parse(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Parse raw bytes into a structured dict. Returns None on failure."""
        ...

    @abstractmethod
    def encode(self, data: dict[str, Any]) -> Optional[bytes]:
        """Encode a structured dict back to raw bytes. Returns None if unsupported."""
        ...


# ──────────────────────── LiqiCodec ─────────────────────────


class LiqiCodec(GameCodec):
    """Majsoul/liqi protobuf WebSocket protocol codec.

    Ported from Akagi's LiqiProto with clean GameCodec interface.
    Handles Notify/Request/Response message types with XOR-encrypted actions.
    """

    def __init__(self) -> None:
        self._msg_id = 1
        self._res_type: dict[int, tuple[str, Optional[str]]] = {}
        self._proto_schema: Optional[dict[str, Any]] = None
        self._load_schema()

    def _load_schema(self) -> None:
        """Load liqi.json proto schema if available."""
        schema_paths = [
            os.path.join(os.path.dirname(__file__), "codecs", "liqi.json"),
            # Fallback: Akagi's location
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                         "Akagi", "mitm", "bridge", "majsoul", "liqi_proto", "liqi.json"),
        ]
        for path in schema_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r") as f:
                        self._proto_schema = json.load(f)
                    return
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("Failed to load liqi.json from %s: %s", path, e)
        # Provide minimal empty schema if not found
        self._proto_schema = {"nested": {}}
        logger.warning("liqi.json not found; LiqiCodec will operate in minimal mode")

    @property
    def name(self) -> str:
        return "liqi"

    @property
    def proto_schema(self) -> Optional[dict[str, Any]]:
        return self._proto_schema

    def init(self) -> None:
        """Reset state for a new session."""
        self._msg_id = 1
        self._res_type.clear()

    def parse(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Parse a raw WebSocket frame into a structured message dict."""
        if not raw or len(raw) < 2:
            return None

        try:
            msg_type_val = raw[0]
            try:
                msg_type = MsgType(msg_type_val)
            except ValueError:
                logger.warning("Unknown message type byte: 0x%02X", msg_type_val)
                return None

            if msg_type == MsgType.NOTIFY:
                msg_block = from_protobuf(raw[1:])
                if len(msg_block) < 1 or not isinstance(msg_block[0].get("data"), bytes):
                    return None
                method_name = msg_block[0]["data"].decode("utf-8", errors="replace")
                dict_obj: dict[str, Any] = {}
                if len(msg_block) > 1 and isinstance(msg_block[1].get("data"), bytes):
                    # In minimal mode, we can't deserialize protobuf without pb2 stubs
                    dict_obj = {"raw_payload_length": len(msg_block[1]["data"])}
                return {"id": -1, "type": msg_type, "method": method_name, "data": dict_obj}

            elif msg_type == MsgType.REQ:
                if len(raw) < 4:
                    return None
                msg_id = struct.unpack("<H", raw[1:3])[0]
                msg_block = from_protobuf(raw[3:])
                if len(msg_block) < 1:
                    return None
                method_name = msg_block[0]["data"].decode("utf-8", errors="replace")
                dict_obj = {}
                if len(msg_block) > 1 and isinstance(msg_block[1].get("data"), bytes):
                    dict_obj = {"raw_payload_length": len(msg_block[1]["data"])}
                # Track for response matching
                self._res_type[msg_id] = (method_name, None)
                self._msg_id = msg_id
                return {"id": msg_id, "type": msg_type, "method": method_name, "data": dict_obj}

            elif msg_type == MsgType.RES:
                if len(raw) < 4:
                    return None
                msg_id = struct.unpack("<H", raw[1:3])[0]
                if msg_id not in self._res_type:
                    logger.warning("Response msg_id=%d has no matching request", msg_id)
                    return None
                method_name, _ = self._res_type.pop(msg_id)
                msg_block = from_protobuf(raw[3:])
                dict_obj = {}
                if len(msg_block) > 1 and isinstance(msg_block[1].get("data"), bytes):
                    dict_obj = {"raw_payload_length": len(msg_block[1]["data"])}
                return {"id": msg_id, "type": msg_type, "method": method_name, "data": dict_obj}

        except Exception as e:
            logger.warning("LiqiCodec.parse error: %s", e)
            return None

        return None

    def encode(self, data: dict[str, Any]) -> Optional[bytes]:
        """Encode a structured dict back to liqi wire format (minimal support)."""
        # Full encoding requires protobuf stubs; return None for now
        return None


# ──────────────────────── LoLCodec ──────────────────────────


def _snake_case(s: str) -> str:
    """Convert camelCase to snake_case."""
    import re
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", s)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _convert_keys(obj: Any) -> Any:
    """Recursively convert dict keys from camelCase to snake_case."""
    if isinstance(obj, dict):
        return {_snake_case(k): _convert_keys(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_keys(item) for item in obj]
    return obj


class LoLCodec(GameCodec):
    """League of Legends Live Client Data API codec.

    Parses the JSON response from https://127.0.0.1:2999/liveclientdata/allgamedata
    into a normalized structure with snake_case keys.
    """

    @property
    def name(self) -> str:
        return "lol"

    def parse(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Parse Live Client Data JSON into normalized dict."""
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
        """Normalize Live Client Data JSON to standard format."""
        result: dict[str, Any] = {}

        # Active player
        ap = data.get("activePlayer")
        if ap:
            result["active_player"] = _convert_keys(ap)
        else:
            result["active_player"] = None if "activePlayer" not in data else {}

        # All players
        all_players = data.get("allPlayers", [])
        result["all_players"] = [_convert_keys(p) for p in all_players]

        # Events
        events_raw = data.get("events", {})
        if isinstance(events_raw, dict):
            result["events"] = [_convert_keys(e) for e in events_raw.get("Events", [])]
        else:
            result["events"] = []

        # Game data
        gd = data.get("gameData", {})
        result["game_data"] = _convert_keys(gd) if gd else {}

        return result

    def encode(self, data: dict[str, Any]) -> Optional[bytes]:
        """Encode back to JSON (simple serialization)."""
        try:
            return json.dumps(data).encode("utf-8")
        except Exception:
            return None


# ──────────────────────── CodecRegistry ─────────────────────


class CodecRegistry:
    """Registry for game protocol codecs."""

    def __init__(self) -> None:
        self._codecs: dict[str, GameCodec] = {}

    def register(self, codec: GameCodec) -> None:
        """Register a codec by its name."""
        self._codecs[codec.name] = codec

    def get(self, name: str) -> Optional[GameCodec]:
        """Look up a codec by name. Returns None if not found."""
        return self._codecs.get(name)

    def list_codecs(self) -> list[str]:
        """List all registered codec names."""
        return list(self._codecs.keys())

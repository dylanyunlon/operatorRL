#!/usr/bin/env python3
"""
M812 - Protocol Decoder
=========================
OperatorRL Historical Battle System - Game Protocol Parsing & Packet Analysis

查看 LCU API 和 Riot 通信协议的实现方式，理解其模式，特别是
协议格式和业务逻辑是如何分离的。遵循该模式实现协议解码器，
使网络捕获层的原始数据可以被结构化解析，并能自动适应协议版本变更。

Core responsibilities:
- Decode HTTP/HTTPS request-response protocol data
- Parse JSON, protobuf, and binary game protocol formats
- Map protocol fields to internal data models
- Handle protocol versioning and backward compatibility
- Provide streaming decode for real-time data processing
"""

import os
import re
import sys
import json
import struct
import base64
import zlib
import gzip
import logging
import hashlib
import datetime
from io import BytesIO
from pathlib import Path
from typing import (
    Dict, List, Any, Optional, Tuple, Union, Callable,
    Iterator, Sequence, Set
)
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from abc import ABC, abstractmethod
from collections import OrderedDict

logger = logging.getLogger("operatorRL.historical_battle.protocol_decoder")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────────────

MAX_PACKET_SIZE = 10 * 1024 * 1024  # 10MB
PROTOCOL_HEADER_SIZE = 8
PROTOCOL_MAGIC_BYTES = b"\x52\x49\x4F\x54"  # "RIOT"
JSON_CONTENT_TYPES = ("application/json", "text/json", "application/x-json")
PROTOBUF_CONTENT_TYPES = ("application/x-protobuf", "application/protobuf")
BINARY_CONTENT_TYPES = ("application/octet-stream",)
SUPPORTED_ENCODINGS = ("gzip", "deflate", "br", "identity")
MAX_DECODE_DEPTH = 10
FIELD_NAME_MAX_LENGTH = 256
TIMESTAMP_EPOCH_2000 = 946684800


class ProtocolType(Enum):
    """Supported protocol types."""
    JSON = "json"
    PROTOBUF = "protobuf"
    BINARY = "binary"
    WAMP = "wamp"
    WEBSOCKET = "websocket"
    HTTP_REST = "http_rest"
    UNKNOWN = "unknown"


class DecodeStatus(Enum):
    """Status of a decode operation."""
    SUCCESS = "success"
    PARTIAL = "partial"
    ERROR = "error"
    UNSUPPORTED = "unsupported"
    TRUNCATED = "truncated"


class PacketDirection(Enum):
    """Direction of protocol packet."""
    CLIENT_TO_SERVER = "c2s"
    SERVER_TO_CLIENT = "s2c"
    BIDIRECTIONAL = "bidi"


class WAMPMessageType(IntEnum):
    """WAMP protocol message types used by LCU."""
    WELCOME = 0
    PREFIX = 1
    CALL = 2
    CALLRESULT = 3
    CALLERROR = 4
    SUBSCRIBE = 5
    UNSUBSCRIBE = 6
    PUBLISH = 7
    EVENT = 8


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class DecodedField:
    """A single decoded protocol field."""
    name: str = ""
    value: Any = None
    field_type: str = "unknown"
    offset: int = 0
    size: int = 0
    path: str = ""  # dot-notation path: "info.participants.0.kills"
    raw_bytes: Optional[bytes] = None

    def __repr__(self):
        val_str = str(self.value)[:50] if self.value else "None"
        return f"DecodedField({self.name}={val_str}, type={self.field_type})"


@dataclass
class DecodedMessage:
    """A fully decoded protocol message."""
    message_id: str = ""
    protocol_type: ProtocolType = ProtocolType.UNKNOWN
    status: DecodeStatus = DecodeStatus.SUCCESS
    direction: PacketDirection = PacketDirection.BIDIRECTIONAL
    fields: List[DecodedField] = field(default_factory=list)
    nested_messages: List["DecodedMessage"] = field(default_factory=list)
    raw_data: Optional[bytes] = None
    decoded_data: Optional[Any] = None
    error_message: str = ""
    decode_time_ms: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now().isoformat()
    )

    def __post_init__(self):
        if not self.message_id:
            source = str(self.raw_data[:32]) if self.raw_data else str(id(self))
            self.message_id = hashlib.md5(source.encode()).hexdigest()[:12]

    @property
    def field_count(self) -> int:
        return len(self.fields)

    @property
    def is_success(self) -> bool:
        return self.status == DecodeStatus.SUCCESS

    def get_field(self, name: str) -> Optional[DecodedField]:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def get_field_value(self, path: str, default: Any = None) -> Any:
        """Get field value by dot-path notation."""
        parts = path.split(".")
        current = self.decoded_data
        try:
            for part in parts:
                if isinstance(current, dict):
                    current = current[part]
                elif isinstance(current, (list, tuple)):
                    current = current[int(part)]
                else:
                    return default
            return current
        except (KeyError, IndexError, ValueError, TypeError):
            return default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "protocol_type": self.protocol_type.value,
            "status": self.status.value,
            "field_count": self.field_count,
            "decoded_data": self.decoded_data,
            "error": self.error_message,
            "decode_time_ms": self.decode_time_ms,
        }


@dataclass
class ProtocolSchema:
    """Schema definition for a known protocol message type."""
    name: str = ""
    version: str = ""
    fields: Dict[str, str] = field(default_factory=dict)  # name -> type
    required_fields: List[str] = field(default_factory=list)
    endpoint_pattern: str = ""
    description: str = ""


@dataclass
class DecodeResult:
    """Result of a batch decode operation."""
    total_packets: int = 0
    decoded_count: int = 0
    error_count: int = 0
    unsupported_count: int = 0
    messages: List[DecodedMessage] = field(default_factory=list)
    total_decode_time_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_packets == 0:
            return 0.0
        return self.decoded_count / self.total_packets


# ─── Content Decoders ────────────────────────────────────────────────────────

class ContentDecoder:
    """Handles content encoding decompression."""

    @staticmethod
    def decompress(data: bytes, encoding: str) -> bytes:
        """Decompress content based on Content-Encoding header."""
        encoding = encoding.lower().strip()

        if encoding in ("identity", ""):
            return data
        elif encoding == "gzip":
            try:
                return gzip.decompress(data)
            except (gzip.BadGzipFile, OSError):
                return data
        elif encoding == "deflate":
            try:
                return zlib.decompress(data)
            except zlib.error:
                try:
                    return zlib.decompress(data, -zlib.MAX_WBITS)
                except zlib.error:
                    return data
        elif encoding == "br":
            try:
                import brotli
                return brotli.decompress(data)
            except (ImportError, Exception):
                logger.warning("Brotli not available, returning raw data")
                return data
        else:
            logger.warning(f"Unknown encoding: {encoding}")
            return data


class JSONDecoder:
    """Decode JSON protocol data with field extraction."""

    @staticmethod
    def decode(
        data: Union[str, bytes], max_depth: int = MAX_DECODE_DEPTH
    ) -> DecodedMessage:
        """Decode JSON data into a DecodedMessage."""
        import time
        start = time.monotonic()

        msg = DecodedMessage(protocol_type=ProtocolType.JSON)

        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")

            parsed = json.loads(data)
            msg.decoded_data = parsed
            msg.status = DecodeStatus.SUCCESS
            msg.raw_data = data.encode() if isinstance(data, str) else data

            # Extract top-level fields
            if isinstance(parsed, dict):
                msg.fields = JSONDecoder._extract_fields(parsed, "", max_depth)
            elif isinstance(parsed, list):
                msg.fields = [
                    DecodedField(
                        name="root",
                        value=parsed,
                        field_type="array",
                        size=len(parsed),
                        path="",
                    )
                ]

        except json.JSONDecodeError as e:
            msg.status = DecodeStatus.ERROR
            msg.error_message = f"JSON decode error: {str(e)}"
        except Exception as e:
            msg.status = DecodeStatus.ERROR
            msg.error_message = f"Unexpected error: {str(e)}"

        msg.decode_time_ms = (time.monotonic() - start) * 1000
        return msg

    @staticmethod
    def _extract_fields(
        data: Dict[str, Any], prefix: str, max_depth: int, depth: int = 0
    ) -> List[DecodedField]:
        """Recursively extract fields from JSON data."""
        fields = []
        if depth >= max_depth:
            return fields

        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key

            if isinstance(value, dict):
                fields.append(DecodedField(
                    name=key,
                    value=f"{{...}} ({len(value)} keys)",
                    field_type="object",
                    path=path,
                    size=len(value),
                ))
                fields.extend(
                    JSONDecoder._extract_fields(value, path, max_depth, depth + 1)
                )
            elif isinstance(value, list):
                fields.append(DecodedField(
                    name=key,
                    value=f"[...] ({len(value)} items)",
                    field_type="array",
                    path=path,
                    size=len(value),
                ))
            else:
                fields.append(DecodedField(
                    name=key,
                    value=value,
                    field_type=type(value).__name__,
                    path=path,
                ))

        return fields


class WAMPDecoder:
    """Decode WAMP protocol messages used by LCU WebSocket."""

    @staticmethod
    def decode(data: Union[str, bytes]) -> DecodedMessage:
        """Decode a WAMP message."""
        import time
        start = time.monotonic()

        msg = DecodedMessage(protocol_type=ProtocolType.WAMP)

        try:
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")

            parsed = json.loads(data)
            if not isinstance(parsed, list) or len(parsed) < 1:
                msg.status = DecodeStatus.ERROR
                msg.error_message = "Invalid WAMP format: not a list"
                return msg

            msg_type = parsed[0]
            msg.decoded_data = parsed

            try:
                wamp_type = WAMPMessageType(msg_type)
            except ValueError:
                wamp_type = None

            msg.fields.append(DecodedField(
                name="message_type",
                value=wamp_type.name if wamp_type else str(msg_type),
                field_type="int",
                path="0",
            ))

            if wamp_type == WAMPMessageType.EVENT and len(parsed) >= 3:
                event_data = parsed[2] if len(parsed) > 2 else {}
                msg.fields.append(DecodedField(
                    name="event_name",
                    value=parsed[1] if len(parsed) > 1 else "",
                    field_type="string",
                    path="1",
                ))
                if isinstance(event_data, dict):
                    msg.fields.append(DecodedField(
                        name="uri",
                        value=event_data.get("uri", ""),
                        field_type="string",
                        path="2.uri",
                    ))
                    msg.fields.append(DecodedField(
                        name="event_type",
                        value=event_data.get("eventType", ""),
                        field_type="string",
                        path="2.eventType",
                    ))
                    event_payload = event_data.get("data")
                    if event_payload:
                        msg.fields.append(DecodedField(
                            name="data",
                            value=str(event_payload)[:100],
                            field_type=type(event_payload).__name__,
                            path="2.data",
                        ))

            elif wamp_type == WAMPMessageType.SUBSCRIBE and len(parsed) >= 2:
                msg.fields.append(DecodedField(
                    name="subscription_topic",
                    value=parsed[1],
                    field_type="string",
                    path="1",
                ))

            msg.status = DecodeStatus.SUCCESS

        except (json.JSONDecodeError, IndexError) as e:
            msg.status = DecodeStatus.ERROR
            msg.error_message = str(e)

        msg.decode_time_ms = (time.monotonic() - start) * 1000
        return msg


class BinaryProtocolDecoder:
    """Decode binary game protocol packets."""

    RIOT_HEADER_FORMAT = ">4sHH"  # magic(4) + version(2) + length(2)

    @staticmethod
    def decode(data: bytes) -> DecodedMessage:
        """Decode a binary protocol packet."""
        import time
        start = time.monotonic()

        msg = DecodedMessage(protocol_type=ProtocolType.BINARY, raw_data=data)

        if len(data) < PROTOCOL_HEADER_SIZE:
            msg.status = DecodeStatus.TRUNCATED
            msg.error_message = f"Packet too small: {len(data)} < {PROTOCOL_HEADER_SIZE}"
            return msg

        try:
            header_size = struct.calcsize(BinaryProtocolDecoder.RIOT_HEADER_FORMAT)
            if len(data) >= header_size:
                magic, version, length = struct.unpack(
                    BinaryProtocolDecoder.RIOT_HEADER_FORMAT,
                    data[:header_size]
                )

                msg.fields.append(DecodedField(
                    name="magic",
                    value=magic.hex(),
                    field_type="bytes",
                    offset=0,
                    size=4,
                ))
                msg.fields.append(DecodedField(
                    name="version",
                    value=version,
                    field_type="uint16",
                    offset=4,
                    size=2,
                ))
                msg.fields.append(DecodedField(
                    name="payload_length",
                    value=length,
                    field_type="uint16",
                    offset=6,
                    size=2,
                ))

                payload = data[header_size:header_size + length]
                msg.fields.append(DecodedField(
                    name="payload",
                    value=f"<{len(payload)} bytes>",
                    field_type="bytes",
                    offset=header_size,
                    size=len(payload),
                    raw_bytes=payload,
                ))

            msg.decoded_data = {
                "total_size": len(data),
                "fields": {f.name: f.value for f in msg.fields},
            }
            msg.status = DecodeStatus.SUCCESS

        except struct.error as e:
            msg.status = DecodeStatus.ERROR
            msg.error_message = f"Struct unpack error: {e}"

        msg.decode_time_ms = (time.monotonic() - start) * 1000
        return msg


# ─── Protocol Decoder Engine ─────────────────────────────────────────────────

class ProtocolDecoder:
    """
    Central protocol decoding engine.
    Automatically detects protocol type and applies correct decoder.
    Implements HistoricalBattleInterface contract.
    """

    def __init__(self):
        self._json_decoder = JSONDecoder()
        self._wamp_decoder = WAMPDecoder()
        self._binary_decoder = BinaryProtocolDecoder()
        self._content_decoder = ContentDecoder()
        self._schemas: Dict[str, ProtocolSchema] = {}
        self._decode_count = 0
        self._error_count = 0
        self._initialized = False

    async def initialize(self, config: Dict[str, Any] = None) -> bool:
        self._load_default_schemas()
        self._initialized = True
        logger.info("ProtocolDecoder initialized")
        return True

    def _load_default_schemas(self):
        """Load known protocol schemas."""
        schemas = [
            ProtocolSchema(
                name="match_history_response",
                version="v1",
                endpoint_pattern=r"/lol-match-history/.*",
                fields={
                    "games": "object",
                    "games.games": "array",
                    "games.gameCount": "int",
                },
                required_fields=["games"],
            ),
            ProtocolSchema(
                name="summoner_response",
                version="v1",
                endpoint_pattern=r"/lol-summoner/.*",
                fields={
                    "accountId": "int",
                    "displayName": "string",
                    "puuid": "string",
                    "summonerLevel": "int",
                },
                required_fields=["puuid"],
            ),
            ProtocolSchema(
                name="gameflow_phase",
                version="v1",
                endpoint_pattern=r"/lol-gameflow/v1/gameflow-phase",
                fields={"phase": "string"},
            ),
            ProtocolSchema(
                name="champ_select_session",
                version="v1",
                endpoint_pattern=r"/lol-champ-select/v1/session",
                fields={
                    "myTeam": "array",
                    "theirTeam": "array",
                    "timer": "object",
                    "actions": "array",
                },
            ),
            ProtocolSchema(
                name="end_of_game_stats",
                version="v1",
                endpoint_pattern=r"/lol-end-of-game/.*",
                fields={
                    "teams": "array",
                    "localPlayer": "object",
                    "gameId": "int",
                    "gameMode": "string",
                },
            ),
        ]
        for schema in schemas:
            self._schemas[schema.name] = schema

    def detect_protocol(
        self, data: Union[str, bytes], content_type: str = ""
    ) -> ProtocolType:
        """Auto-detect the protocol type of the data."""
        ct = content_type.lower()

        if any(t in ct for t in JSON_CONTENT_TYPES):
            return ProtocolType.JSON

        if any(t in ct for t in PROTOBUF_CONTENT_TYPES):
            return ProtocolType.PROTOBUF

        if any(t in ct for t in BINARY_CONTENT_TYPES):
            return ProtocolType.BINARY

        # Try to detect from data content
        if isinstance(data, str):
            data_stripped = data.strip()
            if data_stripped.startswith(("{", "[")):
                # Check for WAMP (JSON array with int first element)
                try:
                    parsed = json.loads(data_stripped)
                    if isinstance(parsed, list) and parsed and isinstance(parsed[0], int):
                        return ProtocolType.WAMP
                except json.JSONDecodeError:
                    pass
                return ProtocolType.JSON

        if isinstance(data, bytes):
            if data[:4] == PROTOCOL_MAGIC_BYTES:
                return ProtocolType.BINARY
            try:
                text = data.decode("utf-8")
                if text.strip().startswith(("{", "[")):
                    return ProtocolType.JSON
            except UnicodeDecodeError:
                return ProtocolType.BINARY

        return ProtocolType.UNKNOWN

    def decode(
        self,
        data: Union[str, bytes],
        content_type: str = "",
        content_encoding: str = "",
        endpoint: str = "",
    ) -> DecodedMessage:
        """Decode data with automatic protocol detection."""
        self._decode_count += 1

        # Decompress if needed
        if content_encoding and isinstance(data, bytes):
            data = self._content_decoder.decompress(data, content_encoding)

        # Detect protocol
        protocol = self.detect_protocol(data, content_type)

        # Apply appropriate decoder
        if protocol == ProtocolType.JSON:
            msg = self._json_decoder.decode(data)
        elif protocol == ProtocolType.WAMP:
            msg = self._wamp_decoder.decode(data)
        elif protocol == ProtocolType.BINARY:
            if isinstance(data, str):
                data = data.encode()
            msg = self._binary_decoder.decode(data)
        else:
            msg = DecodedMessage(
                protocol_type=protocol,
                status=DecodeStatus.UNSUPPORTED,
                error_message=f"Unsupported protocol: {protocol.value}",
            )

        # Validate against schema if endpoint matches
        if endpoint and msg.is_success:
            self._validate_against_schema(msg, endpoint)

        if not msg.is_success:
            self._error_count += 1

        return msg

    def decode_batch(
        self, packets: List[Tuple[Union[str, bytes], str, str]]
    ) -> DecodeResult:
        """Decode a batch of packets: [(data, content_type, encoding), ...]"""
        result = DecodeResult(total_packets=len(packets))

        for data, ct, enc in packets:
            msg = self.decode(data, ct, enc)
            result.messages.append(msg)
            result.total_decode_time_ms += msg.decode_time_ms

            if msg.is_success:
                result.decoded_count += 1
            elif msg.status == DecodeStatus.UNSUPPORTED:
                result.unsupported_count += 1
            else:
                result.error_count += 1

        return result

    def _validate_against_schema(self, msg: DecodedMessage, endpoint: str):
        """Validate decoded message against known schema."""
        for schema_name, schema in self._schemas.items():
            if schema.endpoint_pattern and re.match(schema.endpoint_pattern, endpoint):
                if msg.decoded_data and isinstance(msg.decoded_data, dict):
                    missing = [
                        f for f in schema.required_fields
                        if f not in msg.decoded_data
                    ]
                    if missing:
                        logger.warning(
                            f"Schema {schema_name}: missing fields {missing} "
                            f"in response from {endpoint}"
                        )
                break

    def register_schema(self, schema: ProtocolSchema):
        """Register a protocol schema for validation."""
        self._schemas[schema.name] = schema

    async def health_check(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "total_decoded": self._decode_count,
            "total_errors": self._error_count,
            "error_rate": (
                self._error_count / self._decode_count
                if self._decode_count > 0 else 0
            ),
            "schemas_loaded": len(self._schemas),
        }

    async def shutdown(self):
        self._schemas.clear()
        logger.info("ProtocolDecoder shutdown")

    def get_module_info(self) -> Dict[str, str]:
        return {
            "task_id": "M812",
            "name": "Protocol Decoder",
            "version": "1.0.0",
        }


if __name__ == "__main__":
    print("M812 Protocol Decoder - Self Test")

    decoder = ProtocolDecoder()

    # Test JSON decode
    json_data = json.dumps({"matchId": "NA1_123", "participants": [{"kills": 5}]})
    msg = decoder.decode(json_data, "application/json")
    print(f"JSON decode: {msg.status.value}, fields={msg.field_count}")

    # Test WAMP decode
    wamp_data = json.dumps([8, "OnJsonApiEvent", {"uri": "/lol-gameflow/v1/gameflow-phase", "eventType": "Update", "data": "InProgress"}])
    msg = decoder.decode(wamp_data)
    print(f"WAMP decode: {msg.status.value}, type={msg.protocol_type.value}")

    # Test protocol detection
    assert decoder.detect_protocol('{"key": "value"}') == ProtocolType.JSON
    assert decoder.detect_protocol('[8, "event"]') == ProtocolType.WAMP
    assert decoder.detect_protocol(PROTOCOL_MAGIC_BYTES + b"\x00\x01\x00\x10") == ProtocolType.BINARY

    print("\nM812 self-test passed.")

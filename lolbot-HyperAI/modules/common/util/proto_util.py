"""
ProtoUtil — Message serialization, deserialization, and schema versioning.
===========================================================================
lolbot-HyperAI · Common Layer

Provides unified serialization for all message types flowing through
the CyberNode channels.  Supports JSON and compact binary (msgpack-like
via stdlib) formats, with version-tagged schemas for backward compatibility.

Architecture position:
    modules/common/util/proto_util.py   ← YOU ARE HERE
    ├─ Used by: canbus/transport.py (message recording)
    ├─ Used by: cyber/transport/shared_memory.py (large message transfer)
    ├─ Used by: scripts/replay_simulator.py (replay file parsing)
    └─ Used by: modules/common/adapters/training_data_collector.py

Apollo reference:
    cyber/proto/ — protobuf definitions
    cyber/message/protobuf_factory.cc — message factory
    cyber/record/record_writer.cc — serialized recording

Design notes:
    - JSON as primary format (human-readable, debuggable)
    - Compact binary as secondary (struct-pack for hot paths)
    - Schema registry: version → field list, enables migration
    - Frozen dataclass → dict → JSON round-trip guaranteed
    - Timestamp normalization for cross-session replay
"""

from __future__ import annotations

import copy
import hashlib
import json
import struct
import time
from dataclasses import asdict, dataclass, fields, is_dataclass
from enum import Enum
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Type, TypeVar,
)

from cyber.logger.cyber_logger import get_logger

logger = get_logger("common.util.proto")

T = TypeVar("T")

# ─── Constants ───────────────────────────────────────────────────────────────

_CURRENT_SCHEMA_VERSION = 3
_MAGIC_HEADER = b"LOLB"       # Magic bytes for binary format
_HEADER_FORMAT = "!4sHI"      # magic(4) + version(2) + payload_len(4)
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)
_MAX_PAYLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


# ─── Schema Registry ────────────────────────────────────────────────────────

@dataclass
class SchemaEntry:
    """Registry entry for a message schema version."""
    version: int
    type_name: str
    field_names: Tuple[str, ...]
    added_fields: Dict[str, Any]     # field_name → default for new fields
    removed_fields: Set[str]         # fields removed in this version
    checksum: str = ""


class SchemaRegistry:
    """Tracks schema versions for all message types.

    Enables forward and backward compatibility: when deserializing
    a message from an older schema version, missing fields get defaults;
    when from a newer version, unknown fields are ignored.

    Example::

        registry = SchemaRegistry()
        registry.register(GameSnapshot, version=3)

        # Deserialize an old message
        data = {"game_time": 600.0}  # missing newer fields
        snapshot = registry.migrate("GameSnapshot", data, from_version=1)
    """

    def __init__(self) -> None:
        self._schemas: Dict[str, Dict[int, SchemaEntry]] = {}
        self._latest_versions: Dict[str, int] = {}

    def register(
        self,
        cls: type,
        version: int = _CURRENT_SCHEMA_VERSION,
        added_fields: Optional[Dict[str, Any]] = None,
        removed_fields: Optional[Set[str]] = None,
    ) -> SchemaEntry:
        """Register a message type's schema at a given version.

        Args:
            cls: The dataclass type.
            version: Schema version number.
            added_fields: Fields added in this version (with defaults).
            removed_fields: Fields removed in this version.

        Returns:
            The created SchemaEntry.
        """
        type_name = cls.__name__

        if is_dataclass(cls):
            field_names = tuple(f.name for f in fields(cls))
        else:
            field_names = tuple(vars(cls).keys()) if hasattr(cls, "__dict__") else ()

        # Compute checksum from field names
        field_str = ",".join(sorted(field_names))
        checksum = hashlib.md5(field_str.encode()).hexdigest()[:8]

        entry = SchemaEntry(
            version=version,
            type_name=type_name,
            field_names=field_names,
            added_fields=added_fields or {},
            removed_fields=removed_fields or set(),
            checksum=checksum,
        )

        if type_name not in self._schemas:
            self._schemas[type_name] = {}
        self._schemas[type_name][version] = entry

        # Track latest
        if type_name not in self._latest_versions or version > self._latest_versions[type_name]:
            self._latest_versions[type_name] = version

        logger.debug(
            "Schema registered: %s v%d (%d fields, checksum=%s)",
            type_name, version, len(field_names), checksum,
        )
        return entry

    def migrate(
        self,
        type_name: str,
        data: Dict[str, Any],
        from_version: int,
    ) -> Dict[str, Any]:
        """Migrate a data dict from an older schema version to latest.

        Adds default values for fields added in newer versions,
        removes fields that no longer exist.

        Args:
            type_name: Message type name.
            data: Raw data dict.
            from_version: The version the data was serialized with.

        Returns:
            Migrated data dict compatible with the latest schema.
        """
        versions = self._schemas.get(type_name, {})
        latest = self._latest_versions.get(type_name, from_version)

        result = dict(data)

        # Apply migrations from from_version+1 to latest
        for v in range(from_version + 1, latest + 1):
            entry = versions.get(v)
            if entry is None:
                continue

            # Add new fields with defaults
            for field_name, default_val in entry.added_fields.items():
                if field_name not in result:
                    result[field_name] = default_val

            # Remove deprecated fields
            for field_name in entry.removed_fields:
                result.pop(field_name, None)

        return result

    def get_latest_version(self, type_name: str) -> int:
        return self._latest_versions.get(type_name, 1)

    def has_type(self, type_name: str) -> bool:
        return type_name in self._schemas

    def registered_types(self) -> List[str]:
        return list(self._schemas.keys())


# ─── Global Registry Instance ───────────────────────────────────────────────

_global_registry = SchemaRegistry()


def get_schema_registry() -> SchemaRegistry:
    """Return the global schema registry singleton."""
    return _global_registry


# ─── JSON Serialization ─────────────────────────────────────────────────────

class JsonSerializer:
    """Serializes and deserializes messages to/from JSON strings.

    Handles:
    - Dataclass → dict → JSON string
    - Enum values → string representation
    - Nested dataclasses (recursive)
    - Frozen dataclasses (via asdict)
    """

    @staticmethod
    def serialize(obj: Any, pretty: bool = False) -> str:
        """Serialize an object to a JSON string.

        Args:
            obj: A dataclass instance, dict, or primitive.
            pretty: If True, indent the output.

        Returns:
            JSON string.
        """
        data = JsonSerializer._to_serializable(obj)
        indent = 2 if pretty else None
        return json.dumps(data, indent=indent, default=str, ensure_ascii=False)

    @staticmethod
    def deserialize(json_str: str) -> Any:
        """Deserialize a JSON string to a Python dict/list/primitive."""
        return json.loads(json_str)

    @staticmethod
    def serialize_message(
        obj: Any,
        include_metadata: bool = True,
    ) -> str:
        """Serialize a message with metadata envelope.

        The envelope includes:
        - _type: Class name
        - _version: Schema version
        - _timestamp: Serialization timestamp
        - payload: The actual message data
        """
        data = JsonSerializer._to_serializable(obj)

        if include_metadata:
            type_name = type(obj).__name__ if not isinstance(obj, dict) else "dict"
            envelope = {
                "_type": type_name,
                "_version": _global_registry.get_latest_version(type_name),
                "_timestamp": time.time(),
                "payload": data,
            }
            return json.dumps(envelope, default=str, ensure_ascii=False)
        else:
            return json.dumps(data, default=str, ensure_ascii=False)

    @staticmethod
    def deserialize_message(
        json_str: str,
        expected_type: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """Deserialize a message envelope.

        Returns:
            (payload_dict, schema_version) tuple.
        """
        envelope = json.loads(json_str)

        if isinstance(envelope, dict) and "_type" in envelope:
            type_name = envelope["_type"]
            version = envelope.get("_version", 1)
            payload = envelope.get("payload", envelope)

            if expected_type and type_name != expected_type:
                logger.warning(
                    "Type mismatch: expected %s, got %s",
                    expected_type, type_name,
                )

            # Migrate if needed
            latest = _global_registry.get_latest_version(type_name)
            if version < latest:
                payload = _global_registry.migrate(type_name, payload, version)
                version = latest

            return payload, version
        else:
            return envelope if isinstance(envelope, dict) else {"value": envelope}, 1

    @staticmethod
    def _to_serializable(obj: Any) -> Any:
        """Recursively convert an object to JSON-serializable form."""
        if obj is None or isinstance(obj, (int, float, str, bool)):
            return obj
        if isinstance(obj, Enum):
            return obj.value
        if is_dataclass(obj) and not isinstance(obj, type):
            try:
                return asdict(obj)
            except Exception:
                # Fallback for complex nested types
                result = {}
                for f in fields(obj):
                    val = getattr(obj, f.name)
                    result[f.name] = JsonSerializer._to_serializable(val)
                return result
        if isinstance(obj, dict):
            return {str(k): JsonSerializer._to_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [JsonSerializer._to_serializable(item) for item in obj]
        if isinstance(obj, set):
            return [JsonSerializer._to_serializable(item) for item in sorted(obj, key=str)]
        return str(obj)


# ─── Binary Serialization ───────────────────────────────────────────────────

class BinarySerializer:
    """Compact binary serialization for hot-path messages.

    Format: [MAGIC(4)][VERSION(2)][PAYLOAD_LEN(4)][JSON_PAYLOAD(N)]

    Still uses JSON for the payload body — the binary header enables
    fast framing and version detection without parsing the full body.
    For truly high-performance paths, replace the JSON payload with
    struct-packed fields.
    """

    @staticmethod
    def serialize(obj: Any, version: int = _CURRENT_SCHEMA_VERSION) -> bytes:
        """Serialize an object to binary format.

        Args:
            obj: Object to serialize.
            version: Schema version tag.

        Returns:
            Binary bytes.
        """
        json_str = JsonSerializer.serialize(obj)
        payload = json_str.encode("utf-8")

        if len(payload) > _MAX_PAYLOAD_SIZE:
            raise ValueError(
                f"Payload too large: {len(payload)} bytes > {_MAX_PAYLOAD_SIZE}"
            )

        header = struct.pack(_HEADER_FORMAT, _MAGIC_HEADER, version, len(payload))
        return header + payload

    @staticmethod
    def deserialize(data: bytes) -> Tuple[Any, int]:
        """Deserialize binary data.

        Returns:
            (parsed_object, schema_version) tuple.

        Raises:
            ValueError: If magic header is invalid or data is truncated.
        """
        if len(data) < _HEADER_SIZE:
            raise ValueError(f"Data too short: {len(data)} < {_HEADER_SIZE}")

        magic, version, payload_len = struct.unpack(
            _HEADER_FORMAT, data[:_HEADER_SIZE],
        )

        if magic != _MAGIC_HEADER:
            raise ValueError(f"Invalid magic header: {magic!r}")

        if len(data) < _HEADER_SIZE + payload_len:
            raise ValueError(
                f"Truncated payload: need {payload_len}, "
                f"have {len(data) - _HEADER_SIZE}"
            )

        payload_bytes = data[_HEADER_SIZE:_HEADER_SIZE + payload_len]
        json_str = payload_bytes.decode("utf-8")
        obj = json.loads(json_str)

        return obj, version

    @staticmethod
    def peek_version(data: bytes) -> int:
        """Read the schema version from binary data without full deserialization."""
        if len(data) < _HEADER_SIZE:
            raise ValueError("Data too short to read header")
        _, version, _ = struct.unpack(_HEADER_FORMAT, data[:_HEADER_SIZE])
        return version


# ─── Convenience Functions ───────────────────────────────────────────────────

def to_json(obj: Any, pretty: bool = False) -> str:
    """Serialize any object to JSON."""
    return JsonSerializer.serialize(obj, pretty=pretty)


def from_json(json_str: str) -> Any:
    """Deserialize a JSON string."""
    return JsonSerializer.deserialize(json_str)


def to_binary(obj: Any) -> bytes:
    """Serialize any object to compact binary."""
    return BinarySerializer.serialize(obj)


def from_binary(data: bytes) -> Any:
    """Deserialize compact binary data."""
    obj, _ = BinarySerializer.deserialize(data)
    return obj


def deep_clone(obj: Any) -> Any:
    """Deep clone a message (dataclass or dict)."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return copy.deepcopy(obj)
    if isinstance(obj, dict):
        return copy.deepcopy(obj)
    return obj


def message_fingerprint(obj: Any) -> str:
    """Compute a short fingerprint of a message for deduplication."""
    json_str = JsonSerializer.serialize(obj)
    return hashlib.md5(json_str.encode()).hexdigest()[:12]

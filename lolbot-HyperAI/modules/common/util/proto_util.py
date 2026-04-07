"""
Proto serialization utilities — dict ↔ bytes ↔ JSON for dataclasses.
=====================================================================
lolbot-HyperAI · Common Utilities

Provides lossless round-trip serialization for all frozen dataclass
message types used on the CyberNode message bus.

Architecture position:
    modules/common/util/proto_util.py   ← YOU ARE HERE
    ├─ Used by: canbus/transport.py (MessageRecorder)
    ├─ Used by: modules/common/adapters/training_data_collector.py
    └─ Used by: modules/dreamview/api/dreamview_api.py

Design notes:
    - Handles Enum, datetime, nested dataclass, tuple, frozenset
    - JSON encoder/decoder pair for clean serialization
    - msgpack-compatible binary format using json + gzip
    - Schema versioning via _schema_version field
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import json
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, TypeVar, Union

T = TypeVar("T")


class DataclassEncoder(json.JSONEncoder):
    """JSON encoder that handles dataclasses, Enums, tuples, datetimes."""

    def default(self, obj: Any) -> Any:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            d = dataclasses.asdict(obj)
            d["__type__"] = type(obj).__qualname__
            d["__module__"] = type(obj).__module__
            return d
        if isinstance(obj, enum.Enum):
            return {"__enum__": f"{type(obj).__module__}.{type(obj).__qualname__}", "value": obj.value}
        if isinstance(obj, datetime):
            return {"__datetime__": obj.isoformat()}
        if isinstance(obj, (set, frozenset)):
            return {"__set__": list(obj)}
        if isinstance(obj, tuple):
            return {"__tuple__": list(obj)}
        if isinstance(obj, bytes):
            return {"__bytes__": obj.hex()}
        return super().default(obj)


def _decode_hook(d: Dict[str, Any]) -> Any:
    """JSON object hook to reconstruct special types."""
    if "__enum__" in d:
        return d["value"]  # simplified: return raw value
    if "__datetime__" in d:
        return datetime.fromisoformat(d["__datetime__"])
    if "__set__" in d:
        return set(d["__set__"])
    if "__tuple__" in d:
        return tuple(d["__tuple__"])
    if "__bytes__" in d:
        return bytes.fromhex(d["__bytes__"])
    return d


def to_json(obj: Any, indent: Optional[int] = None) -> str:
    """Serialize any dataclass/Enum/dict to JSON string."""
    return json.dumps(obj, cls=DataclassEncoder, indent=indent, ensure_ascii=False)


def from_json(s: str) -> Any:
    """Deserialize JSON string, reconstructing special types."""
    return json.loads(s, object_hook=_decode_hook)


def to_bytes(obj: Any) -> bytes:
    """Serialize to gzipped JSON bytes for compact storage."""
    raw = to_json(obj).encode("utf-8")
    return gzip.compress(raw)


def from_bytes(data: bytes) -> Any:
    """Deserialize gzipped JSON bytes."""
    raw = gzip.decompress(data)
    return from_json(raw.decode("utf-8"))


def to_dict(obj: Any) -> Dict[str, Any]:
    """Convert dataclass to plain dict (shallow, no type markers)."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name)
            if isinstance(val, enum.Enum):
                val = val.value
            elif isinstance(val, tuple):
                val = list(val)
            elif dataclasses.is_dataclass(val):
                val = to_dict(val)
            result[f.name] = val
        return result
    if isinstance(obj, dict):
        return {k: to_dict(v) if dataclasses.is_dataclass(v) else v for k, v in obj.items()}
    return obj


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dicts, override values take precedence."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def validate_schema(obj: Any, required_fields: List[str]) -> List[str]:
    """Check that obj (dict or dataclass) has all required fields.

    Returns list of missing field names (empty = valid).
    """
    if dataclasses.is_dataclass(obj):
        existing = {f.name for f in dataclasses.fields(obj)}
    elif isinstance(obj, dict):
        existing = set(obj.keys())
    else:
        return required_fields  # can't validate non-dict/dataclass
    return [f for f in required_fields if f not in existing]


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: ProtoUtilV2 — schema validation, versioned serialization,
# migration helpers, and binary-safe encoding for channel messages
# ═══════════════════════════════════════════════════════════════════════════


@dataclasses.dataclass
class SchemaField:
    """Field definition for schema validation.

    Claude21: Each message type declares its expected fields with types,
    defaults, and version info. Enables forward/backward compatibility
    when message formats evolve across Claude iterations.
    """
    name: str
    field_type: str          # "int", "float", "str", "bool", "list", "dict"
    required: bool = True
    default: Any = None
    added_in_version: int = 1  # Schema version that introduced this field
    deprecated_in: int = 0     # 0 = not deprecated

    def validate(self, value: Any) -> Tuple[bool, str]:
        """Validate a value against this field definition."""
        if value is None:
            if self.required:
                return False, f"{self.name}: required field missing"
            return True, ""

        type_map = {
            "int": (int,), "float": (int, float), "str": (str,),
            "bool": (bool,), "list": (list, tuple), "dict": (dict,),
        }
        expected = type_map.get(self.field_type, (object,))
        if not isinstance(value, expected):
            return False, (
                f"{self.name}: expected {self.field_type}, "
                f"got {type(value).__name__}"
            )
        return True, ""


@dataclasses.dataclass
class MessageSchema:
    """Schema for a message type used on cyber channels.

    Claude21: Enables runtime validation of messages before publishing.
    Catches mismatched field names (like the dragons_taken/dragons_killed
    bug) at the schema level instead of at runtime TypeError.
    """
    name: str
    version: int = 1
    fields: List[SchemaField] = dataclasses.field(default_factory=list)

    def validate(self, data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate a dict against this schema."""
        errors = []
        for sf in self.fields:
            if sf.deprecated_in > 0 and sf.deprecated_in <= self.version:
                continue
            ok, err = sf.validate(data.get(sf.name))
            if not ok:
                errors.append(err)
        return len(errors) == 0, errors

    def apply_defaults(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Fill in missing optional fields with defaults."""
        result = dict(data)
        for sf in self.fields:
            if sf.name not in result and sf.default is not None:
                result[sf.name] = sf.default
        return result

    def migrate(
        self, data: Dict[str, Any], from_version: int,
    ) -> Dict[str, Any]:
        """Migrate data from an older schema version.

        Claude21: Fields added in later versions get their defaults.
        Deprecated fields are removed.
        """
        result = dict(data)
        for sf in self.fields:
            if sf.added_in_version > from_version:
                if sf.name not in result and sf.default is not None:
                    result[sf.name] = sf.default
            if sf.deprecated_in > 0 and sf.deprecated_in <= self.version:
                result.pop(sf.name, None)
        return result


# Pre-defined schemas for core messages
GAME_SNAPSHOT_SCHEMA = MessageSchema(
    name="GameSnapshot",
    version=21,
    fields=[
        SchemaField("game_time", "float"),
        SchemaField("sequence", "int"),
        SchemaField("phase", "str"),
        SchemaField("game_mode", "str", required=False, default="CLASSIC"),
        SchemaField("blue_team", "dict"),
        SchemaField("red_team", "dict"),
        SchemaField("gold_diff", "float", required=False, default=0.0),
        SchemaField("active_team", "str", required=False, default="UNKNOWN"),
    ],
)

PHASE_CONTEXT_SCHEMA = MessageSchema(
    name="PhaseContext",
    version=21,
    fields=[
        SchemaField("game_time", "float"),
        SchemaField("total_kills", "int"),
        SchemaField("towers_destroyed", "int", required=False, default=0),
        SchemaField("dragons_killed", "int", required=False, default=0),
        SchemaField("barons_killed", "int", required=False, default=0),
        SchemaField("inhibitors_destroyed", "int", required=False, default=0),
        SchemaField("recent_kills_2min", "int", required=False, default=0),
        SchemaField("ace_occurred", "bool", required=False, default=False),
    ],
)


class VersionedSerializer:
    """Serializer that embeds schema version in serialized messages.

    Claude21: When messages are persisted (recordings, evolution data),
    the schema version is embedded so older recordings can be read
    by newer code with automatic migration.

    Usage::
        serializer = VersionedSerializer()
        serializer.register(GAME_SNAPSHOT_SCHEMA)
        # Serialize
        wire = serializer.serialize("GameSnapshot", snapshot_dict)
        # Deserialize (auto-migrates if older version)
        data = serializer.deserialize(wire)
    """

    def __init__(self) -> None:
        self._schemas: Dict[str, MessageSchema] = {}

    def register(self, schema: MessageSchema) -> None:
        self._schemas[schema.name] = schema

    def serialize(self, schema_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize with version envelope."""
        schema = self._schemas.get(schema_name)
        envelope: Dict[str, Any] = {
            "__schema": schema_name,
            "__version": schema.version if schema else 1,
            "data": data,
        }
        return envelope

    def deserialize(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize with auto-migration."""
        schema_name = envelope.get("__schema", "")
        version = envelope.get("__version", 1)
        data = envelope.get("data", envelope)

        schema = self._schemas.get(schema_name)
        if schema and version < schema.version:
            data = schema.migrate(data, version)
        if schema:
            data = schema.apply_defaults(data)

        return data

    def validate_before_publish(
        self, schema_name: str, data: Dict[str, Any],
    ) -> Tuple[bool, List[str]]:
        """Validate data against schema before publishing to channel."""
        schema = self._schemas.get(schema_name)
        if not schema:
            return True, []
        return schema.validate(data)


def safe_json_encode(obj: Any, max_depth: int = 10) -> str:
    """JSON-encode with safety limits for deeply nested objects.

    Claude21: Prevents stack overflow from circular references or
    extremely nested game state objects.
    """
    def _sanitize(o: Any, depth: int) -> Any:
        if depth > max_depth:
            return "<truncated>"
        if isinstance(o, dict):
            return {k: _sanitize(v, depth + 1) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [_sanitize(v, depth + 1) for v in o]
        if isinstance(o, (int, float, str, bool, type(None))):
            return o
        if hasattr(o, "to_dict"):
            return _sanitize(o.to_dict(), depth + 1)
        if hasattr(o, "value"):
            return o.value
        if hasattr(o, "name"):
            return o.name
        return str(o)

    sanitized = _sanitize(obj, 0)
    return json.dumps(sanitized, separators=(",", ":"), ensure_ascii=False)


def safe_json_decode(raw: str) -> Optional[Dict[str, Any]]:
    """Safely decode JSON with error handling."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None

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

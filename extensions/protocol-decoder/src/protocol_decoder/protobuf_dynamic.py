"""
Dynamic Protobuf Loader — load and parse protobuf schemas at runtime.

Enables runtime loading of .proto/.json schema definitions without
compiled _pb2.py stubs. Used by LiqiCodec to decode Majsoul messages
when compiled protobuf modules are not available.

Location: extensions/protocol-decoder/src/protocol_decoder/protobuf_dynamic.py
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PROTOBUF_DYN_EVOLUTION_KEY: str = "protocol_decoder.protobuf_dynamic.v1"


@dataclass
class ProtoField:
    """A single field in a protobuf message definition."""
    name: str
    field_id: int
    field_type: str  # "string", "int32", "bool", "message", etc.
    repeated: bool = False
    optional: bool = False
    message_type: str = ""  # For nested message references


@dataclass
class ProtoMessage:
    """A protobuf message definition."""
    name: str
    fields: dict[int, ProtoField] = field(default_factory=dict)
    nested: dict[str, "ProtoMessage"] = field(default_factory=dict)


@dataclass
class ProtoService:
    """A protobuf service definition with RPC methods."""
    name: str
    methods: dict[str, dict[str, str]] = field(default_factory=dict)
    # methods: { rpc_name: {"requestType": "...", "responseType": "..."} }


class DynamicProtoSchema:
    """Runtime protobuf schema from liqi.json-style definitions.

    Loads the nested JSON schema exported by protobuf tooling and provides
    lookup for message types and service methods.
    """

    def __init__(self, schema: Optional[dict[str, Any]] = None) -> None:
        self._raw = schema or {}
        self._messages: dict[str, ProtoMessage] = {}
        self._services: dict[str, ProtoService] = {}
        if self._raw:
            self._parse_nested(self._raw.get("nested", {}), prefix="")

    @classmethod
    def from_json_file(cls, path: str) -> "DynamicProtoSchema":
        """Load schema from a liqi.json-style file."""
        if not os.path.exists(path):
            logger.warning("Proto schema file not found: %s", path)
            return cls()
        try:
            with open(path, "r") as f:
                data = json.load(f)
            return cls(schema=data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load proto schema from %s: %s", path, e)
            return cls()

    def _parse_nested(self, nested: dict[str, Any], prefix: str) -> None:
        """Recursively parse nested protobuf definitions."""
        for name, definition in nested.items():
            full_name = f"{prefix}.{name}" if prefix else name

            if "methods" in definition:
                # It's a service
                svc = ProtoService(name=full_name, methods=definition["methods"])
                self._services[full_name] = svc
            elif "fields" in definition:
                # It's a message
                msg = ProtoMessage(name=full_name)
                for field_name, field_def in definition.get("fields", {}).items():
                    pf = ProtoField(
                        name=field_name,
                        field_id=field_def.get("id", 0),
                        field_type=field_def.get("type", "string"),
                        repeated=field_def.get("rule", "") == "repeated",
                        optional=field_def.get("rule", "") == "optional",
                        message_type=field_def.get("type", ""),
                    )
                    msg.fields[pf.field_id] = pf
                self._messages[full_name] = msg

            # Recurse into nested definitions
            if "nested" in definition:
                self._parse_nested(definition["nested"], full_name)

    def get_message(self, name: str) -> Optional[ProtoMessage]:
        """Look up a message type by name."""
        return self._messages.get(name)

    def get_service(self, name: str) -> Optional[ProtoService]:
        """Look up a service by name."""
        return self._services.get(name)

    def resolve_rpc(self, method_path: str) -> Optional[dict[str, str]]:
        """Resolve an RPC method path like '.lq.Lobby.fetchAccountInfo'.

        Returns {"requestType": "...", "responseType": "..."} or None.
        """
        parts = method_path.lstrip(".").split(".")
        if len(parts) < 3:
            return None
        # parts: [namespace, service, rpc]
        namespace = parts[0]
        service_name = parts[1]
        rpc_name = parts[2]

        # Try various name resolutions
        for svc_key in [f"{namespace}.{service_name}", service_name]:
            svc = self._services.get(svc_key)
            if svc and rpc_name in svc.methods:
                return svc.methods[rpc_name]
        return None

    @property
    def message_count(self) -> int:
        return len(self._messages)

    @property
    def service_count(self) -> int:
        return len(self._services)

    def list_messages(self) -> list[str]:
        return sorted(self._messages.keys())

    def list_services(self) -> list[str]:
        return sorted(self._services.keys())

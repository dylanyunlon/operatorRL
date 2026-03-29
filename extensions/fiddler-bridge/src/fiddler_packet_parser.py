"""
Fiddler Packet Parser — HTTP/WebSocket frame parsing + content extraction.

Parses raw captured packets into structured data: HTTP responses,
WebSocket text/binary frames, with JSON body extraction.

Location: extensions/fiddler-bridge/src/fiddler_packet_parser.py

Reference (拿来主义):
  - Akagi/mitm/bridge/majsoul/liqi.py: struct.unpack + protobuf parsing
  - extensions/protocol-decoder/src/protocol_decoder/websocket.py: WS frame handling
  - extensions/fiddler-bridge/src/fiddler_bridge/client.py: HTTP response categorization
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_packet_parser.v1"


class FiddlerPacketParser:
    """HTTP/WebSocket packet parser.

    Handles:
    - HTTP responses: status code + headers + JSON body parsing
    - WebSocket frames: text (JSON) + binary
    - Unknown types: passthrough with raw data

    Attributes:
        parse_count: Number of packets parsed.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._parse_count: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def parse_count(self) -> int:
        return self._parse_count

    def parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse a raw packet into structured data.

        Args:
            raw: Dict with 'type' key and type-specific fields.

        Returns:
            Parsed packet dict.
        """
        self._parse_count += 1
        ptype = raw.get("type", "unknown")

        if ptype == "http_response":
            result = self._parse_http_response(raw)
        elif ptype == "websocket":
            result = self._parse_websocket(raw)
        else:
            result = {"type": ptype, "raw": raw}

        self._fire_evolution({"action": "parse", "type": ptype})
        return result

    def _parse_http_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse HTTP response packet."""
        status_code = raw.get("status_code", 0)
        headers = raw.get("headers", {})
        body = raw.get("body", "")

        result: dict[str, Any] = {
            "type": "http_response",
            "status_code": status_code,
            "headers": headers,
        }

        content_type = headers.get("content-type", "")
        if "json" in content_type and body:
            try:
                result["parsed_body"] = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                result["parsed_body"] = None
                result["error"] = "invalid_json"
        else:
            result["parsed_body"] = body if body else None

        return result

    def _parse_websocket(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse WebSocket frame."""
        opcode = raw.get("opcode", "text")
        payload = raw.get("payload", "")

        result: dict[str, Any] = {
            "type": "websocket",
            "frame_type": opcode,
        }

        if opcode == "text":
            try:
                result["parsed_payload"] = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                result["parsed_payload"] = payload
        else:
            result["raw_payload"] = payload

        return result

    # --- Evolution pattern ---
    def _fire_evolution(self, detail: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "key": _EVOLUTION_KEY,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                logger.warning("Evolution callback error (fiddler_packet_parser)")

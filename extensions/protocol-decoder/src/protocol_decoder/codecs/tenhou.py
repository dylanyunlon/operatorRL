"""
Tenhou Codec — XML-based WebSocket protocol parser for Tenhou (天凤).

Parses the XML messages exchanged over WebSocket with Tenhou's mahjong server.
Reference: Akagi/mitm/bridge/tenhou/ architecture.

Location: extensions/protocol-decoder/src/protocol_decoder/codecs/tenhou.py
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from xml.etree import ElementTree as ET

from protocol_decoder.codec import GameCodec

logger = logging.getLogger(__name__)

_TENHOU_EVOLUTION_KEY: str = "protocol_decoder.codecs.tenhou.v1"

# Tenhou message types we recognize
_DRAW_PATTERN = re.compile(r"^[TUVW]\d+$")  # T=self draw, U/V/W=other draws
_DISCARD_PATTERN = re.compile(r"^[DEFG]\d+$")  # D=self discard, E/F/G=other discards


class TenhouCodec(GameCodec):
    """Tenhou XML WebSocket protocol codec.

    Tenhou uses XML-like messages over WebSocket. Each message is a single
    XML tag with attributes containing game state data.
    """

    def __init__(self) -> None:
        self._version = "0.1.0"

    @property
    def name(self) -> str:
        return "tenhou"

    @property
    def version(self) -> str:
        return self._version

    def parse(self, raw: bytes) -> Optional[dict[str, Any]]:
        """Parse a Tenhou XML message."""
        if not raw:
            return None

        try:
            text = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return None

        if not text.startswith("<"):
            return None

        try:
            # Tenhou messages are self-closing tags: <TAG attr="val"/>
            # Wrap in root for parsing if needed
            if not text.endswith("/>") and not text.endswith(">"):
                return None
            root = ET.fromstring(text)
        except ET.ParseError:
            # Try wrapping
            try:
                root = ET.fromstring(f"<root>{text}</root>")
                if len(root) > 0:
                    root = root[0]
                else:
                    return None
            except ET.ParseError:
                return None

        tag = root.tag
        attrs = dict(root.attrib)

        result: dict[str, Any] = {
            "type": self._classify_tag(tag),
            "tag": tag,
            "attributes": attrs,
        }

        # Parse specific message types
        if tag == "INIT":
            result["action"] = "init"
            result["seed"] = attrs.get("seed", "")
            result["hand"] = self._parse_hai(attrs.get("hai", ""))
        elif tag == "AGARI":
            result["action"] = "agari"  # win
            result["who"] = int(attrs.get("who", -1))
        elif tag == "RYUUKYOKU":
            result["action"] = "ryuukyoku"  # draw game
        elif tag == "N":
            result["action"] = "naki"  # call (chi/pon/kan)
            result["who"] = int(attrs.get("who", -1))
        elif _DRAW_PATTERN.match(tag):
            result["action"] = "draw"
            result["tile"] = int(tag[1:])
            result["player"] = "TUVW".index(tag[0])
        elif _DISCARD_PATTERN.match(tag):
            result["action"] = "discard"
            result["tile"] = int(tag[1:])
            result["player"] = "DEFG".index(tag[0])

        return result

    def _classify_tag(self, tag: str) -> str:
        """Classify message tag into category."""
        if tag in ("HELO", "LN", "PXR"):
            return "lobby"
        elif tag in ("GO", "INIT", "AGARI", "RYUUKYOKU", "PROF", "OWARI"):
            return "game_control"
        elif tag == "N":
            return "call"
        elif _DRAW_PATTERN.match(tag):
            return "draw"
        elif _DISCARD_PATTERN.match(tag):
            return "discard"
        return "unknown"

    def _parse_hai(self, hai_str: str) -> list[int]:
        """Parse comma-separated tile IDs."""
        if not hai_str:
            return []
        try:
            return [int(x) for x in hai_str.split(",")]
        except ValueError:
            return []

    def encode(self, data: dict[str, Any]) -> Optional[bytes]:
        """Encode back to Tenhou XML format."""
        tag = data.get("tag", "")
        attrs = data.get("attributes", {})
        if not tag:
            return None
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        xml = f"<{tag} {attr_str}/>" if attr_str else f"<{tag}/>"
        return xml.encode("utf-8")

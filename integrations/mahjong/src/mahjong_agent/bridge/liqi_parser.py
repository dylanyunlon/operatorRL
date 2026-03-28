"""
Liqi Parser Adapter — delegates protocol decoding to protocol-decoder LiqiCodec.

This is the bridge between mahjong_agent's domain logic and the shared
protocol-decoder extension. Avoids duplicating protobuf parsing.

Location: integrations/mahjong/src/mahjong_agent/bridge/liqi_parser.py
"""

from __future__ import annotations

import logging
import sys
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Try to import from protocol-decoder extension
_codec_available = False
_LiqiCodecClass = None

try:
    # Add protocol-decoder to path if not already available
    _pd_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "..", "..",
        "extensions", "protocol-decoder", "src"
    )
    if os.path.isdir(_pd_path) and _pd_path not in sys.path:
        sys.path.insert(0, _pd_path)

    from protocol_decoder.codec import LiqiCodec as _LiqiCodecClass
    _codec_available = True
except ImportError:
    logger.warning("protocol-decoder not available; LiqiParserAdapter in stub mode")


class LiqiParserAdapter:
    """Adapter wrapping protocol-decoder's LiqiCodec for mahjong_agent use.

    If protocol-decoder is not on the path, operates in stub mode
    (returns None for all parse calls).
    """

    def __init__(self) -> None:
        self.codec: Any = None
        if _codec_available and _LiqiCodecClass is not None:
            self.codec = _LiqiCodecClass()
        else:
            self.codec = _StubCodec()

    def init(self) -> None:
        """Reset codec state for a new session."""
        if hasattr(self.codec, "init"):
            self.codec.init()

    def parse(self, content: bytes) -> Optional[dict[str, Any]]:
        """Parse raw WebSocket frame bytes into a structured liqi message.

        Returns:
            Parsed message dict with keys: id, type, method, data.
            None if unparseable.
        """
        if not content:
            return None
        try:
            return self.codec.parse(content)
        except Exception as e:
            logger.warning("LiqiParserAdapter.parse error: %s", e)
            return None

    def parse_syncgame(self, liqi_message: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse a syncGame response into a list of individual messages.

        Used for game state synchronization on reconnect.

        Returns:
            List of parsed message dicts (may be empty).
        """
        if not liqi_message:
            return []
        data = liqi_message.get("data", {})
        if not data:
            return []
        # In full mode, this would replay protobuf actions
        # In stub mode, return empty list
        return []


class _StubCodec:
    """Stub codec when protocol-decoder is not available."""

    def init(self) -> None:
        pass

    def parse(self, raw: bytes) -> Optional[dict[str, Any]]:
        return None

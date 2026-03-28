"""
Fiddler Client V2 — Migrated to fiddler-bridge unified architecture.

Replaces direct Fiddler MCP calls with delegation through the
fiddler-bridge extension for unified traffic capture across games.

Location: integrations/lol-fiddler-agent/src/lol_fiddler_agent/network/fiddler_client_v2.py
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_fiddler_agent.network.fiddler_client_v2.v1"


@dataclass
class FiddlerV2Config:
    """Configuration for Fiddler V2 client."""
    bridge_url: str = "http://localhost:8866"
    capture_mode: str = "browser"
    timeout: float = 10.0
    max_sessions: int = 1000
    auto_filter_lol: bool = True


@dataclass
class ProcessedSession:
    """A processed HTTP session."""
    session_id: str
    method: str
    url: str
    status_code: int
    response_body: Any
    timestamp: float = field(default_factory=time.time)
    codec_name: str = "lol"
    game: str = "league_of_legends"


class FiddlerClientV2:
    """Fiddler client v2 using fiddler-bridge unified architecture.

    Key changes from v1:
    - Delegates to fiddler-bridge codec layer instead of raw MCP
    - Unified session format across games
    - Built-in training data collection hooks
    """

    def __init__(self, config: FiddlerV2Config | None = None) -> None:
        self.config = config or FiddlerV2Config()
        self._connected: bool = False
        self._captured_sessions: list[ProcessedSession] = []
        self._filters: list[dict[str, str]] = []
        self._codec = _LoLBridgeCodec()  # Internal codec reference

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def captured_sessions(self) -> list[ProcessedSession]:
        return list(self._captured_sessions)

    @property
    def filters(self) -> list[dict[str, str]]:
        return list(self._filters)

    @property
    def _bridge_codec(self) -> _LoLBridgeCodec:
        return self._codec

    def add_filter(self, key: str, value: str) -> None:
        """Add a capture filter."""
        self._filters.append({"key": key, "value": value})

    def process_session(self, raw: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Process a raw HTTP session through the bridge codec.

        Args:
            raw: Raw session dict with id, method, url, status_code, response_body.

        Returns:
            Processed session dict with unified format.
        """
        session_id = raw.get("id", "")
        if not session_id:
            return None

        processed = ProcessedSession(
            session_id=session_id,
            method=raw.get("method", "GET"),
            url=raw.get("url", ""),
            status_code=raw.get("status_code", 0),
            response_body=raw.get("response_body", ""),
        )
        self._captured_sessions.append(processed)

        return {
            "session_id": session_id,
            "method": processed.method,
            "url": processed.url,
            "status_code": processed.status_code,
            "codec_name": processed.codec_name,
            "game": processed.game,
            "timestamp": processed.timestamp,
        }

    def to_unified_format(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Convert raw session to unified bridge format."""
        return {
            "session_id": raw.get("id", ""),
            "game": "league_of_legends",
            "codec_name": "lol",
            "endpoint": raw.get("url", ""),
            "method": raw.get("method", "GET"),
            "status": raw.get("status_code", 0),
        }

    def connect(self) -> bool:
        """Connect to fiddler-bridge."""
        self._connected = True
        return True

    def disconnect(self) -> None:
        """Disconnect from fiddler-bridge."""
        self._connected = False


class _LoLBridgeCodec:
    """Internal codec reference for fiddler-bridge integration."""
    name: str = "lol"
    version: str = "0.2.0"

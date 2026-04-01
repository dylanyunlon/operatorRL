"""
FiddlerMCPBridge — Fiddler network capture bridge via MCP protocol.
=====================================================================

Interfaces with a Fiddler MCP server to capture and decode LoL game
network traffic.  This provides richer data than the Live Client API
alone: exact packet timing, server-side events, and data that the
Live Client API doesn't expose.

Architecture position:
    modules/canbus/fiddler_bridge/fiddler_mcp.py   ← YOU ARE HERE
    ├─ Used by: canbus_component.py (optional data source)
    ├─ Input: Fiddler MCP server API (HTTP JSON-RPC)
    ├─ Output: RawFiddlerData on /lol/raw_fiddler channel
    └─ Decodes: LoL game protocol packets

Apollo reference:
    modules/drivers/canbus/can_client/ — hardware CAN interface
    modules/bridge/ — UDP bridge for external data

Design notes:
    - MCP (Model Context Protocol) integration for Fiddler
    - Session-based capture: filter by LoL process
    - Packet decoding: HTTP request/response pairs
    - Configurable capture filters (URL patterns)
    - Rate-limited polling to avoid overloading Fiddler
    - Graceful degradation when Fiddler is unavailable

Technical decision: Fiddler vs Vision capture
    - Network: <1ms latency, 100% accurate, no hallucination
    - Vision: >100ms latency, OCR errors possible
    - Fiddler + Proxifier routes game traffic through proxy
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("canbus.fiddler")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_MCP_URL = "http://127.0.0.1:8866"
_POLL_TIMEOUT_S = 2.0
_SESSION_BUFFER_SIZE = 100

# URL patterns to capture from LoL traffic
_CAPTURE_PATTERNS = [
    re.compile(r"/liveclientdata/"),
    re.compile(r"/lol-game-client-api/"),
    re.compile(r"riotgames\.com"),
    re.compile(r"leagueoflegends\.com"),
    re.compile(r"127\.0\.0\.1:2999"),
]

# URL patterns to ignore
_IGNORE_PATTERNS = [
    re.compile(r"\.(png|jpg|gif|ico|css|js|woff)"),
    re.compile(r"telemetry"),
    re.compile(r"analytics"),
]


class FiddlerState(Enum):
    """Fiddler bridge connection state."""
    DISABLED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    CAPTURING = auto()
    ERROR = auto()


@dataclass
class CapturedSession:
    """A single captured HTTP session from Fiddler.

    Represents one request/response pair from the LoL game.
    """
    session_id: int = 0
    method: str = "GET"
    url: str = ""
    host: str = ""
    status_code: int = 0
    request_headers: Dict[str, str] = field(default_factory=dict)
    response_headers: Dict[str, str] = field(default_factory=dict)
    request_body: str = ""
    response_body: str = ""
    timestamp: float = 0.0
    duration_ms: float = 0.0
    process_name: str = ""

    @property
    def is_json_response(self) -> bool:
        ct = self.response_headers.get("Content-Type", "")
        return "json" in ct.lower()

    @property
    def response_json(self) -> Optional[Dict[str, Any]]:
        if not self.is_json_response:
            return None
        try:
            return json.loads(self.response_body)
        except (json.JSONDecodeError, TypeError):
            return None

    @property
    def is_lol_traffic(self) -> bool:
        return any(p.search(self.url) for p in _CAPTURE_PATTERNS)

    @property
    def should_ignore(self) -> bool:
        return any(p.search(self.url) for p in _IGNORE_PATTERNS)


@dataclass
class FiddlerConfig:
    """Fiddler MCP bridge configuration."""
    mcp_url: str = _DEFAULT_MCP_URL
    capture_lol_only: bool = True
    max_sessions: int = _SESSION_BUFFER_SIZE
    poll_timeout_s: float = _POLL_TIMEOUT_S
    auto_decode: bool = True


class FiddlerMCPBridge:
    """Bridge between Fiddler MCP server and lolbot-HyperAI canbus.

    Connects to a running Fiddler MCP server, polls for new captured
    sessions, filters for LoL-relevant traffic, and provides decoded
    packet data to the canbus component.

    Usage::

        bridge = FiddlerMCPBridge(config)
        bridge.connect()
        sessions = bridge.poll_new_sessions()
        for s in sessions:
            if s.is_lol_traffic:
                process(s)
    """

    def __init__(self, config: Optional[FiddlerConfig] = None) -> None:
        self._config = config or FiddlerConfig()
        self._state = FiddlerState.DISABLED
        self._last_session_id: int = 0
        self._session_buffer: List[CapturedSession] = []
        self._total_captured: int = 0
        self._total_lol_sessions: int = 0
        self._connect_attempts: int = 0

    def connect(self) -> bool:
        """Connect to the Fiddler MCP server.

        Returns:
            True if connection successful.
        """
        self._state = FiddlerState.CONNECTING
        self._connect_attempts += 1

        try:
            # Probe the MCP server
            resp = self._mcp_request("system.listMethods", {})
            if resp is not None:
                self._state = FiddlerState.CONNECTED
                logger.info("Connected to Fiddler MCP at %s",
                            self._config.mcp_url)
                return True
        except Exception as exc:
            logger.warning("Fiddler MCP connection failed: %s", exc)

        self._state = FiddlerState.ERROR
        return False

    def disconnect(self) -> None:
        self._state = FiddlerState.DISABLED
        logger.info("Fiddler MCP bridge disconnected")

    def poll_new_sessions(self) -> List[CapturedSession]:
        """Poll Fiddler for new captured sessions since last poll.

        Returns:
            List of new CapturedSession objects.
        """
        if self._state not in (FiddlerState.CONNECTED, FiddlerState.CAPTURING):
            return []

        try:
            raw_sessions = self._fetch_sessions()
            if not raw_sessions:
                return []

            self._state = FiddlerState.CAPTURING
            new_sessions: List[CapturedSession] = []

            for raw in raw_sessions:
                session = self._parse_session(raw)
                if session.session_id <= self._last_session_id:
                    continue

                self._last_session_id = session.session_id
                self._total_captured += 1

                # Filter
                if session.should_ignore:
                    continue
                if self._config.capture_lol_only and not session.is_lol_traffic:
                    continue

                self._total_lol_sessions += 1
                new_sessions.append(session)

                # Buffer management
                self._session_buffer.append(session)
                if len(self._session_buffer) > self._config.max_sessions:
                    self._session_buffer = self._session_buffer[
                        -self._config.max_sessions:
                    ]

            return new_sessions

        except Exception as exc:
            logger.warning("Fiddler poll error: %s", exc)
            return []

    def _fetch_sessions(self) -> Optional[List[Dict[str, Any]]]:
        """Fetch session list from Fiddler MCP API."""
        # Try MCP JSON-RPC endpoint
        result = self._mcp_request(
            "sessions.list",
            {"after": self._last_session_id, "limit": 50},
        )
        if result is not None:
            return result

        # Fallback: try REST API (Fiddler Core API)
        return self._rest_request("/api/sessions")

    def _parse_session(self, raw: Dict[str, Any]) -> CapturedSession:
        """Parse a raw session dict into a CapturedSession."""
        return CapturedSession(
            session_id=raw.get("id", raw.get("session_id", 0)),
            method=raw.get("method", raw.get("RequestMethod", "GET")),
            url=raw.get("url", raw.get("RequestURL", "")),
            host=raw.get("host", raw.get("hostname", "")),
            status_code=raw.get("status", raw.get("ResponseCode", 0)),
            request_headers=raw.get("requestHeaders", {}),
            response_headers=raw.get("responseHeaders", {}),
            request_body=raw.get("requestBody", ""),
            response_body=raw.get("responseBody", ""),
            timestamp=raw.get("timestamp", time.time()),
            duration_ms=raw.get("duration_ms", raw.get("clientDuration", 0)),
            process_name=raw.get("process", ""),
        )

    def _mcp_request(
        self, method: str, params: Dict[str, Any]
    ) -> Optional[Any]:
        """Send a JSON-RPC request to the Fiddler MCP server."""
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": int(time.time() * 1000),
        }).encode()

        try:
            req = urllib.request.Request(
                self._config.mcp_url,
                data=payload,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(
                req, timeout=self._config.poll_timeout_s
            ) as resp:
                body = json.loads(resp.read().decode())
                if "result" in body:
                    return body["result"]
                if "error" in body:
                    logger.debug("MCP error: %s", body["error"])
                return None
        except Exception:
            return None

    def _rest_request(self, path: str) -> Optional[List[Dict[str, Any]]]:
        """Fallback REST API request."""
        try:
            url = f"{self._config.mcp_url}{path}"
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(
                req, timeout=self._config.poll_timeout_s
            ) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return None

    # ─── Decoded data extraction ─────────────────────────────────────

    def extract_game_data(
        self, sessions: List[CapturedSession]
    ) -> List[Dict[str, Any]]:
        """Extract decoded game data from captured sessions.

        Filters sessions that contain allgamedata or event responses
        and returns their parsed JSON bodies.
        """
        game_data: List[Dict[str, Any]] = []
        for session in sessions:
            if not session.is_json_response:
                continue
            data = session.response_json
            if data is None:
                continue

            # Check for allgamedata responses
            if "allPlayers" in data or "activePlayer" in data:
                game_data.append({
                    "type": "allgamedata",
                    "data": data,
                    "timestamp": session.timestamp,
                    "latency_ms": session.duration_ms,
                })
            elif "Events" in data:
                game_data.append({
                    "type": "events",
                    "data": data,
                    "timestamp": session.timestamp,
                })

        return game_data

    # ─── Introspection ───────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._state in (
            FiddlerState.CONNECTED, FiddlerState.CAPTURING
        )

    def bridge_status(self) -> Dict[str, Any]:
        return {
            "state": self._state.name,
            "mcp_url": self._config.mcp_url,
            "total_captured": self._total_captured,
            "total_lol_sessions": self._total_lol_sessions,
            "buffer_size": len(self._session_buffer),
            "last_session_id": self._last_session_id,
            "connect_attempts": self._connect_attempts,
        }

"""
CanbusComponent — LCU/Fiddler data acquisition loop (10Hz).
=============================================================

The "CAN bus" of lolbot-HyperAI: periodically polls the LoL Live Client
Data API (localhost:2999) and Fiddler MCP bridge to acquire raw game data,
then publishes it on ``/lol/raw_lcu`` and ``/lol/raw_fiddler`` channels
for the perception pipeline to consume.

Architecture position:
    modules/canbus/canbus_component.py   ← YOU ARE HERE
    ├─ Reads: LCU Live Client Data API (HTTP GET, 100ms cycle)
    ├─ Reads: Fiddler MCP bridge (network captures)
    ├─ Publishes: /lol/raw_lcu (RawLCUData)
    ├─ Publishes: /lol/raw_fiddler (RawFiddlerData)
    └─ Publishes: /lol/canbus_status (StatusMessage)

Apollo reference:
    modules/canbus/canbus_component.cc  — ``Init()``, ``Proc()``
    modules/canbus/canbus_component.h   — readers/writers, vehicle factory

Design notes:
    - SSL verification disabled for localhost:2999 (self-signed cert)
    - Exponential backoff when LCU is not available
    - Fiddler polling is optional; degrades gracefully
    - Publishes even on error (with status code) so perception knows
    - Connection state machine: DISCONNECTED → CONNECTING → CONNECTED
    - Game detection: polls /liveclientdata/gamestats first (lightweight)
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from cyber.component.timer_component import (
    ComponentConfig,
    ComponentState,
    TimerComponent,
)
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import RawLCUData, RawFiddlerData

logger = get_logger("canbus")

# ─── Constants ───────────────────────────────────────────────────────────────

_LCU_BASE_URL = "https://127.0.0.1:2999"
_LCU_ALLGAMEDATA = f"{_LCU_BASE_URL}/liveclientdata/allgamedata"
_LCU_GAMESTATS = f"{_LCU_BASE_URL}/liveclientdata/gamestats"
_LCU_EVENTDATA = f"{_LCU_BASE_URL}/liveclientdata/eventdata"
_LCU_TIMEOUT_S = 2.0
_CANBUS_INTERVAL_MS = 100.0  # 10Hz — matches Apollo canbus Proc() frequency
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
_BACKOFF_MULTIPLIER = 2.0
_FIDDLER_POLL_INTERVAL_TICKS = 5  # poll fiddler every 5th tick (2Hz)
_STALE_THRESHOLD_S = 5.0
_MAX_CONSECUTIVE_ERRORS = 10


class ConnectionState(Enum):
    """LCU connection state machine."""
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    GAME_NOT_ACTIVE = auto()
    ERROR = auto()


@dataclass
class CanbusConfig:
    """Canbus-specific configuration."""
    lcu_base_url: str = _LCU_BASE_URL
    lcu_timeout_s: float = _LCU_TIMEOUT_S
    fiddler_enabled: bool = False
    fiddler_mcp_url: str = "http://127.0.0.1:8866"
    poll_interval_ms: float = _CANBUS_INTERVAL_MS
    enable_ssl_verify: bool = False  # LCU uses self-signed cert


# ─── SSL context for LCU (self-signed certificate) ──────────────────────────

def _create_lcu_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that accepts the LCU's self-signed cert."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_LCU_SSL_CTX = _create_lcu_ssl_context()


# ─── LCU HTTP Client ────────────────────────────────────────────────────────

class LCUClient:
    """Lightweight HTTP client for the Live Client Data API.

    Uses urllib to avoid external dependencies.  The LCU API runs on
    localhost:2999 with a self-signed SSL certificate.
    """

    def __init__(self, base_url: str = _LCU_BASE_URL, timeout: float = _LCU_TIMEOUT_S) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._request_count: int = 0
        self._error_count: int = 0
        self._last_latency_ms: float = 0.0

    def get(self, endpoint: str) -> Tuple[Optional[Dict[str, Any]], Status]:
        """HTTP GET a JSON endpoint.

        Args:
            endpoint: Path like "/liveclientdata/allgamedata".

        Returns:
            Tuple of (parsed JSON dict or None, Status).
        """
        url = f"{self._base_url}{endpoint}"
        self._request_count += 1
        t0 = time.monotonic()

        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(
                req, timeout=self._timeout, context=_LCU_SSL_CTX
            ) as resp:
                self._last_latency_ms = (time.monotonic() - t0) * 1000
                if resp.status != 200:
                    self._error_count += 1
                    return None, Status.error(
                        ErrorCode.CANBUS_LCU_HTTP_ERROR,
                        f"HTTP {resp.status} from {endpoint}",
                        http_status=resp.status,
                    )
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return data, Status.ok()

        except urllib.error.URLError as exc:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            reason = str(getattr(exc, "reason", exc))
            if "Connection refused" in reason or "No connection" in reason:
                return None, Status.error(
                    ErrorCode.CANBUS_LCU_NOT_RUNNING,
                    f"LCU not running: {reason}",
                )
            return None, Status.error(
                ErrorCode.CANBUS_LCU_CONNECTION_FAILED,
                f"URL error: {reason}",
            )

        except TimeoutError:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            return None, Status.error(
                ErrorCode.CANBUS_LCU_TIMEOUT,
                f"Timeout after {self._timeout}s",
            )

        except json.JSONDecodeError as exc:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            return None, Status.error(
                ErrorCode.CANBUS_LCU_INVALID_RESPONSE,
                f"JSON decode error: {exc}",
            )

        except Exception as exc:
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            self._error_count += 1
            return None, Status.error(
                ErrorCode.CANBUS_LCU_CONNECTION_FAILED,
                f"Unexpected error: {type(exc).__name__}: {exc}",
            )

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "request_count": self._request_count,
            "error_count": self._error_count,
            "last_latency_ms": round(self._last_latency_ms, 2),
        }


# ─── Fiddler MCP Client (stub for when Fiddler is enabled) ──────────────────

class FiddlerMCPClient:
    """Client for the Fiddler MCP bridge.

    Fiddler captures LoL game network traffic via proxy.  The MCP
    server exposes captured sessions as JSON.

    This is a lightweight stub; the full implementation lives in
    modules/canbus/fiddler_bridge/fiddler_mcp.py.
    """

    def __init__(self, mcp_url: str = "http://127.0.0.1:8866") -> None:
        self._mcp_url = mcp_url
        self._enabled = False
        self._last_poll_time: float = 0.0

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def poll_sessions(self) -> Tuple[Optional[List[Dict[str, Any]]], Status]:
        """Poll Fiddler for new captured sessions.

        Returns:
            Tuple of (list of session dicts or None, Status).
        """
        if not self._enabled:
            return None, Status.ok("Fiddler disabled")

        try:
            url = f"{self._mcp_url}/api/sessions"
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                body = resp.read().decode("utf-8")
                sessions = json.loads(body)
                self._last_poll_time = time.time()
                return sessions, Status.ok()
        except Exception as exc:
            return None, Status.error(
                ErrorCode.CANBUS_FIDDLER_CONNECTION_FAILED,
                f"Fiddler poll failed: {exc}",
            )


# ─── CanbusComponent ────────────────────────────────────────────────────────

class CanbusComponent(TimerComponent):
    """Apollo-style canbus component: 10Hz LCU data acquisition.

    This is the heartbeat of the entire system.  Every 100ms it:
    1. Checks if a game is in progress (lightweight gamestats probe)
    2. Fetches allgamedata from the LCU API
    3. Optionally polls Fiddler for network captures
    4. Publishes raw data on cyber channels
    5. Publishes status for health monitoring

    Mirrors Apollo's ``CanbusComponent::Proc()`` which reads CAN frames
    at 10ms intervals and publishes ``/apollo/canbus/chassis``.

    Usage::

        config = CanbusConfig(fiddler_enabled=True)
        canbus = CanbusComponent(config)
        # registered with CyberScheduler; Proc() runs automatically
    """

    def __init__(
        self,
        canbus_config: Optional[CanbusConfig] = None,
    ) -> None:
        self._canbus_config = canbus_config or CanbusConfig()
        super().__init__(
            config=ComponentConfig(
                name="canbus",
                interval_ms=self._canbus_config.poll_interval_ms,
                warn_threshold_ms=self._canbus_config.poll_interval_ms * 2,
                max_consecutive_failures=_MAX_CONSECUTIVE_ERRORS,
            ),
        )

        # ── State ────────────────────────────────────────────────────
        self._connection_state = ConnectionState.DISCONNECTED
        self._lcu_client: Optional[LCUClient] = None
        self._fiddler_client: Optional[FiddlerMCPClient] = None
        self._node: Optional[CyberNode] = None

        # Writers
        self._raw_lcu_writer: Optional[Writer[RawLCUData]] = None
        self._raw_fiddler_writer: Optional[Writer[RawFiddlerData]] = None
        self._status_writer: Optional[Writer[StatusMessage]] = None

        # Backoff state for reconnection
        self._backoff_s: float = _BACKOFF_INITIAL_S
        self._last_backoff_time: float = 0.0

        # Game detection
        self._game_active: bool = False
        self._last_game_time: float = -1.0
        self._stale_count: int = 0

        # Tick counter for fiddler sub-sampling
        self._tick: int = 0

    # ─── TimerComponent interface ────────────────────────────────────

    def Init(self) -> bool:
        """Initialize LCU client, Fiddler client, and cyber node.

        Apollo equivalent: ``CanbusComponent::Init()``
        """
        logger.info("Initializing CanbusComponent...")

        # Create LCU HTTP client
        self._lcu_client = LCUClient(
            base_url=self._canbus_config.lcu_base_url,
            timeout=self._canbus_config.lcu_timeout_s,
        )

        # Create Fiddler client if enabled
        if self._canbus_config.fiddler_enabled:
            self._fiddler_client = FiddlerMCPClient(
                mcp_url=self._canbus_config.fiddler_mcp_url,
            )
            self._fiddler_client.enable()
            logger.info("Fiddler MCP bridge enabled at %s",
                        self._canbus_config.fiddler_mcp_url)

        # Create cyber node and channels
        self._node = CyberNode("canbus")
        self._raw_lcu_writer = self._node.CreateWriter(
            "/lol/raw_lcu", RawLCUData
        )
        self._raw_fiddler_writer = self._node.CreateWriter(
            "/lol/raw_fiddler", RawFiddlerData
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/canbus_status", StatusMessage
        )

        self._connection_state = ConnectionState.CONNECTING
        logger.info("CanbusComponent initialized")
        return True

    def Proc(self) -> bool:
        """Execute one canbus acquisition cycle.

        Apollo equivalent: ``CanbusComponent::Proc()``

        Cycle:
            1. Check game active (lightweight probe)
            2. Fetch allgamedata
            3. Poll Fiddler (every Nth tick)
            4. Publish data on channels
            5. Publish status

        Returns:
            True on success (even partial), False on hard failure.
        """
        self._tick += 1
        proc_start = time.monotonic()

        # ── Step 1: Check if game is in progress ─────────────────────
        if not self._game_active:
            game_check = self._check_game_active()
            if not game_check:
                # Backoff: don't hammer the API
                self._apply_backoff()
                self._publish_status(Status.error(
                    ErrorCode.CANBUS_GAME_NOT_IN_PROGRESS,
                    "No active game detected",
                ))
                return True  # not a failure, just no game

        # ── Step 2: Fetch allgamedata from LCU ───────────────────────
        lcu_data, lcu_status = self._fetch_allgamedata()
        if lcu_data is not None:
            self._connection_state = ConnectionState.CONNECTED
            self._backoff_s = _BACKOFF_INITIAL_S  # reset backoff
            self._check_stale(lcu_data)

            # Publish raw LCU data
            raw = RawLCUData(
                allgamedata=lcu_data,
                timestamp=time.time(),
                lcu_latency_ms=self._lcu_client._last_latency_ms if self._lcu_client else 0,
                http_status=200,
                source="lcu",
            )
            if self._raw_lcu_writer:
                self._raw_lcu_writer.Write(raw)
        else:
            self._connection_state = ConnectionState.ERROR
            logger.warning("LCU fetch failed: %s", lcu_status)

        # ── Step 3: Fiddler polling (sub-sampled) ────────────────────
        if (
            self._fiddler_client is not None
            and self._tick % _FIDDLER_POLL_INTERVAL_TICKS == 0
        ):
            self._poll_fiddler()

        # ── Step 4: Publish status ───────────────────────────────────
        self._publish_status(lcu_status)

        elapsed_ms = (time.monotonic() - proc_start) * 1000
        if elapsed_ms > self._canbus_config.poll_interval_ms:
            logger.warning(
                "Canbus Proc() overrun: %.1fms > %.1fms",
                elapsed_ms, self._canbus_config.poll_interval_ms,
            )

        return lcu_data is not None

    def on_shutdown(self) -> None:
        """Clean up resources on shutdown."""
        if self._node:
            self._node.shutdown()
        logger.info("CanbusComponent shutdown complete")

    # ─── Internal methods ────────────────────────────────────────────

    def _check_game_active(self) -> bool:
        """Lightweight probe to check if a game is in progress.

        Uses /liveclientdata/gamestats which is smaller than allgamedata.
        """
        if self._lcu_client is None:
            return False

        data, status = self._lcu_client.get("/liveclientdata/gamestats")
        if data is not None:
            game_time = data.get("gameTime", 0.0)
            if game_time > 0:
                self._game_active = True
                logger.info(
                    "Game detected! game_time=%.1f mode=%s",
                    game_time, data.get("gameMode", ""),
                )
                return True
        self._game_active = False
        return False

    def _fetch_allgamedata(self) -> Tuple[Optional[Dict[str, Any]], Status]:
        """Fetch the complete allgamedata payload."""
        if self._lcu_client is None:
            return None, Status.error(
                ErrorCode.CANBUS_LCU_NOT_RUNNING,
                "LCU client not initialized",
            )
        return self._lcu_client.get("/liveclientdata/allgamedata")

    def _check_stale(self, data: Dict[str, Any]) -> None:
        """Detect if game time has stopped advancing (stale data)."""
        game_data = data.get("gameData", {})
        game_time = game_data.get("gameTime", 0.0)

        if game_time <= self._last_game_time and self._last_game_time > 0:
            self._stale_count += 1
            if self._stale_count > 50:  # 5 seconds of stale data
                logger.warning(
                    "Stale game data detected: time stuck at %.1f for %d ticks",
                    game_time, self._stale_count,
                )
                # Game may have ended
                if self._stale_count > 100:
                    self._game_active = False
                    self._stale_count = 0
        else:
            self._stale_count = 0

        self._last_game_time = game_time

    def _poll_fiddler(self) -> None:
        """Poll Fiddler MCP bridge for network captures."""
        if self._fiddler_client is None:
            return

        sessions, status = self._fiddler_client.poll_sessions()
        if sessions is not None and self._raw_fiddler_writer:
            raw = RawFiddlerData(
                sessions=sessions,
                timestamp=time.time(),
                packet_count=len(sessions),
            )
            self._raw_fiddler_writer.Write(raw)

    def _apply_backoff(self) -> None:
        """Apply exponential backoff between reconnection attempts."""
        now = time.monotonic()
        if now - self._last_backoff_time < self._backoff_s:
            return
        self._last_backoff_time = now
        self._backoff_s = min(self._backoff_s * _BACKOFF_MULTIPLIER, _BACKOFF_MAX_S)

    def _publish_status(self, status: Status) -> None:
        """Publish canbus status on the status channel."""
        if self._status_writer is None:
            return
        msg = StatusMessage(
            status=status,
            sequence=self._tick,
            source_component="canbus",
            game_time=self._last_game_time,
        )
        self._status_writer.Write(msg)

    # ─── Introspection ───────────────────────────────────────────────

    @property
    def connection_state(self) -> ConnectionState:
        return self._connection_state

    @property
    def game_active(self) -> bool:
        return self._game_active

    def canbus_status(self) -> Dict[str, Any]:
        """Extended status for monitoring dashboard."""
        base = self.status()
        base.update({
            "connection_state": self._connection_state.name,
            "game_active": self._game_active,
            "last_game_time": self._last_game_time,
            "backoff_s": self._backoff_s,
            "stale_count": self._stale_count,
            "lcu_stats": self._lcu_client.stats if self._lcu_client else {},
            "fiddler_enabled": self._fiddler_client is not None,
        })
        return base

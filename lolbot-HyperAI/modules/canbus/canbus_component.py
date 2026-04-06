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
from modules.common.component_base import (
    ComponentDependency,
    LifecycleState,
    ManagedComponent,
)
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import RawLCUData, RawFiddlerData
from modules.canbus.vehicle.data_source_factory import (
    DataSource,
    DataSourceFactory,
    PollResult,
)

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
    # Claude16: DataSourceFactory integration (Apollo vehicle_factory pattern)
    data_source: str = "auto"
    replay_file: str = ""
    replay_speed: float = 1.0
    replay_loop: bool = True


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

class CanbusComponent(TimerComponent, ManagedComponent):
    """Apollo-style canbus component: 10Hz LCU data acquisition.

    This is the heartbeat of the entire system.  Every 100ms it:
    1. Checks if a game is in progress (lightweight gamestats probe)
    2. Fetches allgamedata from the LCU API
    3. Optionally polls Fiddler for network captures
    4. Publishes raw data on cyber channels
    5. Publishes status for health monitoring

    Mirrors Apollo's ``CanbusComponent::Proc()`` which reads CAN frames
    at 10ms intervals and publishes ``/apollo/canbus/chassis``.

    Claude11: Added ManagedComponent mixin for lifecycle + circuit breaker.

    Usage::

        config = CanbusConfig(fiddler_enabled=True)
        canbus = CanbusComponent(config)
        # registered with CyberScheduler; Proc() runs automatically
    """

    COMPONENT_NAME = "canbus"
    DEPENDENCIES = []
    VERSION = "2.0.0"
    CB_MAX_FAILURES = 10
    CB_COOLDOWN_S = 2.0

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

        # Claude16: Apollo vehicle_object_ equivalent
        self._data_source: Optional[DataSource] = None
        self._data_source_type: str = "auto"

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
        """Initialize data source, Fiddler client, and cyber node.

        Apollo equivalent: ``CanbusComponent::Init()``

        Claude16: Uses DataSourceFactory (built by previous Claude but never
        wired in) to create LCU/Replay/Mock data source. Matches Apollo's
        vehicle_factory pattern. All existing LCU/Fiddler code preserved.
        """
        self._managed_init()
        cfg = self._canbus_config
        logger.info("Initializing CanbusComponent v%s ...", self.VERSION)

        # ── Step 1: Create data source via factory (Apollo vehicle_factory) ──
        if cfg.data_source == "auto":
            self._data_source_type, self._data_source = (
                DataSourceFactory.auto_detect()
            )
            logger.info("Auto-detected data source: %s", self._data_source_type)
        else:
            self._data_source_type = cfg.data_source
            self._data_source = DataSourceFactory.create_from_config(
                data_source=cfg.data_source,
                lcu_base_url=cfg.lcu_base_url,
                lcu_timeout_s=cfg.lcu_timeout_s,
                replay_file=cfg.replay_file,
                replay_speed=cfg.replay_speed,
                replay_loop=cfg.replay_loop,
            )

        # ── Step 2: Init data source (Apollo vehicle_object_->Start()) ───
        if not self._data_source.init():
            logger.error("Data source '%s' failed to init", self._data_source_type)
            logger.info("Falling back to mock data source")
            self._data_source = DataSourceFactory.create("mock")
            self._data_source_type = "mock"
            self._data_source.init()

        # ── Keep LCU client for game-active probe (backward compat) ──────
        self._lcu_client = LCUClient(
            base_url=cfg.lcu_base_url,
            timeout=cfg.lcu_timeout_s,
        )

        # ── Keep Fiddler client (backward compat) ────────────────────────
        if cfg.fiddler_enabled:
            self._fiddler_client = FiddlerMCPClient(
                mcp_url=cfg.fiddler_mcp_url,
            )
            self._fiddler_client.enable()
            logger.info("Fiddler MCP bridge enabled at %s", cfg.fiddler_mcp_url)

        # ── Step 3: Create cyber writers (Apollo chassis_writer_) ────────
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
        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info("CanbusComponent initialized (source=%s)",
                     self._data_source_type)
        return True

    def Proc(self) -> bool:
        """Execute one canbus acquisition cycle.

        Apollo equivalent: ``CanbusComponent::Proc()``

        Claude16: Refactored to Apollo 3-line Proc() pattern.
        All original logic (game-active, validation, stale, backoff)
        preserved in _poll_and_publish(). Claude13's measure_proc()
        and _validate_lcu_response() kept intact.
        """
        if self.should_skip_proc():
            return True

        self._tick += 1

        with self.measure_proc() as m:
            # Apollo pattern: Proc() = one core call + optional detail
            m.success = self._poll_and_publish()
            if not m.success:
                m.failure_reason = "poll_failed"

            # Fiddler sub-sampled (= Apollo PublishChassisDetail)
            if (
                self._fiddler_client is not None
                and self._tick % _FIDDLER_POLL_INTERVAL_TICKS == 0
            ):
                self._poll_fiddler()

        return m.success

    def _poll_and_publish(self) -> bool:
        """Poll data source and publish to channel.

        Apollo equivalent: ``PublishChassis()`` → vehicle_object_->publish_chassis()

        Claude16: Routes through DataSourceFactory. For LCU source, does
        game-active probe first (original behavior). For mock/replay,
        polls directly. All Claude13 validation logic preserved.
        """
        # ── LCU source: game-active probe (original behavior) ────────
        if self._data_source_type == "lcu" and not self._game_active:
            if not self._check_game_active():
                self._apply_backoff()
                self._publish_status(Status.error(
                    ErrorCode.CANBUS_GAME_NOT_IN_PROGRESS,
                    "No active game detected",
                ))
                return True  # not a failure, just no game

        # ── Core poll via DataSource (= vehicle_object_->publish_chassis()) ──
        result: PollResult = self._data_source.poll()

        if not result.success:
            self._connection_state = ConnectionState.ERROR
            self._publish_status(Status.error(
                ErrorCode.CANBUS_LCU_CONNECTION_FAILED,
                result.error or "Poll failed",
            ))
            return False

        data = result.data
        if data is None:
            return True

        # ── Validate (Claude13's _validate_lcu_response, preserved) ──
        if not self._validate_lcu_response(data):
            logger.warning("Response missing required fields (source=%s)",
                           self._data_source_type)
            self._publish_status(Status.error(
                ErrorCode.CANBUS_LCU_INVALID_RESPONSE,
                "Response missing required fields",
            ))
            return False

        # ── Stale detection (original, preserved) ────────────────────
        self._check_stale(data)
        self._connection_state = ConnectionState.CONNECTED
        self._backoff_s = _BACKOFF_INITIAL_S
        self._game_active = True

        # ── Publish (= chassis_writer_->Write(chassis)) ──────────────
        raw = RawLCUData(
            allgamedata=data,
            timestamp=time.time(),
            lcu_latency_ms=result.latency_ms,
            http_status=200,
            source=self._data_source_type,
        )
        if self._raw_lcu_writer:
            self._raw_lcu_writer.Write(raw)

        self._publish_status(Status.ok())
        return True

    def on_shutdown(self) -> None:
        """Clean up resources on shutdown. Apollo: Clear()"""
        self._managed_shutdown()
        if self._data_source:
            self._data_source.shutdown()
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

    def _validate_lcu_response(self, data: Dict[str, Any]) -> bool:
        """Validate that the LCU allgamedata response has required fields.

        The Live Client Data API returns a JSON object with these
        top-level keys when a game is active:
            - allPlayers: list of player objects
            - activePlayer: the local player's data
            - events: game event log
            - gameData: game metadata (gameTime, gameMode, etc.)

        Without allPlayers and gameData the perception pipeline will
        produce garbage GameSnapshots. This guard prevents that.

        Returns:
            True if the response structure is valid for processing.
        """
        if not isinstance(data, dict):
            return False

        required_keys = ("allPlayers", "gameData")
        for key in required_keys:
            if key not in data:
                logger.warning(
                    "LCU response missing required key: %r (keys=%s)",
                    key, list(data.keys()),
                )
                return False

        players = data.get("allPlayers")
        if not isinstance(players, list) or len(players) == 0:
            logger.warning(
                "LCU allPlayers is empty or not a list: %s",
                type(players).__name__,
            )
            return False

        game_data = data.get("gameData", {})
        if not isinstance(game_data, dict):
            return False
        if "gameTime" not in game_data:
            logger.warning("LCU gameData missing gameTime")
            return False

        return True

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
            # Claude16: data source introspection
            "data_source_type": self._data_source_type,
            "data_source_stats": (
                self._data_source.stats() if self._data_source else {}
            ),
        })
        return base

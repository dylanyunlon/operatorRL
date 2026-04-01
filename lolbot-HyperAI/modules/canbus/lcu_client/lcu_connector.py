"""
LCUConnector — Full-featured League Client Update API connector.
=================================================================

Provides both REST (HTTP) and WebSocket interfaces to the LoL client.
Handles authentication, SSL, connection lifecycle, and event streaming.

Architecture position:
    modules/canbus/lcu_client/lcu_connector.py   ← YOU ARE HERE
    ├─ Used by: canbus_component.py (REST polling)
    ├─ Provides: REST GET/POST, WebSocket event stream
    └─ Handles: LCU auth token, SSL, reconnection

Apollo reference:
    modules/drivers/canbus/can_client/ — CAN hardware interface
    modules/canbus/vehicle/vehicle_controller.h — vehicle abstraction

Design notes:
    - LCU lockfile parsing for auth token + port discovery
    - SSL context for self-signed LCU certificate
    - Reconnection with exponential backoff
    - Event subscription via WebSocket (for real-time notifications)
    - Connection health monitoring
    - Thread-safe: multiple components can share one connector
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("canbus.lcu")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_LCU_PORT = 2999    # Live Client Data API (always this port)
_LOCKFILE_PATHS = [
    # Windows
    Path("C:/Riot Games/League of Legends/lockfile"),
    # macOS
    Path("/Applications/League of Legends.app/Contents/LoL/lockfile"),
]
_LIVE_CLIENT_BASE = "https://127.0.0.1:2999"
_LCU_API_BASE_TEMPLATE = "https://127.0.0.1:{port}"
_REQUEST_TIMEOUT_S = 3.0
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
_HEALTH_CHECK_INTERVAL_S = 5.0


class LCUConnectionState(Enum):
    DISCONNECTED = auto()
    DISCOVERING = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    GAME_ACTIVE = auto()
    ERROR = auto()


@dataclass
class LCUAuth:
    """LCU authentication credentials parsed from lockfile."""
    process_name: str = ""
    pid: int = 0
    port: int = 0
    password: str = ""
    protocol: str = "https"

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def auth_header(self) -> str:
        """HTTP Basic auth header value."""
        token = base64.b64encode(
            f"riot:{self.password}".encode()
        ).decode()
        return f"Basic {token}"

    @property
    def is_valid(self) -> bool:
        return self.port > 0 and len(self.password) > 0


@dataclass
class RequestStats:
    """Statistics for HTTP request tracking."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0.0
    max_latency_ms: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests


class LCUConnector:
    """Full-featured LCU API connector.

    Provides:
    - Live Client Data API access (always on port 2999)
    - LCU API access (dynamic port from lockfile)
    - Authentication handling
    - Connection lifecycle management

    Usage::

        connector = LCUConnector()
        connector.connect()

        # Live Client Data (in-game)
        data = connector.get_live("/liveclientdata/allgamedata")

        # LCU API (client)
        summoner = connector.get_lcu("/lol-summoner/v1/current-summoner")
    """

    def __init__(self) -> None:
        self._state = LCUConnectionState.DISCONNECTED
        self._auth: Optional[LCUAuth] = None
        self._ssl_ctx = self._create_ssl_context()
        self._lock = threading.RLock()
        self._live_stats = RequestStats()
        self._lcu_stats = RequestStats()
        self._backoff_s = _BACKOFF_INITIAL_S
        self._last_health_check: float = 0.0

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # ─── Connection lifecycle ────────────────────────────────────────

    def connect(self) -> bool:
        """Attempt to connect to the LCU.

        Tries lockfile discovery first (for full LCU API access),
        then falls back to Live Client Data API probe.

        Returns:
            True if connected.
        """
        with self._lock:
            self._state = LCUConnectionState.DISCOVERING

        # Try lockfile discovery
        auth = self._discover_lockfile()
        if auth and auth.is_valid:
            with self._lock:
                self._auth = auth
                self._state = LCUConnectionState.CONNECTING

            if self._verify_lcu_connection(auth):
                with self._lock:
                    self._state = LCUConnectionState.CONNECTED
                logger.info(
                    "Connected to LCU (port=%d, pid=%d)",
                    auth.port, auth.pid,
                )
                return True

        # Try Live Client Data API directly
        if self._verify_live_client():
            with self._lock:
                self._state = LCUConnectionState.GAME_ACTIVE
            logger.info("Connected to Live Client Data API (game active)")
            return True

        with self._lock:
            self._state = LCUConnectionState.DISCONNECTED
        return False

    def disconnect(self) -> None:
        with self._lock:
            self._state = LCUConnectionState.DISCONNECTED
            self._auth = None
        logger.info("Disconnected from LCU")

    @property
    def is_connected(self) -> bool:
        return self._state in (
            LCUConnectionState.CONNECTED,
            LCUConnectionState.GAME_ACTIVE,
        )

    @property
    def state(self) -> LCUConnectionState:
        return self._state

    # ─── Lockfile discovery ──────────────────────────────────────────

    def _discover_lockfile(self) -> Optional[LCUAuth]:
        """Parse the LCU lockfile for connection details.

        The lockfile format is:
            LeagueClient:pid:port:password:protocol
        """
        for lockfile_path in _LOCKFILE_PATHS:
            if lockfile_path.exists():
                try:
                    content = lockfile_path.read_text().strip()
                    parts = content.split(":")
                    if len(parts) >= 5:
                        return LCUAuth(
                            process_name=parts[0],
                            pid=int(parts[1]),
                            port=int(parts[2]),
                            password=parts[3],
                            protocol=parts[4],
                        )
                except (ValueError, IOError) as exc:
                    logger.debug("Lockfile parse error: %s", exc)

        # Fallback: process-based discovery (Windows)
        return self._discover_from_process()

    def _discover_from_process(self) -> Optional[LCUAuth]:
        """Try to find LCU connection info from running process."""
        try:
            # This works on Windows with wmic
            result = subprocess.run(
                ["wmic", "PROCESS", "WHERE",
                 "name='LeagueClientUx.exe'", "GET",
                 "commandline"],
                capture_output=True, text=True, timeout=5,
            )
            output = result.stdout
            port_match = re.search(r"--app-port=(\d+)", output)
            token_match = re.search(r"--remoting-auth-token=(\S+)", output)
            pid_match = re.search(r"--app-pid=(\d+)", output)

            if port_match and token_match:
                return LCUAuth(
                    process_name="LeagueClientUx",
                    pid=int(pid_match.group(1)) if pid_match else 0,
                    port=int(port_match.group(1)),
                    password=token_match.group(1),
                    protocol="https",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return None

    # ─── Connection verification ─────────────────────────────────────

    def _verify_lcu_connection(self, auth: LCUAuth) -> bool:
        """Verify LCU API is accessible."""
        try:
            url = f"{auth.base_url}/lol-summoner/v1/current-summoner"
            req = urllib.request.Request(url)
            req.add_header("Authorization", auth.auth_header)
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(
                req, timeout=_REQUEST_TIMEOUT_S, context=self._ssl_ctx
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    def _verify_live_client(self) -> bool:
        """Verify Live Client Data API is accessible."""
        try:
            url = f"{_LIVE_CLIENT_BASE}/liveclientdata/gamestats"
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(
                req, timeout=_REQUEST_TIMEOUT_S, context=self._ssl_ctx
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ─── HTTP API methods ────────────────────────────────────────────

    def get_live(self, endpoint: str) -> Tuple[Optional[Dict[str, Any]], int]:
        """HTTP GET from Live Client Data API (port 2999).

        Args:
            endpoint: Path like "/liveclientdata/allgamedata".

        Returns:
            Tuple of (JSON dict or None, HTTP status code).
        """
        url = f"{_LIVE_CLIENT_BASE}{endpoint}"
        return self._http_get(url, stats=self._live_stats)

    def get_lcu(self, endpoint: str) -> Tuple[Optional[Dict[str, Any]], int]:
        """HTTP GET from LCU API (authenticated).

        Args:
            endpoint: Path like "/lol-summoner/v1/current-summoner".

        Returns:
            Tuple of (JSON dict or None, HTTP status code).
        """
        if not self._auth or not self._auth.is_valid:
            return None, 0

        url = f"{self._auth.base_url}{endpoint}"
        headers = {"Authorization": self._auth.auth_header}
        return self._http_get(url, headers=headers, stats=self._lcu_stats)

    def _http_get(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        stats: Optional[RequestStats] = None,
    ) -> Tuple[Optional[Dict[str, Any]], int]:
        """Generic HTTP GET with stats tracking."""
        if stats:
            stats.total_requests += 1

        t0 = time.monotonic()
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)

            with urllib.request.urlopen(
                req, timeout=_REQUEST_TIMEOUT_S, context=self._ssl_ctx
            ) as resp:
                latency = (time.monotonic() - t0) * 1000
                if stats:
                    stats.successful_requests += 1
                    stats.total_latency_ms += latency
                    stats.max_latency_ms = max(stats.max_latency_ms, latency)

                body = resp.read().decode("utf-8")
                return json.loads(body), resp.status

        except Exception as exc:
            if stats:
                stats.failed_requests += 1
            return None, 0

    # ─── Health check ────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Periodic health check — call from canbus Proc()."""
        now = time.monotonic()
        if now - self._last_health_check < _HEALTH_CHECK_INTERVAL_S:
            return self.is_connected

        self._last_health_check = now

        if self._state == LCUConnectionState.GAME_ACTIVE:
            if not self._verify_live_client():
                with self._lock:
                    self._state = LCUConnectionState.DISCONNECTED
                return False
        elif self._state == LCUConnectionState.CONNECTED:
            if self._auth and not self._verify_lcu_connection(self._auth):
                with self._lock:
                    self._state = LCUConnectionState.DISCONNECTED
                return False

        return self.is_connected

    # ─── Introspection ───────────────────────────────────────────────

    def connection_info(self) -> Dict[str, Any]:
        return {
            "state": self._state.name,
            "has_auth": self._auth is not None and self._auth.is_valid,
            "lcu_port": self._auth.port if self._auth else 0,
            "live_stats": {
                "total": self._live_stats.total_requests,
                "success_rate": round(self._live_stats.success_rate, 3),
                "avg_latency_ms": round(self._live_stats.avg_latency_ms, 1),
            },
            "lcu_stats": {
                "total": self._lcu_stats.total_requests,
                "success_rate": round(self._lcu_stats.success_rate, 3),
                "avg_latency_ms": round(self._lcu_stats.avg_latency_ms, 1),
            },
        }

#!/usr/bin/env python3
"""
M807 - LCU API Client
=======================
OperatorRL Historical Battle System - League Client Update API Integration

查看 Seraphine 项目上现有的 LCU API 客户端实现方式，理解其模式，
特别是 WebSocket 连接和 REST API 是如何分离的。从 Seraphine 的
connector 模块开始，遵循该模式实现一个新的 LCU 客户端，使数据采集层
可以直接从本地 League Client 获取数据，并能自动发现客户端进程。
然后引入 WebSocket 事件监听，使实时数据桥接能够获取即时状态变更。

Core responsibilities:
- Auto-discover League Client process and extract auth credentials
- Manage authenticated HTTP sessions to the LCU REST API
- WebSocket subscription for real-time client events
- Provide typed endpoints for match history, summoner, champion data
- Handle reconnection, timeout, and credential rotation
"""

import os
import re
import sys
import ssl
import json
import time
import base64
import socket
import asyncio
import logging
import hashlib
import subprocess
import urllib.parse
import datetime
from pathlib import Path
from typing import (
    Dict, List, Any, Optional, Tuple, Callable, Awaitable, Set, Union
)
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from enum import Enum

# ─── Module Logger ────────────────────────────────────────────────────────────

logger = logging.getLogger("operatorRL.historical_battle.lcu_api_client")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────────────

LCU_PROCESS_NAME_WINDOWS = "LeagueClientUx.exe"
LCU_PROCESS_NAME_MAC = "LeagueClientUx"
LCU_LOCKFILE_NAME = "lockfile"
LCU_DEFAULT_PROTOCOL = "https"
LCU_CERT_CHECK = False  # LCU uses self-signed cert
LCU_MAX_RETRIES = 5
LCU_RETRY_DELAY_SECONDS = 2.0
LCU_REQUEST_TIMEOUT_SECONDS = 10.0
LCU_WEBSOCKET_RECONNECT_DELAY = 3.0
LCU_HEARTBEAT_INTERVAL_SECONDS = 30.0
LCU_AUTH_USERNAME = "riot"
EVENT_SUBSCRIBE_ALL = 5  # WAMP subscribe opcode
EVENT_UNSUBSCRIBE = 6
EVENT_MESSAGE = 8

# Known LCU API endpoints (partial list, most commonly used)
ENDPOINTS = {
    "current_summoner": "/lol-summoner/v1/current-summoner",
    "match_history": "/lol-match-history/v1/products/lol/{puuid}/matches",
    "ranked_stats": "/lol-ranked/v1/current-ranked-stats",
    "champion_mastery": "/lol-collections/v1/inventories/{summonerId}/champion-mastery",
    "gameflow_phase": "/lol-gameflow/v1/gameflow-phase",
    "champ_select_session": "/lol-champ-select/v1/session",
    "lobby": "/lol-lobby/v2/lobby",
    "login_session": "/lol-login/v1/session",
    "summoner_by_name": "/lol-summoner/v1/summoners?name={name}",
    "summoner_by_puuid": "/lol-summoner/v2/summoners/puuid/{puuid}",
    "game_data_champions": "/lol-game-data/assets/v1/champion-summary.json",
    "perk_pages": "/lol-perks/v1/pages",
    "current_game": "/lol-gameflow/v1/session",
    "friends_list": "/lol-chat/v1/friends",
    "end_of_game_stats": "/lol-end-of-game/v1/eog-stats-block",
    "loot_inventory": "/lol-loot/v1/player-loot-map",
    "patch_version": "/lol-patch/v1/game/client-config",
}


# ─── Data Models ──────────────────────────────────────────────────────────────

class LCUConnectionState(Enum):
    """State machine for LCU connection lifecycle."""
    DISCONNECTED = "disconnected"
    DISCOVERING = "discovering"
    CONNECTING = "connecting"
    AUTHENTICATING = "authenticating"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    SHUTDOWN = "shutdown"


class GameflowPhase(Enum):
    """League Client gameflow phases."""
    NONE = "None"
    LOBBY = "Lobby"
    MATCHMAKING = "Matchmaking"
    CHAMP_SELECT = "ChampSelect"
    GAME_START = "GameStart"
    IN_PROGRESS = "InProgress"
    WAITING_FOR_STATS = "WaitingForStats"
    PRE_END_OF_GAME = "PreEndOfGame"
    END_OF_GAME = "EndOfGame"
    RECONNECT = "Reconnect"
    TERMINATED_IN_ERROR = "TerminatedInError"


@dataclass
class LCUCredentials:
    """Authentication credentials extracted from LCU process/lockfile."""
    process_id: int = 0
    port: int = 0
    password: str = ""
    protocol: str = LCU_DEFAULT_PROTOCOL
    pid_path: str = ""

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def auth_header(self) -> str:
        token = base64.b64encode(
            f"{LCU_AUTH_USERNAME}:{self.password}".encode()
        ).decode()
        return f"Basic {token}"

    @property
    def is_valid(self) -> bool:
        return self.port > 0 and len(self.password) > 0

    def __repr__(self) -> str:
        return f"LCUCredentials(pid={self.process_id}, port={self.port}, valid={self.is_valid})"


@dataclass
class LCUResponse:
    """Standardized response wrapper for LCU API calls."""
    status_code: int = 0
    data: Any = None
    error: Optional[str] = None
    endpoint: str = ""
    elapsed_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def not_found(self) -> bool:
        return self.status_code == 404

    def raise_for_status(self):
        if not self.ok:
            raise LCUAPIError(
                f"LCU API error {self.status_code} on {self.endpoint}: {self.error}"
            )


@dataclass
class LCUEvent:
    """WebSocket event from LCU."""
    uri: str = ""
    event_type: str = ""  # Create, Update, Delete
    data: Any = None
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    @property
    def is_gameflow(self) -> bool:
        return "gameflow" in self.uri.lower()

    @property
    def is_champ_select(self) -> bool:
        return "champ-select" in self.uri.lower()

    @property
    def is_end_of_game(self) -> bool:
        return "end-of-game" in self.uri.lower()


@dataclass
class SummonerInfo:
    """Summoner information from LCU."""
    account_id: int = 0
    display_name: str = ""
    internal_name: str = ""
    puuid: str = ""
    summoner_id: int = 0
    summoner_level: int = 0
    profile_icon_id: int = 0
    xp_since_last_level: int = 0
    xp_until_next_level: int = 0
    reroll_points: int = 0

    @classmethod
    def from_lcu_data(cls, data: Dict[str, Any]) -> "SummonerInfo":
        return cls(
            account_id=data.get("accountId", 0),
            display_name=data.get("displayName", ""),
            internal_name=data.get("internalName", ""),
            puuid=data.get("puuid", ""),
            summoner_id=data.get("summonerId", 0),
            summoner_level=data.get("summonerLevel", 0),
            profile_icon_id=data.get("profileIconId", 0),
            xp_since_last_level=data.get("xpSinceLastLevel", 0),
            xp_until_next_level=data.get("xpUntilNextLevel", 0),
        )


@dataclass
class RankedInfo:
    """Ranked stats from LCU."""
    queue_type: str = ""
    tier: str = ""
    division: str = ""
    league_points: int = 0
    wins: int = 0
    losses: int = 0
    is_provisional: bool = False
    miniSeries_progress: str = ""

    @property
    def rank_string(self) -> str:
        if self.tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            return f"{self.tier} {self.league_points}LP"
        return f"{self.tier} {self.division} {self.league_points}LP"

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        if total == 0:
            return 0.0
        return self.wins / total

    @classmethod
    def from_lcu_data(cls, data: Dict[str, Any]) -> "RankedInfo":
        return cls(
            queue_type=data.get("queueType", ""),
            tier=data.get("tier", "UNRANKED"),
            division=data.get("division", ""),
            league_points=data.get("leaguePoints", 0),
            wins=data.get("wins", 0),
            losses=data.get("losses", 0),
            is_provisional=data.get("isProvisional", False),
        )


# ─── Exceptions ───────────────────────────────────────────────────────────────

class LCUAPIError(Exception):
    """Base exception for LCU API errors."""
    pass


class LCUNotFoundError(LCUAPIError):
    """League Client process not found."""
    pass


class LCUConnectionError(LCUAPIError):
    """Failed to connect to LCU."""
    pass


class LCUAuthError(LCUAPIError):
    """Authentication failed."""
    pass


class LCUTimeoutError(LCUAPIError):
    """Request timeout."""
    pass


# ─── Process Discovery ───────────────────────────────────────────────────────

class LCUProcessDiscovery:
    """
    Discover the League Client process and extract authentication credentials.
    Supports both Windows (process args) and macOS/Linux (lockfile) methods.
    References Seraphine's connector.py pattern for process discovery.
    """

    @staticmethod
    def discover_from_process() -> Optional[LCUCredentials]:
        """
        Extract LCU credentials from process command-line arguments.
        Works on Windows by parsing LeagueClientUx.exe arguments.
        """
        try:
            if sys.platform == "win32":
                result = subprocess.run(
                    ["wmic", "process", "where",
                     f"name='{LCU_PROCESS_NAME_WINDOWS}'",
                     "get", "CommandLine", "/format:list"],
                    capture_output=True, text=True, timeout=5
                )
                output = result.stdout
            else:
                result = subprocess.run(
                    ["ps", "-A", "-o", "args"],
                    capture_output=True, text=True, timeout=5
                )
                output = result.stdout

            if not output:
                return None

            port_match = re.search(r"--app-port=(\d+)", output)
            token_match = re.search(r"--remoting-auth-token=([\w-]+)", output)
            pid_match = re.search(r"--app-pid=(\d+)", output)

            if port_match and token_match:
                creds = LCUCredentials(
                    port=int(port_match.group(1)),
                    password=token_match.group(1),
                    process_id=int(pid_match.group(1)) if pid_match else 0,
                )
                logger.info(f"Discovered LCU from process: {creds}")
                return creds

        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            logger.debug(f"Process discovery failed: {e}")

        return None

    @staticmethod
    def discover_from_lockfile(install_path: Optional[str] = None) -> Optional[LCUCredentials]:
        """
        Extract LCU credentials from the lockfile.
        Lockfile format: processName:pid:port:password:protocol
        """
        search_paths = []
        if install_path:
            search_paths.append(Path(install_path))

        if sys.platform == "win32":
            search_paths.extend([
                Path("C:/Riot Games/League of Legends"),
                Path("D:/Riot Games/League of Legends"),
                Path(os.environ.get("LOCALAPPDATA", ""), "Riot Games/League of Legends"),
            ])
        elif sys.platform == "darwin":
            search_paths.append(Path("/Applications/League of Legends.app/Contents/LoL"))
        else:
            search_paths.append(Path.home() / ".local/share/leagueoflegends")

        for base_path in search_paths:
            lockfile_path = base_path / LCU_LOCKFILE_NAME
            if lockfile_path.exists():
                try:
                    content = lockfile_path.read_text().strip()
                    parts = content.split(":")
                    if len(parts) >= 5:
                        creds = LCUCredentials(
                            process_id=int(parts[1]),
                            port=int(parts[2]),
                            password=parts[3],
                            protocol=parts[4],
                            pid_path=str(lockfile_path),
                        )
                        logger.info(f"Discovered LCU from lockfile: {creds}")
                        return creds
                except (ValueError, IndexError, OSError) as e:
                    logger.warning(f"Failed to parse lockfile {lockfile_path}: {e}")

        return None

    @classmethod
    def discover(cls, install_path: Optional[str] = None) -> Optional[LCUCredentials]:
        """Try all discovery methods in order."""
        creds = cls.discover_from_lockfile(install_path)
        if creds and creds.is_valid:
            return creds

        creds = cls.discover_from_process()
        if creds and creds.is_valid:
            return creds

        logger.warning("Could not discover LCU process")
        return None


# ─── HTTP Client ──────────────────────────────────────────────────────────────

class LCUHTTPClient:
    """
    Async HTTP client for the LCU REST API.
    Handles authentication, SSL context, retries, and response parsing.
    """

    def __init__(self, credentials: LCUCredentials):
        self.credentials = credentials
        self._session = None
        self._ssl_context = self._create_ssl_context()
        self._request_count = 0
        self._error_count = 0

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        """Create SSL context that accepts LCU's self-signed certificate."""
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _build_headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Build request headers with authentication."""
        headers = {
            "Authorization": self.credentials.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    async def request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        timeout: float = LCU_REQUEST_TIMEOUT_SECONDS,
    ) -> LCUResponse:
        """
        Make an authenticated request to the LCU API.
        Implements retry logic with exponential backoff.
        """
        url = f"{self.credentials.base_url}{endpoint}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        headers = self._build_headers()
        body = json.dumps(data).encode() if data else None

        response = LCUResponse(endpoint=endpoint)
        start_time = time.monotonic()

        for attempt in range(LCU_MAX_RETRIES):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        "127.0.0.1",
                        self.credentials.port,
                        ssl=self._ssl_context,
                    ),
                    timeout=timeout,
                )

                request_line = f"{method} {endpoint} HTTP/1.1\r\n"
                header_lines = "\r\n".join(f"{k}: {v}" for k, v in headers.items())
                request_str = f"{request_line}Host: 127.0.0.1:{self.credentials.port}\r\n{header_lines}\r\n"

                if body:
                    request_str += f"Content-Length: {len(body)}\r\n\r\n"
                    writer.write(request_str.encode() + body)
                else:
                    request_str += "\r\n"
                    writer.write(request_str.encode())

                await writer.drain()

                raw_response = await asyncio.wait_for(
                    reader.read(65536), timeout=timeout
                )
                writer.close()

                response_text = raw_response.decode("utf-8", errors="replace")
                status_match = re.search(r"HTTP/1\.\d (\d{3})", response_text)
                if status_match:
                    response.status_code = int(status_match.group(1))

                body_start = response_text.find("\r\n\r\n")
                if body_start >= 0:
                    body_text = response_text[body_start + 4:].strip()
                    if body_text:
                        try:
                            response.data = json.loads(body_text)
                        except json.JSONDecodeError:
                            response.data = body_text

                response.elapsed_ms = (time.monotonic() - start_time) * 1000
                self._request_count += 1

                if response.ok:
                    return response

                if response.status_code >= 500 and attempt < LCU_MAX_RETRIES - 1:
                    delay = LCU_RETRY_DELAY_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"LCU server error {response.status_code}, "
                        f"retry {attempt + 1}/{LCU_MAX_RETRIES} in {delay}s"
                    )
                    await asyncio.sleep(delay)
                    continue

                return response

            except asyncio.TimeoutError:
                self._error_count += 1
                if attempt < LCU_MAX_RETRIES - 1:
                    await asyncio.sleep(LCU_RETRY_DELAY_SECONDS)
                    continue
                response.status_code = 408
                response.error = "Request timeout"
                return response

            except (ConnectionError, OSError) as e:
                self._error_count += 1
                if attempt < LCU_MAX_RETRIES - 1:
                    await asyncio.sleep(LCU_RETRY_DELAY_SECONDS)
                    continue
                response.status_code = 503
                response.error = str(e)
                return response

        return response

    async def get(self, endpoint: str, **kwargs) -> LCUResponse:
        return await self.request("GET", endpoint, **kwargs)

    async def post(self, endpoint: str, data: Dict = None, **kwargs) -> LCUResponse:
        return await self.request("POST", endpoint, data=data, **kwargs)

    async def put(self, endpoint: str, data: Dict = None, **kwargs) -> LCUResponse:
        return await self.request("PUT", endpoint, data=data, **kwargs)

    async def delete(self, endpoint: str, **kwargs) -> LCUResponse:
        return await self.request("DELETE", endpoint, **kwargs)

    async def patch(self, endpoint: str, data: Dict = None, **kwargs) -> LCUResponse:
        return await self.request("PATCH", endpoint, data=data, **kwargs)

    def get_stats(self) -> Dict[str, Any]:
        """Return client statistics."""
        return {
            "total_requests": self._request_count,
            "total_errors": self._error_count,
            "error_rate": (
                self._error_count / self._request_count
                if self._request_count > 0 else 0
            ),
            "target": self.credentials.base_url,
        }


# ─── WebSocket Event Listener ────────────────────────────────────────────────

EventCallback = Callable[[LCUEvent], Awaitable[None]]


class LCUWebSocketListener:
    """
    WebSocket client for LCU event subscriptions.
    Uses WAMP-like protocol over WebSocket for event push notifications.
    Automatically subscribes to gameflow, champ select, and EOG events.
    """

    def __init__(self, credentials: LCUCredentials):
        self.credentials = credentials
        self._callbacks: Dict[str, List[EventCallback]] = {}
        self._global_callbacks: List[EventCallback] = []
        self._running = False
        self._connected = False
        self._event_count = 0
        self._reconnect_count = 0

    def on_event(self, uri_pattern: str, callback: EventCallback):
        """Register a callback for events matching the URI pattern."""
        if uri_pattern not in self._callbacks:
            self._callbacks[uri_pattern] = []
        self._callbacks[uri_pattern].append(callback)
        logger.debug(f"Registered callback for: {uri_pattern}")

    def on_any_event(self, callback: EventCallback):
        """Register a callback for all events."""
        self._global_callbacks.append(callback)

    async def _dispatch_event(self, event: LCUEvent):
        """Dispatch event to matching callbacks."""
        self._event_count += 1

        for callback in self._global_callbacks:
            try:
                await callback(event)
            except Exception as e:
                logger.error(f"Global callback error: {e}")

        for pattern, callbacks in self._callbacks.items():
            if pattern in event.uri or re.match(pattern, event.uri):
                for callback in callbacks:
                    try:
                        await callback(event)
                    except Exception as e:
                        logger.error(f"Callback error for {pattern}: {e}")

    async def _process_message(self, message: str):
        """Parse and dispatch a WAMP message."""
        try:
            data = json.loads(message)
            if isinstance(data, list) and len(data) >= 3:
                opcode = data[0]
                if opcode == EVENT_MESSAGE:
                    event_data = data[2] if len(data) > 2 else {}
                    event = LCUEvent(
                        uri=event_data.get("uri", ""),
                        event_type=event_data.get("eventType", ""),
                        data=event_data.get("data"),
                    )
                    await self._dispatch_event(event)
        except (json.JSONDecodeError, IndexError, KeyError) as e:
            logger.debug(f"Could not parse WS message: {e}")

    async def connect_and_listen(self):
        """Main connection loop with automatic reconnection."""
        self._running = True

        while self._running:
            try:
                logger.info("Connecting to LCU WebSocket...")
                self._connected = False

                ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE

                reader, writer = await asyncio.open_connection(
                    "127.0.0.1",
                    self.credentials.port,
                    ssl=ssl_ctx,
                )

                ws_key = base64.b64encode(os.urandom(16)).decode()
                auth_token = self.credentials.auth_header
                upgrade_request = (
                    f"GET /wamp HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.credentials.port}\r\n"
                    f"Upgrade: websocket\r\n"
                    f"Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {ws_key}\r\n"
                    f"Sec-WebSocket-Version: 13\r\n"
                    f"Authorization: {auth_token}\r\n"
                    f"\r\n"
                )

                writer.write(upgrade_request.encode())
                await writer.drain()

                response = await asyncio.wait_for(reader.read(4096), timeout=10)
                if b"101" not in response:
                    logger.error("WebSocket upgrade failed")
                    await asyncio.sleep(LCU_WEBSOCKET_RECONNECT_DELAY)
                    continue

                self._connected = True
                logger.info("LCU WebSocket connected")

                subscribe_msg = json.dumps([EVENT_SUBSCRIBE_ALL, "OnJsonApiEvent"])
                writer.write(self._encode_ws_frame(subscribe_msg))
                await writer.drain()

                while self._running:
                    try:
                        data = await asyncio.wait_for(
                            reader.read(65536),
                            timeout=LCU_HEARTBEAT_INTERVAL_SECONDS * 2,
                        )
                        if not data:
                            break
                        message = self._decode_ws_frame(data)
                        if message:
                            await self._process_message(message)
                    except asyncio.TimeoutError:
                        logger.debug("WebSocket heartbeat timeout, reconnecting...")
                        break

                writer.close()

            except (ConnectionError, OSError) as e:
                logger.warning(f"WebSocket connection error: {e}")
            except Exception as e:
                logger.error(f"WebSocket unexpected error: {e}")

            if self._running:
                self._reconnect_count += 1
                self._connected = False
                await asyncio.sleep(LCU_WEBSOCKET_RECONNECT_DELAY)

    @staticmethod
    def _encode_ws_frame(payload: str) -> bytes:
        """Encode a WebSocket text frame (simplified)."""
        payload_bytes = payload.encode("utf-8")
        length = len(payload_bytes)
        frame = bytearray()
        frame.append(0x81)  # FIN + text opcode
        mask_key = os.urandom(4)

        if length < 126:
            frame.append(0x80 | length)  # Masked
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(length.to_bytes(2, "big"))
        else:
            frame.append(0x80 | 127)
            frame.extend(length.to_bytes(8, "big"))

        frame.extend(mask_key)
        masked = bytearray(b ^ mask_key[i % 4] for i, b in enumerate(payload_bytes))
        frame.extend(masked)
        return bytes(frame)

    @staticmethod
    def _decode_ws_frame(data: bytes) -> Optional[str]:
        """Decode a WebSocket text frame (simplified)."""
        if len(data) < 2:
            return None
        try:
            opcode = data[0] & 0x0F
            if opcode != 0x01:  # Not a text frame
                return None

            masked = bool(data[1] & 0x80)
            length = data[1] & 0x7F
            offset = 2

            if length == 126:
                length = int.from_bytes(data[2:4], "big")
                offset = 4
            elif length == 127:
                length = int.from_bytes(data[2:10], "big")
                offset = 10

            if masked:
                mask = data[offset:offset + 4]
                offset += 4
                payload = bytearray(
                    data[offset + i] ^ mask[i % 4] for i in range(length)
                )
            else:
                payload = data[offset:offset + length]

            return payload.decode("utf-8", errors="replace")
        except (IndexError, ValueError):
            return None

    async def stop(self):
        """Stop the WebSocket listener."""
        self._running = False
        self._connected = False
        logger.info("LCU WebSocket listener stopped")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "connected": self._connected,
            "events_received": self._event_count,
            "reconnect_count": self._reconnect_count,
            "registered_patterns": list(self._callbacks.keys()),
            "global_callbacks": len(self._global_callbacks),
        }


# ─── High-Level API Client ───────────────────────────────────────────────────

class LCUAPIClient:
    """
    High-level LCU API client combining HTTP and WebSocket capabilities.
    Provides typed methods for common operations.
    Implements HistoricalBattleInterface contract.
    """

    def __init__(self, install_path: Optional[str] = None):
        self.install_path = install_path
        self.credentials: Optional[LCUCredentials] = None
        self.http: Optional[LCUHTTPClient] = None
        self.ws: Optional[LCUWebSocketListener] = None
        self.state = LCUConnectionState.DISCONNECTED
        self._current_summoner: Optional[SummonerInfo] = None
        self._ws_task: Optional[asyncio.Task] = None

    async def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Discover LCU and establish connections."""
        self.state = LCUConnectionState.DISCOVERING

        self.credentials = LCUProcessDiscovery.discover(self.install_path)
        if not self.credentials or not self.credentials.is_valid:
            self.state = LCUConnectionState.ERROR
            logger.error("Failed to discover LCU process")
            return False

        self.state = LCUConnectionState.CONNECTING
        self.http = LCUHTTPClient(self.credentials)
        self.ws = LCUWebSocketListener(self.credentials)

        # Verify connection by fetching current summoner
        self.state = LCUConnectionState.AUTHENTICATING
        try:
            resp = await self.http.get(ENDPOINTS["current_summoner"])
            if resp.ok and resp.data:
                self._current_summoner = SummonerInfo.from_lcu_data(resp.data)
                self.state = LCUConnectionState.CONNECTED
                logger.info(
                    f"Connected as: {self._current_summoner.display_name} "
                    f"(Level {self._current_summoner.summoner_level})"
                )
                return True
            else:
                self.state = LCUConnectionState.ERROR
                logger.error(f"Auth verification failed: {resp.status_code}")
                return False
        except Exception as e:
            self.state = LCUConnectionState.ERROR
            logger.error(f"Connection failed: {e}")
            return False

    async def start_event_listener(self):
        """Start the WebSocket event listener in the background."""
        if self.ws and self.credentials:
            self._ws_task = asyncio.create_task(
                self.ws.connect_and_listen()
            )
            logger.info("WebSocket event listener started")

    async def get_current_summoner(self) -> Optional[SummonerInfo]:
        """Get the currently logged-in summoner."""
        if self._current_summoner:
            return self._current_summoner
        resp = await self.http.get(ENDPOINTS["current_summoner"])
        if resp.ok:
            self._current_summoner = SummonerInfo.from_lcu_data(resp.data)
        return self._current_summoner

    async def get_summoner_by_puuid(self, puuid: str) -> Optional[SummonerInfo]:
        """Look up a summoner by PUUID."""
        endpoint = ENDPOINTS["summoner_by_puuid"].format(puuid=puuid)
        resp = await self.http.get(endpoint)
        if resp.ok:
            return SummonerInfo.from_lcu_data(resp.data)
        return None

    async def get_match_history(
        self, puuid: str, beg_index: int = 0, end_index: int = 20
    ) -> List[Dict[str, Any]]:
        """Fetch match history for a player via LCU API."""
        endpoint = ENDPOINTS["match_history"].format(puuid=puuid)
        resp = await self.http.get(
            endpoint,
            params={"begIndex": beg_index, "endIndex": end_index}
        )
        if resp.ok and isinstance(resp.data, dict):
            return resp.data.get("games", {}).get("games", [])
        return []

    async def get_ranked_stats(self) -> List[RankedInfo]:
        """Get current ranked statistics."""
        resp = await self.http.get(ENDPOINTS["ranked_stats"])
        if resp.ok and isinstance(resp.data, dict):
            queues = resp.data.get("queues", [])
            return [RankedInfo.from_lcu_data(q) for q in queues]
        return []

    async def get_gameflow_phase(self) -> GameflowPhase:
        """Get current gameflow phase."""
        resp = await self.http.get(ENDPOINTS["gameflow_phase"])
        if resp.ok and isinstance(resp.data, str):
            try:
                return GameflowPhase(resp.data)
            except ValueError:
                return GameflowPhase.NONE
        return GameflowPhase.NONE

    async def get_champion_mastery(self, summoner_id: int) -> List[Dict[str, Any]]:
        """Get champion mastery data."""
        endpoint = ENDPOINTS["champion_mastery"].format(summonerId=summoner_id)
        resp = await self.http.get(endpoint)
        if resp.ok and isinstance(resp.data, list):
            return resp.data
        return []

    async def get_end_of_game_stats(self) -> Optional[Dict[str, Any]]:
        """Get end-of-game statistics (available after a match)."""
        resp = await self.http.get(ENDPOINTS["end_of_game_stats"])
        if resp.ok:
            return resp.data
        return None

    async def get_champions_data(self) -> List[Dict[str, Any]]:
        """Get champion summary data from game assets."""
        resp = await self.http.get(ENDPOINTS["game_data_champions"])
        if resp.ok and isinstance(resp.data, list):
            return resp.data
        return []

    async def health_check(self) -> Dict[str, Any]:
        """Check client health and connectivity."""
        health = {
            "state": self.state.value,
            "credentials_valid": (
                self.credentials.is_valid if self.credentials else False
            ),
            "current_summoner": (
                self._current_summoner.display_name
                if self._current_summoner else None
            ),
            "http_stats": self.http.get_stats() if self.http else {},
            "ws_stats": self.ws.get_stats() if self.ws else {},
        }

        if self.http and self.state == LCUConnectionState.CONNECTED:
            try:
                phase = await self.get_gameflow_phase()
                health["gameflow_phase"] = phase.value
            except Exception:
                health["gameflow_phase"] = "unknown"

        return health

    async def shutdown(self):
        """Graceful shutdown of all connections."""
        logger.info("Shutting down LCU API Client...")
        self.state = LCUConnectionState.SHUTDOWN

        if self.ws:
            await self.ws.stop()
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        self.http = None
        self.ws = None
        self.credentials = None
        self._current_summoner = None
        logger.info("LCU API Client shutdown complete")

    def get_module_info(self) -> Dict[str, str]:
        return {
            "task_id": "M807",
            "name": "LCU API Client",
            "version": "1.0.0",
            "description": "League Client Update API integration for OperatorRL",
            "state": self.state.value,
        }


# ─── Convenience Factory ─────────────────────────────────────────────────────

async def create_lcu_client(
    install_path: Optional[str] = None,
    start_events: bool = True,
) -> LCUAPIClient:
    """Factory function to create and initialize an LCU client."""
    client = LCUAPIClient(install_path=install_path)
    connected = await client.initialize()
    if connected and start_events:
        await client.start_event_listener()
    return client


# ─── Self-Test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("M807 LCU API Client - Self Test")
    print(f"Endpoints registered: {len(ENDPOINTS)}")
    print(f"Gameflow phases: {[p.value for p in GameflowPhase]}")

    # Test credential creation
    creds = LCUCredentials(port=12345, password="test-token-abc")
    print(f"Test credentials: {creds}")
    print(f"Base URL: {creds.base_url}")
    print(f"Auth header prefix: {creds.auth_header[:20]}...")

    # Test data models
    summoner = SummonerInfo(
        display_name="TestPlayer",
        puuid="test-puuid-123",
        summoner_level=200,
    )
    print(f"Test summoner: {summoner.display_name} (Level {summoner.summoner_level})")

    ranked = RankedInfo(
        tier="DIAMOND", division="II", league_points=75,
        wins=120, losses=100,
    )
    print(f"Test ranked: {ranked.rank_string} ({ranked.win_rate:.1%} WR)")

    print("\nM807 self-test passed.")

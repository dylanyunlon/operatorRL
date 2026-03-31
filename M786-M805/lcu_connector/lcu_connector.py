#!/usr/bin/env python3
"""
M788: LCU Connector
=====================
查看 Seraphine 上现有 LCU API 连接器的实现方式,理解其模式,
特别是 lockfile 认证和 WebSocket 事件订阅是如何分离的。
从 lcu-driver/lcu-sharp 连接模式这个好例子开始。
然后,遵循该模式实现一个新的 LCUConnector,
让 OperatorRL 可以自动发现并连接英雄联盟客户端,并能监听游戏状态变化。
接着引入 WebSocketEventBus,使所有下游模块能够订阅感兴趣的事件,
同时优化连接重试和心跳检测机制。
随后整合 RequestPool,令系统支持并发API请求,
进而增强 EventRouter 的事件分发效率。
最终完善 ConnectionManager,确保连接状态兼容所有游戏阶段,
全面升级连接管理以达成生产级别的稳定性。
"""

import os
import sys
import json
import time
import ssl
import struct
import base64
import hashlib
import socket
import threading
import http.client
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urljoin, urlencode, quote
import logging
import re

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from logging_system.core_logger import get_logger, EventCategory
except ImportError:
    get_logger = lambda x: None
    EventCategory = type('E', (), {'LCU_API': 'lcu_api', 'SYSTEM': 'system', 'NETWORK': 'network'})()


# ============================================================================
# Constants
# ============================================================================

LOCKFILE_NAME = "lockfile"
RIOT_GAMES_PATHS = {
    "windows": [
        r"C:\Riot Games\League of Legends",
        r"D:\Riot Games\League of Legends",
        r"C:\Program Files\Riot Games\League of Legends",
        r"C:\Program Files (x86)\Riot Games\League of Legends",
    ],
    "wsl": [
        "/mnt/c/Riot Games/League of Legends",
        "/mnt/d/Riot Games/League of Legends",
    ],
    "macos": [
        "/Applications/League of Legends.app/Contents/LoL",
    ],
}

GAME_FLOW_PHASES = {
    "None": "无状态",
    "Lobby": "房间大厅",
    "Matchmaking": "匹配中",
    "CheckedIntoTournament": "锦标赛签到",
    "ReadyCheck": "准备确认",
    "ChampSelect": "英雄选择",
    "GameStart": "游戏开始",
    "FailedToLaunch": "启动失败",
    "InProgress": "游戏进行中",
    "Reconnect": "重新连接",
    "WaitingForStats": "等待统计",
    "PreEndOfGame": "游戏即将结束",
    "EndOfGame": "游戏结束",
    "TerminatedInError": "异常终止",
}

WEBSOCKET_EVENTS = {
    "game_flow": "/lol-gameflow/v1/gameflow-phase",
    "champ_select": "/lol-champ-select/v1/session",
    "lobby": "/lol-lobby/v2/lobby",
    "matchmaking": "/lol-matchmaking/v1/search",
    "end_of_game": "/lol-end-of-game/v1/eog-stats-block",
    "summoner": "/lol-summoner/v1/current-summoner",
    "ranked": "/lol-ranked/v1/current-ranked-stats",
    "chat": "/lol-chat/v1/conversations",
    "friends": "/lol-chat/v1/friends",
    "patcher": "/lol-patch/v1/products/league_of_legends/state",
}

MAX_RETRY_ATTEMPTS = 30
RETRY_INTERVAL_SECONDS = 2
HEARTBEAT_INTERVAL = 10
CONNECTION_TIMEOUT = 5


# ============================================================================
# Data Models
# ============================================================================

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    DISCOVERING = "discovering"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATED = "authenticated"
    ERROR = "error"
    RECONNECTING = "reconnecting"


@dataclass
class LockfileData:
    """Parsed lockfile content from League Client."""
    process_name: str
    process_id: int
    port: int
    password: str
    protocol: str

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def auth_header(self) -> str:
        token = base64.b64encode(f"riot:{self.password}".encode()).decode()
        return f"Basic {token}"

    @classmethod
    def from_lockfile(cls, content: str) -> 'LockfileData':
        parts = content.strip().split(":")
        if len(parts) != 5:
            raise ValueError(f"Invalid lockfile format: expected 5 parts, got {len(parts)}")
        return cls(
            process_name=parts[0],
            process_id=int(parts[1]),
            port=int(parts[2]),
            password=parts[3],
            protocol=parts[4],
        )


@dataclass
class WebSocketEvent:
    """Structured WebSocket event from LCU."""
    event_type: str  # Create, Update, Delete
    uri: str
    data: Any
    timestamp: str = ""
    correlation_id: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "uri": self.uri,
            "data": self.data,
            "timestamp": self.timestamp,
        }


@dataclass
class ConnectionMetrics:
    """Connection health metrics."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_events: int = 0
    reconnections: int = 0
    avg_latency_ms: float = 0.0
    last_request_time: str = ""
    last_event_time: str = ""
    uptime_seconds: float = 0.0
    _latencies: List[float] = field(default_factory=list)

    def record_request(self, latency_ms: float, success: bool):
        self.total_requests += 1
        if success:
            self.successful_requests += 1
        else:
            self.failed_requests += 1
        self._latencies.append(latency_ms)
        if len(self._latencies) > 1000:
            self._latencies = self._latencies[-500:]
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)
        self.last_request_time = datetime.now(timezone.utc).isoformat()

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 100.0
        return self.successful_requests / self.total_requests * 100


# ============================================================================
# Event Bus
# ============================================================================

EventCallback = Callable[[WebSocketEvent], None]


class WebSocketEventBus:
    """
    Event bus for distributing LCU WebSocket events to subscribers.
    Supports topic-based subscription, wildcard matching,
    and asynchronous delivery.
    """

    def __init__(self):
        self._subscribers: Dict[str, List[EventCallback]] = defaultdict(list)
        self._wildcard_subscribers: List[Tuple[str, EventCallback]] = []
        self._lock = threading.Lock()
        self._event_count = 0
        self._event_history: List[WebSocketEvent] = []
        self._max_history = 500

    def subscribe(self, uri: str, callback: EventCallback) -> None:
        with self._lock:
            if "*" in uri:
                self._wildcard_subscribers.append((uri, callback))
            else:
                self._subscribers[uri].append(callback)

    def unsubscribe(self, uri: str, callback: EventCallback) -> bool:
        with self._lock:
            if "*" in uri:
                self._wildcard_subscribers = [
                    (u, cb) for u, cb in self._wildcard_subscribers
                    if not (u == uri and cb == callback)
                ]
                return True
            if uri in self._subscribers:
                try:
                    self._subscribers[uri].remove(callback)
                    return True
                except ValueError:
                    return False
            return False

    def publish(self, event: WebSocketEvent) -> int:
        with self._lock:
            self._event_count += 1
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]

        delivered = 0
        # Exact match subscribers
        for callback in self._subscribers.get(event.uri, []):
            try:
                callback(event)
                delivered += 1
            except Exception as e:
                logging.error(f"Event callback error for {event.uri}: {e}")

        # Wildcard subscribers
        for pattern, callback in self._wildcard_subscribers:
            if self._matches_pattern(pattern, event.uri):
                try:
                    callback(event)
                    delivered += 1
                except Exception as e:
                    logging.error(f"Wildcard callback error for {pattern}: {e}")

        return delivered

    @staticmethod
    def _matches_pattern(pattern: str, uri: str) -> bool:
        regex = pattern.replace("*", ".*")
        return bool(re.match(regex, uri))

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_events": self._event_count,
                "active_subscriptions": sum(
                    len(cbs) for cbs in self._subscribers.values()
                ),
                "wildcard_subscriptions": len(self._wildcard_subscribers),
                "history_size": len(self._event_history),
                "topics": list(self._subscribers.keys()),
            }


# ============================================================================
# Request Pool
# ============================================================================

class LCURequestPool:
    """
    Connection pool for LCU HTTP requests.
    Manages SSL context, authentication, and request rate limiting.
    """

    def __init__(self, lockfile: LockfileData, max_connections: int = 5):
        self.lockfile = lockfile
        self.max_connections = max_connections
        self._ssl_context = self._create_ssl_context()
        self._semaphore = threading.Semaphore(max_connections)
        self._metrics = ConnectionMetrics()
        self._lock = threading.Lock()

    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def request(self, method: str, endpoint: str,
                params: Optional[Dict] = None,
                body: Optional[Dict] = None,
                timeout: int = CONNECTION_TIMEOUT) -> Dict[str, Any]:
        """Execute an HTTP request to the LCU API."""
        url = endpoint
        if params:
            url = f"{endpoint}?{urlencode(params)}"

        headers = {
            "Authorization": self.lockfile.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        self._semaphore.acquire()
        start = time.monotonic()
        try:
            conn = http.client.HTTPSConnection(
                "127.0.0.1",
                self.lockfile.port,
                context=self._ssl_context,
                timeout=timeout
            )

            body_str = json.dumps(body) if body else None
            conn.request(method, url, body=body_str, headers=headers)
            response = conn.getresponse()
            data = response.read().decode('utf-8')

            latency = (time.monotonic() - start) * 1000
            success = 200 <= response.status < 300

            with self._lock:
                self._metrics.record_request(latency, success)

            result = {
                "status": response.status,
                "headers": dict(response.getheaders()),
                "body": json.loads(data) if data else None,
                "latency_ms": round(latency, 2),
                "success": success,
            }

            conn.close()
            return result

        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            with self._lock:
                self._metrics.record_request(latency, False)
            return {
                "status": 0,
                "error": str(e),
                "error_type": type(e).__name__,
                "latency_ms": round(latency, 2),
                "success": False,
            }
        finally:
            self._semaphore.release()

    def get(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        return self.request("GET", endpoint, **kwargs)

    def post(self, endpoint: str, body: Dict = None, **kwargs) -> Dict[str, Any]:
        return self.request("POST", endpoint, body=body, **kwargs)

    def put(self, endpoint: str, body: Dict = None, **kwargs) -> Dict[str, Any]:
        return self.request("PUT", endpoint, body=body, **kwargs)

    def delete(self, endpoint: str, **kwargs) -> Dict[str, Any]:
        return self.request("DELETE", endpoint, **kwargs)

    @property
    def metrics(self) -> ConnectionMetrics:
        return self._metrics


# ============================================================================
# LCU Connector
# ============================================================================

class LCUConnector:
    """
    Main LCU connection manager. Handles:
    1. Lockfile discovery (auto-detect League client)
    2. Authentication via riot: credentials
    3. WebSocket event streaming
    4. Connection lifecycle management
    5. Automatic reconnection
    """

    def __init__(self):
        self._state = ConnectionState.DISCONNECTED
        self._lockfile: Optional[LockfileData] = None
        self._request_pool: Optional[LCURequestPool] = None
        self._event_bus = WebSocketEventBus()
        self._metrics = ConnectionMetrics()
        self._lock = threading.Lock()
        self._connected_event = threading.Event()
        self._shutdown_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._connect_time: Optional[float] = None
        self._game_flow_phase = "None"
        self._current_summoner: Optional[Dict] = None
        self._logger = get_logger("M788") if get_logger("M788") else None

    def _log(self, level: str, message: str, **kwargs):
        if self._logger:
            cat = kwargs.pop('category', EventCategory.LCU_API if hasattr(EventCategory, 'LCU_API') else None)
            if cat:
                kwargs['category'] = cat
            getattr(self._logger, level)(message, **kwargs)

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED)

    @property
    def event_bus(self) -> WebSocketEventBus:
        return self._event_bus

    # ----- Lockfile Discovery -----

    def discover_lockfile(self) -> Optional[LockfileData]:
        """Auto-discover League client lockfile."""
        self._state = ConnectionState.DISCOVERING
        self._log("info", "Discovering League client lockfile...")

        # Try process-based discovery first (Windows)
        lockfile = self._discover_via_process()
        if lockfile:
            return lockfile

        # Fallback to path-based discovery
        lockfile = self._discover_via_path()
        if lockfile:
            return lockfile

        self._log("warn", "League client not found")
        return None

    def _discover_via_process(self) -> Optional[LockfileData]:
        """Discover lockfile by checking running processes."""
        try:
            import subprocess
            # Windows: wmic / tasklist
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq LeagueClientUx.exe",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5
            )
            if "LeagueClientUx.exe" in result.stdout:
                # Parse command line for --app-port and --remoting-auth-token
                cmd_result = subprocess.run(
                    ["wmic", "process", "where",
                     "name='LeagueClientUx.exe'", "get", "commandline"],
                    capture_output=True, text=True, timeout=5
                )
                port_match = re.search(r'--app-port=(\d+)', cmd_result.stdout)
                token_match = re.search(r'--remoting-auth-token=([\w-]+)',
                                        cmd_result.stdout)
                if port_match and token_match:
                    return LockfileData(
                        process_name="LeagueClientUx",
                        process_id=0,
                        port=int(port_match.group(1)),
                        password=token_match.group(1),
                        protocol="https"
                    )
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            pass
        return None

    def _discover_via_path(self) -> Optional[LockfileData]:
        """Discover lockfile by checking known installation paths."""
        platform_key = "windows"
        if sys.platform == "darwin":
            platform_key = "macos"
        elif "microsoft" in os.uname().release.lower() if hasattr(os, 'uname') else False:
            platform_key = "wsl"

        paths = RIOT_GAMES_PATHS.get(platform_key, [])
        for base_path in paths:
            lockfile_path = Path(base_path) / LOCKFILE_NAME
            if lockfile_path.exists():
                try:
                    content = lockfile_path.read_text().strip()
                    lockfile = LockfileData.from_lockfile(content)
                    self._log("info", f"Lockfile found at {lockfile_path}",
                              data={"port": lockfile.port})
                    return lockfile
                except (ValueError, IOError) as e:
                    self._log("warn", f"Invalid lockfile at {lockfile_path}: {e}")
                    continue
        return None

    # ----- Connection Management -----

    def connect(self, lockfile: Optional[LockfileData] = None,
                auto_discover: bool = True,
                max_retries: int = MAX_RETRY_ATTEMPTS) -> bool:
        """Establish connection to League client."""
        self._state = ConnectionState.CONNECTING
        self._log("info", "Initiating LCU connection...")

        if lockfile:
            self._lockfile = lockfile
        elif auto_discover:
            for attempt in range(max_retries):
                self._lockfile = self.discover_lockfile()
                if self._lockfile:
                    break
                self._log("debug", f"Discovery attempt {attempt + 1}/{max_retries}")
                if self._shutdown_event.wait(RETRY_INTERVAL_SECONDS):
                    return False
        else:
            self._log("error", "No lockfile provided and auto_discover disabled")
            self._state = ConnectionState.ERROR
            return False

        if not self._lockfile:
            self._log("error", "Failed to discover lockfile after retries")
            self._state = ConnectionState.ERROR
            return False

        # Create request pool
        self._request_pool = LCURequestPool(self._lockfile)

        # Verify connection
        if self._verify_connection():
            self._state = ConnectionState.AUTHENTICATED
            self._connect_time = time.monotonic()
            self._connected_event.set()
            self._start_heartbeat()
            self._log("info", "LCU connection established",
                      data={
                          "port": self._lockfile.port,
                          "process": self._lockfile.process_name,
                      })
            return True
        else:
            self._state = ConnectionState.ERROR
            self._log("error", "Connection verification failed")
            return False

    def _verify_connection(self) -> bool:
        """Verify connection by fetching current summoner."""
        if not self._request_pool:
            return False
        try:
            result = self._request_pool.get("/lol-summoner/v1/current-summoner")
            if result.get("success"):
                self._current_summoner = result.get("body")
                return True
            return False
        except Exception:
            return False

    def disconnect(self) -> None:
        """Gracefully disconnect from League client."""
        self._shutdown_event.set()
        self._connected_event.clear()
        self._state = ConnectionState.DISCONNECTED
        self._log("info", "LCU connection closed",
                  data={"uptime": self.uptime_seconds})

    @property
    def uptime_seconds(self) -> float:
        if self._connect_time:
            return round(time.monotonic() - self._connect_time, 2)
        return 0.0

    # ----- Heartbeat -----

    def _start_heartbeat(self) -> None:
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="LCU-Heartbeat"
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                result = self._request_pool.get("/lol-gameflow/v1/gameflow-phase")
                if result.get("success"):
                    phase = result.get("body", "None")
                    if isinstance(phase, str) and phase != self._game_flow_phase:
                        old_phase = self._game_flow_phase
                        self._game_flow_phase = phase
                        self._event_bus.publish(WebSocketEvent(
                            event_type="Update",
                            uri="/lol-gameflow/v1/gameflow-phase",
                            data={"old": old_phase, "new": phase},
                        ))
                        self._log("info", f"Game flow: {old_phase} → {phase}",
                                  data={"old_phase": old_phase, "new_phase": phase})
                else:
                    self._handle_connection_loss()
            except Exception as e:
                self._log("warn", f"Heartbeat error: {e}")
                self._handle_connection_loss()

            self._shutdown_event.wait(HEARTBEAT_INTERVAL)

    def _handle_connection_loss(self) -> None:
        if self._state == ConnectionState.RECONNECTING:
            return
        self._state = ConnectionState.RECONNECTING
        self._metrics.reconnections += 1
        self._log("warn", "Connection lost, attempting reconnect...",
                  data={"reconnections": self._metrics.reconnections})

        for attempt in range(5):
            if self._shutdown_event.is_set():
                return
            time.sleep(RETRY_INTERVAL_SECONDS)
            if self._verify_connection():
                self._state = ConnectionState.AUTHENTICATED
                self._log("info", f"Reconnected after {attempt + 1} attempts")
                return

        self._state = ConnectionState.ERROR
        self._log("error", "Failed to reconnect after 5 attempts")

    # ----- API Methods -----

    def get_current_summoner(self) -> Optional[Dict]:
        if not self.is_connected:
            return None
        result = self._request_pool.get("/lol-summoner/v1/current-summoner")
        if result.get("success"):
            self._current_summoner = result.get("body")
            return self._current_summoner
        return None

    def get_match_history(self, puuid: str,
                          begin: int = 0, end: int = 20) -> Optional[Dict]:
        if not self.is_connected:
            return None
        endpoint = f"/lol-match-history/v1/products/lol/{puuid}/matches"
        result = self._request_pool.get(
            endpoint, params={"begIndex": begin, "endIndex": end}
        )
        return result.get("body") if result.get("success") else None

    def get_game_flow_phase(self) -> str:
        return self._game_flow_phase

    def get_champ_select_session(self) -> Optional[Dict]:
        if not self.is_connected:
            return None
        result = self._request_pool.get("/lol-champ-select/v1/session")
        return result.get("body") if result.get("success") else None

    def get_ranked_stats(self, puuid: str) -> Optional[Dict]:
        if not self.is_connected:
            return None
        result = self._request_pool.get(f"/lol-ranked/v1/ranked-stats/{puuid}")
        return result.get("body") if result.get("success") else None

    def get_end_of_game_stats(self) -> Optional[Dict]:
        if not self.is_connected:
            return None
        result = self._request_pool.get("/lol-end-of-game/v1/eog-stats-block")
        return result.get("body") if result.get("success") else None

    # ----- Status -----

    def get_connection_status(self) -> Dict[str, Any]:
        return {
            "state": self._state.value,
            "is_connected": self.is_connected,
            "uptime_seconds": self.uptime_seconds,
            "game_flow_phase": self._game_flow_phase,
            "game_flow_display": GAME_FLOW_PHASES.get(
                self._game_flow_phase, self._game_flow_phase
            ),
            "current_summoner": self._current_summoner.get("displayName")
            if self._current_summoner else None,
            "lockfile": {
                "port": self._lockfile.port,
                "protocol": self._lockfile.protocol,
            } if self._lockfile else None,
            "metrics": {
                "total_requests": self._metrics.total_requests,
                "success_rate": round(self._metrics.success_rate, 2),
                "avg_latency_ms": round(self._metrics.avg_latency_ms, 2),
                "reconnections": self._metrics.reconnections,
            },
            "event_bus": self._event_bus.get_stats(),
        }


# ============================================================================
# Module Self-Test
# ============================================================================

def self_test() -> Dict[str, Any]:
    results = {"module": "M788", "name": "lcu_connector", "tests": []}

    # Test 1: LockfileData parsing
    try:
        lf = LockfileData.from_lockfile("LeagueClientUx:12345:2999:abc123:https")
        assert lf.port == 2999
        assert lf.password == "abc123"
        assert "Basic" in lf.auth_header
        assert lf.base_url == "https://127.0.0.1:2999"
        results["tests"].append({"name": "lockfile_parse", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "lockfile_parse", "status": "fail", "error": str(e)})

    # Test 2: WebSocketEventBus
    try:
        bus = WebSocketEventBus()
        received = []
        bus.subscribe("/test/event", lambda e: received.append(e))
        event = WebSocketEvent("Update", "/test/event", {"key": "value"})
        delivered = bus.publish(event)
        assert delivered == 1
        assert len(received) == 1
        assert received[0].data["key"] == "value"
        results["tests"].append({"name": "event_bus", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "event_bus", "status": "fail", "error": str(e)})

    # Test 3: Wildcard subscription
    try:
        bus = WebSocketEventBus()
        received = []
        bus.subscribe("/lol-gameflow/*", lambda e: received.append(e))
        bus.publish(WebSocketEvent("Update", "/lol-gameflow/v1/phase", "InProgress"))
        assert len(received) == 1
        results["tests"].append({"name": "wildcard_sub", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "wildcard_sub", "status": "fail", "error": str(e)})

    # Test 4: ConnectionMetrics
    try:
        metrics = ConnectionMetrics()
        metrics.record_request(15.5, True)
        metrics.record_request(25.3, True)
        metrics.record_request(100.0, False)
        assert metrics.total_requests == 3
        assert metrics.successful_requests == 2
        assert metrics.failed_requests == 1
        assert 40 < metrics.avg_latency_ms < 50
        results["tests"].append({"name": "metrics", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "metrics", "status": "fail", "error": str(e)})

    # Test 5: Connector initialization
    try:
        connector = LCUConnector()
        assert connector.state == ConnectionState.DISCONNECTED
        assert not connector.is_connected
        status = connector.get_connection_status()
        assert status["state"] == "disconnected"
        results["tests"].append({"name": "connector_init", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "connector_init", "status": "fail", "error": str(e)})

    results["overall"] = "pass" if all(t["status"] == "pass" for t in results["tests"]) else "fail"
    return results


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2))

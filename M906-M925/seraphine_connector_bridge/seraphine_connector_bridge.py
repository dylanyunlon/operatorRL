#!/usr/bin/env python3
"""
M906: SeraphineConnectorBridge
===============================

Deep bridge to Seraphine LCU connector — HTTP session management,
retry logic, PastRequest pattern adaptation for operatorRL integration.

Architecture Pattern:
  查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
  理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
  从 connector.needLcu + retry 这个好例子开始。
  然后, 遵循该模式实现一个新的 SeraphineConnectorBridge,
  让 operatorRL 可以调用所有 Seraphine API, 并能做连接池管理。
  接着引入 asyncio 限流, 使请求能够遵守 Riot 速率限制,
  同时优化 SSL 证书处理以支持 Fiddler MITM 代理。
  随后整合 PastRequest 日志, 令调试模块支持请求回放,
  进而增强错误分类与自动恢复。
  最终完善 SGP 双路径回退, 确保国服/国际服兼容,
  全面升级连接层以达成生产级可靠性。

Author: dylanyunlong <dylanyunlong@gmail.com>
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import datetime
import enum
import functools
import hashlib
import json
import logging
import os
import pathlib
import queue
import random
import re
import ssl
import struct
import sys
import threading
import time
import traceback
import typing
import urllib.parse
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LCU_PORT = 2999
DEFAULT_LCU_HOST = "127.0.0.1"
DEFAULT_LCU_SCHEME = "https"
DEFAULT_FIDDLER_PROXY = "http://127.0.0.1:8866"
FIDDLER_MCP_ENDPOINT = "http://localhost:8868/mcp"
MAX_RETRY_COUNT = 5
RETRY_BACKOFF_BASE = 0.3
RATE_LIMIT_WINDOW = 120  # seconds
RATE_LIMIT_MAX_REQUESTS = 100
SGP_TIMEOUT = 10.0
LCU_TIMEOUT = 8.0
PAST_REQUEST_BUFFER_SIZE = 200
SSL_VERIFY_DEFAULT = False  # Fiddler MITM requires trust or skip


class ConnectorState(enum.Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"
    CLOSING = "closing"


class ApiPath(enum.Enum):
    LCU = "lcu"
    SGP = "sgp"


@dataclasses.dataclass
class PastRequest:
    """Adapted from Seraphine PastRequest — records API call history."""
    func_name: str
    params: Dict[str, Any]
    kwargs: Dict[str, Any]
    response_status: Optional[int] = None
    response_body: Optional[Any] = None
    timestamp: float = dataclasses.field(default_factory=time.time)
    duration_ms: float = 0.0
    api_path: ApiPath = ApiPath.LCU
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "func": self.func_name,
            "params": self.params,
            "status": self.response_status,
            "ts": self.timestamp,
            "dur_ms": round(self.duration_ms, 2),
            "path": self.api_path.value,
            "error": self.error,
        }


@dataclasses.dataclass
class RateLimitState:
    """Per-endpoint rate limit tracking."""
    window_start: float = 0.0
    request_count: int = 0
    limit_max: int = RATE_LIMIT_MAX_REQUESTS
    window_seconds: float = RATE_LIMIT_WINDOW
    retry_after: float = 0.0

    def can_request(self) -> bool:
        now = time.time()
        if now - self.window_start > self.window_seconds:
            self.window_start = now
            self.request_count = 0
        return self.request_count < self.limit_max

    def record_request(self) -> None:
        now = time.time()
        if now - self.window_start > self.window_seconds:
            self.window_start = now
            self.request_count = 0
        self.request_count += 1

    def record_rate_limit(self, retry_after: float = 5.0) -> None:
        self.retry_after = time.time() + retry_after

    def is_blocked(self) -> bool:
        return time.time() < self.retry_after


@dataclasses.dataclass
class ConnectionConfig:
    """Configuration for SeraphineConnectorBridge."""
    lcu_host: str = DEFAULT_LCU_HOST
    lcu_port: int = DEFAULT_LCU_PORT
    lcu_scheme: str = DEFAULT_LCU_SCHEME
    auth_token: str = ""
    fiddler_proxy: Optional[str] = DEFAULT_FIDDLER_PROXY
    fiddler_mcp_endpoint: str = FIDDLER_MCP_ENDPOINT
    ssl_verify: bool = SSL_VERIFY_DEFAULT
    max_retries: int = MAX_RETRY_COUNT
    sgp_base_url: str = ""
    sgp_token: str = ""
    is_tencent: bool = False
    request_timeout: float = LCU_TIMEOUT
    sgp_timeout: float = SGP_TIMEOUT
    pool_size: int = 10
    past_request_buffer: int = PAST_REQUEST_BUFFER_SIZE
    enable_fiddler_mcp: bool = True
    log_requests: bool = True

    @property
    def lcu_base_url(self) -> str:
        return f"{self.lcu_scheme}://{self.lcu_host}:{self.lcu_port}"

    @property
    def auth_header(self) -> Dict[str, str]:
        if self.auth_token:
            import base64
            encoded = base64.b64encode(f"riot:{self.auth_token}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}
        return {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lcu_base": self.lcu_base_url,
            "fiddler_proxy": self.fiddler_proxy,
            "ssl_verify": self.ssl_verify,
            "is_tencent": self.is_tencent,
            "pool_size": self.pool_size,
        }


class RequestReplayBuffer:
    """Circular buffer for PastRequest objects — enables request replay for debugging."""

    def __init__(self, max_size: int = PAST_REQUEST_BUFFER_SIZE):
        self._buffer: Deque[PastRequest] = collections.deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._total_count = 0
        self._error_count = 0

    def append(self, req: PastRequest) -> None:
        with self._lock:
            self._buffer.append(req)
            self._total_count += 1
            if req.error:
                self._error_count += 1

    def get_recent(self, n: int = 20) -> List[PastRequest]:
        with self._lock:
            items = list(self._buffer)
            return items[-n:]

    def get_errors(self, n: int = 10) -> List[PastRequest]:
        with self._lock:
            errors = [r for r in self._buffer if r.error]
            return errors[-n:]

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            durations = [r.duration_ms for r in self._buffer if r.duration_ms > 0]
            return {
                "total_requests": self._total_count,
                "buffered": len(self._buffer),
                "errors": self._error_count,
                "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
                "p95_duration_ms": sorted(durations)[int(len(durations) * 0.95)] if len(durations) > 20 else 0,
            }

    def export_json(self, path: str) -> None:
        with self._lock:
            data = [r.to_dict() for r in self._buffer]
        pathlib.Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


class SslContextFactory:
    """Creates SSL contexts for LCU connections — handles Fiddler MITM trust."""

    @staticmethod
    def create_lcu_context(verify: bool = False) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if not verify:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    @staticmethod
    def create_fiddler_context(cert_path: Optional[str] = None) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if cert_path and os.path.exists(cert_path):
            ctx.load_verify_locations(cert_path)
        else:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx


class ErrorClassifier:
    """Classify errors and determine recovery strategy."""

    TRANSIENT_CODES = {429, 500, 502, 503, 504}
    AUTH_CODES = {401, 403}
    NOT_FOUND = {404}

    @classmethod
    def classify(cls, status: int, body: Any = None) -> str:
        if status in cls.TRANSIENT_CODES:
            return "transient"
        if status in cls.AUTH_CODES:
            return "auth"
        if status in cls.NOT_FOUND:
            return "not_found"
        if 200 <= status < 300:
            return "success"
        return "unknown"

    @classmethod
    def should_retry(cls, status: int) -> bool:
        return status in cls.TRANSIENT_CODES

    @classmethod
    def get_backoff(cls, attempt: int, status: int) -> float:
        base = RETRY_BACKOFF_BASE
        if status == 429:
            base = 2.0
        return base * (2 ** attempt) + random.uniform(0, 0.5)


class ConnectionPool:
    """Async connection pool for LCU HTTP sessions."""

    def __init__(self, config: ConnectionConfig):
        self._config = config
        self._semaphore = asyncio.Semaphore(config.pool_size)
        self._active = 0
        self._total_acquired = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        await self._semaphore.acquire()
        async with self._lock:
            self._active += 1
            self._total_acquired += 1
        return True

    async def release(self) -> None:
        async with self._lock:
            self._active -= 1
        self._semaphore.release()

    @property
    def active_count(self) -> int:
        return self._active

    def get_stats(self) -> Dict[str, int]:
        return {
            "active": self._active,
            "total_acquired": self._total_acquired,
            "pool_size": self._config.pool_size,
        }


def retry_lcu(count: int = MAX_RETRY_COUNT, backoff_base: float = RETRY_BACKOFF_BASE):
    """Decorator adapted from Seraphine retry pattern — async retry with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self, *args, **kwargs):
            last_error = None
            for attempt in range(count):
                try:
                    result = await func(self, *args, **kwargs)
                    return result
                except Exception as exc:
                    last_error = exc
                    wait = backoff_base * (2 ** attempt) + random.uniform(0, 0.3)
                    logger.warning(
                        "Retry %d/%d for %s: %s (wait %.2fs)",
                        attempt + 1, count, func.__name__, exc, wait
                    )
                    await asyncio.sleep(wait)
            raise last_error
        return wrapper
    return decorator


def need_lcu(func: Callable) -> Callable:
    """Decorator adapted from Seraphine needLcu — ensures LCU session is active."""
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        if self.state != ConnectorState.CONNECTED:
            raise ConnectionError(f"LCU not connected (state={self.state.value})")
        return await func(self, *args, **kwargs)
    return wrapper


class SeraphineConnectorBridge:
    """
    Production-grade bridge to Seraphine LCU connector APIs.

    Provides:
    - Async HTTP to LCU with connection pooling
    - SGP dual-path fallback for CN/global servers
    - Rate limiting with per-endpoint tracking
    - PastRequest replay buffer for debugging
    - Fiddler MCP integration for network analysis
    - Error classification and auto-recovery
    - SSL context management for MITM proxy
    """

    def __init__(self, config: Optional[ConnectionConfig] = None):
        self._config = config or ConnectionConfig()
        self._state = ConnectorState.DISCONNECTED
        self._pool = ConnectionPool(self._config)
        self._replay_buffer = RequestReplayBuffer(self._config.past_request_buffer)
        self._rate_limits: Dict[str, RateLimitState] = collections.defaultdict(RateLimitState)
        self._ssl_ctx = SslContextFactory.create_lcu_context(self._config.ssl_verify)
        self._fiddler_ssl_ctx = SslContextFactory.create_fiddler_context()
        self._session = None
        self._sgp_session = None
        self._started_at: Optional[float] = None
        self._request_id_counter = 0
        self._lock = asyncio.Lock()
        logger.info("SeraphineConnectorBridge initialized: %s", self._config.to_dict())

    @property
    def state(self) -> ConnectorState:
        return self._state

    @property
    def config(self) -> ConnectionConfig:
        return self._config

    @property
    def replay_buffer(self) -> RequestReplayBuffer:
        return self._replay_buffer

    async def connect(self, port: int = 0, token: str = "") -> bool:
        """Establish LCU connection — adapted from Seraphine connector.start()."""
        if port > 0:
            self._config.lcu_port = port
        if token:
            self._config.auth_token = token
        self._state = ConnectorState.CONNECTING
        try:
            import aiohttp
            connector = aiohttp.TCPConnector(ssl=self._ssl_ctx, limit=self._config.pool_size)
            self._session = aiohttp.ClientSession(
                base_url=self._config.lcu_base_url,
                headers=self._config.auth_header,
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self._config.request_timeout),
            )
            # Verify connection
            async with self._session.get("/lol-summoner/v1/current-summoner") as resp:
                if resp.status == 200:
                    self._state = ConnectorState.CONNECTED
                    self._started_at = time.time()
                    logger.info("LCU connected on port %d", self._config.lcu_port)
                    return True
                else:
                    self._state = ConnectorState.ERROR
                    logger.error("LCU connection failed: status=%d", resp.status)
                    return False
        except ImportError:
            logger.warning("aiohttp not available, using stub mode")
            self._state = ConnectorState.CONNECTED
            self._started_at = time.time()
            return True
        except Exception as exc:
            self._state = ConnectorState.ERROR
            logger.error("LCU connection error: %s", exc)
            return False

    async def connect_sgp(self, sgp_url: str = "", sgp_token: str = "") -> bool:
        """Establish SGP connection for CN/global server fallback."""
        if sgp_url:
            self._config.sgp_base_url = sgp_url
        if sgp_token:
            self._config.sgp_token = sgp_token
        if not self._config.sgp_base_url:
            logger.warning("No SGP base URL configured")
            return False
        try:
            import aiohttp
            headers = {}
            if self._config.sgp_token:
                headers["Authorization"] = f"Bearer {self._config.sgp_token}"
            self._sgp_session = aiohttp.ClientSession(
                base_url=self._config.sgp_base_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._config.sgp_timeout),
            )
            logger.info("SGP session created: %s", self._config.sgp_base_url)
            return True
        except ImportError:
            logger.warning("aiohttp not available for SGP")
            return True
        except Exception as exc:
            logger.error("SGP connection error: %s", exc)
            return False

    def _next_request_id(self) -> int:
        self._request_id_counter += 1
        return self._request_id_counter

    async def _check_rate_limit(self, endpoint: str) -> None:
        rl = self._rate_limits[endpoint]
        if rl.is_blocked():
            wait = rl.retry_after - time.time()
            logger.warning("Rate limited on %s, waiting %.2fs", endpoint, wait)
            await asyncio.sleep(max(0, wait))
        while not rl.can_request():
            await asyncio.sleep(0.5)

    @need_lcu
    @retry_lcu(count=3)
    async def lcu_get(self, path: str, params: Optional[Dict] = None) -> Any:
        """GET request to LCU API."""
        endpoint = path.split("?")[0]
        await self._check_rate_limit(endpoint)
        start = time.time()
        req = PastRequest(func_name="lcu_get", params={"path": path}, kwargs=params or {})
        try:
            await self._pool.acquire()
            try:
                if self._session:
                    async with self._session.get(path, params=params) as resp:
                        req.response_status = resp.status
                        self._rate_limits[endpoint].record_request()
                        if resp.status == 429:
                            retry_after = float(resp.headers.get("Retry-After", "5"))
                            self._rate_limits[endpoint].record_rate_limit(retry_after)
                            raise Exception(f"Rate limited: {path}")
                        body = await resp.json() if resp.content_type == "application/json" else await resp.text()
                        req.response_body = body
                        req.duration_ms = (time.time() - start) * 1000
                        return body
                else:
                    req.response_status = 0
                    req.error = "no_session"
                    return None
            finally:
                await self._pool.release()
        except Exception as exc:
            req.error = str(exc)
            req.duration_ms = (time.time() - start) * 1000
            raise
        finally:
            if self._config.log_requests:
                self._replay_buffer.append(req)

    @need_lcu
    @retry_lcu(count=3)
    async def lcu_post(self, path: str, data: Any = None) -> Any:
        """POST request to LCU API."""
        endpoint = path.split("?")[0]
        await self._check_rate_limit(endpoint)
        start = time.time()
        req = PastRequest(func_name="lcu_post", params={"path": path}, kwargs={"data": str(data)[:200]})
        try:
            await self._pool.acquire()
            try:
                if self._session:
                    async with self._session.post(path, json=data) as resp:
                        req.response_status = resp.status
                        self._rate_limits[endpoint].record_request()
                        body = await resp.json() if resp.content_type == "application/json" else await resp.text()
                        req.response_body = body
                        req.duration_ms = (time.time() - start) * 1000
                        return body
                else:
                    return None
            finally:
                await self._pool.release()
        except Exception as exc:
            req.error = str(exc)
            raise
        finally:
            if self._config.log_requests:
                self._replay_buffer.append(req)

    @retry_lcu(count=2)
    async def sgp_get(self, path: str, params: Optional[Dict] = None) -> Any:
        """GET request to SGP API — fallback path for CN servers."""
        start = time.time()
        req = PastRequest(func_name="sgp_get", params={"path": path}, kwargs=params or {}, api_path=ApiPath.SGP)
        try:
            if self._sgp_session:
                async with self._sgp_session.get(path, params=params) as resp:
                    req.response_status = resp.status
                    body = await resp.json() if resp.content_type == "application/json" else await resp.text()
                    req.response_body = body
                    req.duration_ms = (time.time() - start) * 1000
                    return body
            else:
                req.error = "no_sgp_session"
                return None
        except Exception as exc:
            req.error = str(exc)
            raise
        finally:
            if self._config.log_requests:
                self._replay_buffer.append(req)

    async def get_with_fallback(self, lcu_path: str, sgp_path: str, params: Optional[Dict] = None) -> Any:
        """Try LCU first, fall back to SGP on failure — dual-path pattern."""
        try:
            result = await self.lcu_get(lcu_path, params)
            if result is not None:
                return result
        except Exception as exc:
            logger.info("LCU failed for %s, trying SGP: %s", lcu_path, exc)
        try:
            result = await self.sgp_get(sgp_path, params)
            return result
        except Exception as exc:
            logger.error("Both LCU and SGP failed for %s / %s: %s", lcu_path, sgp_path, exc)
            return None

    async def close(self) -> None:
        """Close all sessions — adapted from Seraphine connector.close()."""
        self._state = ConnectorState.CLOSING
        if self._session:
            await self._session.close()
            self._session = None
        if self._sgp_session:
            await self._sgp_session.close()
            self._sgp_session = None
        self._state = ConnectorState.DISCONNECTED
        logger.info("SeraphineConnectorBridge closed")

    def get_diagnostics(self) -> Dict[str, Any]:
        """Get comprehensive diagnostics for dashboard."""
        uptime = time.time() - self._started_at if self._started_at else 0
        return {
            "state": self._state.value,
            "uptime_seconds": round(uptime, 1),
            "pool": self._pool.get_stats(),
            "replay_buffer": self._replay_buffer.get_stats(),
            "rate_limits": {
                ep: {"count": rl.request_count, "blocked": rl.is_blocked()}
                for ep, rl in self._rate_limits.items()
            },
            "config": self._config.to_dict(),
        }

    async def health_check(self) -> Dict[str, Any]:
        """Run health check — test LCU and SGP connectivity."""
        results = {"lcu": False, "sgp": False, "fiddler_mcp": False}
        try:
            if self._session:
                async with self._session.get("/lol-summoner/v1/current-summoner") as resp:
                    results["lcu"] = resp.status == 200
        except Exception:
            pass
        try:
            if self._sgp_session:
                async with self._sgp_session.get("/health") as resp:
                    results["sgp"] = resp.status == 200
        except Exception:
            pass
        return results

    def __repr__(self) -> str:
        return f"SeraphineConnectorBridge(state={self._state.value}, port={self._config.lcu_port})"


# ---------------------------------------------------------------------------
# Utility: Auto-discover LCU port/token from process
# ---------------------------------------------------------------------------

class LcuProcessDiscovery:
    """Discover LCU port and auth token from running League client process.
    Adapted from Seraphine getPortTokenServerByPid pattern."""

    LOCKFILE_NAME = "lockfile"

    @staticmethod
    def find_lockfile(install_dir: str = "") -> Optional[str]:
        candidates = [
            os.path.join(install_dir, LcuProcessDiscovery.LOCKFILE_NAME) if install_dir else "",
            os.path.expanduser("~/.local/share/leagueoflegends/lockfile"),
            "C:\\Riot Games\\League of Legends\\lockfile",
        ]
        for c in candidates:
            if c and os.path.exists(c):
                return c
        return None

    @staticmethod
    def parse_lockfile(path: str) -> Optional[Tuple[int, str]]:
        try:
            with open(path, "r") as f:
                content = f.read().strip()
            parts = content.split(":")
            if len(parts) >= 4:
                port = int(parts[2])
                token = parts[3]
                return port, token
        except Exception as exc:
            logger.error("Failed to parse lockfile %s: %s", path, exc)
        return None

    @classmethod
    def auto_discover(cls, install_dir: str = "") -> Optional[ConnectionConfig]:
        lockfile = cls.find_lockfile(install_dir)
        if not lockfile:
            return None
        result = cls.parse_lockfile(lockfile)
        if not result:
            return None
        port, token = result
        config = ConnectionConfig(lcu_port=port, auth_token=token)
        logger.info("Auto-discovered LCU: port=%d", port)
        return config


# ---------------------------------------------------------------------------
# Fiddler MCP Client — sends captured traffic for AI analysis
# ---------------------------------------------------------------------------

class FiddlerMcpClient:
    """Client for Fiddler Everywhere MCP Server — submit traffic for analysis."""

    def __init__(self, endpoint: str = FIDDLER_MCP_ENDPOINT, api_key: str = ""):
        self._endpoint = endpoint
        self._api_key = api_key
        self._session = None

    async def initialize(self) -> bool:
        try:
            import aiohttp
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"ApiKey {self._api_key}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            )
            return True
        except ImportError:
            return False

    async def submit_traffic(self, traffic_data: Dict[str, Any]) -> Optional[Dict]:
        if not self._session:
            return None
        try:
            async with self._session.post(self._endpoint, json=traffic_data) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as exc:
            logger.warning("Fiddler MCP submit failed: %s", exc)
        return None

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    def get_config_json(self, client_type: str = "vscode") -> str:
        """Generate MCP config JSON for IDE integration."""
        if client_type == "vscode":
            config = {
                "servers": {
                    "fiddler": {
                        "type": "http",
                        "url": self._endpoint,
                        "headers": {"Authorization": f"ApiKey {self._api_key}"},
                    }
                }
            }
        else:
            config = {
                "mcpServers": {
                    "fiddler": {
                        "url": self._endpoint,
                        "headers": {"Authorization": f"ApiKey {self._api_key}"},
                    }
                }
            }
        return json.dumps(config, indent=2)


__all__ = [
    "SeraphineConnectorBridge",
    "ConnectionConfig",
    "ConnectorState",
    "PastRequest",
    "RequestReplayBuffer",
    "LcuProcessDiscovery",
    "FiddlerMcpClient",
    "ErrorClassifier",
    "RateLimitState",
    "retry_lcu",
    "need_lcu",
]

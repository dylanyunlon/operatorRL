"""
Fiddler Bridge Client — Async MCP client for Fiddler Everywhere.

This is the core module that communicates with Fiddler's MCP server to
capture HTTP/HTTPS/WebSocket traffic from game clients.

Location: extensions/fiddler-bridge/src/fiddler_bridge/client.py

Reference: Akagi MITM architecture + lol-fiddler-agent FiddlerMCPClient pattern.
Difference: This is a *shared* bridge usable by ALL game integrations,
not tied to a single game like the lol-fiddler-agent version.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ──────────────────────── Exceptions ────────────────────────


class FiddlerBridgeError(Exception):
    """Base exception for Fiddler Bridge operations."""
    pass


class FiddlerBridgeConnectionError(FiddlerBridgeError):
    """Connection to Fiddler MCP server failed after all retries."""
    pass


# ──────────────────────── Enums ─────────────────────────────


class SessionStatus(str, Enum):
    """HTTP session status categories."""
    SUCCESS = "success"       # 2xx
    REDIRECT = "redirect"     # 3xx
    CLIENT_ERROR = "client"   # 4xx
    SERVER_ERROR = "server"   # 5xx
    UNKNOWN = "unknown"


# ──────────────────────── Config ────────────────────────────


@dataclass
class FiddlerBridgeConfig:
    """Configuration for Fiddler Bridge client.

    Attributes:
        host: MCP server host.
        port: MCP server port.
        timeout: Request timeout in seconds.
        max_retries: Max retry attempts for transient failures.
        retry_delay: Base delay between retries (seconds); exponential backoff applied.
        rate_limit_rps: Max requests per second (0 = unlimited).
    """
    host: str = "localhost"
    port: int = 8868
    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 0.5
    rate_limit_rps: int = 0

    def __post_init__(self) -> None:
        if self.port < 0 or self.port > 65535:
            raise ValueError(f"Invalid port: {self.port}")

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


# ──────────────────────── Data Models ───────────────────────


class HTTPSession(BaseModel):
    """A captured HTTP session from Fiddler."""
    id: int
    method: str
    url: str
    status_code: int
    content_type: str = ""
    request_headers: dict[str, Any] = Field(default_factory=dict)
    response_headers: dict[str, Any] = Field(default_factory=dict)
    request_body: str = ""
    response_body: str = ""
    timestamp: str = ""
    duration_ms: int = 0

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        return v.upper()

    @property
    def session_status(self) -> SessionStatus:
        code = self.status_code
        if 200 <= code < 300:
            return SessionStatus.SUCCESS
        elif 300 <= code < 400:
            return SessionStatus.REDIRECT
        elif 400 <= code < 500:
            return SessionStatus.CLIENT_ERROR
        elif 500 <= code < 600:
            return SessionStatus.SERVER_ERROR
        return SessionStatus.UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "url": self.url,
            "status_code": self.status_code,
            "content_type": self.content_type,
            "request_headers": self.request_headers,
            "response_headers": self.response_headers,
            "request_body": self.request_body,
            "response_body": self.response_body,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }

    def parse_json_body(self) -> Optional[dict[str, Any]]:
        try:
            return json.loads(self.response_body)
        except (json.JSONDecodeError, TypeError):
            return None


@dataclass
class FilterCriteria:
    """Criteria for filtering captured sessions."""
    url_pattern: str = ""
    process_name: str = ""
    methods: list[str] = field(default_factory=list)
    status_codes: list[int] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.url_pattern:
            payload["url_pattern"] = self.url_pattern
        if self.process_name:
            payload["process_name"] = self.process_name
        if self.methods:
            payload["methods"] = self.methods
        if self.status_codes:
            payload["status_codes"] = self.status_codes
        return payload


# ──────────────────────── Rate Limiter ──────────────────────


class _TokenBucketLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, rps: int) -> None:
        self._rps = rps
        self._tokens = float(rps)
        self._last_refill = time.monotonic()

    async def acquire(self) -> None:
        if self._rps <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self._rps), self._tokens + elapsed * self._rps)
        self._last_refill = now
        if self._tokens < 1.0:
            wait = (1.0 - self._tokens) / self._rps
            await asyncio.sleep(wait)
            self._tokens = 0.0
        else:
            self._tokens -= 1.0


# ──────────────────────── Client ────────────────────────────


class FiddlerBridgeClient:
    """Production-grade async MCP client for Fiddler Everywhere.

    Features:
    - Retry with exponential backoff
    - Rate limiting (token bucket)
    - Auto-reconnect on transient failure
    - Structured HTTPSession typing

    Usage:
        async with FiddlerBridgeClient(config) as client:
            sessions = await client.get_sessions()
    """

    def __init__(
        self,
        config: FiddlerBridgeConfig | None = None,
        _transport: Any = None,
    ) -> None:
        self._config = config or FiddlerBridgeConfig()
        self._transport = _transport
        self._connected = False
        self._limiter = _TokenBucketLimiter(self._config.rate_limit_rps)
        self._http: Optional[httpx.AsyncClient] = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def __aenter__(self) -> "FiddlerBridgeClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        """Connect to Fiddler MCP server with retry."""
        transport_kwargs: dict[str, Any] = {
            "base_url": self._config.base_url,
            "timeout": self._config.timeout,
        }
        if self._transport is not None:
            transport_kwargs["transport"] = self._transport

        last_err: Optional[Exception] = None
        for attempt in range(1, self._config.max_retries + 1):
            try:
                self._http = httpx.AsyncClient(**transport_kwargs)
                resp = await self._http.get("/mcp/health")
                resp.raise_for_status()
                self._connected = True
                logger.info("Connected to Fiddler MCP at %s (attempt %d)", self._config.base_url, attempt)
                return
            except Exception as e:
                last_err = e
                logger.warning("Connect attempt %d/%d failed: %s", attempt, self._config.max_retries, e)
                if self._http:
                    await self._http.aclose()
                    self._http = None
                if attempt < self._config.max_retries:
                    delay = self._config.retry_delay * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        raise FiddlerBridgeConnectionError(
            f"Failed to connect after {self._config.max_retries} attempts: {last_err}"
        )

    async def disconnect(self) -> None:
        """Disconnect from Fiddler MCP server."""
        if self._http:
            await self._http.aclose()
            self._http = None
        self._connected = False

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Make an HTTP request with rate limiting and auto-reconnect."""
        await self._limiter.acquire()

        for attempt in range(1, self._config.max_retries + 1):
            try:
                if self._http is None or not self._connected:
                    await self._ensure_client()
                assert self._http is not None
                resp = await self._http.request(method, path, **kwargs)
                resp.raise_for_status()
                return resp.json()
            except httpx.ConnectError as e:
                logger.warning("Request %s %s failed (attempt %d): %s", method, path, attempt, e)
                self._connected = False
                if self._http:
                    await self._http.aclose()
                    self._http = None
                if attempt < self._config.max_retries:
                    await asyncio.sleep(self._config.retry_delay * (2 ** (attempt - 1)))
                else:
                    raise
            except Exception:
                raise

        return {}  # unreachable but satisfies type checker

    async def _ensure_client(self) -> None:
        """Ensure an httpx.AsyncClient exists (without health check)."""
        if self._http is not None and self._connected:
            return
        transport_kwargs: dict[str, Any] = {
            "base_url": self._config.base_url,
            "timeout": self._config.timeout,
        }
        if self._transport is not None:
            transport_kwargs["transport"] = self._transport
        self._http = httpx.AsyncClient(**transport_kwargs)
        self._connected = True

    # ─────────── Public API ───────────

    async def health_check(self) -> dict[str, Any]:
        """Check MCP server health."""
        return await self._request("GET", "/mcp/health")

    async def get_sessions(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[HTTPSession]:
        """Get captured HTTP sessions as typed objects."""
        data = await self._request("GET", "/mcp/sessions", params={"limit": limit, "offset": offset})
        sessions_raw = data.get("sessions", [])
        return [HTTPSession(**s) for s in sessions_raw]

    async def get_sessions_count(self) -> int:
        """Get total number of captured sessions."""
        data = await self._request("GET", "/mcp/sessions/count")
        return int(data.get("count", 0))

    async def apply_filters(self, criteria: FilterCriteria) -> None:
        """Apply filter criteria to capture."""
        payload = criteria.to_payload()
        await self._request("POST", "/mcp/filters", json=payload)

    async def clear_sessions(self) -> None:
        """Clear all captured sessions."""
        await self._request("DELETE", "/mcp/sessions")

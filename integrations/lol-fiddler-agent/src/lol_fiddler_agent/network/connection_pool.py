"""
Connection Pool - Managed async HTTP connection pool for LoL API endpoints.

Provides connection reuse, health checking, and automatic failover
for communicating with multiple LoL-related endpoints (Live Client API,
Riot Web API, Fiddler MCP server).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class EndpointHealth(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class EndpointConfig:
    """Configuration for a single endpoint."""
    name: str
    base_url: str
    timeout: float = 10.0
    max_retries: int = 3
    health_check_interval: float = 30.0
    health_check_path: str = "/"
    headers: dict[str, str] = field(default_factory=dict)
    verify_ssl: bool = True


@dataclass
class EndpointState:
    """Runtime state of a managed endpoint."""
    config: EndpointConfig
    health: EndpointHealth = EndpointHealth.UNKNOWN
    last_health_check: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    consecutive_failures: int = 0
    total_requests: int = 0
    total_errors: int = 0
    avg_latency_ms: float = 0.0


class ManagedConnection:
    """A managed HTTP connection wrapping httpx.AsyncClient.

    Tracks per-connection metrics and provides automatic retry.
    """

    def __init__(self, endpoint: EndpointConfig) -> None:
        self._endpoint = endpoint
        self._client: Optional[httpx.AsyncClient] = None
        self._state = EndpointState(config=endpoint)

    async def connect(self) -> None:
        """Create the underlying HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self._endpoint.base_url,
            headers=self._endpoint.headers,
            timeout=self._endpoint.timeout,
            verify=self._endpoint.verify_ssl,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def request(
        self,
        method: str,
        path: str,
        json: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        """Make an HTTP request with retry logic."""
        if not self._client:
            await self.connect()
        assert self._client is not None

        last_error: Optional[Exception] = None
        for attempt in range(self._endpoint.max_retries):
            start = time.monotonic()
            try:
                response = await self._client.request(
                    method, path, json=json, params=params,
                )
                elapsed_ms = (time.monotonic() - start) * 1000
                self._record_success(elapsed_ms)
                return response
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                elapsed_ms = (time.monotonic() - start) * 1000
                self._record_failure(elapsed_ms)
                last_error = e
                if attempt < self._endpoint.max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))

        raise last_error or RuntimeError("Request failed with no error captured")

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", path, **kwargs)

    async def health_check(self) -> EndpointHealth:
        """Perform a health check."""
        try:
            response = await self.get(self._endpoint.health_check_path)
            self._state.last_health_check = time.time()
            if response.status_code < 400:
                self._state.health = EndpointHealth.HEALTHY
            elif response.status_code < 500:
                self._state.health = EndpointHealth.DEGRADED
            else:
                self._state.health = EndpointHealth.UNHEALTHY
        except Exception:
            self._state.health = EndpointHealth.UNHEALTHY
            self._state.last_health_check = time.time()
        return self._state.health

    def _record_success(self, latency_ms: float) -> None:
        self._state.total_requests += 1
        self._state.last_success = time.time()
        self._state.consecutive_failures = 0
        self._state.health = EndpointHealth.HEALTHY
        n = self._state.total_requests
        self._state.avg_latency_ms += (latency_ms - self._state.avg_latency_ms) / n

    def _record_failure(self, latency_ms: float) -> None:
        self._state.total_requests += 1
        self._state.total_errors += 1
        self._state.last_failure = time.time()
        self._state.consecutive_failures += 1
        if self._state.consecutive_failures >= 3:
            self._state.health = EndpointHealth.UNHEALTHY
        elif self._state.consecutive_failures >= 1:
            self._state.health = EndpointHealth.DEGRADED

    @property
    def state(self) -> EndpointState:
        return self._state

    @property
    def is_healthy(self) -> bool:
        return self._state.health in (EndpointHealth.HEALTHY, EndpointHealth.UNKNOWN)


# Predefined endpoint configurations
LOL_LIVE_CLIENT = EndpointConfig(
    name="lol_live_client",
    base_url="https://127.0.0.1:2999",
    timeout=5.0,
    max_retries=2,
    health_check_path="/liveclientdata/gamestats",
    verify_ssl=False,
)

FIDDLER_MCP = EndpointConfig(
    name="fiddler_mcp",
    base_url="http://localhost:8868",
    timeout=30.0,
    max_retries=3,
    health_check_path="/mcp",
)


class ConnectionPool:
    """Pool of managed connections to LoL-related endpoints.

    Example::

        pool = ConnectionPool()
        pool.add_endpoint(LOL_LIVE_CLIENT)
        pool.add_endpoint(FIDDLER_MCP)
        await pool.connect_all()

        response = await pool.get("lol_live_client", "/liveclientdata/allgamedata")
    """

    def __init__(self) -> None:
        self._connections: dict[str, ManagedConnection] = {}
        self._health_task: Optional[asyncio.Task] = None

    def add_endpoint(self, config: EndpointConfig) -> None:
        self._connections[config.name] = ManagedConnection(config)

    async def connect_all(self) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for name, conn in self._connections.items():
            try:
                await conn.connect()
                results[name] = True
            except Exception as e:
                logger.warning("Failed to connect %s: %s", name, e)
                results[name] = False
        return results

    async def close_all(self) -> None:
        for conn in self._connections.values():
            await conn.close()
        if self._health_task:
            self._health_task.cancel()

    def get_connection(self, name: str) -> ManagedConnection:
        conn = self._connections.get(name)
        if not conn:
            raise KeyError(f"No endpoint registered: {name}")
        return conn

    async def request(
        self, endpoint_name: str, method: str, path: str, **kwargs: Any,
    ) -> httpx.Response:
        conn = self.get_connection(endpoint_name)
        return await conn.request(method, path, **kwargs)

    async def get(self, endpoint_name: str, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request(endpoint_name, "GET", path, **kwargs)

    async def post(self, endpoint_name: str, path: str, **kwargs: Any) -> httpx.Response:
        return await self.request(endpoint_name, "POST", path, **kwargs)

    async def health_check_all(self) -> dict[str, EndpointHealth]:
        results: dict[str, EndpointHealth] = {}
        for name, conn in self._connections.items():
            results[name] = await conn.health_check()
        return results

    async def start_health_monitoring(self, interval: float = 30.0) -> None:
        self._health_task = asyncio.create_task(self._health_loop(interval))

    async def _health_loop(self, interval: float) -> None:
        while True:
            try:
                await self.health_check_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Health check error: %s", e)
            await asyncio.sleep(interval)

    def get_all_states(self) -> dict[str, EndpointState]:
        return {name: conn.state for name, conn in self._connections.items()}

    @property
    def endpoint_names(self) -> list[str]:
        return list(self._connections.keys())

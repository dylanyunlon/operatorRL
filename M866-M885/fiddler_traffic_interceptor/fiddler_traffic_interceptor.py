#!/usr/bin/env python3
"""
M866: FiddlerTrafficInterceptor
===============================

Intercepts and classifies LoL client traffic via Fiddler MCP proxy pipeline

Part of OperatorRL M866-M885 Historical Battle Intelligence Fusion subsystem.

Architecture Pattern:
  Query Seraphine LCU connector patterns → Parse Riot API responses
  → Transform via data pipeline → Store in structured format
  → Serve via dashboard API → Alert via voice coach

Network Capture (Fiddler + Proxifier) is preferred over vision:
  - Zero hallucination from raw network data
  - Full API responses vs visible UI only
  - <10ms latency vs 70-200ms for screen capture
  - Aligns with reverse engineering skill direction

Dependencies: None

Reference Projects:
  - github.com/ljszx/Seraphine (LCU API connector patterns)
  - github.com/oracle-devrel/leagueoflegends-optimizer (data pipeline & ML)
  - telerik.com/fiddler (network analysis via MCP server)
  - github.com/forest0xia/dota2bot-OpenHyperAI (MOBA AI patterns)
  - github.com/dylanyunlon/operatorRL (parent agentic system)
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
import math
import os
import pathlib
import queue
import random
import re
import statistics
import struct
import sys
import threading
import time
import typing
import uuid
from typing import (
    Any, Callable, ClassVar, Coroutine, Deque, Dict, Final,
    FrozenSet, Generator, Iterable, Iterator, List, Mapping,
    NamedTuple, Optional, Protocol, Sequence, Set, Tuple, Type,
    TypeVar, Union, runtime_checkable,
)

logger = logging.getLogger("M866.FiddlerTrafficInterceptor")


# ===========================================================================
# Constants & Configuration
# ===========================================================================

FIDDLER_MCP_ENDPOINT: Final[str] = "http://localhost:8868/mcp"
FIDDLER_DEFAULT_PORT: Final[int] = 8868
LOL_PROCESS_NAMES: Final[Tuple[str, ...]] = (
    "LeagueClient.exe", "LeagueClientUx.exe", "League of Legends.exe",
    "RiotClientServices.exe", "RiotClientUx.exe",
)
RIOT_API_DOMAINS: Final[Tuple[str, ...]] = (
    "127.0.0.1:2999", "riot.api", "ddragon.leagueoflegends.com",
    "americas.api.riotgames.com", "euw1.api.riotgames.com",
    "na1.api.riotgames.com", "kr.api.riotgames.com",
)
MAX_CAPTURE_BUFFER: Final[int] = 10000
CAPTURE_FLUSH_INTERVAL_S: Final[float] = 5.0


class TrafficType(enum.Enum):
    """Classification of intercepted traffic."""
    LCU_API = "lcu_api"
    LIVE_CLIENT = "live_client"
    RIOT_API = "riot_api"
    DDRAGON = "ddragon"
    GAME_SERVER = "game_server"
    UNKNOWN = "unknown"


class CaptureState(enum.Enum):
    """Fiddler capture session state."""
    IDLE = "idle"
    STARTING = "starting"
    CAPTURING = "capturing"
    PAUSED = "paused"
    STOPPING = "stopping"
    ERROR = "error"


@dataclasses.dataclass(frozen=True)
class InterceptedRequest:
    """Single intercepted HTTP request from Fiddler."""
    request_id: str
    timestamp: float
    method: str
    url: str
    headers: Dict[str, str]
    body: Optional[str]
    traffic_type: TrafficType
    process_name: str
    response_status: Optional[int] = None
    response_headers: Optional[Dict[str, str]] = None
    response_body: Optional[str] = None
    latency_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for storage."""
        result: Dict[str, Any] = {
            "request_id": self.request_id,
            "timestamp": self.timestamp,
            "method": self.method,
            "url": self.url,
            "headers": dict(self.headers),
            "traffic_type": self.traffic_type.value,
            "process_name": self.process_name,
        }
        if self.body is not None:
            result["body"] = self.body
        if self.response_status is not None:
            result["response_status"] = self.response_status
        if self.response_body is not None:
            result["response_body"] = self.response_body
        if self.latency_ms is not None:
            result["latency_ms"] = self.latency_ms
        return result


@dataclasses.dataclass
class CaptureMetrics:
    """Metrics for the traffic capture session."""
    total_requests: int = 0
    lcu_requests: int = 0
    live_client_requests: int = 0
    riot_api_requests: int = 0
    ddragon_requests: int = 0
    game_server_requests: int = 0
    unknown_requests: int = 0
    errors: int = 0
    bytes_captured: int = 0
    start_time: Optional[float] = None
    last_request_time: Optional[float] = None

    def increment(self, traffic_type: TrafficType, size: int = 0) -> None:
        """Increment counters for a captured request."""
        self.total_requests += 1
        self.bytes_captured += size
        self.last_request_time = time.time()
        counter_map = {
            TrafficType.LCU_API: "lcu_requests",
            TrafficType.LIVE_CLIENT: "live_client_requests",
            TrafficType.RIOT_API: "riot_api_requests",
            TrafficType.DDRAGON: "ddragon_requests",
            TrafficType.GAME_SERVER: "game_server_requests",
            TrafficType.UNKNOWN: "unknown_requests",
        }
        attr = counter_map.get(traffic_type, "unknown_requests")
        setattr(self, attr, getattr(self, attr) + 1)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize metrics to dictionary."""
        return dataclasses.asdict(self)

    @property
    def requests_per_second(self) -> float:
        """Calculate average requests per second."""
        if self.start_time is None or self.total_requests == 0:
            return 0.0
        elapsed = time.time() - self.start_time
        return self.total_requests / max(elapsed, 0.001)


class TrafficClassifier:
    """Classifies intercepted traffic by domain and path patterns."""

    _LCU_PATTERNS: ClassVar[List[re.Pattern]] = [
        re.compile(r"https?://127\.0\.0\.1:\d+/lol-"),
        re.compile(r"https?://127\.0\.0\.1:\d+/riotclient/"),
        re.compile(r"https?://127\.0\.0\.1:\d+/lol-game-data/"),
    ]
    _LIVE_CLIENT_PATTERNS: ClassVar[List[re.Pattern]] = [
        re.compile(r"https?://127\.0\.0\.1:2999/"),
        re.compile(r"https?://localhost:2999/"),
    ]
    _RIOT_API_PATTERNS: ClassVar[List[re.Pattern]] = [
        re.compile(r"https?://\w+\.api\.riotgames\.com/"),
        re.compile(r"https?://americas\.api\.riotgames\.com/"),
    ]
    _DDRAGON_PATTERNS: ClassVar[List[re.Pattern]] = [
        re.compile(r"https?://ddragon\.leagueoflegends\.com/"),
        re.compile(r"https?://cdn\.communitydragon\.org/"),
    ]

    @classmethod
    def classify(cls, url: str) -> TrafficType:
        """Classify a URL into a traffic type."""
        for pattern in cls._LIVE_CLIENT_PATTERNS:
            if pattern.match(url):
                return TrafficType.LIVE_CLIENT
        for pattern in cls._LCU_PATTERNS:
            if pattern.match(url):
                return TrafficType.LCU_API
        for pattern in cls._RIOT_API_PATTERNS:
            if pattern.match(url):
                return TrafficType.RIOT_API
        for pattern in cls._DDRAGON_PATTERNS:
            if pattern.match(url):
                return TrafficType.DDRAGON
        return TrafficType.UNKNOWN


class FiddlerMCPClient:
    """Client for communicating with Fiddler Everywhere MCP server.

    Connects to the Fiddler MCP server (http://localhost:8868/mcp) to:
    - Start/stop traffic capture sessions
    - Retrieve captured traffic data
    - Filter traffic by process and domain
    - Export captured data in HAR format

    Configuration with Proxifier:
      1. Install Proxifier and create a profile for LoL traffic
      2. Add rules to route LeagueClient.exe through Fiddler proxy
      3. Configure Fiddler to decrypt HTTPS traffic
      4. Start MCP server in Fiddler Everywhere settings
    """

    def __init__(
        self,
        endpoint: str = FIDDLER_MCP_ENDPOINT,
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_s
        self._session_id: Optional[str] = None
        self._headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"ApiKey {api_key}"
        logger.info("FiddlerMCPClient initialized: endpoint=%s", self._endpoint)

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send an MCP request to Fiddler server.

        In production, this uses aiohttp. Here we provide the interface
        contract for integration with the Fiddler MCP server.
        """
        url = f"{self._endpoint}{path}"
        request_body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": payload or {},
        }
        logger.debug("MCP request: %s %s", method, url)
        # Production: aiohttp.ClientSession().post(url, json=request_body)
        # For now, return simulated response structure
        return {
            "jsonrpc": "2.0",
            "id": request_body["id"],
            "result": {"status": "ok", "session_id": self._session_id},
        }

    async def start_capture(
        self,
        filter_processes: Optional[List[str]] = None,
        filter_domains: Optional[List[str]] = None,
    ) -> str:
        """Start a new traffic capture session.

        Args:
            filter_processes: Process names to capture (e.g., LeagueClient.exe)
            filter_domains: Domain patterns to capture

        Returns:
            Session ID for the capture session
        """
        params: Dict[str, Any] = {}
        if filter_processes:
            params["processFilter"] = filter_processes
        if filter_domains:
            params["domainFilter"] = filter_domains
        response = await self._request("capture/start", "/", params)
        self._session_id = response.get("result", {}).get(
            "session_id", str(uuid.uuid4())
        )
        logger.info("Capture started: session_id=%s", self._session_id)
        return self._session_id

    async def stop_capture(self) -> Dict[str, Any]:
        """Stop the current capture session."""
        if not self._session_id:
            logger.warning("No active capture session to stop")
            return {"status": "no_session"}
        response = await self._request("capture/stop", "/", {
            "session_id": self._session_id,
        })
        logger.info("Capture stopped: session_id=%s", self._session_id)
        old_session = self._session_id
        self._session_id = None
        return {"status": "stopped", "session_id": old_session}

    async def get_traffic(
        self,
        limit: int = 100,
        offset: int = 0,
        traffic_type: Optional[TrafficType] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve captured traffic from current session.

        Args:
            limit: Maximum number of entries to return
            offset: Offset for pagination
            traffic_type: Optional filter by traffic classification

        Returns:
            List of captured traffic entries
        """
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
        }
        if self._session_id:
            params["session_id"] = self._session_id
        if traffic_type:
            params["type_filter"] = traffic_type.value
        response = await self._request("traffic/list", "/", params)
        entries = response.get("result", {}).get("entries", [])
        logger.debug("Retrieved %d traffic entries", len(entries))
        return entries

    async def export_har(self, output_path: str) -> str:
        """Export captured traffic as HAR file.

        Args:
            output_path: File path for the HAR export

        Returns:
            Path to the exported HAR file
        """
        params = {"output_path": output_path}
        if self._session_id:
            params["session_id"] = self._session_id
        await self._request("traffic/export", "/", params)
        logger.info("HAR exported to: %s", output_path)
        return output_path


class CaptureBuffer:
    """Thread-safe circular buffer for intercepted requests.

    Stores captured requests in memory with configurable max size.
    Oldest entries are evicted when buffer is full.
    """

    def __init__(self, max_size: int = MAX_CAPTURE_BUFFER) -> None:
        self._buffer: Deque[InterceptedRequest] = collections.deque(maxlen=max_size)
        self._lock = threading.Lock()
        self._flush_callbacks: List[Callable[[List[InterceptedRequest]], None]] = []

    def add(self, request: InterceptedRequest) -> None:
        """Add a request to the buffer."""
        with self._lock:
            self._buffer.append(request)

    def flush(self) -> List[InterceptedRequest]:
        """Flush all buffered requests and return them."""
        with self._lock:
            items = list(self._buffer)
            self._buffer.clear()
        for cb in self._flush_callbacks:
            try:
                cb(items)
            except Exception:
                logger.exception("Flush callback error")
        return items

    def on_flush(self, callback: Callable[[List[InterceptedRequest]], None]) -> None:
        """Register a callback for buffer flush events."""
        self._flush_callbacks.append(callback)

    @property
    def size(self) -> int:
        """Current buffer size."""
        with self._lock:
            return len(self._buffer)

    def get_by_type(self, traffic_type: TrafficType) -> List[InterceptedRequest]:
        """Get all requests of a specific traffic type."""
        with self._lock:
            return [r for r in self._buffer if r.traffic_type == traffic_type]

    def get_recent(self, count: int = 10) -> List[InterceptedRequest]:
        """Get the most recent N requests."""
        with self._lock:
            items = list(self._buffer)
        return items[-count:] if len(items) > count else items

    def search(self, url_pattern: str) -> List[InterceptedRequest]:
        """Search requests by URL pattern."""
        compiled = re.compile(url_pattern, re.IGNORECASE)
        with self._lock:
            return [r for r in self._buffer if compiled.search(r.url)]


class FiddlerTrafficInterceptor:
    """Main traffic interceptor integrating Fiddler MCP, Proxifier, and LCU.

    This is the core data acquisition component for the M866-M885 subsystem.
    It intercepts LoL client traffic through Fiddler proxy, classifies requests,
    and feeds them into the operatorRL agentic pipeline for analysis.

    Architecture:
      LoL Client → Proxifier → Fiddler Proxy → FiddlerTrafficInterceptor
                                                     │
                                              TrafficClassifier
                                                     │
                                              CaptureBuffer → Downstream modules

    Usage:
        interceptor = FiddlerTrafficInterceptor(api_key="your_key")
        await interceptor.start()
        # ... game runs ...
        traffic = await interceptor.get_classified_traffic()
        await interceptor.stop()
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        endpoint: str = FIDDLER_MCP_ENDPOINT,
        buffer_size: int = MAX_CAPTURE_BUFFER,
        flush_interval: float = CAPTURE_FLUSH_INTERVAL_S,
        auto_classify: bool = True,
    ) -> None:
        self._client = FiddlerMCPClient(endpoint=endpoint, api_key=api_key)
        self._buffer = CaptureBuffer(max_size=buffer_size)
        self._classifier = TrafficClassifier()
        self._metrics = CaptureMetrics()
        self._state = CaptureState.IDLE
        self._flush_interval = flush_interval
        self._auto_classify = auto_classify
        self._flush_task: Optional[asyncio.Task] = None
        self._event_handlers: Dict[TrafficType, List[Callable]] = {
            t: [] for t in TrafficType
        }
        self._lock = asyncio.Lock()
        logger.info("FiddlerTrafficInterceptor initialized")

    @property
    def state(self) -> CaptureState:
        """Current capture state."""
        return self._state

    @property
    def metrics(self) -> CaptureMetrics:
        """Current capture metrics."""
        return self._metrics

    async def start(
        self,
        process_filter: Optional[List[str]] = None,
        domain_filter: Optional[List[str]] = None,
    ) -> str:
        """Start traffic interception.

        Args:
            process_filter: LoL process names to capture
            domain_filter: API domains to capture

        Returns:
            Capture session ID
        """
        async with self._lock:
            if self._state == CaptureState.CAPTURING:
                logger.warning("Already capturing")
                return ""
            self._state = CaptureState.STARTING
            procs = process_filter or list(LOL_PROCESS_NAMES)
            domains = domain_filter or list(RIOT_API_DOMAINS)
            try:
                session_id = await self._client.start_capture(procs, domains)
                self._metrics.start_time = time.time()
                self._state = CaptureState.CAPTURING
                self._flush_task = asyncio.create_task(self._periodic_flush())
                logger.info("Interception started: session=%s", session_id)
                return session_id
            except Exception as exc:
                self._state = CaptureState.ERROR
                logger.exception("Failed to start capture: %s", exc)
                raise

    async def stop(self) -> Dict[str, Any]:
        """Stop traffic interception and return final metrics."""
        async with self._lock:
            if self._state != CaptureState.CAPTURING:
                return {"status": "not_capturing"}
            self._state = CaptureState.STOPPING
            if self._flush_task:
                self._flush_task.cancel()
                try:
                    await self._flush_task
                except asyncio.CancelledError:
                    pass
            result = await self._client.stop_capture()
            self._state = CaptureState.IDLE
            result["metrics"] = self._metrics.to_dict()
            logger.info("Interception stopped: %s", json.dumps(result, default=str))
            return result

    async def _periodic_flush(self) -> None:
        """Periodically flush the capture buffer."""
        while self._state == CaptureState.CAPTURING:
            await asyncio.sleep(self._flush_interval)
            flushed = self._buffer.flush()
            if flushed:
                logger.debug("Flushed %d requests from buffer", len(flushed))

    async def ingest_request(self, raw_data: Dict[str, Any]) -> InterceptedRequest:
        """Ingest a raw request from Fiddler and classify it.

        Args:
            raw_data: Raw request data from Fiddler MCP

        Returns:
            Classified InterceptedRequest
        """
        url = raw_data.get("url", "")
        traffic_type = (
            self._classifier.classify(url) if self._auto_classify
            else TrafficType.UNKNOWN
        )
        request = InterceptedRequest(
            request_id=raw_data.get("id", str(uuid.uuid4())),
            timestamp=raw_data.get("timestamp", time.time()),
            method=raw_data.get("method", "GET"),
            url=url,
            headers=raw_data.get("headers", {}),
            body=raw_data.get("body"),
            traffic_type=traffic_type,
            process_name=raw_data.get("process", "unknown"),
            response_status=raw_data.get("response_status"),
            response_headers=raw_data.get("response_headers"),
            response_body=raw_data.get("response_body"),
            latency_ms=raw_data.get("latency_ms"),
        )
        self._buffer.add(request)
        body_size = len(request.body or "") + len(request.response_body or "")
        self._metrics.increment(traffic_type, body_size)
        handlers = self._event_handlers.get(traffic_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(request)
                else:
                    handler(request)
            except Exception:
                logger.exception("Event handler error for %s", traffic_type)
        return request

    def on_traffic(
        self, traffic_type: TrafficType, handler: Callable
    ) -> None:
        """Register an event handler for a specific traffic type.

        Args:
            traffic_type: Type of traffic to handle
            handler: Callback function (sync or async)
        """
        self._event_handlers[traffic_type].append(handler)
        logger.debug("Registered handler for %s", traffic_type.value)

    async def get_classified_traffic(
        self,
        traffic_type: Optional[TrafficType] = None,
        limit: int = 100,
    ) -> List[InterceptedRequest]:
        """Get classified traffic from the buffer.

        Args:
            traffic_type: Optional filter by type
            limit: Maximum entries to return

        Returns:
            List of classified requests
        """
        if traffic_type:
            results = self._buffer.get_by_type(traffic_type)
        else:
            results = self._buffer.get_recent(limit)
        return results[:limit]

    async def get_lcu_api_calls(self) -> List[InterceptedRequest]:
        """Get all LCU API calls from buffer."""
        return await self.get_classified_traffic(TrafficType.LCU_API)

    async def get_live_client_data(self) -> List[InterceptedRequest]:
        """Get all Live Client Data API calls."""
        return await self.get_classified_traffic(TrafficType.LIVE_CLIENT)

    async def export_session(self, output_dir: str) -> str:
        """Export the current session data.

        Args:
            output_dir: Directory to save exported data

        Returns:
            Path to exported file
        """
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        export_path = os.path.join(output_dir, f"capture_{timestamp}.json")
        all_traffic = self._buffer.flush()
        export_data = {
            "session_metrics": self._metrics.to_dict(),
            "traffic": [r.to_dict() for r in all_traffic],
            "export_time": datetime.datetime.now().isoformat(),
            "module": "M866-FiddlerTrafficInterceptor",
        }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, default=str)
        logger.info("Session exported: %s (%d entries)", export_path, len(all_traffic))
        return export_path

    def get_health_status(self) -> Dict[str, Any]:
        """Get health status for dashboard integration."""
        return {
            "module_id": "M866",
            "module_name": "FiddlerTrafficInterceptor",
            "state": self._state.value,
            "metrics": self._metrics.to_dict(),
            "buffer_size": self._buffer.size,
            "uptime_s": (
                time.time() - self._metrics.start_time
                if self._metrics.start_time else 0
            ),
        }

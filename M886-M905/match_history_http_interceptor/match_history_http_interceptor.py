#!/usr/bin/env python3
"""
M886 — MatchHistoryHttpInterceptor
===================================
Intercepts Riot /lol-match-history/v1/products/lol endpoint traffic via Fiddler
MCP proxy. Follows Seraphine connector.py patterns for retry logic, session
management, and ring-buffered response caching.

Reference: Seraphine connector.py::__initSessions, __json_retry_get, retry()
Fiddler MCP: localhost:8868/mcp (ApiKey auth)
Proxifier: LeagueClient.exe → 127.0.0.1:8866 → Fiddler HTTPS

Architecture:
    Proxifier routes LeagueClient → Fiddler → Internet
    Fiddler MCP exposes captured traffic via HTTP API
    This module polls Fiddler MCP for match-history endpoint hits
    Stores raw JSON in a thread-safe RingBuffer(capacity=100)
"""

from __future__ import annotations

import asyncio
import collections
import copy
import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import (
    Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple, TypeVar,
)

logger = logging.getLogger("M886.MatchHistoryHttpInterceptor")

# ---------------------------------------------------------------------------
# Constants — Riot API endpoints we intercept
# ---------------------------------------------------------------------------
MATCH_HISTORY_PATTERN = re.compile(
    r"/lol-match-history/v[12]/products/lol/"
    r"(?P<puuid>[a-f0-9\-]{36})/matches",
    re.IGNORECASE,
)
MATCH_DETAIL_PATTERN = re.compile(
    r"/lol-match-history/v[12]/games/(?P<game_id>\d+)",
    re.IGNORECASE,
)
FIDDLER_MCP_DEFAULT_URL = "http://localhost:8868/mcp"
FIDDLER_MCP_POLL_INTERVAL = 1.0  # seconds between Fiddler traffic polls
RING_BUFFER_CAPACITY = 100
MAX_RETRY_COUNT = 5
RETRY_BACKOFF_BASE = 0.3  # exponential backoff base seconds
REQUEST_TIMEOUT = 10.0


class InterceptorState(Enum):
    """Mirrors Seraphine connector lifecycle states."""
    IDLE = auto()
    CONNECTING = auto()
    ACTIVE = auto()
    DEGRADED = auto()  # Fiddler unreachable, serving from cache
    SHUTTING_DOWN = auto()
    CLOSED = auto()


class TrafficDirection(Enum):
    REQUEST = "request"
    RESPONSE = "response"


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InterceptedEntry:
    """Single intercepted HTTP request/response pair from Fiddler."""
    entry_id: str
    timestamp: datetime
    method: str
    url: str
    status_code: int
    request_headers: Dict[str, str]
    response_headers: Dict[str, str]
    request_body: Optional[str]
    response_body: Optional[str]
    duration_ms: float
    process_name: str  # e.g. "LeagueClient.exe"
    puuid: Optional[str] = None
    game_id: Optional[str] = None

    @property
    def content_hash(self) -> str:
        """SHA-256 of response body for deduplication."""
        payload = (self.response_body or "").encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    @property
    def is_match_history(self) -> bool:
        return bool(MATCH_HISTORY_PATTERN.search(self.url))

    @property
    def is_match_detail(self) -> bool:
        return bool(MATCH_DETAIL_PATTERN.search(self.url))


@dataclass
class InterceptorStats:
    """Runtime statistics following Seraphine's PastRequest tracking."""
    total_intercepted: int = 0
    match_history_hits: int = 0
    match_detail_hits: int = 0
    errors: int = 0
    fiddler_polls: int = 0
    cache_hits: int = 0
    last_intercept_time: Optional[datetime] = None
    uptime_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_intercepted": self.total_intercepted,
            "match_history_hits": self.match_history_hits,
            "match_detail_hits": self.match_detail_hits,
            "errors": self.errors,
            "fiddler_polls": self.fiddler_polls,
            "cache_hits": self.cache_hits,
            "last_intercept": (
                self.last_intercept_time.isoformat()
                if self.last_intercept_time else None
            ),
            "uptime_seconds": round(self.uptime_seconds, 2),
        }


# ---------------------------------------------------------------------------
# RingBuffer — Thread-safe circular buffer for recent match data
# ---------------------------------------------------------------------------
class RingBuffer:
    """
    Thread-safe ring buffer storing the last N intercepted entries.

    Design rationale: We need bounded memory usage since this interceptor
    runs continuously for 30+ minute game sessions. A deque with maxlen
    provides O(1) append with automatic eviction of oldest entries.

    The lock protects concurrent access from the Fiddler polling thread
    and consumer threads (analytics modules).
    """

    def __init__(self, capacity: int = RING_BUFFER_CAPACITY):
        self._capacity = capacity
        self._buffer: Deque[InterceptedEntry] = collections.deque(
            maxlen=capacity
        )
        self._lock = threading.RLock()
        self._seen_hashes: collections.OrderedDict[str, None] = (
            collections.OrderedDict()
        )
        self._total_added = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)

    def add(self, entry: InterceptedEntry) -> bool:
        """
        Add entry if not a duplicate. Returns True if added.

        Deduplication uses content_hash to avoid storing identical
        responses that occur during Riot API retries.
        """
        with self._lock:
            content_hash = entry.content_hash
            if content_hash in self._seen_hashes:
                return False

            self._buffer.append(entry)
            self._seen_hashes[content_hash] = None
            self._total_added += 1

            # Keep seen_hashes bounded to 2x capacity
            while len(self._seen_hashes) > self._capacity * 2:
                self._seen_hashes.popitem(last=False)

            return True

    def get_recent(self, count: int = 10) -> List[InterceptedEntry]:
        """Return the most recent N entries (newest first)."""
        with self._lock:
            items = list(self._buffer)
            items.reverse()
            return items[:count]

    def get_by_puuid(self, puuid: str) -> List[InterceptedEntry]:
        """Filter entries by summoner PUUID."""
        with self._lock:
            return [e for e in self._buffer if e.puuid == puuid]

    def get_by_game_id(self, game_id: str) -> List[InterceptedEntry]:
        """Filter entries by game ID."""
        with self._lock:
            return [e for e in self._buffer if e.game_id == game_id]

    def get_match_histories(self) -> List[InterceptedEntry]:
        """Return only match-history endpoint responses."""
        with self._lock:
            return [e for e in self._buffer if e.is_match_history]

    def clear(self) -> int:
        """Clear buffer, return count of cleared entries."""
        with self._lock:
            count = len(self._buffer)
            self._buffer.clear()
            self._seen_hashes.clear()
            return count

    def snapshot(self) -> Dict[str, Any]:
        """Serializable snapshot for persistence/debugging."""
        with self._lock:
            return {
                "capacity": self._capacity,
                "current_size": len(self._buffer),
                "total_added": self._total_added,
                "entries": [
                    {
                        "id": e.entry_id,
                        "url": e.url,
                        "status": e.status_code,
                        "timestamp": e.timestamp.isoformat(),
                        "puuid": e.puuid,
                        "game_id": e.game_id,
                        "hash": e.content_hash,
                    }
                    for e in self._buffer
                ],
            }


# ---------------------------------------------------------------------------
# Retry Decorator — Following Seraphine's retry() pattern
# ---------------------------------------------------------------------------
T = TypeVar("T")


def retry_async(
    count: int = MAX_RETRY_COUNT,
    backoff_base: float = RETRY_BACKOFF_BASE,
    exceptions: Tuple = (Exception,),
):
    """
    Async retry decorator following Seraphine connector.py retry() pattern.

    Implements exponential backoff with jitter to prevent thundering herd
    when Fiddler MCP becomes temporarily unavailable.
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, count + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exception = exc
                    if attempt < count:
                        # Exponential backoff: 0.3, 0.6, 1.2, 2.4, 4.8
                        delay = backoff_base * (2 ** (attempt - 1))
                        # Add jitter ±25%
                        import random
                        jitter = delay * 0.25 * (2 * random.random() - 1)
                        wait = max(0.05, delay + jitter)
                        logger.warning(
                            "Attempt %d/%d failed for %s: %s. "
                            "Retrying in %.2fs",
                            attempt, count, func.__name__, exc, wait,
                        )
                        await asyncio.sleep(wait)

            logger.error(
                "All %d attempts failed for %s: %s",
                count, func.__name__, last_exception,
            )
            raise last_exception  # type: ignore[misc]

        wrapper.__name__ = func.__name__
        wrapper.__qualname__ = func.__qualname__
        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# FiddlerMCPClient — Communicates with Fiddler Everywhere MCP Server
# ---------------------------------------------------------------------------
class FiddlerMCPClient:
    """
    Client for Fiddler Everywhere MCP Server (http://localhost:8868/mcp).

    Follows Seraphine's __initSessions pattern for session lifecycle.
    The Fiddler MCP provides captured HTTPS traffic data that we parse
    into InterceptedEntry objects.
    """

    def __init__(
        self,
        mcp_url: str = FIDDLER_MCP_DEFAULT_URL,
        api_key: Optional[str] = None,
        timeout: float = REQUEST_TIMEOUT,
    ):
        self._mcp_url = mcp_url
        self._api_key = api_key
        self._timeout = timeout
        self._session = None  # aiohttp.ClientSession
        self._is_connected = False
        self._last_poll_id: Optional[str] = None

    async def connect(self):
        """
        Initialize HTTP session to Fiddler MCP.
        Mirrors Seraphine connector.__initSessions().
        """
        try:
            import aiohttp
            headers = {"Content-Type": "application/json"}
            if self._api_key:
                headers["Authorization"] = f"ApiKey {self._api_key}"

            timeout = aiohttp.ClientTimeout(total=self._timeout)
            self._session = aiohttp.ClientSession(
                headers=headers, timeout=timeout
            )
            self._is_connected = True
            logger.info(
                "Connected to Fiddler MCP at %s", self._mcp_url
            )
        except ImportError:
            logger.warning(
                "aiohttp not available, using mock Fiddler client"
            )
            self._is_connected = True

    async def disconnect(self):
        """Close session gracefully."""
        if self._session:
            await self._session.close()
            self._session = None
        self._is_connected = False
        logger.info("Disconnected from Fiddler MCP")

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @retry_async(count=3, backoff_base=0.5)
    async def fetch_recent_traffic(
        self,
        process_filter: str = "LeagueClient",
        url_filter: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent captured traffic from Fiddler MCP.

        Mirrors Seraphine's __json_retry_get pattern with structured
        error handling and automatic retry on transient failures.
        """
        if not self._is_connected:
            raise ConnectionError("Fiddler MCP client not connected")

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "get_captured_traffic",
                "arguments": {
                    "process": process_filter,
                    "limit": limit,
                },
            },
            "id": int(time.time() * 1000),
        }
        if url_filter:
            payload["params"]["arguments"]["urlFilter"] = url_filter

        if self._session:
            async with self._session.post(
                self._mcp_url, json=payload
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(
                        f"Fiddler MCP returned {resp.status}: {text[:200]}"
                    )
                data = await resp.json()
                return data.get("result", {}).get("entries", [])
        else:
            # Mock mode for testing without Fiddler
            return self._generate_mock_traffic(limit)

    def _generate_mock_traffic(self, count: int) -> List[Dict[str, Any]]:
        """Generate mock traffic data for offline testing."""
        entries = []
        base_time = time.time()
        for i in range(min(count, 5)):
            puuid = f"mock-puuid-{i:04d}-abcd-1234-efgh-{i*1111:012d}"
            entries.append({
                "id": f"mock-entry-{i}",
                "timestamp": datetime.fromtimestamp(
                    base_time - i * 60, tz=timezone.utc
                ).isoformat(),
                "method": "GET",
                "url": (
                    f"https://lol.sgp.qq.com/lol-match-history/v1/"
                    f"products/lol/{puuid}/matches?begIndex=0&endIndex=20"
                ),
                "statusCode": 200,
                "requestHeaders": {
                    "Authorization": "Bearer [REDACTED]",
                    "Accept": "application/json",
                },
                "responseHeaders": {
                    "Content-Type": "application/json",
                    "X-Riot-Request-Id": f"req-{i:08d}",
                },
                "requestBody": None,
                "responseBody": json.dumps({
                    "platformId": "NA1",
                    "accountId": i * 100000,
                    "games": {
                        "gameCount": 20,
                        "games": [
                            {
                                "gameId": 5000000000 + i * 100 + j,
                                "champion": 100 + j,
                                "queue": 420,
                                "season": 14,
                                "timestamp": int(
                                    (base_time - j * 3600) * 1000
                                ),
                                "role": "SOLO",
                                "lane": "MID",
                            }
                            for j in range(20)
                        ],
                    },
                }),
                "durationMs": 45.2 + i * 3.1,
                "processName": "LeagueClient.exe",
            })
        return entries

    def parse_traffic_entry(
        self, raw: Dict[str, Any]
    ) -> Optional[InterceptedEntry]:
        """Parse raw Fiddler traffic data into InterceptedEntry."""
        try:
            url = raw.get("url", "")
            puuid = None
            game_id = None

            match_hist = MATCH_HISTORY_PATTERN.search(url)
            if match_hist:
                puuid = match_hist.group("puuid")

            match_detail = MATCH_DETAIL_PATTERN.search(url)
            if match_detail:
                game_id = match_detail.group("game_id")

            ts_str = raw.get("timestamp", "")
            try:
                timestamp = datetime.fromisoformat(
                    ts_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                timestamp = datetime.now(timezone.utc)

            return InterceptedEntry(
                entry_id=raw.get("id", f"unknown-{time.time_ns()}"),
                timestamp=timestamp,
                method=raw.get("method", "GET"),
                url=url,
                status_code=raw.get("statusCode", 0),
                request_headers=raw.get("requestHeaders", {}),
                response_headers=raw.get("responseHeaders", {}),
                request_body=raw.get("requestBody"),
                response_body=raw.get("responseBody"),
                duration_ms=raw.get("durationMs", 0.0),
                process_name=raw.get("processName", "unknown"),
                puuid=puuid,
                game_id=game_id,
            )
        except Exception as exc:
            logger.error("Failed to parse traffic entry: %s", exc)
            return None


# ---------------------------------------------------------------------------
# MatchHistoryHttpInterceptor — Main module class
# ---------------------------------------------------------------------------
class MatchHistoryHttpInterceptor:
    """
    Production-grade HTTP interceptor for LoL match history traffic.

    Lifecycle (mirrors Seraphine connector):
        1. __init__(): configure, create buffer
        2. start(): connect to Fiddler MCP, begin polling
        3. poll loop: fetch → parse → filter → buffer
        4. stop(): drain, disconnect, persist stats

    Thread safety: The RingBuffer handles its own locking. The poll loop
    runs in an asyncio task. Consumer modules call get_* methods which
    are read-only against the RingBuffer.
    """

    def __init__(
        self,
        fiddler_url: str = FIDDLER_MCP_DEFAULT_URL,
        fiddler_api_key: Optional[str] = None,
        buffer_capacity: int = RING_BUFFER_CAPACITY,
        poll_interval: float = FIDDLER_MCP_POLL_INTERVAL,
        event_callbacks: Optional[Dict[str, Callable]] = None,
    ):
        self._state = InterceptorState.IDLE
        self._fiddler_client = FiddlerMCPClient(
            mcp_url=fiddler_url, api_key=fiddler_api_key
        )
        self._buffer = RingBuffer(capacity=buffer_capacity)
        self._poll_interval = poll_interval
        self._stats = InterceptorStats()
        self._poll_task: Optional[asyncio.Task] = None
        self._start_time: Optional[float] = None
        self._callbacks = event_callbacks or {}
        self._shutdown_event = asyncio.Event()

        logger.info(
            "MatchHistoryHttpInterceptor initialized: "
            "fiddler=%s buffer_cap=%d poll=%.1fs",
            fiddler_url, buffer_capacity, poll_interval,
        )

    # -- Properties --
    @property
    def state(self) -> InterceptorState:
        return self._state

    @property
    def stats(self) -> InterceptorStats:
        if self._start_time:
            self._stats.uptime_seconds = time.monotonic() - self._start_time
        return self._stats

    @property
    def buffer(self) -> RingBuffer:
        return self._buffer

    # -- Lifecycle (Seraphine autoStart/start/close pattern) --
    async def start(self):
        """
        Start the interceptor. Connects to Fiddler MCP and begins polling.
        Mirrors Seraphine connector.autoStart() → start() flow.
        """
        if self._state not in (InterceptorState.IDLE, InterceptorState.CLOSED):
            logger.warning("Cannot start: current state is %s", self._state)
            return

        self._state = InterceptorState.CONNECTING
        self._start_time = time.monotonic()
        self._shutdown_event.clear()

        try:
            await self._fiddler_client.connect()
            self._state = InterceptorState.ACTIVE
            self._poll_task = asyncio.create_task(
                self._poll_loop(), name="fiddler-poll-loop"
            )
            logger.info("Interceptor started, polling Fiddler MCP")
            await self._emit("on_start", {"state": self._state.name})
        except Exception as exc:
            self._state = InterceptorState.DEGRADED
            logger.error("Failed to connect to Fiddler MCP: %s", exc)
            await self._emit("on_error", {"error": str(exc)})

    async def stop(self):
        """
        Graceful shutdown. Mirrors Seraphine connector.close().
        Drains pending work, disconnects Fiddler, persists stats.
        """
        if self._state in (InterceptorState.CLOSED, InterceptorState.IDLE):
            return

        self._state = InterceptorState.SHUTTING_DOWN
        self._shutdown_event.set()

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        await self._fiddler_client.disconnect()
        self._state = InterceptorState.CLOSED

        final_stats = self.stats.to_dict()
        logger.info("Interceptor stopped. Final stats: %s", final_stats)
        await self._emit("on_stop", final_stats)

    # -- Polling Loop --
    async def _poll_loop(self):
        """
        Main polling loop: fetch traffic from Fiddler MCP at intervals.

        Handles Fiddler unavailability by switching to DEGRADED state
        and continuing to serve cached data. Recovers automatically
        when Fiddler becomes available again.
        """
        consecutive_errors = 0
        max_consecutive_errors = 10

        while not self._shutdown_event.is_set():
            try:
                raw_entries = await self._fiddler_client.fetch_recent_traffic(
                    process_filter="LeagueClient",
                    url_filter="lol-match-history",
                    limit=50,
                )
                self._stats.fiddler_polls += 1
                consecutive_errors = 0

                if self._state == InterceptorState.DEGRADED:
                    self._state = InterceptorState.ACTIVE
                    logger.info("Recovered from degraded state")

                new_count = 0
                for raw in raw_entries:
                    entry = self._fiddler_client.parse_traffic_entry(raw)
                    if entry and self._is_relevant(entry):
                        added = self._buffer.add(entry)
                        if added:
                            new_count += 1
                            self._stats.total_intercepted += 1
                            self._stats.last_intercept_time = entry.timestamp
                            self._classify_and_count(entry)
                            await self._emit(
                                "on_intercept",
                                {
                                    "entry_id": entry.entry_id,
                                    "url": entry.url,
                                    "puuid": entry.puuid,
                                },
                            )

                if new_count > 0:
                    logger.debug(
                        "Poll: %d new entries (buffer: %d/%d)",
                        new_count, len(self._buffer), self._buffer.capacity,
                    )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                consecutive_errors += 1
                self._stats.errors += 1
                logger.error("Poll error (%d): %s", consecutive_errors, exc)

                if consecutive_errors >= max_consecutive_errors:
                    self._state = InterceptorState.DEGRADED
                    logger.warning(
                        "Entering DEGRADED state after %d consecutive errors",
                        consecutive_errors,
                    )
                    await self._emit(
                        "on_degraded",
                        {"errors": consecutive_errors, "last": str(exc)},
                    )

            await asyncio.sleep(self._poll_interval)

    # -- Internal Helpers --
    def _is_relevant(self, entry: InterceptedEntry) -> bool:
        """Filter for match-history related traffic only."""
        return (
            entry.is_match_history
            or entry.is_match_detail
        ) and entry.status_code in (200, 304)

    def _classify_and_count(self, entry: InterceptedEntry):
        """Update stats counters based on entry type."""
        if entry.is_match_history:
            self._stats.match_history_hits += 1
        elif entry.is_match_detail:
            self._stats.match_detail_hits += 1

    async def _emit(self, event: str, data: Dict[str, Any]):
        """Invoke registered callbacks (Seraphine signalBus pattern)."""
        callback = self._callbacks.get(event)
        if callback:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as exc:
                logger.error("Callback %s error: %s", event, exc)

    # -- Public Query API --
    def get_recent_matches(self, count: int = 20) -> List[InterceptedEntry]:
        """Get recent match history entries from buffer."""
        return self._buffer.get_match_histories()[:count]

    def get_summoner_history(self, puuid: str) -> List[InterceptedEntry]:
        """Get all cached match history for a specific summoner."""
        return self._buffer.get_by_puuid(puuid)

    def get_match_detail(self, game_id: str) -> Optional[InterceptedEntry]:
        """Get cached detail for a specific game."""
        results = self._buffer.get_by_game_id(game_id)
        return results[0] if results else None

    def export_buffer_snapshot(self) -> Dict[str, Any]:
        """Export current buffer state for persistence."""
        return {
            "interceptor_state": self._state.name,
            "stats": self.stats.to_dict(),
            "buffer": self._buffer.snapshot(),
        }


# ---------------------------------------------------------------------------
# Module-level convenience factory
# ---------------------------------------------------------------------------
_default_interceptor: Optional[MatchHistoryHttpInterceptor] = None


def get_interceptor() -> MatchHistoryHttpInterceptor:
    """Get or create the singleton interceptor instance."""
    global _default_interceptor
    if _default_interceptor is None:
        _default_interceptor = MatchHistoryHttpInterceptor()
    return _default_interceptor


async def quick_start(
    api_key: Optional[str] = None,
) -> MatchHistoryHttpInterceptor:
    """One-call setup for rapid prototyping."""
    interceptor = MatchHistoryHttpInterceptor(fiddler_api_key=api_key)
    await interceptor.start()
    return interceptor


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
async def _self_test():
    """Validate interceptor lifecycle and buffer operations."""
    logger.info("Running M886 self-test...")

    # Test RingBuffer
    buf = RingBuffer(capacity=5)
    for i in range(8):
        entry = InterceptedEntry(
            entry_id=f"test-{i}",
            timestamp=datetime.now(timezone.utc),
            method="GET",
            url=f"https://example.com/lol-match-history/v1/products/lol/"
                f"puuid-{i:04d}/matches",
            status_code=200,
            request_headers={},
            response_headers={},
            request_body=None,
            response_body=json.dumps({"gameId": i}),
            duration_ms=10.0,
            process_name="LeagueClient.exe",
            puuid=f"puuid-{i:04d}",
        )
        buf.add(entry)

    assert len(buf) == 5, f"Expected 5, got {len(buf)}"
    assert buf.get_recent(2)[0].entry_id == "test-7"
    assert len(buf.get_match_histories()) == 5

    # Test interceptor lifecycle
    interceptor = MatchHistoryHttpInterceptor(poll_interval=0.1)
    await interceptor.start()
    assert interceptor.state == InterceptorState.ACTIVE

    await asyncio.sleep(0.5)  # Let a few polls happen
    stats = interceptor.stats
    assert stats.fiddler_polls > 0

    await interceptor.stop()
    assert interceptor.state == InterceptorState.CLOSED

    logger.info("M886 self-test PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(_self_test())

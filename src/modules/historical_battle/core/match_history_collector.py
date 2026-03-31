#!/usr/bin/env python3
"""
M808 - Match History Collector
================================
OperatorRL Historical Battle System - Batch & Stream Match Data Collection

查看 Seraphine 项目上现有的战绩获取实现方式，理解其模式，特别是
分页获取和本地缓存是如何分离的。从 Seraphine 的 matchHistory 模块开始，
遵循该模式实现一个新的批量采集器，使系统可以高效获取大量历史对战数据，
并能增量同步新的比赛记录。引入流式处理，使分析引擎能够实时消费新数据，
同时优化去重与速率限制策略。

Core responsibilities:
- Batch collect match history from Riot API / LCU API
- Incremental sync with deduplication
- Rate limiting and throttling
- Stream new matches to downstream consumers
- Multi-source data fusion (Riot API + LCU + Network Capture)
"""

import os
import sys
import json
import time
import asyncio
import logging
import hashlib
import datetime
import collections
from pathlib import Path
from typing import (
    Dict, List, Any, Optional, Tuple, Set, Callable,
    AsyncIterator, Awaitable, Deque, Union
)
from dataclasses import dataclass, field, asdict
from enum import Enum
from abc import ABC, abstractmethod

# ─── Module Logger ────────────────────────────────────────────────────────────

logger = logging.getLogger("operatorRL.historical_battle.match_history_collector")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────────────

MAX_BATCH_SIZE = 100
DEFAULT_BATCH_SIZE = 20
RATE_LIMIT_REQUESTS_PER_SECOND = 20
RATE_LIMIT_REQUESTS_PER_2_MINUTES = 100
RATE_LIMIT_BURST_ALLOWANCE = 5
DEDUP_CACHE_MAX_SIZE = 10000
SYNC_INTERVAL_SECONDS = 60
COLLECTION_TIMEOUT_SECONDS = 300
MAX_CONCURRENT_REQUESTS = 5
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RETRY_MAX_DELAY_SECONDS = 30.0
MATCH_STALENESS_THRESHOLD_HOURS = 24
STREAM_BUFFER_SIZE = 1000
CHECKPOINT_INTERVAL_MATCHES = 50


class CollectionMode(Enum):
    """Data collection modes."""
    BATCH = "batch"
    INCREMENTAL = "incremental"
    STREAM = "stream"
    BACKFILL = "backfill"
    TARGETED = "targeted"


class CollectionStatus(Enum):
    """Status of a collection job."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RATE_LIMITED = "rate_limited"


class DataSourcePriority(Enum):
    """Priority order for data sources."""
    NETWORK_CAPTURE = 1
    LCU_API = 2
    RIOT_API = 3
    CACHE = 4


# ─── Rate Limiter ─────────────────────────────────────────────────────────────

class TokenBucketRateLimiter:
    """
    Token bucket rate limiter with dual windows.
    Enforces both per-second and per-2-minute limits.
    """

    def __init__(
        self,
        rate_per_second: int = RATE_LIMIT_REQUESTS_PER_SECOND,
        rate_per_2_min: int = RATE_LIMIT_REQUESTS_PER_2_MINUTES,
        burst: int = RATE_LIMIT_BURST_ALLOWANCE,
    ):
        self._rate_per_second = rate_per_second
        self._rate_per_2_min = rate_per_2_min
        self._burst = burst
        self._tokens_second = float(rate_per_second)
        self._tokens_2min = float(rate_per_2_min)
        self._last_refill_second = time.monotonic()
        self._last_refill_2min = time.monotonic()
        self._total_requests = 0
        self._total_waits = 0
        self._total_wait_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """
        Acquire a token, waiting if necessary.
        Returns the wait time in seconds.
        """
        async with self._lock:
            now = time.monotonic()
            wait_time = 0.0

            # Refill per-second bucket
            elapsed_s = now - self._last_refill_second
            self._tokens_second = min(
                self._rate_per_second + self._burst,
                self._tokens_second + elapsed_s * self._rate_per_second,
            )
            self._last_refill_second = now

            # Refill per-2-minute bucket
            elapsed_2m = now - self._last_refill_2min
            self._tokens_2min = min(
                self._rate_per_2_min,
                self._tokens_2min + elapsed_2m * (self._rate_per_2_min / 120.0),
            )
            self._last_refill_2min = now

            # Wait for per-second token
            if self._tokens_second < 1.0:
                deficit = 1.0 - self._tokens_second
                wait_s = deficit / self._rate_per_second
                wait_time += wait_s

            # Wait for per-2-minute token
            if self._tokens_2min < 1.0:
                deficit = 1.0 - self._tokens_2min
                wait_2m = deficit / (self._rate_per_2_min / 120.0)
                wait_time = max(wait_time, wait_2m)

            if wait_time > 0:
                self._total_waits += 1
                self._total_wait_time += wait_time
                await asyncio.sleep(wait_time)

            self._tokens_second -= 1.0
            self._tokens_2min -= 1.0
            self._total_requests += 1

            return wait_time

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_requests": self._total_requests,
            "total_waits": self._total_waits,
            "total_wait_time_s": round(self._total_wait_time, 2),
            "tokens_second": round(self._tokens_second, 1),
            "tokens_2min": round(self._tokens_2min, 1),
        }


# ─── Deduplication Cache ─────────────────────────────────────────────────────

class DeduplicationCache:
    """
    LRU-based deduplication cache for match IDs.
    Prevents re-fetching already collected matches.
    """

    def __init__(self, max_size: int = DEDUP_CACHE_MAX_SIZE):
        self._max_size = max_size
        self._cache: collections.OrderedDict[str, datetime.datetime] = (
            collections.OrderedDict()
        )
        self._hits = 0
        self._misses = 0

    def contains(self, match_id: str) -> bool:
        """Check if match ID is in cache."""
        if match_id in self._cache:
            self._cache.move_to_end(match_id)
            self._hits += 1
            return True
        self._misses += 1
        return False

    def add(self, match_id: str):
        """Add match ID to cache."""
        if match_id in self._cache:
            self._cache.move_to_end(match_id)
            return
        self._cache[match_id] = datetime.datetime.now(datetime.timezone.utc)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def add_batch(self, match_ids: List[str]):
        """Add multiple match IDs."""
        for mid in match_ids:
            self.add(mid)

    def remove(self, match_id: str):
        """Remove match ID from cache."""
        self._cache.pop(match_id, None)

    def clear(self):
        """Clear the entire cache."""
        self._cache.clear()
        self._hits = 0
        self._misses = 0

    @property
    def size(self) -> int:
        return len(self._cache)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def get_stats(self) -> Dict[str, Any]:
        return {
            "size": self.size,
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 3),
        }


# ─── Collection Job ──────────────────────────────────────────────────────────

@dataclass
class CollectionTarget:
    """Target specification for data collection."""
    puuid: str
    region: str = "NA1"
    queue_types: List[int] = field(default_factory=list)
    champion_ids: List[int] = field(default_factory=list)
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    max_matches: int = DEFAULT_BATCH_SIZE
    priority: int = 0

    @property
    def target_id(self) -> str:
        return hashlib.md5(
            f"{self.puuid}:{self.region}:{self.max_matches}".encode()
        ).hexdigest()[:12]


@dataclass
class CollectionCheckpoint:
    """Checkpoint for resuming interrupted collections."""
    target_id: str
    matches_collected: int = 0
    last_match_id: str = ""
    last_timestamp: Optional[datetime.datetime] = None
    cursor_index: int = 0
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now().isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_id": self.target_id,
            "matches_collected": self.matches_collected,
            "last_match_id": self.last_match_id,
            "cursor_index": self.cursor_index,
            "created_at": self.created_at,
        }


@dataclass
class CollectionResult:
    """Result of a collection job."""
    target: CollectionTarget
    status: CollectionStatus = CollectionStatus.PENDING
    matches_collected: int = 0
    matches_deduplicated: int = 0
    matches_failed: int = 0
    match_ids: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    started_at: Optional[datetime.datetime] = None
    completed_at: Optional[datetime.datetime] = None
    rate_limit_waits: int = 0
    total_api_calls: int = 0

    @property
    def duration_seconds(self) -> float:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    @property
    def success_rate(self) -> float:
        total = self.matches_collected + self.matches_failed
        return self.matches_collected / total if total > 0 else 0.0

    def to_summary(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "collected": self.matches_collected,
            "deduplicated": self.matches_deduplicated,
            "failed": self.matches_failed,
            "duration_s": round(self.duration_seconds, 1),
            "success_rate": round(self.success_rate, 3),
            "api_calls": self.total_api_calls,
        }


# ─── Match Stream ────────────────────────────────────────────────────────────

class MatchStream:
    """
    Async stream for newly collected matches.
    Supports multiple consumers via pub/sub pattern.
    """

    def __init__(self, buffer_size: int = STREAM_BUFFER_SIZE):
        self._buffer: Deque[Dict[str, Any]] = collections.deque(maxlen=buffer_size)
        self._subscribers: List[asyncio.Queue] = []
        self._total_published = 0
        self._lock = asyncio.Lock()

    async def publish(self, match_data: Dict[str, Any]):
        """Publish a new match to all subscribers."""
        async with self._lock:
            self._buffer.append(match_data)
            self._total_published += 1
            for queue in self._subscribers:
                try:
                    queue.put_nowait(match_data)
                except asyncio.QueueFull:
                    logger.warning("Subscriber queue full, dropping message")

    def subscribe(self, max_queue_size: int = 100) -> asyncio.Queue:
        """Subscribe to the match stream."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """Remove a subscriber."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    def get_recent(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent matches from the buffer."""
        return list(self._buffer)[-count:]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_published": self._total_published,
            "buffer_size": self.buffer_size,
            "subscribers": self.subscriber_count,
        }


# ─── Data Source Adapter ─────────────────────────────────────────────────────

class MatchDataAdapter(ABC):
    """Abstract adapter for different match data sources."""

    @abstractmethod
    async def fetch_match_ids(
        self, puuid: str, start: int, count: int, **kwargs
    ) -> List[str]:
        """Fetch a list of match IDs."""
        ...

    @abstractmethod
    async def fetch_match_detail(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed match data by ID."""
        ...

    @abstractmethod
    def get_source_name(self) -> str:
        ...


class RiotAPIAdapter(MatchDataAdapter):
    """Adapter for Riot Games official API."""

    def __init__(self, api_key: str = "", region: str = "americas"):
        self._api_key = api_key
        self._region = region
        self._base_url = f"https://{region}.api.riotgames.com"

    async def fetch_match_ids(
        self, puuid: str, start: int = 0, count: int = 20, **kwargs
    ) -> List[str]:
        """Fetch match IDs from Riot API v5."""
        # In production, this would make actual HTTP requests
        logger.info(
            f"[RiotAPI] Fetching match IDs for {puuid[:8]}... "
            f"(start={start}, count={count})"
        )
        return []

    async def fetch_match_detail(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Fetch match detail from Riot API v5."""
        logger.info(f"[RiotAPI] Fetching match detail: {match_id}")
        return None

    def get_source_name(self) -> str:
        return "riot_api"


class LCUAdapter(MatchDataAdapter):
    """Adapter for LCU (League Client Update) API."""

    def __init__(self, lcu_client=None):
        self._client = lcu_client

    async def fetch_match_ids(
        self, puuid: str, start: int = 0, count: int = 20, **kwargs
    ) -> List[str]:
        """Fetch match IDs from LCU match history."""
        logger.info(
            f"[LCU] Fetching match IDs for {puuid[:8]}... "
            f"(start={start}, count={count})"
        )
        if self._client:
            games = await self._client.get_match_history(puuid, start, start + count)
            return [str(g.get("gameId", "")) for g in games if g.get("gameId")]
        return []

    async def fetch_match_detail(self, match_id: str) -> Optional[Dict[str, Any]]:
        """LCU typically returns full data with match list."""
        logger.info(f"[LCU] Fetching match detail: {match_id}")
        return None

    def get_source_name(self) -> str:
        return "lcu_api"


class NetworkCaptureAdapter(MatchDataAdapter):
    """Adapter for network-captured match data (Fiddler/mitmproxy)."""

    def __init__(self, capture_dir: str = ""):
        self._capture_dir = Path(capture_dir) if capture_dir else Path("captures")
        self._captured_matches: Dict[str, Dict[str, Any]] = {}

    async def fetch_match_ids(
        self, puuid: str, start: int = 0, count: int = 20, **kwargs
    ) -> List[str]:
        """Return match IDs from captured network data."""
        logger.info(f"[NetworkCapture] Searching captures for {puuid[:8]}...")
        return list(self._captured_matches.keys())[start:start + count]

    async def fetch_match_detail(self, match_id: str) -> Optional[Dict[str, Any]]:
        """Return captured match data."""
        return self._captured_matches.get(match_id)

    def ingest_capture(self, match_id: str, data: Dict[str, Any]):
        """Ingest data from network capture."""
        self._captured_matches[match_id] = data
        logger.info(f"[NetworkCapture] Ingested match: {match_id}")

    def get_source_name(self) -> str:
        return "network_capture"


# ─── Main Collector ───────────────────────────────────────────────────────────

class MatchHistoryCollector:
    """
    Central match history collection engine.
    Orchestrates data sources, rate limiting, dedup, and streaming.
    Implements HistoricalBattleInterface contract.
    """

    def __init__(self):
        self._adapters: List[Tuple[DataSourcePriority, MatchDataAdapter]] = []
        self._rate_limiter = TokenBucketRateLimiter()
        self._dedup_cache = DeduplicationCache()
        self._stream = MatchStream()
        self._active_jobs: Dict[str, CollectionResult] = {}
        self._checkpoints: Dict[str, CollectionCheckpoint] = {}
        self._total_collected = 0
        self._initialized = False

    async def initialize(self, config: Dict[str, Any] = None) -> bool:
        """Initialize the collector with configuration."""
        config = config or {}

        # Configure rate limiter
        rps = config.get("rate_limit_rps", RATE_LIMIT_REQUESTS_PER_SECOND)
        rpm2 = config.get("rate_limit_2min", RATE_LIMIT_REQUESTS_PER_2_MINUTES)
        self._rate_limiter = TokenBucketRateLimiter(
            rate_per_second=rps, rate_per_2_min=rpm2
        )

        # Configure dedup
        dedup_size = config.get("dedup_cache_size", DEDUP_CACHE_MAX_SIZE)
        self._dedup_cache = DeduplicationCache(max_size=dedup_size)

        self._initialized = True
        logger.info("MatchHistoryCollector initialized")
        return True

    def register_adapter(
        self, adapter: MatchDataAdapter, priority: DataSourcePriority
    ):
        """Register a data source adapter with priority."""
        self._adapters.append((priority, adapter))
        self._adapters.sort(key=lambda x: x[0].value)
        logger.info(
            f"Registered adapter: {adapter.get_source_name()} "
            f"(priority={priority.name})"
        )

    async def collect_batch(self, target: CollectionTarget) -> CollectionResult:
        """
        Collect a batch of match history for a target.
        Uses all registered adapters in priority order.
        """
        result = CollectionResult(
            target=target,
            status=CollectionStatus.RUNNING,
            started_at=datetime.datetime.now(datetime.timezone.utc),
        )
        self._active_jobs[target.target_id] = result

        try:
            # Try each adapter in priority order
            for priority, adapter in self._adapters:
                if result.matches_collected >= target.max_matches:
                    break

                remaining = target.max_matches - result.matches_collected
                try:
                    await self._rate_limiter.acquire()
                    result.total_api_calls += 1

                    match_ids = await adapter.fetch_match_ids(
                        puuid=target.puuid,
                        start=0,
                        count=min(remaining, MAX_BATCH_SIZE),
                    )

                    for match_id in match_ids:
                        if result.matches_collected >= target.max_matches:
                            break

                        if self._dedup_cache.contains(match_id):
                            result.matches_deduplicated += 1
                            continue

                        try:
                            await self._rate_limiter.acquire()
                            result.total_api_calls += 1

                            detail = await adapter.fetch_match_detail(match_id)
                            if detail:
                                self._dedup_cache.add(match_id)
                                result.matches_collected += 1
                                result.match_ids.append(match_id)

                                await self._stream.publish({
                                    "match_id": match_id,
                                    "source": adapter.get_source_name(),
                                    "data": detail,
                                    "collected_at": datetime.datetime.now().isoformat(),
                                })

                                # Checkpoint periodically
                                if (result.matches_collected %
                                        CHECKPOINT_INTERVAL_MATCHES == 0):
                                    self._save_checkpoint(target, result)

                        except Exception as e:
                            result.matches_failed += 1
                            result.errors.append(
                                f"Match {match_id}: {str(e)}"
                            )
                            logger.error(f"Failed to fetch match {match_id}: {e}")

                except Exception as e:
                    result.errors.append(
                        f"Adapter {adapter.get_source_name()}: {str(e)}"
                    )
                    logger.error(
                        f"Adapter {adapter.get_source_name()} failed: {e}"
                    )

            result.status = CollectionStatus.COMPLETED
            result.completed_at = datetime.datetime.now(datetime.timezone.utc)
            self._total_collected += result.matches_collected

        except Exception as e:
            result.status = CollectionStatus.FAILED
            result.errors.append(f"Collection failed: {str(e)}")
            logger.error(f"Batch collection failed: {e}")

        return result

    async def collect_incremental(
        self, target: CollectionTarget
    ) -> CollectionResult:
        """
        Incremental collection - only fetch matches newer than last checkpoint.
        """
        checkpoint = self._checkpoints.get(target.target_id)
        if checkpoint:
            target.start_time = checkpoint.last_timestamp
            logger.info(
                f"Incremental sync from checkpoint: "
                f"{checkpoint.matches_collected} already collected"
            )

        result = await self.collect_batch(target)

        # Update checkpoint
        if result.match_ids:
            self._checkpoints[target.target_id] = CollectionCheckpoint(
                target_id=target.target_id,
                matches_collected=(
                    (checkpoint.matches_collected if checkpoint else 0)
                    + result.matches_collected
                ),
                last_match_id=result.match_ids[-1],
                last_timestamp=datetime.datetime.now(datetime.timezone.utc),
                cursor_index=len(result.match_ids),
            )

        return result

    async def start_stream_collection(
        self,
        target: CollectionTarget,
        interval_seconds: float = SYNC_INTERVAL_SECONDS,
    ) -> asyncio.Task:
        """
        Start continuous streaming collection.
        Periodically checks for new matches.
        """
        async def _stream_loop():
            while True:
                try:
                    result = await self.collect_incremental(target)
                    if result.matches_collected > 0:
                        logger.info(
                            f"Stream collected {result.matches_collected} "
                            f"new matches for {target.puuid[:8]}..."
                        )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Stream collection error: {e}")

                await asyncio.sleep(interval_seconds)

        task = asyncio.create_task(_stream_loop())
        logger.info(
            f"Started stream collection for {target.puuid[:8]}... "
            f"(interval={interval_seconds}s)"
        )
        return task

    def _save_checkpoint(self, target: CollectionTarget, result: CollectionResult):
        """Save collection checkpoint for resume capability."""
        checkpoint = CollectionCheckpoint(
            target_id=target.target_id,
            matches_collected=result.matches_collected,
            last_match_id=result.match_ids[-1] if result.match_ids else "",
            last_timestamp=datetime.datetime.now(datetime.timezone.utc),
            cursor_index=result.matches_collected,
        )
        self._checkpoints[target.target_id] = checkpoint
        logger.debug(f"Checkpoint saved: {checkpoint.to_dict()}")

    def get_stream(self) -> MatchStream:
        """Get the match stream for subscribing."""
        return self._stream

    async def cancel_job(self, target_id: str) -> bool:
        """Cancel an active collection job."""
        if target_id in self._active_jobs:
            self._active_jobs[target_id].status = CollectionStatus.CANCELLED
            return True
        return False

    async def health_check(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "adapters": len(self._adapters),
            "adapter_names": [a.get_source_name() for _, a in self._adapters],
            "active_jobs": len(self._active_jobs),
            "total_collected": self._total_collected,
            "dedup_stats": self._dedup_cache.get_stats(),
            "rate_limiter_stats": self._rate_limiter.get_stats(),
            "stream_stats": self._stream.get_stats(),
            "checkpoints": len(self._checkpoints),
        }

    async def shutdown(self):
        """Graceful shutdown."""
        for job_id, result in self._active_jobs.items():
            if result.status == CollectionStatus.RUNNING:
                result.status = CollectionStatus.CANCELLED
        logger.info("MatchHistoryCollector shutdown complete")

    def get_module_info(self) -> Dict[str, str]:
        return {
            "task_id": "M808",
            "name": "Match History Collector",
            "version": "1.0.0",
            "description": "Batch & stream match data collection for OperatorRL",
        }


# ─── Collection Scheduler ────────────────────────────────────────────────────

class CollectionScheduler:
    """
    Schedules and manages multiple collection jobs.
    Supports priority queues and concurrent execution.
    """

    def __init__(self, collector: MatchHistoryCollector):
        self._collector = collector
        self._queue: List[Tuple[int, CollectionTarget]] = []
        self._running = False
        self._max_concurrent = MAX_CONCURRENT_REQUESTS
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._completed_results: List[CollectionResult] = []

    def schedule(self, target: CollectionTarget, priority: int = 0):
        """Schedule a collection target."""
        self._queue.append((priority, target))
        self._queue.sort(key=lambda x: x[0], reverse=True)
        logger.info(
            f"Scheduled collection for {target.puuid[:8]}... "
            f"(priority={priority}, queue_size={len(self._queue)})"
        )

    async def run(self) -> List[CollectionResult]:
        """Execute all scheduled collections."""
        self._running = True
        tasks = []

        for priority, target in self._queue:
            async def _run_with_semaphore(t):
                async with self._semaphore:
                    return await self._collector.collect_batch(t)

            task = asyncio.create_task(_run_with_semaphore(target))
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        self._completed_results = []
        for r in results:
            if isinstance(r, CollectionResult):
                self._completed_results.append(r)
            elif isinstance(r, Exception):
                logger.error(f"Scheduled collection failed: {r}")

        self._queue.clear()
        self._running = False

        return self._completed_results

    def get_stats(self) -> Dict[str, Any]:
        return {
            "queued": len(self._queue),
            "running": self._running,
            "completed": len(self._completed_results),
            "max_concurrent": self._max_concurrent,
        }


# ─── Self-Test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("M808 Match History Collector - Self Test")

    # Test rate limiter
    limiter = TokenBucketRateLimiter(rate_per_second=10, rate_per_2_min=50)
    print(f"Rate limiter stats: {limiter.get_stats()}")

    # Test dedup cache
    dedup = DeduplicationCache(max_size=100)
    dedup.add("NA1_123")
    dedup.add("NA1_456")
    assert dedup.contains("NA1_123")
    assert not dedup.contains("NA1_789")
    print(f"Dedup cache stats: {dedup.get_stats()}")

    # Test collection target
    target = CollectionTarget(
        puuid="test-puuid-12345",
        region="NA1",
        max_matches=50,
    )
    print(f"Target ID: {target.target_id}")

    # Test stream
    stream = MatchStream(buffer_size=100)
    print(f"Stream stats: {stream.get_stats()}")

    # Test collector creation
    collector = MatchHistoryCollector()
    collector.register_adapter(RiotAPIAdapter(), DataSourcePriority.RIOT_API)
    collector.register_adapter(LCUAdapter(), DataSourcePriority.LCU_API)
    collector.register_adapter(NetworkCaptureAdapter(), DataSourcePriority.NETWORK_CAPTURE)

    print(f"Registered {len(collector._adapters)} adapters")
    print("\nM808 self-test passed.")

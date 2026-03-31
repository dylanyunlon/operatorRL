#!/usr/bin/env python3
"""
M887 — SummonerProfileCrawler
==============================
Batch-crawls opponent summoner profiles via Fiddler-intercepted Riot API data,
respecting rate limits. Follows Seraphine connector.py getCurrentSummoner and
getProfileIcon patterns for session management and structured data extraction.

Dependencies: M886 (MatchHistoryHttpInterceptor)
Reference: Seraphine connector.py::getCurrentSummoner, getProfileIcon
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum, auto
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("M887.SummonerProfileCrawler")

# Rate limit: Riot API allows 20 requests/second, 100 requests/2 minutes
RATE_LIMIT_PER_SECOND = 18  # conservative margin
RATE_LIMIT_WINDOW_MS = 1000
CRAWL_BATCH_SIZE = 10
PROFILE_CACHE_TTL_SECONDS = 300  # 5 min cache
MAX_CONCURRENT_REQUESTS = 5


class CrawlPriority(Enum):
    IMMEDIATE = 0   # current game opponents
    HIGH = 1        # recently seen players
    NORMAL = 2      # background crawl
    LOW = 3         # historical backfill


class CrawlStatus(Enum):
    QUEUED = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()
    RATE_LIMITED = auto()


@dataclass
class SummonerProfile:
    """Structured summoner data extracted from Riot API responses."""
    puuid: str
    summoner_id: str
    account_id: str
    summoner_name: str
    tag_line: str
    profile_icon_id: int
    summoner_level: int
    ranked_solo: Optional[Dict[str, Any]] = None
    ranked_flex: Optional[Dict[str, Any]] = None
    top_champions: List[Dict[str, Any]] = field(default_factory=list)
    recent_matches_summary: Optional[Dict[str, Any]] = None
    last_updated: Optional[datetime] = None
    data_source: str = "fiddler_intercept"

    @property
    def display_name(self) -> str:
        return f"{self.summoner_name}#{self.tag_line}" if self.tag_line else self.summoner_name

    @property
    def solo_rank(self) -> str:
        if not self.ranked_solo:
            return "Unranked"
        tier = self.ranked_solo.get("tier", "UNRANKED")
        division = self.ranked_solo.get("rank", "")
        lp = self.ranked_solo.get("leaguePoints", 0)
        return f"{tier} {division} {lp}LP"

    @property
    def winrate_solo(self) -> float:
        if not self.ranked_solo:
            return 0.0
        wins = self.ranked_solo.get("wins", 0)
        losses = self.ranked_solo.get("losses", 0)
        total = wins + losses
        return (wins / total * 100) if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "puuid": self.puuid,
            "summoner_id": self.summoner_id,
            "account_id": self.account_id,
            "display_name": self.display_name,
            "level": self.summoner_level,
            "icon_id": self.profile_icon_id,
            "solo_rank": self.solo_rank,
            "winrate": round(self.winrate_solo, 1),
            "top_champions": self.top_champions[:5],
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


@dataclass
class CrawlRequest:
    """Single crawl request in the priority queue."""
    puuid: str
    priority: CrawlPriority
    status: CrawlStatus = CrawlStatus.QUEUED
    created_at: float = field(default_factory=time.monotonic)
    attempts: int = 0
    max_attempts: int = 3
    last_error: Optional[str] = None
    callback: Optional[Callable] = None

    def __lt__(self, other: CrawlRequest) -> bool:
        return self.priority.value < other.priority.value


class RateLimiter:
    """
    Token-bucket rate limiter for Riot API compliance.
    Prevents exceeding 20 req/s with a sliding window approach.
    """

    def __init__(self, max_per_second: int = RATE_LIMIT_PER_SECOND):
        self._max_per_second = max_per_second
        self._timestamps: Deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Wait until a request slot is available. Returns wait time in ms."""
        async with self._lock:
            now = time.monotonic()
            cutoff = now - 1.0  # 1-second window

            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()

            if len(self._timestamps) >= self._max_per_second:
                oldest = self._timestamps[0]
                wait_time = (oldest + 1.0) - now
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    now = time.monotonic()
                    cutoff = now - 1.0
                    while self._timestamps and self._timestamps[0] < cutoff:
                        self._timestamps.popleft()

            self._timestamps.append(now)
            return 0.0

    @property
    def current_usage(self) -> int:
        now = time.monotonic()
        cutoff = now - 1.0
        return sum(1 for t in self._timestamps if t >= cutoff)


class ProfileCache:
    """
    TTL-based cache for summoner profiles.
    Follows Seraphine connector's session-based caching approach.
    """

    def __init__(self, ttl_seconds: int = PROFILE_CACHE_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: Dict[str, Tuple[SummonerProfile, float]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, puuid: str) -> Optional[SummonerProfile]:
        entry = self._store.get(puuid)
        if entry is None:
            self._misses += 1
            return None
        profile, cached_at = entry
        if time.monotonic() - cached_at > self._ttl:
            del self._store[puuid]
            self._misses += 1
            return None
        self._hits += 1
        return profile

    def put(self, profile: SummonerProfile):
        self._store[profile.puuid] = (profile, time.monotonic())

    def invalidate(self, puuid: str) -> bool:
        return self._store.pop(puuid, None) is not None

    def clear(self):
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return (self._hits / total * 100) if total > 0 else 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "size": self.size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 1),
            "ttl_seconds": self._ttl,
        }


class SummonerProfileCrawler:
    """
    Batch-crawls summoner profiles from Fiddler-intercepted API traffic.

    Lifecycle mirrors Seraphine connector.py:
      __init__ → start → crawl loop → stop

    Data flow:
      M886 RingBuffer → extract PUUIDs → enqueue crawl requests
      → rate-limited fetch → parse into SummonerProfile → cache

    All data comes from Fiddler MCP intercepted traffic, not direct
    Riot API calls. This ensures zero additional API load.
    """

    def __init__(
        self,
        interceptor=None,  # M886 MatchHistoryHttpInterceptor instance
        rate_limit: int = RATE_LIMIT_PER_SECOND,
        cache_ttl: int = PROFILE_CACHE_TTL_SECONDS,
        batch_size: int = CRAWL_BATCH_SIZE,
    ):
        self._interceptor = interceptor
        self._rate_limiter = RateLimiter(max_per_second=rate_limit)
        self._cache = ProfileCache(ttl_seconds=cache_ttl)
        self._batch_size = batch_size
        self._queue: List[CrawlRequest] = []
        self._in_progress: Set[str] = set()
        self._crawl_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._stats = {
            "total_crawled": 0,
            "successful": 0,
            "failed": 0,
            "rate_limited": 0,
            "from_cache": 0,
        }
        logger.info("SummonerProfileCrawler initialized (batch=%d)", batch_size)

    async def start(self):
        """Start the background crawl loop."""
        self._shutdown.clear()
        self._crawl_task = asyncio.create_task(
            self._crawl_loop(), name="summoner-crawl-loop"
        )
        logger.info("Crawler started")

    async def stop(self):
        """Graceful shutdown."""
        self._shutdown.set()
        if self._crawl_task and not self._crawl_task.done():
            self._crawl_task.cancel()
            try:
                await self._crawl_task
            except asyncio.CancelledError:
                pass
        logger.info("Crawler stopped. Stats: %s", self._stats)

    def enqueue(self, puuid: str, priority: CrawlPriority = CrawlPriority.NORMAL,
                callback: Optional[Callable] = None):
        """Add a summoner to the crawl queue."""
        cached = self._cache.get(puuid)
        if cached:
            self._stats["from_cache"] += 1
            if callback:
                callback(cached)
            return

        if puuid in self._in_progress:
            return
        if any(r.puuid == puuid for r in self._queue):
            return

        request = CrawlRequest(puuid=puuid, priority=priority, callback=callback)
        self._queue.append(request)
        self._queue.sort()
        logger.debug("Enqueued %s (priority=%s, queue=%d)", puuid, priority.name, len(self._queue))

    def enqueue_batch(self, puuids: List[str], priority: CrawlPriority = CrawlPriority.NORMAL):
        """Enqueue multiple summoners at once."""
        for puuid in puuids:
            self.enqueue(puuid, priority)

    def get_profile(self, puuid: str) -> Optional[SummonerProfile]:
        """Get a cached profile (non-blocking)."""
        return self._cache.get(puuid)

    def get_all_profiles(self) -> List[SummonerProfile]:
        """Return all cached profiles."""
        results = []
        for puuid, (profile, _) in self._cache._store.items():
            results.append(profile)
        return results

    async def _crawl_loop(self):
        """Background loop processing the crawl queue in batches."""
        while not self._shutdown.is_set():
            try:
                batch = self._dequeue_batch()
                if not batch:
                    await asyncio.sleep(0.5)
                    continue

                tasks = []
                for request in batch:
                    request.status = CrawlStatus.IN_PROGRESS
                    self._in_progress.add(request.puuid)
                    tasks.append(self._process_request(request))

                await asyncio.gather(*tasks, return_exceptions=True)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Crawl loop error: %s", exc)
                await asyncio.sleep(1.0)

    def _dequeue_batch(self) -> List[CrawlRequest]:
        """Take up to batch_size requests from the queue."""
        batch = []
        remaining = []
        for req in self._queue:
            if len(batch) < self._batch_size and req.status == CrawlStatus.QUEUED:
                batch.append(req)
            else:
                remaining.append(req)
        self._queue = remaining
        return batch

    async def _process_request(self, request: CrawlRequest):
        """Process a single crawl request with rate limiting."""
        async with self._semaphore:
            try:
                await self._rate_limiter.acquire()
                request.attempts += 1
                self._stats["total_crawled"] += 1

                profile = await self._fetch_profile(request.puuid)
                if profile:
                    self._cache.put(profile)
                    request.status = CrawlStatus.COMPLETED
                    self._stats["successful"] += 1
                    if request.callback:
                        request.callback(profile)
                    logger.debug("Crawled %s → %s", request.puuid[:12], profile.display_name)
                else:
                    raise ValueError(f"No profile data for {request.puuid}")

            except Exception as exc:
                request.last_error = str(exc)
                if request.attempts < request.max_attempts:
                    request.status = CrawlStatus.QUEUED
                    self._queue.append(request)
                    self._queue.sort()
                else:
                    request.status = CrawlStatus.FAILED
                    self._stats["failed"] += 1
                    logger.warning("Failed to crawl %s after %d attempts: %s",
                                   request.puuid[:12], request.attempts, exc)
            finally:
                self._in_progress.discard(request.puuid)

    async def _fetch_profile(self, puuid: str) -> Optional[SummonerProfile]:
        """
        Fetch profile from Fiddler-intercepted data or construct from
        match history entries. Does NOT make direct Riot API calls.

        Data sources (in priority order):
        1. Fiddler MCP intercepted /lol-summoner/v1/summoners/by-puuid/
        2. Extracted from match-history response participant data
        3. LCU /lol-ranked/v1/ranked-stats endpoint intercepts
        """
        if self._interceptor:
            history_entries = self._interceptor.get_summoner_history(puuid)
            if history_entries:
                return self._parse_from_match_history(puuid, history_entries)

        return self._build_minimal_profile(puuid)

    def _parse_from_match_history(self, puuid: str, entries) -> Optional[SummonerProfile]:
        """Extract summoner info from intercepted match history data."""
        for entry in entries:
            try:
                body = entry.response_body
                if not body:
                    continue
                data = json.loads(body)
                games = data.get("games", {}).get("games", [])
                if not games:
                    continue

                latest = games[0]
                participants = latest.get("participantIdentities", [])
                for p in participants:
                    player = p.get("player", {})
                    if player.get("puuid") == puuid or player.get("accountId"):
                        champion_counts: Dict[int, int] = collections.Counter()
                        wins = 0
                        losses = 0
                        for g in games:
                            for part in g.get("participants", []):
                                pid = part.get("participantId")
                                for pi in g.get("participantIdentities", []):
                                    if pi.get("participantId") == pid:
                                        if pi.get("player", {}).get("puuid") == puuid:
                                            champion_counts[part.get("championId", 0)] += 1
                                            stats = part.get("stats", {})
                                            if stats.get("win"):
                                                wins += 1
                                            else:
                                                losses += 1

                        top_champs = [
                            {"championId": cid, "games": cnt}
                            for cid, cnt in champion_counts.most_common(5)
                        ]

                        return SummonerProfile(
                            puuid=puuid,
                            summoner_id=str(player.get("summonerId", "")),
                            account_id=str(player.get("accountId", "")),
                            summoner_name=player.get("summonerName", player.get("gameName", "Unknown")),
                            tag_line=player.get("tagLine", ""),
                            profile_icon_id=player.get("profileIcon", 0),
                            summoner_level=0,
                            top_champions=top_champs,
                            recent_matches_summary={"wins": wins, "losses": losses, "total": wins + losses},
                            last_updated=datetime.now(timezone.utc),
                        )
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.debug("Parse error for %s: %s", puuid[:12], exc)
                continue
        return None

    def _build_minimal_profile(self, puuid: str) -> SummonerProfile:
        """Build a minimal profile when no intercepted data is available."""
        return SummonerProfile(
            puuid=puuid,
            summoner_id="",
            account_id="",
            summoner_name=f"Summoner-{puuid[:8]}",
            tag_line="",
            profile_icon_id=0,
            summoner_level=0,
            last_updated=datetime.now(timezone.utc),
            data_source="minimal_stub",
        )

    def export_stats(self) -> Dict[str, Any]:
        return {
            "crawler_stats": self._stats,
            "cache_stats": self._cache.stats(),
            "queue_size": len(self._queue),
            "in_progress": len(self._in_progress),
            "rate_limiter_usage": self._rate_limiter.current_usage,
        }



class BatchCrawlOrchestrator:
    """Orchestrates batch crawl operations across multiple game lobbies."""

    def __init__(self, crawler: SummonerProfileCrawler):
        self._crawler = crawler
        self._batch_history: List[Dict[str, Any]] = []
        self._total_batches = 0

    async def crawl_lobby(self, lobby_puuids: List[str]) -> Dict[str, Any]:
        """Crawl all players from a game lobby."""
        self._total_batches += 1
        start = time.monotonic()
        self._crawler.enqueue_batch(lobby_puuids, CrawlPriority.IMMEDIATE)
        # Wait for completion with timeout
        timeout = 30.0
        elapsed = 0
        while elapsed < timeout:
            all_done = all(self._crawler.get_profile(p) is not None for p in lobby_puuids)
            if all_done:
                break
            await asyncio.sleep(0.5)
            elapsed = time.monotonic() - start
        profiles = {p: self._crawler.get_profile(p) for p in lobby_puuids}
        result = {
            "batch_id": self._total_batches,
            "total": len(lobby_puuids),
            "found": sum(1 for v in profiles.values() if v is not None),
            "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
            "profiles": {k: v.to_dict() if v else None for k, v in profiles.items()},
        }
        self._batch_history.append(result)
        return result

    def get_batch_history(self) -> List[Dict[str, Any]]:
        return list(self._batch_history)


class CrawlMetricsExporter:
    """Exports crawler metrics for monitoring dashboards."""

    @staticmethod
    def export(crawler: SummonerProfileCrawler) -> Dict[str, Any]:
        stats = crawler.export_stats()
        profiles = crawler.get_all_profiles()
        rank_dist: Dict[str, int] = collections.Counter()
        for p in profiles:
            if p.ranked_solo:
                tier = p.ranked_solo.get("tier", "UNRANKED")
            else:
                tier = "UNRANKED"
            rank_dist[tier] += 1
        stats["rank_distribution"] = dict(rank_dist)
        stats["total_profiles"] = len(profiles)
        return stats

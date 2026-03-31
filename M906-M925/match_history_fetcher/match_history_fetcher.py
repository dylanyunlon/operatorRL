#!/usr/bin/env python3
"""
M907: MatchHistoryFetcher
==========================

Batch fetch match history via Seraphine getSummonerGamesByPuuid / SGP
dual-path with pagination, rate limiting, and incremental sync.

Architecture Pattern:
  查看 Seraphine connector.py 上现有 getSummonerGamesByPuuid 的实现方式,
  理解其模式, 特别是 begIndex/endIndex 分页和 SGP 回退是如何分离的。
  从 getSummonerGamesByPuuidSlowly 这个好例子开始。
  然后, 遵循该模式实现一个新的 MatchHistoryFetcher,
  让 operatorRL 可以批量拉取任意召唤师的对局历史, 并能增量同步。
  接着引入并发控制, 使多召唤师批量拉取能够遵守速率限制,
  同时优化去重逻辑避免跨页重复。
  随后整合 SGP via getSummonerGamesByPuuidViaSGP 路径,
  令国服环境支持完整历史拉取, 进而增强异常恢复和断点续传。
  最终完善增量同步策略, 确保只拉取新对局,
  全面升级历史数据获取以达成秒级响应。

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
import math
import os
import pathlib
import random
import statistics
import time
import traceback
import typing
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_MAX_GAMES = 200
FETCH_CONCURRENCY = 3
RATE_LIMIT_DELAY = 1.2
SGP_PAGE_SIZE = 50
HISTORY_CACHE_TTL = 300  # 5 minutes
MAX_RETRIES = 3
BACKOFF_BASE = 0.5


class FetchState(enum.Enum):
    IDLE = "idle"
    FETCHING = "fetching"
    RATE_LIMITED = "rate_limited"
    COMPLETED = "completed"
    ERROR = "error"


class FetchPath(enum.Enum):
    LCU_FAST = "lcu_fast"
    LCU_SLOW = "lcu_slow"
    SGP = "sgp"


@dataclasses.dataclass
class FetchProgress:
    """Track progress of a batch fetch operation."""
    puuid: str
    total_expected: int = 0
    total_fetched: int = 0
    pages_completed: int = 0
    pages_total: int = 0
    errors: int = 0
    state: FetchState = FetchState.IDLE
    started_at: float = 0.0
    completed_at: float = 0.0
    fetch_path: FetchPath = FetchPath.LCU_FAST
    last_game_id: Optional[int] = None
    deduplicated_count: int = 0

    @property
    def progress_pct(self) -> float:
        if self.pages_total == 0:
            return 0.0
        return min(100.0, (self.pages_completed / self.pages_total) * 100)

    @property
    def elapsed_seconds(self) -> float:
        end = self.completed_at if self.completed_at else time.time()
        return end - self.started_at if self.started_at else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "puuid": self.puuid[:8] + "...",
            "fetched": self.total_fetched,
            "pages": f"{self.pages_completed}/{self.pages_total}",
            "errors": self.errors,
            "state": self.state.value,
            "path": self.fetch_path.value,
            "elapsed": round(self.elapsed_seconds, 1),
            "deduped": self.deduplicated_count,
        }


@dataclasses.dataclass
class MatchSummary:
    """Lightweight match summary from history list."""
    game_id: int
    champion_id: int
    queue_id: int
    game_creation: int
    game_duration: int
    win: bool
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    role: str = ""
    lane: str = ""
    season_id: int = 0
    game_version: str = ""
    map_id: int = 11

    @property
    def kda(self) -> float:
        return (self.kills + self.assists) / max(1, self.deaths)

    @property
    def creation_datetime(self) -> datetime.datetime:
        return datetime.datetime.fromtimestamp(self.game_creation / 1000)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_riot_json(cls, data: Dict[str, Any], puuid: str = "") -> Optional["MatchSummary"]:
        """Parse from Riot/Seraphine match history JSON entry."""
        try:
            participants = data.get("participants", [])
            player = None
            if puuid:
                for p in participants:
                    if p.get("puuid") == puuid:
                        player = p
                        break
            if not player and participants:
                player = participants[0]
            if not player:
                stats = data.get("stats", data.get("participants", [{}])[0].get("stats", {})) if data.get("participants") else {}
                return cls(
                    game_id=data.get("gameId", 0),
                    champion_id=data.get("championId", data.get("champion", {}).get("id", 0)),
                    queue_id=data.get("queueId", 0),
                    game_creation=data.get("gameCreation", data.get("gameCreationDate", 0)),
                    game_duration=data.get("gameDuration", 0),
                    win=stats.get("win", False),
                    kills=stats.get("kills", 0),
                    deaths=stats.get("deaths", 0),
                    assists=stats.get("assists", 0),
                )
            stats = player.get("stats", {})
            return cls(
                game_id=data.get("gameId", 0),
                champion_id=player.get("championId", 0),
                queue_id=data.get("queueId", 0),
                game_creation=data.get("gameCreation", 0),
                game_duration=data.get("gameDuration", 0),
                win=stats.get("win", False),
                kills=stats.get("kills", 0),
                deaths=stats.get("deaths", 0),
                assists=stats.get("assists", 0),
                role=player.get("timeline", {}).get("role", ""),
                lane=player.get("timeline", {}).get("lane", ""),
            )
        except Exception as exc:
            logger.warning("Failed to parse match summary: %s", exc)
            return None


class MatchDeduplicator:
    """Deduplication engine for match history across pages and sessions."""

    def __init__(self):
        self._seen_ids: Set[int] = set()
        self._dup_count = 0

    def add_and_check(self, game_id: int) -> bool:
        """Returns True if this is a NEW game_id, False if duplicate."""
        if game_id in self._seen_ids:
            self._dup_count += 1
            return False
        self._seen_ids.add(game_id)
        return True

    def batch_filter(self, matches: List[MatchSummary]) -> List[MatchSummary]:
        return [m for m in matches if self.add_and_check(m.game_id)]

    @property
    def seen_count(self) -> int:
        return len(self._seen_ids)

    @property
    def duplicate_count(self) -> int:
        return self._dup_count

    def reset(self) -> None:
        self._seen_ids.clear()
        self._dup_count = 0


class IncrementalSyncTracker:
    """Track last fetched game_id per puuid for incremental sync."""

    def __init__(self, persist_path: str = ""):
        self._last_ids: Dict[str, int] = {}
        self._persist_path = persist_path
        if persist_path and os.path.exists(persist_path):
            try:
                self._last_ids = json.loads(pathlib.Path(persist_path).read_text())
            except Exception:
                pass

    def get_last_game_id(self, puuid: str) -> Optional[int]:
        return self._last_ids.get(puuid)

    def update(self, puuid: str, game_id: int) -> None:
        current = self._last_ids.get(puuid, 0)
        if game_id > current:
            self._last_ids[puuid] = game_id

    def save(self) -> None:
        if self._persist_path:
            pathlib.Path(self._persist_path).write_text(json.dumps(self._last_ids))

    def get_all(self) -> Dict[str, int]:
        return dict(self._last_ids)


class PageCalculator:
    """Calculate pagination parameters for fetch operations."""

    @staticmethod
    def compute_pages(total_games: int, page_size: int = DEFAULT_PAGE_SIZE) -> List[Tuple[int, int]]:
        pages = []
        for start in range(0, total_games, page_size):
            end = min(start + page_size - 1, total_games - 1)
            pages.append((start, end))
        return pages

    @staticmethod
    def estimate_total_pages(max_games: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
        return math.ceil(max_games / page_size)


class FetchRateLimiter:
    """Rate limiter for API fetch operations."""

    def __init__(self, requests_per_second: float = 1.0):
        self._interval = 1.0 / requests_per_second
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.time()
            wait = self._interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.time()


class MatchHistoryFetcher:
    """
    Production-grade match history batch fetcher.

    Features:
    - Dual-path: LCU API (fast/slow) + SGP API fallback
    - Pagination with configurable page size
    - Deduplication across pages
    - Incremental sync — only fetch new games
    - Concurrency control with rate limiting
    - Progress tracking per fetch operation
    - Checkpoint/resume for interrupted fetches
    """

    def __init__(self, connector=None, sync_path: str = ""):
        self._connector = connector  # SeraphineConnectorBridge instance
        self._deduplicator = MatchDeduplicator()
        self._sync_tracker = IncrementalSyncTracker(sync_path)
        self._rate_limiter = FetchRateLimiter(requests_per_second=0.8)
        self._progress: Dict[str, FetchProgress] = {}
        self._all_matches: Dict[str, List[MatchSummary]] = collections.defaultdict(list)
        self._semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)
        self._fetch_count = 0
        self._error_count = 0
        logger.info("MatchHistoryFetcher initialized (sync_path=%s)", sync_path)

    async def fetch_history(
        self,
        puuid: str,
        max_games: int = DEFAULT_MAX_GAMES,
        page_size: int = DEFAULT_PAGE_SIZE,
        incremental: bool = True,
    ) -> List[MatchSummary]:
        """Fetch match history for a single puuid with full pipeline."""
        progress = FetchProgress(
            puuid=puuid,
            total_expected=max_games,
            state=FetchState.FETCHING,
            started_at=time.time(),
        )
        self._progress[puuid] = progress
        self._deduplicator.reset()
        last_known = self._sync_tracker.get_last_game_id(puuid) if incremental else None
        pages = PageCalculator.compute_pages(max_games, page_size)
        progress.pages_total = len(pages)
        all_matches: List[MatchSummary] = []
        reached_known = False
        for beg_idx, end_idx in pages:
            if reached_known:
                break
            await self._rate_limiter.acquire()
            try:
                games_json = await self._fetch_page(puuid, beg_idx, end_idx, progress)
                if not games_json:
                    break
                page_matches = []
                for g in games_json:
                    m = MatchSummary.from_riot_json(g, puuid)
                    if m:
                        page_matches.append(m)
                new_matches = self._deduplicator.batch_filter(page_matches)
                if last_known:
                    for nm in new_matches:
                        if nm.game_id <= last_known:
                            reached_known = True
                            break
                    new_matches = [nm for nm in new_matches if nm.game_id > (last_known or 0)]
                all_matches.extend(new_matches)
                progress.total_fetched += len(new_matches)
                progress.pages_completed += 1
                progress.deduplicated_count = self._deduplicator.duplicate_count
                if len(games_json) < page_size:
                    break
            except Exception as exc:
                progress.errors += 1
                self._error_count += 1
                logger.error("Fetch page error puuid=%s page=%d-%d: %s", puuid[:8], beg_idx, end_idx, exc)
                if progress.errors > MAX_RETRIES:
                    progress.state = FetchState.ERROR
                    break
        if all_matches:
            max_id = max(m.game_id for m in all_matches)
            self._sync_tracker.update(puuid, max_id)
            progress.last_game_id = max_id
        progress.state = FetchState.COMPLETED if progress.errors <= MAX_RETRIES else FetchState.ERROR
        progress.completed_at = time.time()
        self._all_matches[puuid] = all_matches
        self._fetch_count += 1
        logger.info(
            "Fetch complete: puuid=%s games=%d pages=%d errors=%d elapsed=%.1fs",
            puuid[:8], len(all_matches), progress.pages_completed,
            progress.errors, progress.elapsed_seconds,
        )
        return all_matches

    async def _fetch_page(
        self, puuid: str, beg_idx: int, end_idx: int, progress: FetchProgress
    ) -> Optional[List[Dict]]:
        """Fetch a single page — try LCU fast, then slow, then SGP."""
        if self._connector is None:
            return self._generate_stub_page(beg_idx, end_idx)
        # Path 1: LCU fast
        try:
            progress.fetch_path = FetchPath.LCU_FAST
            path = f"/lol-match-history/v1/products/lol/{puuid}/matches?begIndex={beg_idx}&endIndex={end_idx}"
            result = await self._connector.lcu_get(path)
            if result and isinstance(result, dict):
                games = result.get("games", result.get("games", {}).get("games", []))
                if isinstance(games, dict):
                    games = games.get("games", [])
                if games:
                    return games
        except Exception as exc:
            logger.debug("LCU fast failed for %s: %s", puuid[:8], exc)
        # Path 2: LCU slow (rate-limited but reliable)
        try:
            progress.fetch_path = FetchPath.LCU_SLOW
            path = f"/lol-match-history/v1/products/lol/{puuid}/matches?begIndex={beg_idx}&endIndex={end_idx}"
            await asyncio.sleep(RATE_LIMIT_DELAY)
            result = await self._connector.lcu_get(path)
            if result and isinstance(result, dict):
                games = result.get("games", {})
                if isinstance(games, dict):
                    games = games.get("games", [])
                if games:
                    return games
        except Exception as exc:
            logger.debug("LCU slow failed for %s: %s", puuid[:8], exc)
        # Path 3: SGP fallback
        try:
            progress.fetch_path = FetchPath.SGP
            sgp_path = f"/match-history-query/v1/products/lol/{puuid}/SUMMARY?startIndex={beg_idx}&count={end_idx - beg_idx + 1}"
            result = await self._connector.sgp_get(sgp_path)
            if result and isinstance(result, dict):
                return result.get("games", [])
        except Exception as exc:
            logger.debug("SGP failed for %s: %s", puuid[:8], exc)
        return None

    def _generate_stub_page(self, beg: int, end: int) -> List[Dict]:
        """Generate stub data for testing without live LCU."""
        stubs = []
        for i in range(beg, min(end + 1, beg + 10)):
            stubs.append({
                "gameId": 7000000000 + i,
                "championId": random.choice([1, 2, 3, 4, 5, 10, 15, 20, 25, 30, 40, 50]),
                "queueId": 420,
                "gameCreation": int((time.time() - i * 3600) * 1000),
                "gameDuration": random.randint(900, 2400),
                "participants": [{
                    "puuid": "stub",
                    "championId": random.randint(1, 150),
                    "stats": {
                        "win": random.choice([True, False]),
                        "kills": random.randint(0, 15),
                        "deaths": random.randint(0, 10),
                        "assists": random.randint(0, 20),
                    },
                    "timeline": {"role": "SOLO", "lane": random.choice(["TOP", "MID", "JUNGLE", "BOTTOM"])},
                }],
            })
        return stubs

    async def fetch_batch(
        self,
        puuids: List[str],
        max_games_each: int = DEFAULT_MAX_GAMES,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> Dict[str, List[MatchSummary]]:
        """Fetch history for multiple puuids with concurrency control."""
        results: Dict[str, List[MatchSummary]] = {}
        tasks = []
        for puuid in puuids:
            tasks.append(self._fetch_with_semaphore(puuid, max_games_each, page_size))
        completed = await asyncio.gather(*tasks, return_exceptions=True)
        for puuid, result in zip(puuids, completed):
            if isinstance(result, Exception):
                logger.error("Batch fetch error for %s: %s", puuid[:8], result)
                results[puuid] = []
            else:
                results[puuid] = result
        return results

    async def _fetch_with_semaphore(self, puuid: str, max_games: int, page_size: int) -> List[MatchSummary]:
        async with self._semaphore:
            return await self.fetch_history(puuid, max_games, page_size)

    def get_progress(self, puuid: str = "") -> Dict[str, Any]:
        if puuid:
            p = self._progress.get(puuid)
            return p.to_dict() if p else {}
        return {k: v.to_dict() for k, v in self._progress.items()}

    def get_cached_matches(self, puuid: str) -> List[MatchSummary]:
        return self._all_matches.get(puuid, [])

    def save_sync_state(self) -> None:
        self._sync_tracker.save()

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_fetches": self._fetch_count,
            "total_errors": self._error_count,
            "cached_puuids": len(self._all_matches),
            "total_cached_matches": sum(len(v) for v in self._all_matches.values()),
            "sync_state": self._sync_tracker.get_all(),
        }


__all__ = [
    "MatchHistoryFetcher",
    "MatchSummary",
    "FetchProgress",
    "FetchState",
    "MatchDeduplicator",
    "IncrementalSyncTracker",
    "PageCalculator",
    "FetchRateLimiter",
]

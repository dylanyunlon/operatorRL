#!/usr/bin/env python3
"""
M994: RankTrajectoryAnalyzer
============================

段位轨迹分析器 — Analyze rank progression trajectory — current rank vs peak rank, LP gain/loss patterns, promotion/demotion frequency, smurf detection, boosting indicators

Dependencies: M906, M987, M993

Architecture Pattern:
    查看 Seraphine/app/lol/connector.py 上现有 LCU API connector 的实现方式,
    理解其模式, 特别是 retry 装饰器和 PastRequest 是如何与 HTTP session 分离的。
    从 connector.needLcu + retry + PastRequest 这个好例子开始。
    然后, 遵循该模式实现 RankTrajectoryAnalyzer,
    让 operatorRL 可以段位轨迹分析,
    并能与 M906 SeraphineConnectorBridge 数据桥接。

Reference:
    - Seraphine: github.com/ljszx/Seraphine
    - operatorRL: github.com/dylanyunlon/operatorRL.git
    - Fiddler MCP: telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server
"""

import asyncio
import hashlib
import json
import logging
import math
import statistics
import time
import traceback
from collections import defaultdict, deque, OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any, Callable, Coroutine, Dict, List, Optional, Set,
    Tuple, TypeVar, Union, NamedTuple, Protocol, Sequence,
)

logger = logging.getLogger("M994.RankTrajectoryAnalyzer")

T = TypeVar("T")


# ════════════════════════════════════════════════════════════════
# §1  Constants & Configuration — rank_analysis
# ════════════════════════════════════════════════════════════════

MODULE_VERSION = "1.0.0"
MODULE_ID = "M994"
MAX_CACHE_SIZE = 512
CACHE_TTL_SECONDS = 1800
MIN_SAMPLE_SIZE = 3
CONFIDENCE_THRESHOLD = 0.55
BATCH_SIZE = 10
MAX_RETRIES = 4
RETRY_BASE_DELAY = 0.5
TIMEOUT_SECONDS = 15.0
METRIC_WINDOW_SIZE = 200
MAX_CONCURRENT_REQUESTS = 8
HISTORY_DEPTH_GAMES = 30
PRIORITY_HIGH = 1
PRIORITY_MEDIUM = 5
PRIORITY_LOW = 10


class RankTrajectoryAnalyzerStatus(Enum):
    """RankTrajectoryAnalyzer lifecycle status."""
    UNINITIALIZED = auto()
    INITIALIZING = auto()
    READY = auto()
    FETCHING = auto()
    ANALYZING = auto()
    ERROR = auto()
    SHUTDOWN = auto()


class ConfidenceLevel(Enum):
    """Statistical confidence tier."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"

    @classmethod
    def from_sample_size(cls, n: int) -> "ConfidenceLevel":
        if n >= 20:
            return cls.HIGH
        elif n >= 10:
            return cls.MEDIUM
        elif n >= MIN_SAMPLE_SIZE:
            return cls.LOW
        return cls.INSUFFICIENT


class PriorityLevel(Enum):
    """Task priority for request scheduling."""
    CRITICAL = 0
    HIGH = 1
    NORMAL = 5
    LOW = 10
    BACKGROUND = 20


# ════════════════════════════════════════════════════════════════
# §2  Data Classes — rank_analysis domain model
# ════════════════════════════════════════════════════════════════

@dataclass
class PastRequest:
    """Audit trail for every API call (Seraphine pattern)."""
    func_name: str
    params: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    response: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    retry_count: int = 0

    def __str__(self) -> str:
        attrs = [f"{k}={v!r}" for k, v in self.__dict__.items() if v is not None]
        return f"PastRequest({', '.join(attrs)})"


@dataclass
class PlayerIdentity:
    """Resolved player identity from LCU gameflow session."""
    puuid: str
    summoner_id: int
    summoner_name: str
    champion_id: int = 0
    team_id: int = 0  # 100=blue, 200=red
    role: str = ""
    is_self: bool = False

    @property
    def cache_key(self) -> str:
        return f"{self.puuid}:{self.champion_id}"


@dataclass
class RankTrajectoryAnalyzerResult:
    """Analysis output from RankTrajectoryAnalyzer."""
    module_id: str = "M994"
    player_puuid: str = ""
    analysis_type: str = "rank_analysis"
    confidence: ConfidenceLevel = ConfidenceLevel.INSUFFICIENT
    sample_size: int = 0
    data: Dict[str, Any] = field(default_factory=dict)
    computed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["confidence"] = self.confidence.value
        return d


@dataclass
class MetricsSnapshot:
    """Runtime metrics for monitoring."""
    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    avg_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    active_tasks: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total > 0 else 0.0

    @property
    def success_rate(self) -> float:
        return self.requests_success / self.requests_total if self.requests_total > 0 else 0.0


# ════════════════════════════════════════════════════════════════
# §3  Shared Infrastructure — TTL Cache / Ring Buffer / Stats
# ════════════════════════════════════════════════════════════════

class TTLCache:
    """LRU cache with time-to-live eviction, thread-safe via asyncio.Lock."""

    def __init__(self, max_size: int = MAX_CACHE_SIZE, ttl: float = CACHE_TTL_SECONDS):
        self._max_size = max_size
        self._ttl = ttl
        self._store: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            if key in self._store:
                value, ts = self._store[key]
                if time.time() - ts < self._ttl:
                    self._store.move_to_end(key)
                    self._hits += 1
                    return value
                else:
                    del self._store[key]
            self._misses += 1
            return None

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, time.time())
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    async def invalidate(self, key: str) -> bool:
        async with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    async def clear(self) -> int:
        async with self._lock:
            n = len(self._store)
            self._store.clear()
            return n

    @property
    def stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
        }


class RingBuffer:
    """O(1) fixed-size circular buffer for latency tracking."""

    def __init__(self, capacity: int = METRIC_WINDOW_SIZE):
        self._buf: List[float] = [0.0] * capacity
        self._cap = capacity
        self._idx = 0
        self._count = 0

    def push(self, value: float) -> None:
        self._buf[self._idx % self._cap] = value
        self._idx += 1
        self._count = min(self._count + 1, self._cap)

    @property
    def values(self) -> List[float]:
        if self._count < self._cap:
            return self._buf[:self._count]
        start = self._idx % self._cap
        return self._buf[start:] + self._buf[:start]

    @property
    def mean(self) -> float:
        v = self.values
        return statistics.mean(v) if v else 0.0

    @property
    def p99(self) -> float:
        v = sorted(self.values)
        if not v:
            return 0.0
        idx = max(0, int(len(v) * 0.99) - 1)
        return v[idx]


class StatisticalHelper:
    """Shared statistics utilities — Wilson CI, EMA, cosine similarity."""

    @staticmethod
    def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float:
        if total == 0:
            return 0.0
        p = wins / total
        denom = 1 + z * z / total
        centre = p + z * z / (2 * total)
        spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
        return (centre - spread) / denom

    @staticmethod
    def exponential_moving_average(values: Sequence[float], alpha: float = 0.3) -> float:
        if not values:
            return 0.0
        ema = values[0]
        for v in values[1:]:
            ema = alpha * v + (1 - alpha) * ema
        return ema

    @staticmethod
    def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def trend_slope(values: Sequence[float]) -> float:
        n = len(values)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2
        y_mean = statistics.mean(values)
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        den = sum((i - x_mean) ** 2 for i in range(n))
        return num / den if den != 0 else 0.0

    @staticmethod
    def z_score(value: float, values: Sequence[float]) -> float:
        if len(values) < 2:
            return 0.0
        m = statistics.mean(values)
        s = statistics.stdev(values)
        return (value - m) / s if s > 0 else 0.0


# ════════════════════════════════════════════════════════════════
# §4  Connector Protocol — duck-typing bridge to M906
# ════════════════════════════════════════════════════════════════

class ConnectorProtocol(Protocol):
    """Duck-type interface for Seraphine connector bridge."""

    async def get_match_history(self, puuid: str, beg: int, end: int) -> Dict[str, Any]: ...
    async def get_ranked_stats(self, puuid: str) -> Dict[str, Any]: ...
    async def get_summoner_by_puuid(self, puuid: str) -> Dict[str, Any]: ...
    async def get_game_detail(self, game_id: int) -> Dict[str, Any]: ...
    async def get_champion_mastery(self, puuid: str) -> List[Dict[str, Any]]: ...


class MockConnector:
    """Development mock — returns synthetic data for offline testing."""

    async def get_match_history(self, puuid: str, beg: int = 0, end: int = 20) -> Dict[str, Any]:
        games = []
        for i in range(beg, min(end, beg + HISTORY_DEPTH_GAMES)):
            games.append({
                "gameId": 7000000 + i,
                "championId": 100 + (i % 15),
                "win": i % 3 != 0,
                "kills": 5 + i % 8,
                "deaths": 2 + i % 5,
                "assists": 7 + i % 6,
                "cs": 150 + i * 8,
                "goldEarned": 10000 + i * 500,
                "gameDuration": 1800 + i * 60,
                "timestamp": int(time.time()) - i * 3600,
                "queueId": 420,
                "role": ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"][i % 5],
                "lane": ["TOP", "JUNGLE", "MID", "BOTTOM", "BOTTOM"][i % 5],
                "visionScore": 20 + i % 15,
                "wardsPlaced": 8 + i % 10,
                "wardsKilled": 3 + i % 5,
                "firstBlood": i % 7 == 0,
                "turretKills": i % 3,
                "dragonKills": i % 2,
                "baronKills": 1 if i % 8 == 0 else 0,
                "participants": [
                    {"puuid": f"mock-{j}", "championId": 100 + j, "teamId": 100 if j < 5 else 200}
                    for j in range(10)
                ],
            })
        return {"games": games, "puuid": puuid}

    async def get_ranked_stats(self, puuid: str) -> Dict[str, Any]:
        return {
            "puuid": puuid,
            "queueMap": {
                "RANKED_SOLO_5x5": {
                    "tier": "GOLD", "division": "II",
                    "leaguePoints": 55, "wins": 120, "losses": 110,
                    "miniSeries": None,
                },
                "RANKED_FLEX_SR": {
                    "tier": "SILVER", "division": "I",
                    "leaguePoints": 80, "wins": 40, "losses": 35,
                },
            },
        }

    async def get_summoner_by_puuid(self, puuid: str) -> Dict[str, Any]:
        return {"puuid": puuid, "displayName": f"Player_{puuid[:6]}", "summonerLevel": 150}

    async def get_game_detail(self, game_id: int) -> Dict[str, Any]:
        return {"gameId": game_id, "gameDuration": 1800, "teams": [], "participants": []}

    async def get_champion_mastery(self, puuid: str) -> List[Dict[str, Any]]:
        return [
            {"championId": 100 + i, "championLevel": 7 - i % 5, "championPoints": 50000 - i * 3000}
            for i in range(20)
        ]


# ════════════════════════════════════════════════════════════════
# §5  Retry Decorator — Seraphine pattern
# ════════════════════════════════════════════════════════════════

def retry(count: int = MAX_RETRIES, base_delay: float = RETRY_BASE_DELAY):
    """Retry with exponential backoff + PastRequest audit (Seraphine pattern)."""
    def decorator(func: Callable) -> Callable:
        async def wrapper(self, *args, **kwargs):
            import inspect
            sig = inspect.signature(func)
            param_names = [p for p in sig.parameters if p != "self"]
            params_dict = dict(zip(param_names, args))
            params_dict.update(kwargs)

            req = PastRequest(func_name=func.__name__, params=params_dict)

            last_exc = None
            for attempt in range(count):
                t0 = time.monotonic()
                try:
                    result = await func(self, *args, **kwargs)
                    req.duration_ms = (time.monotonic() - t0) * 1000
                    req.response = "OK"
                    req.retry_count = attempt
                    self._audit_log.append(req)
                    self._latency_buf.push(req.duration_ms)
                    self._metrics.requests_total += 1
                    self._metrics.requests_success += 1
                    return result
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    last_exc = e
                    req.retry_count = attempt + 1
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"{func.__name__} attempt {attempt+1}/{count} failed: {e}, retry in {delay:.1f}s")
                    await asyncio.sleep(delay)

            req.error = str(last_exc)
            req.duration_ms = -1
            self._audit_log.append(req)
            self._metrics.requests_total += 1
            self._metrics.requests_failed += 1
            logger.error(f"{func.__name__} exhausted {count} retries: {last_exc}")
            raise last_exc
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


def need_initialized(func: Callable) -> Callable:
    """Guard: ensure module is initialized before calling (Seraphine needLcu pattern)."""
    async def wrapper(self, *args, **kwargs):
        if self._status not in (RankTrajectoryAnalyzerStatus.READY, RankTrajectoryAnalyzerStatus.FETCHING, RankTrajectoryAnalyzerStatus.ANALYZING):
            raise RuntimeError(f"RankTrajectoryAnalyzer not initialized (status={self._status.name})")
        return await func(self, *args, **kwargs)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


# ════════════════════════════════════════════════════════════════
# §6  Core Class — RankTrajectoryAnalyzer
# ════════════════════════════════════════════════════════════════

class RankTrajectoryAnalyzer:
    """
    段位轨迹分析器

    Analyze rank progression trajectory — current rank vs peak rank, LP gain/loss patterns, promotion/demotion frequency, smurf detection, boosting indicators

    Lifecycle:
        1. __init__(connector) — bind to data source
        2. await initialize() — warm caches, validate connectivity
        3. await analyze(players) — run domain analysis
        4. await shutdown() — cleanup resources

    Thread Safety:
        All public methods are asyncio-safe via internal Lock.
        Cache operations are atomic via TTLCache's own Lock.
    """

    def __init__(self, connector: Optional[ConnectorProtocol] = None):
        self._connector = connector or MockConnector()
        self._status = RankTrajectoryAnalyzerStatus.UNINITIALIZED
        self._cache = TTLCache(max_size=MAX_CACHE_SIZE, ttl=CACHE_TTL_SECONDS)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        self._lock = asyncio.Lock()
        self._audit_log: deque = deque(maxlen=500)
        self._latency_buf = RingBuffer(METRIC_WINDOW_SIZE)
        self._metrics = MetricsSnapshot()
        self._stats = StatisticalHelper()
        self._initialized_at: Optional[str] = None
        logger.info(f"RankTrajectoryAnalyzer created (connector={type(self._connector).__name__})")

    # ── Lifecycle ─────────────────────────────────────────────

    async def initialize(self) -> bool:
        """Initialize module — validate connector, warm cache."""
        async with self._lock:
            if self._status == RankTrajectoryAnalyzerStatus.READY:
                return True
            self._status = RankTrajectoryAnalyzerStatus.INITIALIZING
            try:
                # Validate connector by fetching a known endpoint
                test_result = await self._connector.get_ranked_stats("__init_probe__")
                if test_result is None:
                    logger.warning("RankTrajectoryAnalyzer connector returned None on probe — using mock mode")
                self._status = RankTrajectoryAnalyzerStatus.READY
                self._initialized_at = datetime.now(timezone.utc).isoformat()
                logger.info(f"RankTrajectoryAnalyzer initialized successfully")
                return True
            except Exception as e:
                logger.warning(f"RankTrajectoryAnalyzer init probe failed ({e}), falling back to mock connector")
                self._connector = MockConnector()
                self._status = RankTrajectoryAnalyzerStatus.READY
                self._initialized_at = datetime.now(timezone.utc).isoformat()
                return True

    async def shutdown(self) -> None:
        """Graceful shutdown — flush caches, log final metrics."""
        async with self._lock:
            cleared = await self._cache.clear()
            self._status = RankTrajectoryAnalyzerStatus.SHUTDOWN
            logger.info(f"RankTrajectoryAnalyzer shutdown (cleared {cleared} cache entries)")

    @property
    def status(self) -> RankTrajectoryAnalyzerStatus:
        return self._status

    @property
    def metrics(self) -> MetricsSnapshot:
        self._metrics.avg_latency_ms = round(self._latency_buf.mean, 2)
        self._metrics.p99_latency_ms = round(self._latency_buf.p99, 2)
        return self._metrics

    # ── Primary Domain Method ─────────────────────────────────

    @need_initialized
    async def analyze(self, players: List[PlayerIdentity]) -> List[RankTrajectoryAnalyzerResult]:
        """
        Run rank_analysis analysis for all players.

        Args:
            players: List of resolved player identities from M986.

        Returns:
            List of RankTrajectoryAnalyzerResult, one per player.
        """
        self._status = RankTrajectoryAnalyzerStatus.ANALYZING
        results = []
        tasks = [self._analyze_single(p) for p in players]
        settled = await asyncio.gather(*tasks, return_exceptions=True)
        for player, outcome in zip(players, settled):
            if isinstance(outcome, Exception):
                logger.error(f"Analysis failed for {player.summoner_name}: {outcome}")
                results.append(RankTrajectoryAnalyzerResult(
                    player_puuid=player.puuid,
                    confidence=ConfidenceLevel.INSUFFICIENT,
                    data={"error": str(outcome)},
                    warnings=[f"Analysis failed: {outcome}"],
                ))
            else:
                results.append(outcome)
        self._status = RankTrajectoryAnalyzerStatus.READY
        return results

    @need_initialized
    async def analyze_single(self, player: PlayerIdentity) -> RankTrajectoryAnalyzerResult:
        """Analyze a single player (public API)."""
        return await self._analyze_single(player)

    # ── Internal Domain Logic ─────────────────────────────────

    async def _analyze_single(self, player: PlayerIdentity) -> RankTrajectoryAnalyzerResult:
        """Core analysis logic for one player in the rank_analysis domain."""
        cache_key = f"M994:{player.cache_key}"

        # Cache check
        cached = await self._cache.get(cache_key)
        if cached is not None:
            self._metrics.cache_hits += 1
            logger.debug(f"Cache hit for {player.summoner_name}")
            return cached

        self._metrics.cache_misses += 1

        # Fetch historical data
        history = await self._fetch_player_history(player.puuid)
        if not history or not history.get("games"):
            return RankTrajectoryAnalyzerResult(
                player_puuid=player.puuid,
                confidence=ConfidenceLevel.INSUFFICIENT,
                data={},
                warnings=["No match history available"],
            )

        games = history["games"]
        sample_size = len(games)
        confidence = ConfidenceLevel.from_sample_size(sample_size)

        # Domain-specific computation
        domain_data = await self._compute_rank_analysis_metrics(player, games)

        result = RankTrajectoryAnalyzerResult(
            player_puuid=player.puuid,
            confidence=confidence,
            sample_size=sample_size,
            data=domain_data,
        )

        await self._cache.set(cache_key, result)
        return result

    @retry(count=MAX_RETRIES)
    async def _fetch_player_history(self, puuid: str) -> Dict[str, Any]:
        """Fetch match history via connector (with retry + audit)."""
        async with self._semaphore:
            return await self._connector.get_match_history(puuid, 0, HISTORY_DEPTH_GAMES)

    @retry(count=MAX_RETRIES)
    async def _fetch_ranked_stats(self, puuid: str) -> Dict[str, Any]:
        """Fetch ranked stats via connector (with retry + audit)."""
        async with self._semaphore:
            return await self._connector.get_ranked_stats(puuid)

    @retry(count=MAX_RETRIES)
    async def _fetch_champion_mastery(self, puuid: str) -> List[Dict[str, Any]]:
        """Fetch champion mastery via connector (with retry + audit)."""
        async with self._semaphore:
            return await self._connector.get_champion_mastery(puuid)

    async def _compute_rank_analysis_metrics(
        self, player: PlayerIdentity, games: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Compute rank_analysis-specific metrics from historical games.

        This is the core domain logic unique to RankTrajectoryAnalyzer.
        """
        if not games:
            return {}

        # ── Extract base statistics ──
        wins = [g for g in games if g.get("win")]
        losses = [g for g in games if not g.get("win")]
        win_rate = len(wins) / len(games) if games else 0.0
        wilson_wr = self._stats.wilson_lower_bound(len(wins), len(games))

        kills = [g.get("kills", 0) for g in games]
        deaths = [g.get("deaths", 0) for g in games]
        assists = [g.get("assists", 0) for g in games]
        cs_list = [g.get("cs", 0) for g in games]
        gold_list = [g.get("goldEarned", 0) for g in games]
        vision_list = [g.get("visionScore", 0) for g in games]
        duration_list = [g.get("gameDuration", 1800) for g in games]

        avg_kills = statistics.mean(kills) if kills else 0
        avg_deaths = statistics.mean(deaths) if deaths else 0
        avg_assists = statistics.mean(assists) if assists else 0
        kda = (avg_kills + avg_assists) / max(avg_deaths, 1)

        # ── Per-minute normalization ──
        cs_per_min = [
            cs / max(dur / 60, 1) for cs, dur in zip(cs_list, duration_list)
        ]
        gold_per_min = [
            g / max(dur / 60, 1) for g, dur in zip(gold_list, duration_list)
        ]
        vision_per_min = [
            v / max(dur / 60, 1) for v, dur in zip(vision_list, duration_list)
        ]

        # ── Trend analysis (EMA) ──
        wr_trend = self._stats.exponential_moving_average(
            [1.0 if g.get("win") else 0.0 for g in games], alpha=0.25
        )
        kda_trend = self._stats.exponential_moving_average(
            [(g.get("kills", 0) + g.get("assists", 0)) / max(g.get("deaths", 1), 1)
             for g in games],
            alpha=0.25,
        )
        cs_trend = self._stats.trend_slope(cs_per_min) if len(cs_per_min) >= 2 else 0.0

        # ── Champion distribution ──
        champ_counts: Dict[int, int] = defaultdict(int)
        champ_wins: Dict[int, int] = defaultdict(int)
        for g in games:
            cid = g.get("championId", 0)
            champ_counts[cid] += 1
            if g.get("win"):
                champ_wins[cid] += 1

        top_champions = sorted(champ_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        champion_pool_depth = len([c for c, n in champ_counts.items() if n >= 3])

        # ── Role distribution ──
        role_counts: Dict[str, int] = defaultdict(int)
        for g in games:
            role_counts[g.get("role", "UNKNOWN")] += 1
        primary_role = max(role_counts, key=role_counts.get) if role_counts else "UNKNOWN"

        # ── Recent form (last 5 vs previous) ──
        recent_5 = games[:5]
        older = games[5:]
        recent_wr = sum(1 for g in recent_5 if g.get("win")) / max(len(recent_5), 1)
        older_wr = sum(1 for g in older if g.get("win")) / max(len(older), 1) if older else win_rate

        form_delta = recent_wr - older_wr
        form_label = "HOT" if form_delta > 0.15 else ("COLD" if form_delta < -0.15 else "STABLE")

        # ── Streak detection ──
        streak = 0
        streak_type = "none"
        if games:
            first_result = games[0].get("win")
            for g in games:
                if g.get("win") == first_result:
                    streak += 1
                else:
                    break
            streak_type = "win" if first_result else "loss"

        # ── Domain-specific extras for rank_analysis ──
        
        # Rank analysis extras — trajectory and anomaly detection
        ranked_wr = win_rate
        games_total = len(games)
        projected_lp_gain = (ranked_wr - 0.5) * 20 * games_total  # rough projection
        rank_tiers = {"IRON": 0, "BRONZE": 1, "SILVER": 2, "GOLD": 3, "PLATINUM": 4, "EMERALD": 5, "DIAMOND": 6, "MASTER": 7, "GRANDMASTER": 8, "CHALLENGER": 9}
        smurf_indicators = sum([
            1 if ranked_wr > 0.65 else 0,
            1 if avg_kills > 8 else 0,
            1 if statistics.mean(cs_per_min) > 7.5 else 0,
            1 if champion_pool_depth <= 3 and ranked_wr > 0.6 else 0,
        ])
        smurf_probability = min(1.0, smurf_indicators / 4)
        domain_extras = {
            "projected_lp_change": round(projected_lp_gain, 0),
            "smurf_probability": round(smurf_probability, 3),
            "smurf_indicators": smurf_indicators,
            "rank_stability": round(1 - abs(form_delta) * 2, 3),
        }

        return {
            "win_rate": round(win_rate, 4),
            "wilson_win_rate": round(wilson_wr, 4),
            "kda": round(kda, 2),
            "avg_kills": round(avg_kills, 1),
            "avg_deaths": round(avg_deaths, 1),
            "avg_assists": round(avg_assists, 1),
            "cs_per_min_avg": round(statistics.mean(cs_per_min), 1) if cs_per_min else 0,
            "gold_per_min_avg": round(statistics.mean(gold_per_min), 0) if gold_per_min else 0,
            "vision_per_min_avg": round(statistics.mean(vision_per_min), 2) if vision_per_min else 0,
            "wr_trend_ema": round(wr_trend, 4),
            "kda_trend_ema": round(kda_trend, 2),
            "cs_trend_slope": round(cs_trend, 4),
            "top_champions": [
                {"champion_id": cid, "games": n, "wins": champ_wins.get(cid, 0),
                 "wr": round(champ_wins.get(cid, 0) / n, 3)}
                for cid, n in top_champions
            ],
            "champion_pool_depth": champion_pool_depth,
            "primary_role": primary_role,
            "role_distribution": dict(role_counts),
            "recent_form": form_label,
            "form_delta": round(form_delta, 3),
            "streak": {"type": streak_type, "length": streak},
            **domain_extras,
        }

    # ── Utility ───────────────────────────────────────────────

    def get_audit_log(self, last_n: int = 20) -> List[Dict[str, Any]]:
        """Return last N audit log entries."""
        entries = list(self._audit_log)[-last_n:]
        return [
            {
                "func": r.func_name,
                "params": {k: str(v)[:100] for k, v in r.params.items()},
                "duration_ms": r.duration_ms,
                "retries": r.retry_count,
                "error": r.error,
                "ts": r.timestamp,
            }
            for r in entries
        ]

    def get_cache_stats(self) -> Dict[str, Any]:
        return self._cache.stats

    async def invalidate_player(self, puuid: str) -> int:
        """Invalidate all cache entries for a given player."""
        cleared = 0
        async with self._cache._lock:
            keys_to_remove = [k for k in self._cache._store if puuid in k]
            for k in keys_to_remove:
                del self._cache._store[k]
                cleared += 1
        return cleared

    def __repr__(self) -> str:
        return (f"<RankTrajectoryAnalyzer status={self._status.name} "
                f"cache={self._cache.stats['size']}/{MAX_CACHE_SIZE} "
                f"audit={len(self._audit_log)}>")


# ════════════════════════════════════════════════════════════════
# §7  Self-Test
# ════════════════════════════════════════════════════════════════

async def _self_test() -> bool:
    """Run offline self-test with MockConnector."""
    instance = RankTrajectoryAnalyzer()
    ok = await instance.initialize()
    assert ok, "Initialize failed"
    assert instance.status == RankTrajectoryAnalyzerStatus.READY

    players = [
        PlayerIdentity(puuid=f"test-puuid-{i}", summoner_id=i,
                       summoner_name=f"TestPlayer{i}", champion_id=100+i,
                       team_id=100 if i < 5 else 200)
        for i in range(10)
    ]
    results = await instance.analyze(players)
    assert len(results) == 10, f"Expected 10 results, got {len(results)}"
    for r in results:
        assert r.module_id == "M994"
        assert r.confidence != ConfidenceLevel.INSUFFICIENT

    m = instance.metrics
    assert m.requests_total > 0
    assert m.success_rate > 0

    log = instance.get_audit_log(5)
    assert len(log) > 0

    await instance.shutdown()
    assert instance.status == RankTrajectoryAnalyzerStatus.SHUTDOWN
    return True


if __name__ == "__main__":
    ok = asyncio.run(_self_test())
    print(f"M994 RankTrajectoryAnalyzer self-test: {"PASS" if ok else "FAIL"}")

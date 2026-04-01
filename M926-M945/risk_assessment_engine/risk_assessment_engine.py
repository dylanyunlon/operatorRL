#!/usr/bin/env python3
"""
M940: RiskAssessmentEngine
==========================

风险评估引擎 — 对线期/团战期/推进期的风险评分+预警(gank概率/被反野概率)

Part of OperatorRL M926-M945 Advanced Predictive Analytics & Real-Time History Fusion subsystem.

Architecture Pattern:
  查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
  理解其模式, 特别是 LCU API 和数据变换是如何分离的。
  从 M906 SeraphineConnectorBridge 的 retry + PastRequest 这个好例子开始。
  然后, 遵循该模式实现 RiskAssessmentEngine,
  让 operatorRL 可以 风险评估引擎,
  并能与 M906-M925 历史情报层及 M926-M945 预测分析层集成。

Dependencies: M906, M917, M933, M938
# Depends on: M906
# Depends on: M917
# Depends on: M933
# Depends on: M938

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
import re
import statistics
import struct
import sys
import threading
import time
import traceback
import typing
from typing import (
    Any, Callable, Deque, Dict, FrozenSet, Generator,
    List, Optional, Protocol, Set, Tuple, Union,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------

MODULE_ID = "M940"
MODULE_NAME = "RiskAssessmentEngine"
MODULE_VERSION = "1.0.0"
DEFAULT_CACHE_TTL = 300
DEFAULT_MAX_ENTRIES = 10000
DEFAULT_BATCH_SIZE = 50
CONFIDENCE_THRESHOLD = 0.6
MIN_SAMPLE_SIZE = 5
ANALYSIS_WINDOW_GAMES = 20
RANKED_QUEUE_IDS = {420, 440}
ALL_QUEUE_IDS = {420, 440, 400, 430, 450}
LANE_NAMES = ["TOP", "JUNGLE", "MID", "BOTTOM", "SUPPORT"]
TIER_ORDER = [
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM",
    "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
]
DIVISION_ORDER = ["IV", "III", "II", "I"]
MAX_RETRY_COUNT = 5
RETRY_BACKOFF_BASE = 1.5
CONNECTION_TIMEOUT = 30.0
READ_TIMEOUT = 60.0
FIDDLER_MCP_PORT = 8868
LCU_BASE_URL = "https://127.0.0.1"
SGP_FALLBACK_ENABLED = True


class AnalysisState(enum.Enum):
    """Module processing state machine."""
    IDLE = "idle"
    INITIALIZING = "initializing"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    STALE = "stale"
    CANCELLED = "cancelled"


class ConfidenceLevel(enum.Enum):
    """Statistical confidence classification."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"

    @classmethod
    def from_sample_size(cls, n: int) -> "ConfidenceLevel":
        if n < 3:
            return cls.LOW
        if n < 10:
            return cls.MEDIUM
        if n < 30:
            return cls.HIGH
        return cls.VERY_HIGH


class PriorityLevel(enum.Enum):
    """Alert / recommendation priority."""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4
    INFO = 5


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class ModuleConfig:
    """Immutable module configuration loaded from config.json."""
    module_id: str = MODULE_ID
    module_name: str = MODULE_NAME
    version: str = MODULE_VERSION
    cache_ttl: float = DEFAULT_CACHE_TTL
    max_entries: int = DEFAULT_MAX_ENTRIES
    batch_size: int = DEFAULT_BATCH_SIZE
    confidence_threshold: float = CONFIDENCE_THRESHOLD
    min_sample_size: int = MIN_SAMPLE_SIZE
    retry_count: int = MAX_RETRY_COUNT
    connection_timeout: float = CONNECTION_TIMEOUT
    read_timeout: float = READ_TIMEOUT

    @classmethod
    def from_file(cls, path: Union[str, pathlib.Path]) -> "ModuleConfig":
        p = pathlib.Path(path)
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()


@dataclasses.dataclass
class AnalysisResult:
    """Generic analysis result with confidence scoring and metadata."""
    module_id: str = MODULE_ID
    timestamp: float = dataclasses.field(default_factory=time.time)
    state: AnalysisState = AnalysisState.COMPLETED
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    priority: PriorityLevel = PriorityLevel.MEDIUM
    sample_size: int = 0
    data: Dict[str, Any] = dataclasses.field(default_factory=dict)
    warnings: List[str] = dataclasses.field(default_factory=list)
    errors: List[str] = dataclasses.field(default_factory=list)
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)
    processing_time_ms: float = 0.0

    @property
    def is_reliable(self) -> bool:
        return (
            self.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH)
            and not self.errors
            and self.sample_size >= MIN_SAMPLE_SIZE
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module_id,
            "ts": self.timestamp,
            "state": self.state.value,
            "confidence": self.confidence.value,
            "priority": self.priority.value,
            "sample_size": self.sample_size,
            "data": self.data,
            "warnings": self.warnings,
            "errors": self.errors,
            "reliable": self.is_reliable,
            "processing_time_ms": self.processing_time_ms,
            "metadata": self.metadata,
        }

    def merge(self, other: "AnalysisResult") -> "AnalysisResult":
        """Merge two results, combining data and taking worse confidence."""
        merged_data = {**self.data, **other.data}
        worse_conf = min(self.confidence, other.confidence, key=lambda c: c.value)
        return AnalysisResult(
            module_id=self.module_id,
            confidence=worse_conf,
            sample_size=self.sample_size + other.sample_size,
            data=merged_data,
            warnings=self.warnings + other.warnings,
            errors=self.errors + other.errors,
        )


@dataclasses.dataclass
class CacheEntry:
    """TTL-aware cache entry with access tracking."""
    key: str
    value: Any
    created_at: float = dataclasses.field(default_factory=time.time)
    ttl: float = DEFAULT_CACHE_TTL
    hit_count: int = 0
    last_accessed: float = dataclasses.field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def touch(self) -> None:
        self.hit_count += 1
        self.last_accessed = time.time()


# ---------------------------------------------------------------------------
# Cache System
# ---------------------------------------------------------------------------

class AnalysisCache:
    """Thread-safe LRU + TTL cache for analysis results."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES, default_ttl: float = DEFAULT_CACHE_TTL):
        self._store: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self._max = max_entries
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.is_expired:
                del self._store[key]
                self._misses += 1
                return None
            entry.touch()
            self._store.move_to_end(key)
            self._hits += 1
            return entry.value

    def put(self, key: str, value: Any, ttl: float = 0) -> None:
        with self._lock:
            if key in self._store:
                del self._store[key]
            while len(self._store) >= self._max:
                self._store.popitem(last=False)
            self._store[key] = CacheEntry(
                key=key, value=value, ttl=ttl or self._default_ttl,
            )

    def invalidate(self, key: str) -> bool:
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def invalidate_prefix(self, prefix: str) -> int:
        with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._store),
                "max": self._max,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / total, 4) if total > 0 else 0.0,
            }


# ---------------------------------------------------------------------------
# Statistical Helpers
# ---------------------------------------------------------------------------

class StatisticalHelper:
    """Statistical utility functions for analysis modules."""

    @staticmethod
    def safe_mean(values: List[float]) -> float:
        return statistics.mean(values) if values else 0.0

    @staticmethod
    def safe_median(values: List[float]) -> float:
        return statistics.median(values) if values else 0.0

    @staticmethod
    def safe_stdev(values: List[float]) -> float:
        return statistics.stdev(values) if len(values) >= 2 else 0.0

    @staticmethod
    def percentile(values: List[float], pct: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = (len(sorted_vals) - 1) * (pct / 100.0)
        lower = int(math.floor(idx))
        upper = int(math.ceil(idx))
        if lower == upper:
            return sorted_vals[lower]
        frac = idx - lower
        return sorted_vals[lower] * (1 - frac) + sorted_vals[upper] * frac

    @staticmethod
    def z_score(value: float, mean: float, stdev: float) -> float:
        if stdev == 0:
            return 0.0
        return (value - mean) / stdev

    @staticmethod
    def wilson_score(positive: int, total: int, z: float = 1.96) -> float:
        """Wilson score interval lower bound for binomial proportion."""
        if total == 0:
            return 0.0
        p = positive / total
        denominator = 1 + z * z / total
        centre = p + z * z / (2 * total)
        offset = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
        return max(0.0, (centre - offset) / denominator)

    @staticmethod
    def exponential_decay_weight(age_seconds: float, half_life: float = 86400.0) -> float:
        """Weight older data exponentially less."""
        if half_life <= 0:
            return 1.0
        return math.exp(-0.693147 * age_seconds / half_life)

    @staticmethod
    def cosine_similarity(vec_a: List[float], vec_b: List[float]) -> float:
        if len(vec_a) != len(vec_b) or not vec_a:
            return 0.0
        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        mag_a = math.sqrt(sum(a * a for a in vec_a))
        mag_b = math.sqrt(sum(b * b for b in vec_b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    @staticmethod
    def moving_average(values: List[float], window: int = 5) -> List[float]:
        if not values or window <= 0:
            return []
        result = []
        for i in range(len(values)):
            start = max(0, i - window + 1)
            result.append(statistics.mean(values[start : i + 1]))
        return result

    @staticmethod
    def trend_slope(values: List[float]) -> float:
        """Simple linear regression slope."""
        n = len(values)
        if n < 2:
            return 0.0
        x_mean = (n - 1) / 2.0
        y_mean = statistics.mean(values)
        numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        denominator = sum((i - x_mean) ** 2 for i in range(n))
        return numerator / denominator if denominator != 0 else 0.0


# ---------------------------------------------------------------------------
# Connector Protocol (duck-typed bridge to M906)
# ---------------------------------------------------------------------------

class ConnectorProtocol(Protocol):
    """Protocol for M906 SeraphineConnectorBridge compatibility."""

    async def get_match_history(self, puuid: str, begin: int, end: int) -> Dict[str, Any]: ...
    async def get_game_detail(self, game_id: int) -> Dict[str, Any]: ...
    async def get_ranked_stats(self, puuid: str) -> Dict[str, Any]: ...
    async def get_current_summoner(self) -> Dict[str, Any]: ...
    async def get_champion_mastery(self, puuid: str) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Core RiskAssessmentEngine Implementation
# ---------------------------------------------------------------------------

class RiskAssessmentEngine:
    """
    风险评估引擎 — 对线期/团战期/推进期的风险评分+预警(gank概率/被反野概率)

    Follows Seraphine connector pattern:
    - Async-first with retry and exponential backoff
    - LCU + SGP dual-path data retrieval
    - Thread-safe caching with TTL
    - Structured AnalysisResult output
    """

    def __init__(
        self,
        connector: Optional[Any] = None,
        config: Optional[ModuleConfig] = None,
        cache: Optional[AnalysisCache] = None,
    ):
        self._connector = connector
        self._config = config or ModuleConfig.from_file(
            pathlib.Path(__file__).parent / "config.json"
        )
        self._cache = cache or AnalysisCache(
            max_entries=self._config.max_entries,
            default_ttl=self._config.cache_ttl,
        )
        self._state = AnalysisState.IDLE
        self._stats = StatisticalHelper()
        self._lock = asyncio.Lock()
        self._initialized = False
        self._processing_count = 0
        self._total_processed = 0
        self._error_count = 0
        self._last_result: Optional[AnalysisResult] = None
        logger.info(f"[{MODULE_ID}] {MODULE_NAME} v{MODULE_VERSION} created")

    # -- Lifecycle -----------------------------------------------------------

    async def initialize(self) -> bool:
        """Initialize module, verify dependencies."""
        async with self._lock:
            if self._initialized:
                return True
            try:
                self._state = AnalysisState.INITIALIZING
                logger.info(f"[{MODULE_ID}] Initializing...")
                if self._connector is None:
                    logger.warning(f"[{MODULE_ID}] No connector provided — running in offline mode")
                self._initialized = True
                self._state = AnalysisState.IDLE
                logger.info(f"[{MODULE_ID}] Initialization complete")
                return True
            except Exception as exc:
                self._state = AnalysisState.ERROR
                logger.error(f"[{MODULE_ID}] Init failed: {exc}")
                return False

    async def shutdown(self) -> None:
        """Graceful shutdown."""
        async with self._lock:
            self._state = AnalysisState.IDLE
            self._cache.clear()
            self._initialized = False
            logger.info(f"[{MODULE_ID}] Shutdown complete")

    @property
    def state(self) -> AnalysisState:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._initialized and self._state != AnalysisState.ERROR

    def get_health(self) -> Dict[str, Any]:
        return {
            "module_id": MODULE_ID,
            "module_name": MODULE_NAME,
            "version": MODULE_VERSION,
            "state": self._state.value,
            "initialized": self._initialized,
            "processing_count": self._processing_count,
            "total_processed": self._total_processed,
            "error_count": self._error_count,
            "cache_stats": self._cache.get_stats(),
        }

    # -- Data Retrieval with Retry -------------------------------------------

    async def _fetch_with_retry(
        self,
        fetch_fn: Callable,
        *args: Any,
        max_retries: int = MAX_RETRY_COUNT,
        **kwargs: Any,
    ) -> Optional[Any]:
        """Retry wrapper following Seraphine connector.retry pattern."""
        last_error = None
        for attempt in range(max_retries):
            try:
                result = await fetch_fn(*args, **kwargs)
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                wait = RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    f"[{MODULE_ID}] Retry {attempt + 1}/{max_retries} "
                    f"for {fetch_fn.__name__}: {exc} (wait {wait:.1f}s)"
                )
                await asyncio.sleep(wait)
        logger.error(f"[{MODULE_ID}] All retries exhausted: {last_error}")
        return None

    async def _fetch_match_history(self, puuid: str, begin: int = 0, end: int = 20) -> List[Dict[str, Any]]:
        """Fetch match history via connector with cache."""
        cache_key = f"history:{puuid}:{begin}:{end}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if self._connector is None:
            return []
        raw = await self._fetch_with_retry(
            self._connector.get_match_history, puuid, begin, end,
        )
        if raw and "games" in raw:
            games = raw["games"].get("games", [])
            self._cache.put(cache_key, games)
            return games
        return []

    async def _fetch_game_detail(self, game_id: int) -> Optional[Dict[str, Any]]:
        """Fetch single game detail via connector with cache."""
        cache_key = f"detail:{game_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if self._connector is None:
            return None
        detail = await self._fetch_with_retry(
            self._connector.get_game_detail, game_id,
        )
        if detail:
            self._cache.put(cache_key, detail, ttl=3600)
        return detail

    async def _fetch_ranked_stats(self, puuid: str) -> Optional[Dict[str, Any]]:
        """Fetch ranked stats via connector with cache."""
        cache_key = f"ranked:{puuid}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        if self._connector is None:
            return None
        stats = await self._fetch_with_retry(
            self._connector.get_ranked_stats, puuid,
        )
        if stats:
            self._cache.put(cache_key, stats)
        return stats

    # -- Core Analysis -------------------------------------------------------

    async def analyze(self, puuid: str, **kwargs: Any) -> AnalysisResult:
        """
        Main analysis entry point.

        Follows the pattern: fetch data → validate → compute → score → emit result.
        """
        start_time = time.monotonic()
        self._state = AnalysisState.PROCESSING
        self._processing_count += 1

        try:
            # Phase 1: Data collection
            games = await self._fetch_match_history(puuid, 0, ANALYSIS_WINDOW_GAMES)
            ranked = await self._fetch_ranked_stats(puuid)

            if not games:
                return AnalysisResult(
                    state=AnalysisState.COMPLETED,
                    confidence=ConfidenceLevel.LOW,
                    sample_size=0,
                    data={"status": "no_data", "puuid": puuid},
                    warnings=["No match history available for analysis"],
                    processing_time_ms=(time.monotonic() - start_time) * 1000,
                )

            # Phase 2: Feature extraction
            features = self._extract_features(games, ranked, **kwargs)

            # Phase 3: Statistical analysis
            analysis = self._compute_analysis(features, **kwargs)

            # Phase 4: Confidence scoring
            confidence = ConfidenceLevel.from_sample_size(len(games))

            # Phase 5: Build result
            elapsed = (time.monotonic() - start_time) * 1000
            result = AnalysisResult(
                state=AnalysisState.COMPLETED,
                confidence=confidence,
                sample_size=len(games),
                data=analysis,
                processing_time_ms=elapsed,
                metadata={
                    "puuid": puuid,
                    "games_analyzed": len(games),
                    "ranked_data_available": ranked is not None,
                    **kwargs,
                },
            )

            self._last_result = result
            self._total_processed += 1
            self._state = AnalysisState.IDLE
            logger.info(
                f"[{MODULE_ID}] Analysis complete: {len(games)} games, "
                f"confidence={confidence.value}, {elapsed:.1f}ms"
            )
            return result

        except Exception as exc:
            self._error_count += 1
            self._state = AnalysisState.ERROR
            logger.error(f"[{MODULE_ID}] Analysis failed: {exc}")
            return AnalysisResult(
                state=AnalysisState.ERROR,
                confidence=ConfidenceLevel.LOW,
                errors=[str(exc)],
                processing_time_ms=(time.monotonic() - start_time) * 1000,
            )
        finally:
            self._processing_count -= 1

    def _extract_features(
        self,
        games: List[Dict[str, Any]],
        ranked: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Extract relevant features from raw game data."""
        features: Dict[str, Any] = {
            "game_count": len(games),
            "queue_distribution": {},
            "champion_frequency": {},
            "role_distribution": {},
            "outcome_sequence": [],
            "kda_values": [],
            "cs_values": [],
            "gold_values": [],
            "vision_scores": [],
            "game_durations": [],
            "damage_values": [],
            "kill_participation": [],
            "timestamps": [],
        }

        for game in games:
            participants = game.get("participants", [])
            if not participants:
                continue

            # Find the target player's participant data
            player = participants[0] if len(participants) == 1 else None
            for p in participants:
                if p.get("puuid") == kwargs.get("target_puuid", ""):
                    player = p
                    break
            if player is None and participants:
                player = participants[0]

            stats = player.get("stats", {})
            champion_id = player.get("championId", 0)
            queue_id = game.get("queueId", 0)
            role = player.get("timeline", {}).get("lane", "UNKNOWN")

            features["queue_distribution"][queue_id] = (
                features["queue_distribution"].get(queue_id, 0) + 1
            )
            features["champion_frequency"][champion_id] = (
                features["champion_frequency"].get(champion_id, 0) + 1
            )
            features["role_distribution"][role] = (
                features["role_distribution"].get(role, 0) + 1
            )

            win = stats.get("win", False)
            features["outcome_sequence"].append(1 if win else 0)

            kills = stats.get("kills", 0)
            deaths = stats.get("deaths", 0)
            assists = stats.get("assists", 0)
            kda = (kills + assists) / max(deaths, 1)
            features["kda_values"].append(kda)

            cs = stats.get("totalMinionsKilled", 0) + stats.get("neutralMinionsKilled", 0)
            features["cs_values"].append(cs)
            features["gold_values"].append(stats.get("goldEarned", 0))
            features["vision_scores"].append(stats.get("visionScore", 0))
            features["damage_values"].append(stats.get("totalDamageDealtToChampions", 0))

            duration = game.get("gameDuration", 0)
            features["game_durations"].append(duration)
            features["timestamps"].append(game.get("gameCreation", 0))

            total_team_kills = max(stats.get("totalTeamKills", 1), 1)
            kp = (kills + assists) / total_team_kills
            features["kill_participation"].append(kp)

        if ranked:
            features["ranked_info"] = ranked

        return features

    def _compute_analysis(self, features: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        """Compute statistical analysis from extracted features."""
        analysis: Dict[str, Any] = {
            "module": MODULE_ID,
            "summary": {},
            "trends": {},
            "alerts": [],
            "recommendations": [],
        }

        n = features["game_count"]
        if n == 0:
            return analysis

        # Win rate analysis
        outcomes = features["outcome_sequence"]
        overall_wr = self._stats.safe_mean(outcomes)
        recent_wr = self._stats.safe_mean(outcomes[:5]) if len(outcomes) >= 5 else overall_wr
        analysis["summary"]["win_rate"] = round(overall_wr, 4)
        analysis["summary"]["recent_win_rate"] = round(recent_wr, 4)
        analysis["summary"]["games_analyzed"] = n

        # KDA analysis
        kda_vals = features["kda_values"]
        analysis["summary"]["avg_kda"] = round(self._stats.safe_mean(kda_vals), 2)
        analysis["summary"]["median_kda"] = round(self._stats.safe_median(kda_vals), 2)

        # CS analysis
        cs_vals = features["cs_values"]
        durations = features["game_durations"]
        cs_per_min = []
        for cs, dur in zip(cs_vals, durations):
            if dur > 0:
                cs_per_min.append(cs / (dur / 60.0))
        analysis["summary"]["avg_cs_per_min"] = round(self._stats.safe_mean(cs_per_min), 1)

        # Vision analysis
        vision = features["vision_scores"]
        analysis["summary"]["avg_vision_score"] = round(self._stats.safe_mean(vision), 1)

        # Damage analysis
        dmg = features["damage_values"]
        analysis["summary"]["avg_damage"] = round(self._stats.safe_mean(dmg), 0)

        # Kill participation
        kp = features["kill_participation"]
        analysis["summary"]["avg_kill_participation"] = round(self._stats.safe_mean(kp), 3)

        # Trend analysis
        if len(kda_vals) >= 3:
            analysis["trends"]["kda_slope"] = round(self._stats.trend_slope(kda_vals), 4)
            analysis["trends"]["kda_moving_avg"] = [
                round(v, 2) for v in self._stats.moving_average(kda_vals, 3)
            ]

        if len(outcomes) >= 3:
            analysis["trends"]["wr_slope"] = round(self._stats.trend_slope(outcomes), 4)

        if len(cs_per_min) >= 3:
            analysis["trends"]["cs_slope"] = round(self._stats.trend_slope(cs_per_min), 4)

        # Champion distribution (top 5)
        champ_freq = features["champion_frequency"]
        sorted_champs = sorted(champ_freq.items(), key=lambda x: x[1], reverse=True)
        analysis["summary"]["top_champions"] = [
            {"champion_id": cid, "games": count, "pct": round(count / n, 3)}
            for cid, count in sorted_champs[:5]
        ]

        # Role distribution
        role_freq = features["role_distribution"]
        analysis["summary"]["role_distribution"] = {
            role: {"count": c, "pct": round(c / n, 3)}
            for role, c in sorted(role_freq.items(), key=lambda x: x[1], reverse=True)
        }

        # Alert generation
        if recent_wr < 0.35 and len(outcomes) >= 5:
            analysis["alerts"].append({
                "type": "low_recent_winrate",
                "priority": PriorityLevel.HIGH.value,
                "message": f"Recent win rate critically low: {recent_wr:.0%}",
                "value": recent_wr,
            })

        if len(kda_vals) >= 5 and self._stats.trend_slope(kda_vals) < -0.3:
            analysis["alerts"].append({
                "type": "kda_declining",
                "priority": PriorityLevel.MEDIUM.value,
                "message": "KDA showing declining trend over recent games",
                "slope": round(self._stats.trend_slope(kda_vals), 4),
            })

        return analysis


    # -- Domain: In-Game Risk Scoring & Gank Probability -----------------------

    async def assess_gank_risk(
        self, lane: str, game_minute: int, enemy_jg_last_seen: Optional[Dict[str, Any]] = None,
        ward_coverage: float = 0.0,
    ) -> Dict[str, Any]:
        """Compute gank probability for a given lane based on game state."""
        base_risk = 0.3
        if game_minute < 3:
            base_risk = 0.05
        elif game_minute < 6:
            base_risk = 0.4
        elif game_minute < 15:
            base_risk = 0.35
        else:
            base_risk = 0.25
        ward_reduction = ward_coverage * 0.3
        jg_factor = 0.0
        if enemy_jg_last_seen:
            last_seen_minute = enemy_jg_last_seen.get("minute", 0)
            unseen_duration = game_minute - last_seen_minute
            if unseen_duration > 1.5:
                jg_factor = min(unseen_duration * 0.1, 0.3)
        risk = min(0.95, max(0.05, base_risk - ward_reduction + jg_factor))
        return {
            "lane": lane,
            "gank_probability": round(risk, 3),
            "risk_level": "HIGH" if risk > 0.6 else "MEDIUM" if risk > 0.35 else "LOW",
            "factors": {
                "base_risk": round(base_risk, 3),
                "ward_reduction": round(ward_reduction, 3),
                "jungle_unseen_factor": round(jg_factor, 3),
            },
            "recommendation": "Play safe, hug tower" if risk > 0.6 else "Standard play" if risk > 0.35 else "Can play aggressive",
        }

    async def assess_objective_risk(
        self, objective: str, our_alive: int, their_alive: int, gold_diff: int,
    ) -> Dict[str, Any]:
        """Assess risk of taking an objective (dragon/baron/herald)."""
        number_advantage = our_alive - their_alive
        gold_factor = gold_diff / 5000.0
        base_success = 0.5 + number_advantage * 0.15 + gold_factor * 0.1
        base_success = max(0.1, min(0.95, base_success))
        if objective == "baron" and our_alive < 4:
            base_success *= 0.6
        return {
            "objective": objective,
            "success_probability": round(base_success, 3),
            "risk_level": "LOW" if base_success > 0.7 else "MEDIUM" if base_success > 0.45 else "HIGH",
            "recommendation": "Take it" if base_success > 0.65 else "Risky — need more info" if base_success > 0.4 else "Avoid — too risky",
        }

    # -- Batch Operations ----------------------------------------------------

    async def analyze_batch(
        self,
        puuids: List[str],
        **kwargs: Any,
    ) -> Dict[str, AnalysisResult]:
        """Analyze multiple players concurrently."""
        results: Dict[str, AnalysisResult] = {}
        semaphore = asyncio.Semaphore(self._config.batch_size)

        async def _process(puuid: str) -> None:
            async with semaphore:
                results[puuid] = await self.analyze(puuid, **kwargs)

        tasks = [_process(p) for p in puuids]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    # -- Serialization -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module_id": MODULE_ID,
            "module_name": MODULE_NAME,
            "version": MODULE_VERSION,
            "state": self._state.value,
            "health": self.get_health(),
            "last_result": self._last_result.to_dict() if self._last_result else None,
        }

    def __repr__(self) -> str:
        return (
            f"<{MODULE_NAME}(state={self._state.value}, "
            f"processed={self._total_processed}, errors={self._error_count})>"
        )


# ---------------------------------------------------------------------------
# Module Entry Point
# ---------------------------------------------------------------------------

async def main() -> None:
    """Standalone test / demo entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    analyzer = RiskAssessmentEngine()
    await analyzer.initialize()
    logger.info(f"Health: {json.dumps(analyzer.get_health(), indent=2)}")
    # Demo with mock data
    result = await analyzer.analyze("mock-puuid-000", target_puuid="mock-puuid-000")
    logger.info(f"Result: {json.dumps(result.to_dict(), indent=2)}")
    await analyzer.shutdown()


if __name__ == "__main__":
    asyncio.run(main())

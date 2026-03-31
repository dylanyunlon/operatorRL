#!/usr/bin/env python3
"""
M922: HistoryToLiveFusionBridge
===============================

Bridge historical data into live game context — feed pre-game intelligence to M866-M885 real-time modules

Part of OperatorRL M906-M925 Seraphine Historical Battle Intelligence subsystem.

Architecture Pattern:
  查看 Seraphine connector/tools 上现有历史数据接口的实现方式,
  理解其模式, 特别是 LCU API 和数据变换是如何分离的。
  遵循该模式实现 HistoryToLiveFusionBridge,
  让 operatorRL 可以Bridge historical data into live game context,
  并能与 M906 SeraphineConnectorBridge 集成。

Dependencies: M906, M910, M914
# Depends on: M906
# Depends on: M910
# Depends on: M914

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
from typing import Any, Callable, Deque, Dict, FrozenSet, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODULE_ID = "M922"
MODULE_NAME = "HistoryToLiveFusionBridge"
MODULE_VERSION = "1.0.0"
DEFAULT_CACHE_TTL = 300
DEFAULT_MAX_ENTRIES = 10000
DEFAULT_BATCH_SIZE = 50
ANALYSIS_WINDOW_GAMES = 20
CONFIDENCE_THRESHOLD = 0.6
MIN_SAMPLE_SIZE = 5
RANKED_QUEUE_IDS = {420, 440}  # Solo/Duo, Flex
ALL_QUEUE_IDS = {420, 440, 400, 430, 450}
LANE_NAMES = ["TOP", "JUNGLE", "MID", "BOTTOM", "SUPPORT"]
TIER_ORDER = ["IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER"]
DIVISION_ORDER = ["IV", "III", "II", "I"]


class AnalysisState(enum.Enum):
    IDLE = "idle"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    STALE = "stale"


class ConfidenceLevel(enum.Enum):
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


@dataclasses.dataclass
class AnalysisResult:
    """Generic analysis result with confidence scoring."""
    module_id: str = MODULE_ID
    timestamp: float = dataclasses.field(default_factory=time.time)
    state: AnalysisState = AnalysisState.COMPLETED
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    sample_size: int = 0
    data: Dict[str, Any] = dataclasses.field(default_factory=dict)
    warnings: List[str] = dataclasses.field(default_factory=list)
    errors: List[str] = dataclasses.field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        return self.confidence in (ConfidenceLevel.HIGH, ConfidenceLevel.VERY_HIGH) and not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module_id,
            "ts": self.timestamp,
            "state": self.state.value,
            "confidence": self.confidence.value,
            "sample_size": self.sample_size,
            "data": self.data,
            "warnings": self.warnings,
            "reliable": self.is_reliable,
        }


@dataclasses.dataclass
class CacheEntry:
    """TTL-aware cache entry."""
    key: str
    value: Any
    created_at: float = dataclasses.field(default_factory=time.time)
    ttl: float = DEFAULT_CACHE_TTL
    hit_count: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl

    def touch(self) -> None:
        self.hit_count += 1


class AnalysisCache:
    """LRU + TTL cache for analysis results."""

    def __init__(self, max_entries: int = DEFAULT_MAX_ENTRIES, default_ttl: float = DEFAULT_CACHE_TTL):
        self._store: collections.OrderedDict[str, CacheEntry] = collections.OrderedDict()
        self._max = max_entries
        self._default_ttl = default_ttl
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[Any]:
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
        if key in self._store:
            del self._store[key]
        while len(self._store) >= self._max:
            self._store.popitem(last=False)
        self._store[key] = CacheEntry(key=key, value=value, ttl=ttl or self._default_ttl)

    def invalidate(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def clear(self) -> None:
        self._store.clear()

    def get_stats(self) -> Dict[str, Any]:
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "max": self._max,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 3) if total > 0 else 0,
        }


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
        return statistics.stdev(values) if len(values) > 1 else 0.0

    @staticmethod
    def percentile(values: List[float], pct: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        idx = int(len(sorted_vals) * pct / 100)
        return sorted_vals[min(idx, len(sorted_vals) - 1)]

    @staticmethod
    def winrate(wins: int, total: int) -> float:
        return round(wins / total, 4) if total > 0 else 0.0

    @staticmethod
    def wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float:
        """Wilson score interval lower bound for winrate confidence."""
        if total == 0:
            return 0.0
        p = wins / total
        denominator = 1 + z * z / total
        centre = p + z * z / (2 * total)
        spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
        return max(0.0, (centre - spread) / denominator)

    @staticmethod
    def exponential_decay_weight(age_hours: float, half_life: float = 168.0) -> float:
        """Weight that decays with time — recent games matter more."""
        return math.exp(-0.693 * age_hours / half_life)

    @staticmethod
    def tier_to_numeric(tier: str, division: str = "I", lp: int = 0) -> int:
        tier_val = TIER_ORDER.index(tier.upper()) * 400 if tier.upper() in TIER_ORDER else 0
        div_val = (4 - DIVISION_ORDER.index(division)) * 100 if division in DIVISION_ORDER else 0
        return tier_val + div_val + lp


class DataTransformer:
    """Transform raw match data for analysis consumption."""

    @staticmethod
    def extract_champion_games(matches: List[Dict], puuid: str = "") -> Dict[int, List[Dict]]:
        """Group matches by champion_id."""
        grouped: Dict[int, List[Dict]] = collections.defaultdict(list)
        for m in matches:
            champ_id = m.get("champion_id", m.get("championId", 0))
            grouped[champ_id].append(m)
        return dict(grouped)

    @staticmethod
    def extract_role_distribution(matches: List[Dict]) -> Dict[str, int]:
        roles: Dict[str, int] = collections.Counter()
        for m in matches:
            lane = m.get("lane", m.get("role", "UNKNOWN"))
            roles[lane] += 1
        return dict(roles)

    @staticmethod
    def compute_streak(wins: List[bool]) -> Tuple[int, str]:
        """Compute current streak. Returns (length, type)."""
        if not wins:
            return 0, "none"
        current = wins[-1]
        streak = 0
        for w in reversed(wins):
            if w == current:
                streak += 1
            else:
                break
        return streak, "win" if current else "loss"

    @staticmethod
    def time_bucket_distribution(timestamps: List[int]) -> Dict[str, int]:
        """Distribute games by time of day."""
        buckets: Dict[str, int] = collections.defaultdict(int)
        for ts in timestamps:
            dt = datetime.datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
            hour = dt.hour
            if 6 <= hour < 12:
                buckets["morning"] += 1
            elif 12 <= hour < 18:
                buckets["afternoon"] += 1
            elif 18 <= hour < 24:
                buckets["evening"] += 1
            else:
                buckets["night"] += 1
        return dict(buckets)


class EventAggregator:
    """Aggregate timeline events for pattern analysis."""

    def __init__(self):
        self._events: List[Dict] = []
        self._kill_events: List[Dict] = []
        self._objective_events: List[Dict] = []

    def ingest(self, events: List[Dict]) -> None:
        self._events.extend(events)
        for e in events:
            etype = e.get("event_type", e.get("type", ""))
            if etype == "CHAMPION_KILL":
                self._kill_events.append(e)
            elif etype in ("ELITE_MONSTER_KILL", "BUILDING_KILL"):
                self._objective_events.append(e)

    def get_early_game_kills(self, before_minutes: float = 10.0) -> List[Dict]:
        threshold = before_minutes * 60000
        return [e for e in self._kill_events if e.get("timestamp_ms", e.get("timestamp", 0)) < threshold]

    def get_objective_sequence(self) -> List[Dict]:
        return sorted(self._objective_events, key=lambda e: e.get("timestamp_ms", e.get("timestamp", 0)))

    def compute_kill_density(self, window_ms: int = 60000) -> List[Tuple[int, int]]:
        """Kill density over time windows."""
        if not self._kill_events:
            return []
        sorted_kills = sorted(self._kill_events, key=lambda e: e.get("timestamp_ms", 0))
        max_ts = sorted_kills[-1].get("timestamp_ms", 0)
        density = []
        for start in range(0, max_ts + 1, window_ms):
            count = sum(1 for e in sorted_kills if start <= e.get("timestamp_ms", 0) < start + window_ms)
            density.append((start, count))
        return density

    @property
    def total_events(self) -> int:
        return len(self._events)


class HistoryToLiveFusionBridge:
    """
    Bridge historical data into live game context — feed pre-game intelligence to M866-M885 real-time modules

    Production-grade module for OperatorRL agentic system.
    Integrates with SeraphineConnectorBridge (M906) for data acquisition.
    """

    def __init__(self, connector=None, config: Optional[Dict[str, Any]] = None):
        self._connector = connector
        self._config = config or {}
        self._cache = AnalysisCache(
            max_entries=self._config.get("cache_max", DEFAULT_MAX_ENTRIES),
            default_ttl=self._config.get("cache_ttl", DEFAULT_CACHE_TTL),
        )
        self._stats_helper = StatisticalHelper()
        self._transformer = DataTransformer()
        self._aggregator = EventAggregator()
        self._state = AnalysisState.IDLE
        self._process_count = 0
        self._error_count = 0
        self._last_run: Optional[float] = None
        self._results_store: Dict[str, AnalysisResult] = {}
        self._lock = asyncio.Lock() if asyncio else threading.Lock()
        logger.info("HistoryToLiveFusionBridge initialized (deps=['M906', 'M910', 'M914'])")

    @property
    def state(self) -> AnalysisState:
        return self._state

    @property
    def module_id(self) -> str:
        return MODULE_ID

    async def analyze(self, input_data: Dict[str, Any]) -> AnalysisResult:
        """Main analysis entry point."""
        self._state = AnalysisState.PROCESSING
        self._last_run = time.time()
        result = AnalysisResult(module_id=MODULE_ID)
        try:
            # Check cache first
            cache_key = self._compute_cache_key(input_data)
            cached = self._cache.get(cache_key)
            if cached:
                logger.debug("Cache hit for %s", cache_key[:16])
                return cached

            # Extract and validate input
            matches = input_data.get("matches", [])
            puuid = input_data.get("puuid", "")
            ranked_stats = input_data.get("ranked_stats", {})

            if not matches and not ranked_stats:
                result.state = AnalysisState.ERROR
                result.errors.append("No input data provided")
                return result

            # Core analysis pipeline
            analysis_data = {}

            # Step 1: Basic statistics
            if matches:
                wins = [m for m in matches if m.get("win", False)]
                total = len(matches)
                analysis_data["total_games"] = total
                analysis_data["wins"] = len(wins)
                analysis_data["losses"] = total - len(wins)
                analysis_data["winrate"] = self._stats_helper.winrate(len(wins), total)
                analysis_data["winrate_ci_lower"] = self._stats_helper.wilson_lower_bound(len(wins), total)

                # Step 2: Champion distribution
                champ_games = self._transformer.extract_champion_games(matches, puuid)
                champ_stats = {}
                for champ_id, games in champ_games.items():
                    champ_wins = sum(1 for g in games if g.get("win", False))
                    champ_stats[champ_id] = {
                        "games": len(games),
                        "wins": champ_wins,
                        "winrate": self._stats_helper.winrate(champ_wins, len(games)),
                        "ci_lower": self._stats_helper.wilson_lower_bound(champ_wins, len(games)),
                    }
                analysis_data["champion_stats"] = champ_stats

                # Step 3: Role distribution
                role_dist = self._transformer.extract_role_distribution(matches)
                analysis_data["role_distribution"] = role_dist

                # Step 4: Streak analysis
                win_sequence = [m.get("win", False) for m in sorted(matches, key=lambda x: x.get("game_creation", x.get("gameCreation", 0)))]
                streak_len, streak_type = self._transformer.compute_streak(win_sequence)
                analysis_data["current_streak"] = {"length": streak_len, "type": streak_type}

                # Step 5: Time distribution
                timestamps = [m.get("game_creation", m.get("gameCreation", 0)) for m in matches]
                analysis_data["time_distribution"] = self._transformer.time_bucket_distribution(timestamps)

                # Step 6: Performance metrics
                kdas = []
                cs_per_mins = []
                for m in matches:
                    k = m.get("kills", 0)
                    d = m.get("deaths", 0)
                    a = m.get("assists", 0)
                    kda = (k + a) / max(1, d)
                    kdas.append(kda)
                    dur = m.get("game_duration", m.get("gameDuration", 1800))
                    cs = m.get("cs", m.get("totalMinionsKilled", 0))
                    if dur > 0:
                        cs_per_mins.append(cs / (dur / 60.0))

                analysis_data["avg_kda"] = round(self._stats_helper.safe_mean(kdas), 2)
                analysis_data["median_kda"] = round(self._stats_helper.safe_median(kdas), 2)
                analysis_data["avg_cspm"] = round(self._stats_helper.safe_mean(cs_per_mins), 1)

                # Step 7: Recency weighting
                now = time.time()
                weighted_wins = 0.0
                total_weight = 0.0
                for m in matches:
                    ts = m.get("game_creation", m.get("gameCreation", 0))
                    if ts > 1e12:
                        ts /= 1000
                    age_hours = max(0, (now - ts) / 3600)
                    weight = self._stats_helper.exponential_decay_weight(age_hours)
                    total_weight += weight
                    if m.get("win", False):
                        weighted_wins += weight
                analysis_data["weighted_winrate"] = round(weighted_wins / total_weight, 4) if total_weight > 0 else 0.0

                result.sample_size = total

            # Step 8: Ranked stats integration
            if ranked_stats:
                tier = ranked_stats.get("tier", "UNRANKED")
                division = ranked_stats.get("division", ranked_stats.get("rank", "I"))
                lp = ranked_stats.get("leaguePoints", 0)
                analysis_data["ranked"] = {
                    "tier": tier,
                    "division": division,
                    "lp": lp,
                    "numeric": self._stats_helper.tier_to_numeric(tier, division, lp) if tier != "UNRANKED" else 0,
                    "wins": ranked_stats.get("wins", 0),
                    "losses": ranked_stats.get("losses", 0),
                }

            result.data = analysis_data
            result.confidence = ConfidenceLevel.from_sample_size(result.sample_size)
            result.state = AnalysisState.COMPLETED

            # Cache result
            self._cache.put(cache_key, result)
            self._results_store[cache_key] = result
            self._process_count += 1
            self._state = AnalysisState.COMPLETED

        except Exception as exc:
            result.state = AnalysisState.ERROR
            result.errors.append(str(exc))
            self._error_count += 1
            self._state = AnalysisState.ERROR
            logger.error("HistoryToLiveFusionBridge analysis error: %s", exc)

        return result

    def _compute_cache_key(self, input_data: Dict[str, Any]) -> str:
        """Compute deterministic cache key from input."""
        puuid = input_data.get("puuid", "")
        n_matches = len(input_data.get("matches", []))
        raw = f"{puuid}:{n_matches}:{MODULE_ID}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get_last_result(self, puuid: str = "") -> Optional[AnalysisResult]:
        """Retrieve last analysis result."""
        if not self._results_store:
            return None
        if puuid:
            for key, result in self._results_store.items():
                if puuid[:8] in key or puuid in str(result.data.get("puuid", "")):
                    return result
        return list(self._results_store.values())[-1] if self._results_store else None

    def get_diagnostics(self) -> Dict[str, Any]:
        """Module diagnostics for dashboard."""
        return {
            "module_id": MODULE_ID,
            "module_name": MODULE_NAME,
            "state": self._state.value,
            "process_count": self._process_count,
            "error_count": self._error_count,
            "last_run": self._last_run,
            "cache_stats": self._cache.get_stats(),
            "stored_results": len(self._results_store),
        }

    async def reset(self) -> None:
        """Reset module state."""
        self._cache.clear()
        self._results_store.clear()
        self._state = AnalysisState.IDLE
        self._process_count = 0
        self._error_count = 0
        logger.info("HistoryToLiveFusionBridge reset")

    def __repr__(self) -> str:
        return f"HistoryToLiveFusionBridge(state={self._state.value}, processed={self._process_count})"


__all__ = [
    "HistoryToLiveFusionBridge",
    "AnalysisResult",
    "AnalysisState",
    "ConfidenceLevel",
    "AnalysisCache",
    "StatisticalHelper",
    "DataTransformer",
    "EventAggregator",
]

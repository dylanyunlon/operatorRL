#!/usr/bin/env python3
"""
M810 - Champion Statistics Engine
===================================
OperatorRL Historical Battle System - Champion Win/Pick/Ban Rate & Tier Analysis

查看现有的英雄统计平台实现方式，理解其模式，特别是数据聚合和
分析层是如何分离的。遵循该模式实现英雄统计引擎，使对战分析可以
引用英雄强度数据，并能按段位、版本、位置进行分层分析。

Core responsibilities:
- Aggregate champion statistics across match history
- Calculate win rate, pick rate, ban rate per champion
- Tier list generation with confidence intervals
- Patch-aware statistics tracking
- Counter/synergy relationship analysis
"""

import os
import sys
import math
import json
import logging
import datetime
import statistics
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set, Sequence
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict, Counter

logger = logging.getLogger("operatorRL.historical_battle.champion_statistics_engine")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────────────

MIN_GAMES_FOR_STATS = 10
MIN_GAMES_FOR_COUNTER = 5
CONFIDENCE_Z_SCORE_95 = 1.96
CONFIDENCE_Z_SCORE_99 = 2.576
TIER_BOUNDARIES = {"S": 0.85, "A": 0.70, "B": 0.50, "C": 0.30, "D": 0.15, "F": 0.0}
DEFAULT_SAMPLE_SIZE = 1000
PATCH_PATTERN_REGEX = r"(\d+)\.(\d+)"
MAX_CHAMPIONS = 200
SYNERGY_THRESHOLD = 0.55
COUNTER_THRESHOLD = 0.55
BAN_RATE_HIGH_THRESHOLD = 0.30
PICK_RATE_HIGH_THRESHOLD = 0.15


class TierLabel(Enum):
    S_PLUS = "S+"
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class StatScope(Enum):
    ALL_RANKS = "all_ranks"
    IRON_BRONZE = "iron_bronze"
    SILVER_GOLD = "silver_gold"
    PLATINUM_EMERALD = "platinum_emerald"
    DIAMOND_PLUS = "diamond_plus"
    MASTER_PLUS = "master_plus"


class RoleFilter(Enum):
    ALL = "ALL"
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MIDDLE"
    ADC = "BOTTOM"
    SUPPORT = "UTILITY"


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class WinRateStats:
    """Win rate statistics with confidence intervals."""
    wins: int = 0
    losses: int = 0
    total_games: int = 0
    win_rate: float = 0.0
    ci_lower: float = 0.0
    ci_upper: float = 0.0
    confidence_level: float = 0.95

    @classmethod
    def calculate(cls, wins: int, total: int, confidence: float = 0.95) -> "WinRateStats":
        """Calculate win rate with Wilson score confidence interval."""
        if total == 0:
            return cls(total_games=0, win_rate=0.5)
        
        p = wins / total
        z = CONFIDENCE_Z_SCORE_95 if confidence == 0.95 else CONFIDENCE_Z_SCORE_99
        
        denominator = 1 + z * z / total
        centre = (p + z * z / (2 * total)) / denominator
        spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
        
        return cls(
            wins=wins,
            losses=total - wins,
            total_games=total,
            win_rate=round(p, 4),
            ci_lower=round(max(0, centre - spread), 4),
            ci_upper=round(min(1, centre + spread), 4),
            confidence_level=confidence,
        )

    @property
    def is_reliable(self) -> bool:
        return self.total_games >= MIN_GAMES_FOR_STATS

    @property
    def margin_of_error(self) -> float:
        return (self.ci_upper - self.ci_lower) / 2


@dataclass
class ChampionPickBanStats:
    """Pick and ban rate statistics."""
    champion_id: int = 0
    champion_name: str = ""
    total_matches_in_dataset: int = 0
    times_picked: int = 0
    times_banned: int = 0
    pick_rate: float = 0.0
    ban_rate: float = 0.0
    presence_rate: float = 0.0

    @classmethod
    def calculate(
        cls, champion_id: int, champion_name: str,
        picked: int, banned: int, total_matches: int
    ) -> "ChampionPickBanStats":
        if total_matches == 0:
            return cls(champion_id=champion_id, champion_name=champion_name)
        
        pick_rate = picked / total_matches
        ban_rate = banned / total_matches
        
        return cls(
            champion_id=champion_id,
            champion_name=champion_name,
            total_matches_in_dataset=total_matches,
            times_picked=picked,
            times_banned=banned,
            pick_rate=round(pick_rate, 4),
            ban_rate=round(ban_rate, 4),
            presence_rate=round(pick_rate + ban_rate, 4),
        )

    @property
    def is_meta(self) -> bool:
        return self.presence_rate > 0.20

    @property
    def is_contested(self) -> bool:
        return self.ban_rate > BAN_RATE_HIGH_THRESHOLD


@dataclass
class ChampionPerformanceStats:
    """Detailed performance statistics for a champion."""
    champion_id: int = 0
    champion_name: str = ""
    role: str = "ALL"
    
    # Win rate
    win_rate_stats: WinRateStats = field(default_factory=WinRateStats)
    
    # Pick/ban
    pick_ban_stats: ChampionPickBanStats = field(default_factory=ChampionPickBanStats)
    
    # Average stats per game
    avg_kills: float = 0.0
    avg_deaths: float = 0.0
    avg_assists: float = 0.0
    avg_cs_per_min: float = 0.0
    avg_damage_per_min: float = 0.0
    avg_gold_per_min: float = 0.0
    avg_vision_score: float = 0.0
    avg_game_duration_min: float = 0.0
    
    # Performance score
    composite_score: float = 0.0
    tier: TierLabel = TierLabel.C
    tier_rank: int = 0
    
    # Counters and synergies
    best_counters: List[Tuple[str, float]] = field(default_factory=list)
    worst_matchups: List[Tuple[str, float]] = field(default_factory=list)
    best_synergies: List[Tuple[str, float]] = field(default_factory=list)
    
    # Patch info
    patch_version: str = ""
    scope: StatScope = StatScope.ALL_RANKS
    
    @property
    def kda_ratio(self) -> float:
        if self.avg_deaths == 0:
            return self.avg_kills + self.avg_assists
        return (self.avg_kills + self.avg_assists) / self.avg_deaths

    @property
    def is_op(self) -> bool:
        return (
            self.win_rate_stats.win_rate > 0.53
            and self.pick_ban_stats.presence_rate > 0.30
            and self.win_rate_stats.is_reliable
        )


@dataclass
class TierListEntry:
    """Entry in a generated tier list."""
    champion_name: str = ""
    champion_id: int = 0
    tier: TierLabel = TierLabel.C
    score: float = 0.0
    win_rate: float = 0.0
    pick_rate: float = 0.0
    ban_rate: float = 0.0
    games_analyzed: int = 0
    role: str = "ALL"
    trend: str = "stable"  # "rising", "falling", "stable", "new"


@dataclass
class TierList:
    """Complete tier list for a role and scope."""
    role: str = "ALL"
    scope: StatScope = StatScope.ALL_RANKS
    patch_version: str = ""
    generated_at: str = field(
        default_factory=lambda: datetime.datetime.now().isoformat()
    )
    total_matches_analyzed: int = 0
    entries: List[TierListEntry] = field(default_factory=list)

    @property
    def s_tier(self) -> List[TierListEntry]:
        return [e for e in self.entries if e.tier in (TierLabel.S_PLUS, TierLabel.S)]

    @property
    def op_picks(self) -> List[TierListEntry]:
        return [e for e in self.entries if e.tier == TierLabel.S_PLUS]


@dataclass
class MatchupData:
    """Win rate in a specific champion vs champion matchup."""
    champion_a: str = ""
    champion_b: str = ""
    champion_a_wins: int = 0
    total_games: int = 0
    win_rate_a: float = 0.0
    
    @property
    def win_rate_b(self) -> float:
        return 1.0 - self.win_rate_a if self.total_games > 0 else 0.5

    @property
    def is_counter(self) -> bool:
        return self.win_rate_a > COUNTER_THRESHOLD and self.total_games >= MIN_GAMES_FOR_COUNTER


# ─── Aggregation Engine ──────────────────────────────────────────────────────

class ChampionDataAggregator:
    """
    Aggregates raw match data into champion-level statistics.
    Supports filtering by rank, role, and patch version.
    """

    def __init__(self):
        self._champion_games: Dict[str, List[Dict]] = defaultdict(list)
        self._matchup_tracker: Dict[str, Dict[str, List[bool]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._ban_tracker: Dict[str, int] = Counter()
        self._total_matches = 0

    def ingest_match(self, match_data: Dict[str, Any]):
        """Ingest a single match's data into aggregation pools."""
        self._total_matches += 1
        participants = match_data.get("participants", [])
        
        # Track bans
        for team in match_data.get("teams", []):
            for ban_id in team.get("bans", []):
                self._ban_tracker[str(ban_id)] += 1

        # Track per-champion stats
        for p in participants:
            champ_name = p.get("champion_name", "Unknown")
            role = p.get("role", "UNKNOWN")
            combat = p.get("combat", {})
            farming = p.get("farming", {})
            vision = p.get("vision", {})
            items = p.get("items", {})
            duration = match_data.get("game_duration", 1800) / 60

            game_entry = {
                "win": p.get("win", False),
                "kills": combat.get("kills", 0),
                "deaths": combat.get("deaths", 0),
                "assists": combat.get("assists", 0),
                "cs": farming.get("total_minions_killed", 0) + farming.get("neutral_minions_killed", 0),
                "damage": combat.get("total_damage_to_champions", 0),
                "gold": items.get("gold_earned", 0),
                "vision": vision.get("vision_score", 0),
                "duration_min": duration,
                "role": role,
                "team_id": p.get("team_id", 0),
                "champion_id": p.get("champion_id", 0),
            }
            self._champion_games[champ_name].append(game_entry)

        # Track matchups (same role, opposing teams)
        blue = [p for p in participants if p.get("team_id") == 100]
        red = [p for p in participants if p.get("team_id") == 200]
        for bp in blue:
            for rp in red:
                if bp.get("role") == rp.get("role") and bp.get("role") not in ("", "UNKNOWN"):
                    a = bp.get("champion_name", "")
                    b = rp.get("champion_name", "")
                    a_won = bp.get("win", False)
                    self._matchup_tracker[a][b].append(a_won)
                    self._matchup_tracker[b][a].append(not a_won)

    def ingest_batch(self, matches: List[Dict[str, Any]]):
        """Ingest a batch of matches."""
        for match in matches:
            self.ingest_match(match)

    def get_champion_stats(
        self, champion_name: str, role: str = "ALL"
    ) -> ChampionPerformanceStats:
        """Calculate statistics for a specific champion."""
        games = self._champion_games.get(champion_name, [])
        if role != "ALL":
            games = [g for g in games if g["role"] == role]

        if not games:
            return ChampionPerformanceStats(champion_name=champion_name, role=role)

        wins = sum(1 for g in games if g["win"])
        total = len(games)
        wr_stats = WinRateStats.calculate(wins, total)

        champ_id = games[0].get("champion_id", 0)
        picked = total
        banned = self._ban_tracker.get(str(champ_id), 0)
        pb_stats = ChampionPickBanStats.calculate(
            champ_id, champion_name, picked, banned, self._total_matches
        )

        def safe_mean(values):
            return statistics.mean(values) if values else 0.0

        stats = ChampionPerformanceStats(
            champion_id=champ_id,
            champion_name=champion_name,
            role=role,
            win_rate_stats=wr_stats,
            pick_ban_stats=pb_stats,
            avg_kills=round(safe_mean([g["kills"] for g in games]), 1),
            avg_deaths=round(safe_mean([g["deaths"] for g in games]), 1),
            avg_assists=round(safe_mean([g["assists"] for g in games]), 1),
            avg_cs_per_min=round(
                safe_mean([g["cs"] / max(g["duration_min"], 1) for g in games]), 1
            ),
            avg_damage_per_min=round(
                safe_mean([g["damage"] / max(g["duration_min"], 1) for g in games]), 0
            ),
            avg_gold_per_min=round(
                safe_mean([g["gold"] / max(g["duration_min"], 1) for g in games]), 0
            ),
            avg_vision_score=round(safe_mean([g["vision"] for g in games]), 1),
            avg_game_duration_min=round(safe_mean([g["duration_min"] for g in games]), 1),
        )

        # Counter/synergy analysis
        matchups = self._matchup_tracker.get(champion_name, {})
        best_counters = []
        worst_matchups = []
        for opponent, results in matchups.items():
            if len(results) >= MIN_GAMES_FOR_COUNTER:
                wr = sum(1 for r in results if r) / len(results)
                if wr >= COUNTER_THRESHOLD:
                    best_counters.append((opponent, round(wr, 3)))
                elif wr <= (1 - COUNTER_THRESHOLD):
                    worst_matchups.append((opponent, round(wr, 3)))

        stats.best_counters = sorted(best_counters, key=lambda x: x[1], reverse=True)[:5]
        stats.worst_matchups = sorted(worst_matchups, key=lambda x: x[1])[:5]

        return stats

    def generate_tier_list(
        self, role: str = "ALL", scope: StatScope = StatScope.ALL_RANKS,
        patch_version: str = ""
    ) -> TierList:
        """Generate a tier list for a specific role and scope."""
        all_stats: List[ChampionPerformanceStats] = []

        for champ_name in self._champion_games:
            stats = self.get_champion_stats(champ_name, role)
            if stats.win_rate_stats.total_games >= MIN_GAMES_FOR_STATS:
                # Composite score: weighted combination
                score = (
                    stats.win_rate_stats.win_rate * 40 +
                    min(stats.pick_ban_stats.pick_rate * 100, 20) +
                    stats.kda_ratio * 5 +
                    min(stats.avg_cs_per_min, 10) * 2 +
                    min(stats.avg_damage_per_min / 100, 15)
                )
                stats.composite_score = round(score, 1)
                all_stats.append(stats)

        # Sort by composite score
        all_stats.sort(key=lambda s: s.composite_score, reverse=True)

        # Assign tiers
        entries = []
        total = len(all_stats)
        for i, stats in enumerate(all_stats):
            percentile = 1.0 - (i / total) if total > 0 else 0.5
            tier = self._percentile_to_tier(percentile, stats)

            entry = TierListEntry(
                champion_name=stats.champion_name,
                champion_id=stats.champion_id,
                tier=tier,
                score=stats.composite_score,
                win_rate=stats.win_rate_stats.win_rate,
                pick_rate=stats.pick_ban_stats.pick_rate,
                ban_rate=stats.pick_ban_stats.ban_rate,
                games_analyzed=stats.win_rate_stats.total_games,
                role=role,
            )
            entries.append(entry)

        return TierList(
            role=role,
            scope=scope,
            patch_version=patch_version,
            total_matches_analyzed=self._total_matches,
            entries=entries,
        )

    @staticmethod
    def _percentile_to_tier(
        percentile: float, stats: ChampionPerformanceStats
    ) -> TierLabel:
        """Convert percentile rank to tier label."""
        if percentile >= 0.95 and stats.win_rate_stats.win_rate > 0.52:
            return TierLabel.S_PLUS
        elif percentile >= TIER_BOUNDARIES["S"]:
            return TierLabel.S
        elif percentile >= TIER_BOUNDARIES["A"]:
            return TierLabel.A
        elif percentile >= TIER_BOUNDARIES["B"]:
            return TierLabel.B
        elif percentile >= TIER_BOUNDARIES["C"]:
            return TierLabel.C
        elif percentile >= TIER_BOUNDARIES["D"]:
            return TierLabel.D
        else:
            return TierLabel.F

    @property
    def champions_tracked(self) -> int:
        return len(self._champion_games)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_matches": self._total_matches,
            "champions_tracked": self.champions_tracked,
            "matchups_tracked": sum(
                len(v) for v in self._matchup_tracker.values()
            ),
            "bans_tracked": len(self._ban_tracker),
        }


# ─── Champion Statistics Engine ──────────────────────────────────────────────

class ChampionStatisticsEngine:
    """
    High-level engine for champion statistics.
    Manages aggregation, tier list generation, and queryable stats.
    """

    def __init__(self):
        self._aggregators: Dict[str, ChampionDataAggregator] = {}
        self._current_patch: str = ""
        self._tier_lists_cache: Dict[str, TierList] = {}
        self._initialized = False

    async def initialize(self, config: Dict[str, Any] = None) -> bool:
        config = config or {}
        self._current_patch = config.get("current_patch", "")
        self._aggregators["all"] = ChampionDataAggregator()
        self._initialized = True
        logger.info("ChampionStatisticsEngine initialized")
        return True

    def ingest_matches(self, matches: List[Dict[str, Any]], patch: str = ""):
        """Ingest matches into the appropriate aggregator."""
        key = patch if patch else "all"
        if key not in self._aggregators:
            self._aggregators[key] = ChampionDataAggregator()
        self._aggregators[key].ingest_batch(matches)

    def get_champion_stats(
        self, champion_name: str, role: str = "ALL", patch: str = ""
    ) -> ChampionPerformanceStats:
        key = patch if patch else "all"
        aggregator = self._aggregators.get(key, self._aggregators.get("all"))
        if aggregator:
            return aggregator.get_champion_stats(champion_name, role)
        return ChampionPerformanceStats(champion_name=champion_name)

    def generate_tier_list(
        self, role: str = "ALL", scope: StatScope = StatScope.ALL_RANKS
    ) -> TierList:
        aggregator = self._aggregators.get("all")
        if not aggregator:
            return TierList(role=role, scope=scope)
        
        cache_key = f"{role}:{scope.value}"
        if cache_key in self._tier_lists_cache:
            cached = self._tier_lists_cache[cache_key]
            age = (datetime.datetime.now() -
                   datetime.datetime.fromisoformat(cached.generated_at))
            if age.total_seconds() < 300:  # 5 min cache
                return cached

        tier_list = aggregator.generate_tier_list(role, scope, self._current_patch)
        self._tier_lists_cache[cache_key] = tier_list
        return tier_list

    def get_matchup(
        self, champion_a: str, champion_b: str, role: str = "ALL"
    ) -> Optional[MatchupData]:
        """Get head-to-head matchup data."""
        aggregator = self._aggregators.get("all")
        if not aggregator:
            return None

        matchups = aggregator._matchup_tracker.get(champion_a, {})
        results = matchups.get(champion_b, [])
        if not results:
            return None

        wins_a = sum(1 for r in results if r)
        return MatchupData(
            champion_a=champion_a,
            champion_b=champion_b,
            champion_a_wins=wins_a,
            total_games=len(results),
            win_rate_a=round(wins_a / len(results), 3),
        )

    def get_counters_for(self, champion_name: str, role: str = "ALL") -> List[Tuple[str, float]]:
        """Get best counters for a champion."""
        stats = self.get_champion_stats(champion_name, role)
        return stats.worst_matchups  # Worst matchups = what counters them

    def get_synergies_for(self, champion_name: str) -> List[Tuple[str, float]]:
        """Get best synergies (placeholder for team comp analysis)."""
        stats = self.get_champion_stats(champion_name)
        return stats.best_synergies

    async def health_check(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "aggregators": {k: v.get_stats() for k, v in self._aggregators.items()},
            "cached_tier_lists": len(self._tier_lists_cache),
            "current_patch": self._current_patch,
        }

    async def shutdown(self):
        self._aggregators.clear()
        self._tier_lists_cache.clear()
        logger.info("ChampionStatisticsEngine shutdown")

    def get_module_info(self) -> Dict[str, str]:
        return {
            "task_id": "M810",
            "name": "Champion Statistics Engine",
            "version": "1.0.0",
        }


if __name__ == "__main__":
    print("M810 Champion Statistics Engine - Self Test")

    # Test WinRateStats
    wr = WinRateStats.calculate(55, 100)
    print(f"Win rate: {wr.win_rate:.1%} (CI: {wr.ci_lower:.1%}-{wr.ci_upper:.1%})")
    assert wr.is_reliable

    # Test PickBanStats
    pb = ChampionPickBanStats.calculate(1, "Aatrox", 150, 50, 1000)
    print(f"Aatrox: pick={pb.pick_rate:.1%}, ban={pb.ban_rate:.1%}, presence={pb.presence_rate:.1%}")

    # Test aggregator
    agg = ChampionDataAggregator()
    print(f"Aggregator stats: {agg.get_stats()}")

    print("\nM810 self-test passed.")

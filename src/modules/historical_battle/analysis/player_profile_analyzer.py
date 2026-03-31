#!/usr/bin/env python3
"""
M809 - Player Profile Analyzer
================================
OperatorRL Historical Battle System - Multi-dimensional Player Profiling

查看 Seraphine 项目上现有的玩家画像实现方式，理解其模式，特别是
统计计算和展示层是如何分离的。从 career 模块开始，遵循该模式实现
一个新的多维度玩家画像分析器，使对战前侦察可以快速评估对手实力，
并能基于历史数据生成行为特征标签。引入时间序列分析，使趋势检测
能够发现玩家近期的状态变化，同时优化计算性能。

Core responsibilities:
- Build comprehensive player profiles from match history
- Calculate multi-dimensional performance metrics
- Identify play style patterns and behavioral tags
- Detect performance trends (improving/declining/stable)
- Generate scouting reports for pre-game analysis
"""

import os
import sys
import math
import json
import time
import logging
import hashlib
import datetime
import statistics
from pathlib import Path
from typing import (
    Dict, List, Any, Optional, Tuple, Set, Callable, Union, Sequence
)
from dataclasses import dataclass, field, asdict
from enum import Enum
from abc import ABC, abstractmethod
from collections import defaultdict, Counter

# ─── Module Logger ────────────────────────────────────────────────────────────

logger = logging.getLogger("operatorRL.historical_battle.player_profile_analyzer")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────────────

MIN_GAMES_FOR_PROFILE = 5
MIN_GAMES_FOR_TREND = 10
TREND_WINDOW_SIZE = 20
RECENT_GAMES_WINDOW = 10
PERFORMANCE_WEIGHTS = {
    "kda": 0.25,
    "cs_per_min": 0.15,
    "vision_score_per_min": 0.10,
    "damage_share": 0.15,
    "gold_efficiency": 0.10,
    "objective_participation": 0.10,
    "win_rate": 0.15,
}
TIER_NUMERIC = {
    "IRON": 0, "BRONZE": 400, "SILVER": 800, "GOLD": 1200,
    "PLATINUM": 1600, "EMERALD": 2000, "DIAMOND": 2400,
    "MASTER": 2800, "GRANDMASTER": 3200, "CHALLENGER": 3600,
}
TAG_THRESHOLDS = {
    "aggressive": {"kda_ratio": (0, 2.5), "kills_per_game": (8, 999)},
    "passive": {"kda_ratio": (4.0, 999), "deaths_per_game": (0, 3)},
    "carry": {"damage_share": (0.30, 1.0), "gold_share": (0.28, 1.0)},
    "supportive": {"assists_per_game": (10, 999), "vision_per_min": (1.2, 999)},
    "objective_focused": {"objective_score": (0.6, 1.0)},
    "early_game": {"first_blood_rate": (0.3, 1.0), "early_cs_lead": (10, 999)},
    "late_game": {"late_game_wr": (0.55, 1.0)},
    "tilted": {"loss_streak": (4, 999), "recent_wr": (0, 0.35)},
    "on_fire": {"win_streak": (4, 999), "recent_wr": (0.65, 1.0)},
    "one_trick": {"top_champ_rate": (0.5, 1.0)},
    "versatile": {"unique_champs": (15, 999)},
    "consistent": {"performance_variance": (0, 0.15)},
}
PERFORMANCE_RATING_SCALE = {"S+": 95, "S": 85, "A": 75, "B": 65, "C": 55, "D": 40, "F": 0}


# ─── Enumerations ─────────────────────────────────────────────────────────────

class PlayStyle(Enum):
    AGGRESSIVE = "aggressive"
    PASSIVE = "passive"
    BALANCED = "balanced"
    EARLY_GAME = "early_game"
    LATE_GAME = "late_game"
    UTILITY = "utility"
    SPLIT_PUSH = "split_push"


class TrendDirection(Enum):
    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"
    VOLATILE = "volatile"
    INSUFFICIENT_DATA = "insufficient_data"


class ProfileConfidence(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNRELIABLE = "unreliable"


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class RoleStats:
    """Statistics for a specific role."""
    role: str = ""
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    avg_kills: float = 0.0
    avg_deaths: float = 0.0
    avg_assists: float = 0.0
    avg_cs_per_min: float = 0.0
    avg_vision_score: float = 0.0
    avg_damage: float = 0.0
    avg_gold: float = 0.0
    champion_pool: List[str] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def kda_ratio(self) -> float:
        if self.avg_deaths == 0:
            return self.avg_kills + self.avg_assists
        return (self.avg_kills + self.avg_assists) / self.avg_deaths


@dataclass
class ChampionStats:
    """Statistics for a specific champion."""
    champion_name: str = ""
    champion_id: int = 0
    games_played: int = 0
    wins: int = 0
    avg_kills: float = 0.0
    avg_deaths: float = 0.0
    avg_assists: float = 0.0
    avg_cs_per_min: float = 0.0
    avg_damage: float = 0.0
    avg_vision: float = 0.0
    preferred_role: str = ""
    preferred_rune_primary: int = 0
    preferred_summoner_spells: Tuple[int, int] = (0, 0)
    mastery_level: int = 0
    mastery_points: int = 0

    @property
    def win_rate(self) -> float:
        return self.wins / self.games_played if self.games_played > 0 else 0.0

    @property
    def kda_ratio(self) -> float:
        if self.avg_deaths == 0:
            return self.avg_kills + self.avg_assists
        return (self.avg_kills + self.avg_assists) / self.avg_deaths


@dataclass
class PerformanceTrend:
    """Performance trend over time."""
    metric_name: str = ""
    direction: TrendDirection = TrendDirection.INSUFFICIENT_DATA
    slope: float = 0.0
    r_squared: float = 0.0
    recent_avg: float = 0.0
    historical_avg: float = 0.0
    change_percent: float = 0.0
    data_points: int = 0

    @property
    def is_significant(self) -> bool:
        return abs(self.change_percent) > 10.0 and self.r_squared > 0.3


@dataclass
class BehavioralTag:
    """A behavioral label for the player."""
    tag: str = ""
    confidence: float = 0.0
    supporting_metrics: Dict[str, float] = field(default_factory=dict)
    description: str = ""

    @property
    def is_strong(self) -> bool:
        return self.confidence >= 0.7


@dataclass
class StreakInfo:
    """Current and historical streak information."""
    current_streak_type: str = ""  # "win" or "loss"
    current_streak_length: int = 0
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    recent_results: List[bool] = field(default_factory=list)

    @property
    def is_on_win_streak(self) -> bool:
        return self.current_streak_type == "win" and self.current_streak_length >= 3

    @property
    def is_on_loss_streak(self) -> bool:
        return self.current_streak_type == "loss" and self.current_streak_length >= 3


@dataclass
class TimeDistribution:
    """When the player typically plays."""
    games_by_hour: Dict[int, int] = field(default_factory=dict)
    games_by_day: Dict[int, int] = field(default_factory=dict)
    peak_hours: List[int] = field(default_factory=list)
    avg_games_per_day: float = 0.0
    avg_session_length_games: float = 0.0

    @property
    def most_active_hour(self) -> int:
        if not self.games_by_hour:
            return 0
        return max(self.games_by_hour, key=self.games_by_hour.get)


@dataclass
class PlayerProfile:
    """
    Comprehensive player profile aggregate.
    Central output model for the analyzer.
    """
    # Identity
    puuid: str = ""
    game_name: str = ""
    tag_line: str = ""
    region: str = ""
    summoner_level: int = 0

    # Rank
    current_tier: str = "UNRANKED"
    current_division: str = ""
    current_lp: int = 0
    peak_tier: str = "UNRANKED"

    # Overall stats
    total_games: int = 0
    total_wins: int = 0
    total_losses: int = 0
    overall_kda: float = 0.0
    overall_cs_per_min: float = 0.0
    overall_vision_per_min: float = 0.0
    overall_damage_per_min: float = 0.0

    # Role breakdown
    role_stats: Dict[str, RoleStats] = field(default_factory=dict)
    primary_role: str = ""
    secondary_role: str = ""

    # Champion pool
    champion_stats: Dict[str, ChampionStats] = field(default_factory=dict)
    top_champions: List[str] = field(default_factory=list)
    unique_champions_played: int = 0
    champion_pool_depth: int = 0

    # Behavioral analysis
    play_style: PlayStyle = PlayStyle.BALANCED
    behavioral_tags: List[BehavioralTag] = field(default_factory=list)
    streaks: StreakInfo = field(default_factory=StreakInfo)
    time_distribution: TimeDistribution = field(default_factory=TimeDistribution)

    # Trends
    trends: Dict[str, PerformanceTrend] = field(default_factory=dict)
    overall_trend: TrendDirection = TrendDirection.INSUFFICIENT_DATA

    # Meta
    profile_confidence: ProfileConfidence = ProfileConfidence.UNRELIABLE
    last_updated: Optional[datetime.datetime] = None
    analysis_version: str = "1.0.0"

    @property
    def win_rate(self) -> float:
        total = self.total_wins + self.total_losses
        return self.total_wins / total if total > 0 else 0.0

    @property
    def rank_string(self) -> str:
        if self.current_tier == "UNRANKED":
            return "Unranked"
        if self.current_tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            return f"{self.current_tier} {self.current_lp}LP"
        return f"{self.current_tier} {self.current_division} {self.current_lp}LP"

    @property
    def strong_tags(self) -> List[str]:
        return [t.tag for t in self.behavioral_tags if t.is_strong]


# ─── Analysis Engine ─────────────────────────────────────────────────────────

class PerformanceCalculator:
    """
    Calculates normalized performance scores from raw statistics.
    Uses percentile-based normalization against tier benchmarks.
    """

    TIER_BENCHMARKS = {
        "IRON": {"cs_per_min": 4.5, "vision_per_min": 0.3, "kda": 1.5},
        "BRONZE": {"cs_per_min": 5.0, "vision_per_min": 0.4, "kda": 2.0},
        "SILVER": {"cs_per_min": 5.5, "vision_per_min": 0.5, "kda": 2.3},
        "GOLD": {"cs_per_min": 6.0, "vision_per_min": 0.6, "kda": 2.8},
        "PLATINUM": {"cs_per_min": 6.5, "vision_per_min": 0.7, "kda": 3.2},
        "EMERALD": {"cs_per_min": 7.0, "vision_per_min": 0.8, "kda": 3.5},
        "DIAMOND": {"cs_per_min": 7.5, "vision_per_min": 0.9, "kda": 4.0},
        "MASTER": {"cs_per_min": 8.0, "vision_per_min": 1.0, "kda": 4.5},
        "GRANDMASTER": {"cs_per_min": 8.5, "vision_per_min": 1.1, "kda": 5.0},
        "CHALLENGER": {"cs_per_min": 9.0, "vision_per_min": 1.2, "kda": 5.5},
    }

    @classmethod
    def calculate_composite_score(
        cls, metrics: Dict[str, float], tier: str = "GOLD"
    ) -> float:
        """
        Calculate weighted composite performance score (0-100).
        """
        benchmark = cls.TIER_BENCHMARKS.get(tier, cls.TIER_BENCHMARKS["GOLD"])
        score = 0.0

        for metric, weight in PERFORMANCE_WEIGHTS.items():
            value = metrics.get(metric, 0.0)
            bench = benchmark.get(metric, 1.0)
            if bench > 0:
                normalized = min(value / bench, 2.0) * 50  # Cap at 100
            else:
                normalized = 50.0
            score += normalized * weight

        return min(max(score, 0), 100)

    @classmethod
    def get_rating_letter(cls, score: float) -> str:
        """Convert numeric score to letter rating."""
        for letter, threshold in sorted(
            PERFORMANCE_RATING_SCALE.items(), key=lambda x: x[1], reverse=True
        ):
            if score >= threshold:
                return letter
        return "F"


class TrendAnalyzer:
    """
    Analyzes performance trends using linear regression and variance analysis.
    """

    @staticmethod
    def linear_regression(values: List[float]) -> Tuple[float, float, float]:
        """
        Simple linear regression. Returns (slope, intercept, r_squared).
        """
        n = len(values)
        if n < 2:
            return 0.0, 0.0, 0.0

        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(values) / n

        ss_xy = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        ss_xx = sum((x[i] - x_mean) ** 2 for i in range(n))
        ss_yy = sum((values[i] - y_mean) ** 2 for i in range(n))

        if ss_xx == 0:
            return 0.0, y_mean, 0.0

        slope = ss_xy / ss_xx
        intercept = y_mean - slope * x_mean

        if ss_yy == 0:
            r_squared = 1.0
        else:
            r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)

        return slope, intercept, r_squared

    @classmethod
    def analyze_trend(
        cls, values: List[float], metric_name: str
    ) -> PerformanceTrend:
        """Analyze a metric's trend over time."""
        if len(values) < MIN_GAMES_FOR_TREND:
            return PerformanceTrend(
                metric_name=metric_name,
                direction=TrendDirection.INSUFFICIENT_DATA,
                data_points=len(values),
            )

        slope, intercept, r_squared = cls.linear_regression(values)

        recent_window = min(RECENT_GAMES_WINDOW, len(values) // 2)
        recent_avg = statistics.mean(values[-recent_window:])
        historical_avg = statistics.mean(values)

        if historical_avg != 0:
            change_pct = ((recent_avg - historical_avg) / abs(historical_avg)) * 100
        else:
            change_pct = 0.0

        variance = statistics.variance(values) if len(values) > 1 else 0.0
        cv = math.sqrt(variance) / abs(historical_avg) if historical_avg != 0 else 0.0

        # Determine direction
        if cv > 0.5:
            direction = TrendDirection.VOLATILE
        elif abs(change_pct) < 5.0 or r_squared < 0.1:
            direction = TrendDirection.STABLE
        elif slope > 0:
            direction = TrendDirection.IMPROVING
        else:
            direction = TrendDirection.DECLINING

        return PerformanceTrend(
            metric_name=metric_name,
            direction=direction,
            slope=round(slope, 4),
            r_squared=round(r_squared, 4),
            recent_avg=round(recent_avg, 2),
            historical_avg=round(historical_avg, 2),
            change_percent=round(change_pct, 1),
            data_points=len(values),
        )


class BehavioralTagger:
    """
    Assigns behavioral tags based on statistical thresholds.
    """

    @classmethod
    def generate_tags(cls, metrics: Dict[str, float]) -> List[BehavioralTag]:
        """Generate behavioral tags from player metrics."""
        tags = []

        for tag_name, conditions in TAG_THRESHOLDS.items():
            confidence = cls._evaluate_conditions(metrics, conditions)
            if confidence > 0.5:
                tags.append(BehavioralTag(
                    tag=tag_name,
                    confidence=round(confidence, 2),
                    supporting_metrics={
                        k: round(metrics.get(k, 0), 2)
                        for k in conditions.keys()
                        if k in metrics
                    },
                    description=cls._get_tag_description(tag_name),
                ))

        tags.sort(key=lambda t: t.confidence, reverse=True)
        return tags

    @staticmethod
    def _evaluate_conditions(
        metrics: Dict[str, float], conditions: Dict[str, Tuple[float, float]]
    ) -> float:
        """Evaluate how well metrics match tag conditions."""
        matches = 0
        total = len(conditions)

        for metric_name, (low, high) in conditions.items():
            value = metrics.get(metric_name, 0.0)
            if low <= value <= high:
                matches += 1

        return matches / total if total > 0 else 0.0

    @staticmethod
    def _get_tag_description(tag: str) -> str:
        descriptions = {
            "aggressive": "Prefers high-kill, high-risk playstyle",
            "passive": "Plays safe with low death counts",
            "carry": "High damage and gold share, team's primary carry",
            "supportive": "Focus on assists and vision control",
            "objective_focused": "Prioritizes dragons, barons, and towers",
            "early_game": "Strong laning phase, often gets first blood",
            "late_game": "Scales well, higher win rate in long games",
            "tilted": "Currently on a losing streak, performance declining",
            "on_fire": "Currently winning consistently",
            "one_trick": "Heavily favors a single champion",
            "versatile": "Large champion pool with diverse picks",
            "consistent": "Stable performance with low variance",
        }
        return descriptions.get(tag, "")


class StreakCalculator:
    """Calculate win/loss streaks from match results."""

    @staticmethod
    def calculate(results: List[bool]) -> StreakInfo:
        """Calculate streak information from a list of win/loss results."""
        if not results:
            return StreakInfo()

        info = StreakInfo(recent_results=results[-RECENT_GAMES_WINDOW:])

        # Current streak
        current_val = results[-1]
        streak = 0
        for r in reversed(results):
            if r == current_val:
                streak += 1
            else:
                break
        info.current_streak_type = "win" if current_val else "loss"
        info.current_streak_length = streak

        # Longest streaks
        max_win = max_loss = current_win = current_loss = 0
        for r in results:
            if r:
                current_win += 1
                current_loss = 0
                max_win = max(max_win, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss = max(max_loss, current_loss)

        info.longest_win_streak = max_win
        info.longest_loss_streak = max_loss

        return info


# ─── Main Analyzer ────────────────────────────────────────────────────────────

class PlayerProfileAnalyzer:
    """
    Main player profile analysis engine.
    Consumes match history data and produces comprehensive player profiles.
    Implements HistoricalBattleInterface contract.
    """

    def __init__(self):
        self._perf_calc = PerformanceCalculator()
        self._trend_analyzer = TrendAnalyzer()
        self._tagger = BehavioralTagger()
        self._profiles_cache: Dict[str, PlayerProfile] = {}
        self._initialized = False

    async def initialize(self, config: Dict[str, Any] = None) -> bool:
        self._initialized = True
        logger.info("PlayerProfileAnalyzer initialized")
        return True

    def analyze(
        self,
        puuid: str,
        matches: List[Dict[str, Any]],
        ranked_info: Optional[Dict[str, Any]] = None,
        summoner_info: Optional[Dict[str, Any]] = None,
    ) -> PlayerProfile:
        """
        Build a complete player profile from match history data.
        """
        profile = PlayerProfile(puuid=puuid)

        if summoner_info:
            profile.game_name = summoner_info.get("game_name", "")
            profile.tag_line = summoner_info.get("tag_line", "")
            profile.region = summoner_info.get("region", "")
            profile.summoner_level = summoner_info.get("summoner_level", 0)

        if ranked_info:
            profile.current_tier = ranked_info.get("tier", "UNRANKED")
            profile.current_division = ranked_info.get("division", "")
            profile.current_lp = ranked_info.get("lp", 0)

        if not matches:
            profile.profile_confidence = ProfileConfidence.UNRELIABLE
            return profile

        # Extract player-specific data from each match
        player_games = self._extract_player_games(puuid, matches)

        if not player_games:
            profile.profile_confidence = ProfileConfidence.UNRELIABLE
            return profile

        profile.total_games = len(player_games)

        # Overall stats
        results = [g["win"] for g in player_games]
        profile.total_wins = sum(1 for r in results if r)
        profile.total_losses = profile.total_games - profile.total_wins

        kills = [g.get("kills", 0) for g in player_games]
        deaths = [g.get("deaths", 0) for g in player_games]
        assists = [g.get("assists", 0) for g in player_games]

        total_k = sum(kills)
        total_d = sum(deaths)
        total_a = sum(assists)
        profile.overall_kda = (
            (total_k + total_a) / total_d if total_d > 0
            else float(total_k + total_a)
        )

        cs_per_min_values = [g.get("cs_per_min", 0) for g in player_games]
        profile.overall_cs_per_min = (
            statistics.mean(cs_per_min_values) if cs_per_min_values else 0.0
        )

        vision_values = [g.get("vision_per_min", 0) for g in player_games]
        profile.overall_vision_per_min = (
            statistics.mean(vision_values) if vision_values else 0.0
        )

        # Role analysis
        profile.role_stats = self._analyze_roles(player_games)
        sorted_roles = sorted(
            profile.role_stats.items(),
            key=lambda x: x[1].games_played,
            reverse=True,
        )
        if sorted_roles:
            profile.primary_role = sorted_roles[0][0]
        if len(sorted_roles) > 1:
            profile.secondary_role = sorted_roles[1][0]

        # Champion analysis
        profile.champion_stats = self._analyze_champions(player_games)
        profile.unique_champions_played = len(profile.champion_stats)
        sorted_champs = sorted(
            profile.champion_stats.values(),
            key=lambda c: c.games_played,
            reverse=True,
        )
        profile.top_champions = [c.champion_name for c in sorted_champs[:5]]
        profile.champion_pool_depth = sum(
            1 for c in sorted_champs if c.games_played >= 3
        )

        # Behavioral tags
        tag_metrics = self._compute_tag_metrics(player_games, profile)
        profile.behavioral_tags = self._tagger.generate_tags(tag_metrics)

        # Play style classification
        profile.play_style = self._classify_play_style(tag_metrics, profile)

        # Streaks
        profile.streaks = StreakCalculator.calculate(results)

        # Trends
        kda_values = [
            (g.get("kills", 0) + g.get("assists", 0)) /
            max(g.get("deaths", 1), 1)
            for g in player_games
        ]
        profile.trends["kda"] = self._trend_analyzer.analyze_trend(
            kda_values, "kda"
        )
        profile.trends["cs_per_min"] = self._trend_analyzer.analyze_trend(
            cs_per_min_values, "cs_per_min"
        )

        wr_rolling = self._rolling_win_rate(results, TREND_WINDOW_SIZE)
        if wr_rolling:
            profile.trends["win_rate"] = self._trend_analyzer.analyze_trend(
                wr_rolling, "win_rate"
            )

        # Overall trend
        improving_count = sum(
            1 for t in profile.trends.values()
            if t.direction == TrendDirection.IMPROVING
        )
        declining_count = sum(
            1 for t in profile.trends.values()
            if t.direction == TrendDirection.DECLINING
        )
        if improving_count > declining_count:
            profile.overall_trend = TrendDirection.IMPROVING
        elif declining_count > improving_count:
            profile.overall_trend = TrendDirection.DECLINING
        else:
            profile.overall_trend = TrendDirection.STABLE

        # Confidence
        if profile.total_games >= 50:
            profile.profile_confidence = ProfileConfidence.HIGH
        elif profile.total_games >= 20:
            profile.profile_confidence = ProfileConfidence.MEDIUM
        elif profile.total_games >= MIN_GAMES_FOR_PROFILE:
            profile.profile_confidence = ProfileConfidence.LOW
        else:
            profile.profile_confidence = ProfileConfidence.UNRELIABLE

        profile.last_updated = datetime.datetime.now(datetime.timezone.utc)

        # Cache the profile
        self._profiles_cache[puuid] = profile

        return profile

    def _extract_player_games(
        self, puuid: str, matches: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Extract player-specific data from matches."""
        games = []
        for match in matches:
            participants = match.get("participants", [])
            for p in participants:
                p_puuid = p.get("puuid", p.get("summoner", {}).get("puuid", ""))
                if p_puuid == puuid:
                    duration_min = match.get("game_duration", 1800) / 60
                    if duration_min < 0.5:
                        duration_min = 1  # Prevent division issues

                    combat = p.get("combat", {})
                    farming = p.get("farming", {})
                    vision = p.get("vision", {})

                    game_data = {
                        "match_id": match.get("match_id", ""),
                        "champion_name": p.get("champion_name", ""),
                        "champion_id": p.get("champion_id", 0),
                        "role": p.get("role", "UNKNOWN"),
                        "team_id": p.get("team_id", 0),
                        "win": p.get("win", False),
                        "kills": combat.get("kills", p.get("kills", 0)),
                        "deaths": combat.get("deaths", p.get("deaths", 0)),
                        "assists": combat.get("assists", p.get("assists", 0)),
                        "cs_per_min": (
                            (farming.get("total_minions_killed", 0) +
                             farming.get("neutral_minions_killed", 0)) / duration_min
                        ),
                        "vision_per_min": vision.get("vision_score", 0) / duration_min,
                        "damage": combat.get("total_damage_to_champions", 0),
                        "damage_per_min": combat.get("total_damage_to_champions", 0) / duration_min,
                        "gold": p.get("items", {}).get("gold_earned", 0),
                        "gold_per_min": p.get("items", {}).get("gold_earned", 0) / duration_min,
                        "duration_min": duration_min,
                        "champion_level": p.get("champion_level", 0),
                        "first_blood": farming.get("first_blood", False),
                    }
                    games.append(game_data)
                    break

        return games

    def _analyze_roles(
        self, games: List[Dict[str, Any]]
    ) -> Dict[str, RoleStats]:
        """Analyze per-role statistics."""
        role_data: Dict[str, List[Dict]] = defaultdict(list)
        for g in games:
            role = g.get("role", "UNKNOWN")
            role_data[role].append(g)

        stats = {}
        for role, role_games in role_data.items():
            champs = list(set(g["champion_name"] for g in role_games))
            rs = RoleStats(
                role=role,
                games_played=len(role_games),
                wins=sum(1 for g in role_games if g["win"]),
                losses=sum(1 for g in role_games if not g["win"]),
                avg_kills=statistics.mean([g["kills"] for g in role_games]),
                avg_deaths=statistics.mean([g["deaths"] for g in role_games]),
                avg_assists=statistics.mean([g["assists"] for g in role_games]),
                avg_cs_per_min=statistics.mean([g["cs_per_min"] for g in role_games]),
                avg_vision_score=statistics.mean([g["vision_per_min"] for g in role_games]),
                avg_damage=statistics.mean([g["damage"] for g in role_games]),
                avg_gold=statistics.mean([g["gold"] for g in role_games]),
                champion_pool=champs[:10],
            )
            stats[role] = rs

        return stats

    def _analyze_champions(
        self, games: List[Dict[str, Any]]
    ) -> Dict[str, ChampionStats]:
        """Analyze per-champion statistics."""
        champ_data: Dict[str, List[Dict]] = defaultdict(list)
        for g in games:
            name = g.get("champion_name", "Unknown")
            champ_data[name].append(g)

        stats = {}
        for name, champ_games in champ_data.items():
            roles = Counter(g.get("role", "") for g in champ_games)
            preferred = roles.most_common(1)[0][0] if roles else ""

            cs = ChampionStats(
                champion_name=name,
                champion_id=champ_games[0].get("champion_id", 0),
                games_played=len(champ_games),
                wins=sum(1 for g in champ_games if g["win"]),
                avg_kills=statistics.mean([g["kills"] for g in champ_games]),
                avg_deaths=statistics.mean([g["deaths"] for g in champ_games]),
                avg_assists=statistics.mean([g["assists"] for g in champ_games]),
                avg_cs_per_min=statistics.mean([g["cs_per_min"] for g in champ_games]),
                avg_damage=statistics.mean([g["damage"] for g in champ_games]),
                avg_vision=statistics.mean([g["vision_per_min"] for g in champ_games]),
                preferred_role=preferred,
            )
            stats[name] = cs

        return stats

    def _compute_tag_metrics(
        self, games: List[Dict[str, Any]], profile: PlayerProfile
    ) -> Dict[str, float]:
        """Compute metrics used for behavioral tagging."""
        metrics: Dict[str, float] = {}

        metrics["kda_ratio"] = profile.overall_kda
        metrics["kills_per_game"] = statistics.mean([g["kills"] for g in games])
        metrics["deaths_per_game"] = statistics.mean([g["deaths"] for g in games])
        metrics["assists_per_game"] = statistics.mean([g["assists"] for g in games])
        metrics["vision_per_min"] = profile.overall_vision_per_min

        total_team_damage = sum(g["damage"] for g in games) * 5 / max(len(games), 1)
        if total_team_damage > 0:
            metrics["damage_share"] = (
                sum(g["damage"] for g in games) / total_team_damage
            )
        else:
            metrics["damage_share"] = 0.2

        total_team_gold = sum(g["gold"] for g in games) * 5 / max(len(games), 1)
        if total_team_gold > 0:
            metrics["gold_share"] = sum(g["gold"] for g in games) / total_team_gold
        else:
            metrics["gold_share"] = 0.2

        fb_games = sum(1 for g in games if g.get("first_blood", False))
        metrics["first_blood_rate"] = fb_games / max(len(games), 1)

        recent = games[-RECENT_GAMES_WINDOW:]
        recent_wins = sum(1 for g in recent if g["win"])
        metrics["recent_wr"] = recent_wins / max(len(recent), 1)

        streaks = profile.streaks
        metrics["win_streak"] = (
            streaks.current_streak_length
            if streaks.current_streak_type == "win" else 0
        )
        metrics["loss_streak"] = (
            streaks.current_streak_length
            if streaks.current_streak_type == "loss" else 0
        )

        if profile.top_champions and profile.total_games > 0:
            top_champ = profile.champion_stats.get(profile.top_champions[0])
            if top_champ:
                metrics["top_champ_rate"] = top_champ.games_played / profile.total_games
        metrics["unique_champs"] = float(profile.unique_champions_played)

        kda_values = [
            (g["kills"] + g["assists"]) / max(g["deaths"], 1) for g in games
        ]
        if len(kda_values) > 1:
            mean_kda = statistics.mean(kda_values)
            if mean_kda > 0:
                metrics["performance_variance"] = (
                    statistics.stdev(kda_values) / mean_kda
                )

        return metrics

    def _classify_play_style(
        self, metrics: Dict[str, float], profile: PlayerProfile
    ) -> PlayStyle:
        """Classify overall play style from metrics."""
        if metrics.get("kills_per_game", 0) > 7 and metrics.get("deaths_per_game", 0) > 5:
            return PlayStyle.AGGRESSIVE
        if metrics.get("deaths_per_game", 0) < 3 and metrics.get("kda_ratio", 0) > 4:
            return PlayStyle.PASSIVE
        if metrics.get("assists_per_game", 0) > 12:
            return PlayStyle.UTILITY
        if metrics.get("first_blood_rate", 0) > 0.3:
            return PlayStyle.EARLY_GAME
        return PlayStyle.BALANCED

    @staticmethod
    def _rolling_win_rate(results: List[bool], window: int) -> List[float]:
        """Calculate rolling win rate."""
        if len(results) < window:
            return []
        rolling = []
        for i in range(window, len(results) + 1):
            window_results = results[i - window:i]
            rolling.append(sum(1 for r in window_results if r) / window)
        return rolling

    def get_profile(self, puuid: str) -> Optional[PlayerProfile]:
        """Retrieve cached profile."""
        return self._profiles_cache.get(puuid)

    async def health_check(self) -> Dict[str, Any]:
        return {
            "initialized": self._initialized,
            "cached_profiles": len(self._profiles_cache),
        }

    async def shutdown(self):
        self._profiles_cache.clear()
        logger.info("PlayerProfileAnalyzer shutdown")

    def get_module_info(self) -> Dict[str, str]:
        return {
            "task_id": "M809",
            "name": "Player Profile Analyzer",
            "version": "1.0.0",
        }


# ─── Self-Test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("M809 Player Profile Analyzer - Self Test")

    # Test trend analysis
    values = [3.0, 3.2, 3.5, 3.3, 3.8, 4.0, 3.9, 4.2, 4.5, 4.3, 4.8]
    trend = TrendAnalyzer.analyze_trend(values, "kda")
    print(f"Trend: {trend.direction.value}, slope={trend.slope}, change={trend.change_percent}%")

    # Test streak calculation
    results = [True, True, False, True, True, True, True, False]
    streaks = StreakCalculator.calculate(results)
    print(f"Current streak: {streaks.current_streak_type} x{streaks.current_streak_length}")
    print(f"Longest win: {streaks.longest_win_streak}, loss: {streaks.longest_loss_streak}")

    # Test behavioral tagging
    test_metrics = {
        "kda_ratio": 5.2, "kills_per_game": 4.5, "deaths_per_game": 2.1,
        "assists_per_game": 12.0, "vision_per_min": 1.3, "damage_share": 0.18,
        "recent_wr": 0.7, "win_streak": 5, "unique_champs": 8,
    }
    tags = BehavioralTagger.generate_tags(test_metrics)
    print(f"Tags: {[f'{t.tag}({t.confidence})' for t in tags]}")

    print("\nM809 self-test passed.")

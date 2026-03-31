#!/usr/bin/env python3
"""
M815 - Performance Metrics Calculator
====================================
OperatorRL Historical Battle System - Multi-dimensional Performance Scoring

查看 Seraphine 项目上现有的玩家表现评分实现方式,理解其模式,
特别是多维度指标是如何归一化和加权的。从基础KDA计算开始,
遵循该模式实现全面的表现指标计算器,使系统可以量化每位玩家
在每局游戏中的综合表现,并能跨局对比趋势。接着引入视野评分、
补刀效率、目标控制等高级指标,优化计算流水线以支持批量分析。

Core responsibilities:
- Calculate KDA, CS/min, damage share, vision score
- Compute gold efficiency and objective participation
- Generate per-minute and per-phase breakdowns
- Normalize metrics for cross-game comparison
- Produce trend analysis across match history
"""

import os, sys, json, time, math, logging, hashlib, statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union
from dataclasses import dataclass, field, asdict
from collections import defaultdict

logger = logging.getLogger("operatorRL.historical_battle.analysis.performance")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────

PERFECT_CS_PER_MIN = 12.6
KDA_DEATH_SUBSTITUTE = 1.0
VISION_SCORE_WEIGHTS = {"wards_placed": 1.0, "wards_destroyed": 1.5, "control_wards_purchased": 2.0}
DAMAGE_SHARE_ROLES = {"TOP": 0.20, "JUNGLE": 0.15, "MID": 0.25, "ADC": 0.30, "SUPPORT": 0.10}
OBJECTIVE_TYPES = ["DRAGON", "BARON", "HERALD", "TOWER", "INHIBITOR"]
EARLY_GAME_END_MIN = 14
MID_GAME_END_MIN = 25
PERCENTILE_THRESHOLDS = [10, 25, 50, 75, 90]
MAX_HISTORY_FOR_TREND = 50

# ─── Enumerations ─────────────────────────────────────────────────────

class PerformanceRating(Enum):
    S_PLUS = "S+"
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"

class MetricCategory(Enum):
    COMBAT = "combat"
    FARMING = "farming"
    VISION = "vision"
    OBJECTIVES = "objectives"
    INCOME = "income"
    SURVIVABILITY = "survivability"
    UTILITY = "utility"

class TrendDirection(Enum):
    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"
    VOLATILE = "volatile"

# ─── Data Models ──────────────────────────────────────────────────────

@dataclass
class RawMatchStats:
    """Raw stats from a single match for one participant."""
    match_id: str
    participant_id: int
    champion_id: int
    role: str
    game_duration_seconds: int
    win: bool
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    total_damage_dealt: int = 0
    total_damage_to_champions: int = 0
    total_damage_taken: int = 0
    total_heal: int = 0
    total_minions_killed: int = 0
    neutral_minions_killed: int = 0
    gold_earned: int = 0
    gold_spent: int = 0
    vision_score: int = 0
    wards_placed: int = 0
    wards_killed: int = 0
    control_wards_purchased: int = 0
    turret_kills: int = 0
    inhibitor_kills: int = 0
    dragon_kills: int = 0
    baron_kills: int = 0
    first_blood_kill: bool = False
    first_blood_assist: bool = False
    first_tower_kill: bool = False
    double_kills: int = 0
    triple_kills: int = 0
    quadra_kills: int = 0
    penta_kills: int = 0
    largest_killing_spree: int = 0
    total_time_cc_dealt: int = 0
    damage_self_mitigated: int = 0
    team_total_damage: int = 0
    team_total_gold: int = 0

    @property
    def game_duration_minutes(self) -> float:
        return self.game_duration_seconds / 60.0

    @property
    def total_cs(self) -> int:
        return self.total_minions_killed + self.neutral_minions_killed


@dataclass
class CalculatedMetrics:
    """Derived performance metrics from raw stats."""
    match_id: str
    participant_id: int
    champion_id: int
    role: str
    win: bool
    kda: float = 0.0
    kill_participation: float = 0.0
    damage_per_minute: float = 0.0
    damage_share: float = 0.0
    damage_taken_per_minute: float = 0.0
    damage_efficiency: float = 0.0
    cs_per_minute: float = 0.0
    cs_efficiency: float = 0.0
    gold_per_minute: float = 0.0
    gold_efficiency: float = 0.0
    gold_share: float = 0.0
    vision_score_per_minute: float = 0.0
    wards_per_minute: float = 0.0
    vision_denial_rate: float = 0.0
    control_ward_investment: int = 0
    objective_participation: float = 0.0
    turret_damage_contribution: float = 0.0
    death_rate_per_minute: float = 0.0
    damage_mitigated_ratio: float = 0.0
    survival_score: float = 0.0
    cc_score_per_minute: float = 0.0
    heal_per_minute: float = 0.0
    multikill_score: float = 0.0
    overall_rating: PerformanceRating = PerformanceRating.C
    overall_score: float = 0.0
    component_scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["overall_rating"] = self.overall_rating.value
        return result


@dataclass
class TrendAnalysis:
    """Trend analysis across multiple games."""
    metric_name: str
    values: List[float] = field(default_factory=list)
    direction: TrendDirection = TrendDirection.STABLE
    slope: float = 0.0
    mean: float = 0.0
    std_dev: float = 0.0
    percentile_25: float = 0.0
    percentile_50: float = 0.0
    percentile_75: float = 0.0
    best: float = 0.0
    worst: float = 0.0
    recent_5_avg: float = 0.0
    improvement_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric_name, "direction": self.direction.value,
            "slope": round(self.slope, 4), "mean": round(self.mean, 3),
            "std_dev": round(self.std_dev, 3), "recent_5_avg": round(self.recent_5_avg, 3),
            "best": round(self.best, 3), "worst": round(self.worst, 3),
            "improvement_rate": round(self.improvement_rate, 4),
        }


@dataclass
class PerformanceReport:
    """Complete performance report for a player."""
    summoner_name: str
    total_games: int
    metrics_by_game: List[CalculatedMetrics] = field(default_factory=list)
    trends: Dict[str, TrendAnalysis] = field(default_factory=dict)
    role_breakdown: Dict[str, Dict[str, float]] = field(default_factory=dict)
    champion_breakdown: Dict[int, Dict[str, float]] = field(default_factory=dict)
    overall_average: Dict[str, float] = field(default_factory=dict)
    rating: PerformanceRating = PerformanceRating.C
    percentile_rank: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summoner": self.summoner_name, "games_analyzed": self.total_games,
            "rating": self.rating.value, "percentile": round(self.percentile_rank * 100, 1),
            "overall_averages": {k: round(v, 3) for k, v in self.overall_average.items()},
            "trends": {k: v.to_dict() for k, v in self.trends.items()},
            "role_breakdown": self.role_breakdown,
        }


# ─── Performance Calculator ──────────────────────────────────────────

class PerformanceMetricsCalculator:
    """Calculates comprehensive performance metrics from match data."""

    def __init__(self):
        self._role_benchmarks: Dict[str, Dict[str, float]] = {}
        self._tier_benchmarks: Dict[str, Dict[str, float]] = {}

    def calculate_single_game(self, stats: RawMatchStats) -> CalculatedMetrics:
        """Calculate all derived metrics for a single game."""
        duration = max(stats.game_duration_minutes, 1.0)
        metrics = CalculatedMetrics(
            match_id=stats.match_id, participant_id=stats.participant_id,
            champion_id=stats.champion_id, role=stats.role, win=stats.win,
        )
        deaths_adj = max(stats.deaths, KDA_DEATH_SUBSTITUTE)
        metrics.kda = (stats.kills + stats.assists) / deaths_adj
        team_kills = max(stats.kills + stats.assists, 1)
        metrics.kill_participation = (stats.kills + stats.assists) / team_kills
        metrics.damage_per_minute = stats.total_damage_to_champions / duration
        if stats.team_total_damage > 0:
            metrics.damage_share = stats.total_damage_to_champions / stats.team_total_damage
        metrics.damage_taken_per_minute = stats.total_damage_taken / duration
        if stats.total_damage_taken > 0:
            metrics.damage_efficiency = stats.total_damage_to_champions / stats.total_damage_taken
        metrics.cs_per_minute = stats.total_cs / duration
        metrics.cs_efficiency = metrics.cs_per_minute / PERFECT_CS_PER_MIN
        metrics.gold_per_minute = stats.gold_earned / duration
        if stats.gold_earned > 0:
            metrics.gold_efficiency = stats.gold_spent / stats.gold_earned
        if stats.team_total_gold > 0:
            metrics.gold_share = stats.gold_earned / stats.team_total_gold
        metrics.vision_score_per_minute = stats.vision_score / duration
        metrics.wards_per_minute = stats.wards_placed / duration
        total_ward_interactions = stats.wards_placed + stats.wards_killed
        if total_ward_interactions > 0:
            metrics.vision_denial_rate = stats.wards_killed / total_ward_interactions
        metrics.control_ward_investment = stats.control_wards_purchased
        obj_total = stats.dragon_kills + stats.baron_kills + stats.turret_kills + stats.inhibitor_kills
        metrics.objective_participation = min(1.0, obj_total / max(duration / 5, 1))
        metrics.death_rate_per_minute = stats.deaths / duration
        if stats.total_damage_taken > 0:
            metrics.damage_mitigated_ratio = stats.damage_self_mitigated / stats.total_damage_taken
        metrics.survival_score = max(0, 1.0 - (metrics.death_rate_per_minute * 3))
        metrics.cc_score_per_minute = stats.total_time_cc_dealt / duration
        metrics.heal_per_minute = stats.total_heal / duration
        metrics.multikill_score = (
            stats.double_kills * 1 + stats.triple_kills * 3 +
            stats.quadra_kills * 7 + stats.penta_kills * 15
        )
        combat_score = self._score_combat(metrics, stats.role)
        farming_score = self._score_farming(metrics, stats.role)
        vision_score = self._score_vision(metrics, stats.role)
        objective_score = self._score_objectives(metrics)
        survival_score_val = metrics.survival_score
        metrics.component_scores = {
            MetricCategory.COMBAT.value: round(combat_score, 3),
            MetricCategory.FARMING.value: round(farming_score, 3),
            MetricCategory.VISION.value: round(vision_score, 3),
            MetricCategory.OBJECTIVES.value: round(objective_score, 3),
            MetricCategory.SURVIVABILITY.value: round(survival_score_val, 3),
        }
        role_weights = self._get_role_weights(stats.role)
        metrics.overall_score = sum(
            metrics.component_scores.get(cat, 0) * weight
            for cat, weight in role_weights.items()
        )
        metrics.overall_rating = self._score_to_rating(metrics.overall_score)
        return metrics

    def _score_combat(self, m: CalculatedMetrics, role: str) -> float:
        kda_score = min(1.0, m.kda / 8.0)
        dpm_target = {"ADC": 700, "MID": 650, "TOP": 500, "JUNGLE": 400, "SUPPORT": 200}
        target = dpm_target.get(role, 500)
        dpm_score = min(1.0, m.damage_per_minute / target)
        return kda_score * 0.5 + dpm_score * 0.3 + m.kill_participation * 0.2

    def _score_farming(self, m: CalculatedMetrics, role: str) -> float:
        cs_targets = {"ADC": 8.0, "MID": 7.5, "TOP": 7.0, "JUNGLE": 5.0, "SUPPORT": 1.0}
        target = cs_targets.get(role, 6.0)
        cs_score = min(1.0, m.cs_per_minute / target)
        gpm_score = min(1.0, m.gold_per_minute / 450)
        return cs_score * 0.6 + gpm_score * 0.4

    def _score_vision(self, m: CalculatedMetrics, role: str) -> float:
        vs_targets = {"SUPPORT": 2.0, "JUNGLE": 1.5, "MID": 0.8, "TOP": 0.6, "ADC": 0.5}
        target = vs_targets.get(role, 1.0)
        vs_score = min(1.0, m.vision_score_per_minute / target)
        cw_score = min(1.0, m.control_ward_investment / 5)
        return vs_score * 0.7 + cw_score * 0.3

    def _score_objectives(self, m: CalculatedMetrics) -> float:
        return min(1.0, m.objective_participation)

    def _get_role_weights(self, role: str) -> Dict[str, float]:
        weights = {
            "ADC": {"combat": 0.35, "farming": 0.30, "vision": 0.10, "objectives": 0.10, "survivability": 0.15},
            "MID": {"combat": 0.35, "farming": 0.25, "vision": 0.10, "objectives": 0.15, "survivability": 0.15},
            "TOP": {"combat": 0.25, "farming": 0.25, "vision": 0.10, "objectives": 0.15, "survivability": 0.25},
            "JUNGLE": {"combat": 0.25, "farming": 0.15, "vision": 0.20, "objectives": 0.25, "survivability": 0.15},
            "SUPPORT": {"combat": 0.15, "farming": 0.05, "vision": 0.40, "objectives": 0.15, "survivability": 0.25},
        }
        return weights.get(role, {"combat": 0.25, "farming": 0.20, "vision": 0.15, "objectives": 0.15, "survivability": 0.25})

    def _score_to_rating(self, score: float) -> PerformanceRating:
        if score >= 0.9: return PerformanceRating.S_PLUS
        if score >= 0.8: return PerformanceRating.S
        if score >= 0.65: return PerformanceRating.A
        if score >= 0.50: return PerformanceRating.B
        if score >= 0.35: return PerformanceRating.C
        if score >= 0.20: return PerformanceRating.D
        return PerformanceRating.F

    def calculate_trend(self, metric_name: str, values: List[float]) -> TrendAnalysis:
        """Analyze trend across multiple games for a specific metric."""
        if not values:
            return TrendAnalysis(metric_name=metric_name)
        trend = TrendAnalysis(
            metric_name=metric_name, values=values,
            mean=statistics.mean(values),
            std_dev=statistics.stdev(values) if len(values) > 1 else 0,
            best=max(values), worst=min(values),
        )
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        trend.percentile_25 = sorted_vals[int(n * 0.25)] if n > 3 else trend.mean
        trend.percentile_50 = sorted_vals[int(n * 0.50)] if n > 1 else trend.mean
        trend.percentile_75 = sorted_vals[int(n * 0.75)] if n > 3 else trend.mean
        if len(values) >= 5:
            trend.recent_5_avg = statistics.mean(values[-5:])
        else:
            trend.recent_5_avg = trend.mean
        if len(values) >= 3:
            x_mean = (len(values) - 1) / 2
            y_mean = trend.mean
            numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
            denominator = sum((i - x_mean) ** 2 for i in range(len(values)))
            trend.slope = numerator / denominator if denominator != 0 else 0
            if trend.slope > 0.01:
                trend.direction = TrendDirection.IMPROVING
            elif trend.slope < -0.01:
                trend.direction = TrendDirection.DECLINING
            else:
                trend.direction = TrendDirection.STABLE
            cv = trend.std_dev / trend.mean if trend.mean != 0 else 0
            if cv > 0.5:
                trend.direction = TrendDirection.VOLATILE
        trend.improvement_rate = (trend.recent_5_avg - trend.mean) / trend.mean if trend.mean != 0 else 0
        return trend

    def generate_report(self, summoner_name: str, match_stats: List[RawMatchStats]) -> PerformanceReport:
        """Generate a comprehensive performance report from match history."""
        report = PerformanceReport(summoner_name=summoner_name, total_games=len(match_stats))
        all_metrics = []
        for stats in match_stats:
            m = self.calculate_single_game(stats)
            all_metrics.append(m)
        report.metrics_by_game = all_metrics
        if all_metrics:
            metric_fields = ["kda", "cs_per_minute", "damage_per_minute", "vision_score_per_minute", "gold_per_minute"]
            for f_name in metric_fields:
                values = [getattr(m, f_name) for m in all_metrics]
                report.trends[f_name] = self.calculate_trend(f_name, values)
                report.overall_average[f_name] = statistics.mean(values) if values else 0
            role_groups = defaultdict(list)
            for m in all_metrics:
                role_groups[m.role].append(m)
            for role, role_metrics in role_groups.items():
                report.role_breakdown[role] = {
                    "games": len(role_metrics),
                    "winrate": sum(1 for m in role_metrics if m.win) / len(role_metrics),
                    "avg_kda": statistics.mean([m.kda for m in role_metrics]),
                    "avg_score": statistics.mean([m.overall_score for m in role_metrics]),
                }
            scores = [m.overall_score for m in all_metrics]
            report.rating = self._score_to_rating(statistics.mean(scores))
        return report

    def compare_to_benchmarks(self, metrics: CalculatedMetrics, tier: str = "GOLD") -> Dict[str, str]:
        """Compare calculated metrics against tier benchmarks."""
        benchmarks = {
            "GOLD": {"kda": 2.5, "cs_per_minute": 6.0, "damage_per_minute": 450, "vision_score_per_minute": 0.8},
            "PLATINUM": {"kda": 3.0, "cs_per_minute": 6.5, "damage_per_minute": 500, "vision_score_per_minute": 1.0},
            "DIAMOND": {"kda": 3.5, "cs_per_minute": 7.0, "damage_per_minute": 550, "vision_score_per_minute": 1.2},
        }
        tier_bench = benchmarks.get(tier, benchmarks["GOLD"])
        comparison = {}
        for metric_name, benchmark_val in tier_bench.items():
            actual = getattr(metrics, metric_name, 0)
            if actual >= benchmark_val * 1.2:
                comparison[metric_name] = "above_average"
            elif actual >= benchmark_val * 0.8:
                comparison[metric_name] = "average"
            else:
                comparison[metric_name] = "below_average"
        return comparison


# ─── Module Self-Test ─────────────────────────────────────────────────


class DataValidator:
    """Validates data integrity for module inputs and outputs."""

    @staticmethod
    def validate_match_data(data: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validate a match data dictionary."""
        errors = []
        required_fields = ["match_id", "game_duration_seconds"]
        for field in required_fields:
            if field not in data:
                errors.append(f"Missing required field: {field}")
        duration = data.get("game_duration_seconds", 0)
        if duration <= 0 or duration > 7200:
            errors.append(f"Invalid game duration: {duration}")
        kills = data.get("kills", 0)
        deaths = data.get("deaths", 0)
        assists = data.get("assists", 0)
        if kills < 0 or deaths < 0 or assists < 0:
            errors.append("Negative KDA values detected")
        return len(errors) == 0, errors

    @staticmethod
    def validate_player_id(player_id: str) -> bool:
        """Validate player identifier format."""
        if not player_id or not isinstance(player_id, str):
            return False
        if len(player_id) > 256:
            return False
        return True

    @staticmethod
    def validate_champion_id(champion_id: int) -> bool:
        """Validate champion identifier range."""
        return isinstance(champion_id, int) and 1 <= champion_id <= 999

    @staticmethod
    def sanitize_stats(stats: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize and normalize raw stats data."""
        sanitized = {}
        int_fields = ["kills", "deaths", "assists", "total_damage_to_champions",
                       "total_damage_taken", "total_minions_killed", "gold_earned",
                       "vision_score", "wards_placed", "wards_killed"]
        for field in int_fields:
            val = stats.get(field, 0)
            sanitized[field] = max(0, int(val)) if isinstance(val, (int, float)) else 0
        bool_fields = ["win", "first_blood_kill", "first_tower_kill"]
        for field in bool_fields:
            sanitized[field] = bool(stats.get(field, False))
        str_fields = ["match_id", "role", "summoner_name"]
        for field in str_fields:
            sanitized[field] = str(stats.get(field, ""))
        return sanitized


class ModuleMetrics:
    """Tracks internal module performance metrics."""

    def __init__(self, module_name: str):
        self._module_name = module_name
        self._call_counts: Dict[str, int] = defaultdict(int)
        self._call_durations: Dict[str, List[float]] = defaultdict(list)
        self._errors: List[Dict[str, Any]] = []
        self._start_time = time.time()

    def record_call(self, method_name: str, duration_ms: float) -> None:
        """Record a method call with its duration."""
        self._call_counts[method_name] += 1
        self._call_durations[method_name].append(duration_ms)

    def record_error(self, method_name: str, error: str) -> None:
        """Record an error occurrence."""
        self._errors.append({
            "method": method_name, "error": error,
            "timestamp": time.time(),
        })

    def get_stats(self) -> Dict[str, Any]:
        """Get module performance statistics."""
        total_calls = sum(self._call_counts.values())
        avg_durations = {}
        for method, durations in self._call_durations.items():
            if durations:
                avg_durations[method] = {
                    "avg_ms": round(statistics.mean(durations), 3),
                    "max_ms": round(max(durations), 3),
                    "min_ms": round(min(durations), 3),
                    "calls": len(durations),
                }
        return {
            "module": self._module_name,
            "total_calls": total_calls,
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "error_count": len(self._errors),
            "method_stats": avg_durations,
        }

    def reset(self) -> None:
        """Reset all tracked metrics."""
        self._call_counts.clear()
        self._call_durations.clear()
        self._errors.clear()
        self._start_time = time.time()
def _self_test() -> Dict[str, Any]:
    results = {"module": "M815_performance_metrics_calculator", "tests": []}
    try:
        stats = RawMatchStats(
            match_id="test_001", participant_id=1, champion_id=1, role="ADC",
            game_duration_seconds=1800, win=True, kills=8, deaths=3, assists=12,
            total_damage_to_champions=25000, total_damage_taken=15000,
            total_minions_killed=180, neutral_minions_killed=20,
            gold_earned=14000, gold_spent=13000, vision_score=25,
            wards_placed=15, wards_killed=5, control_wards_purchased=3,
            team_total_damage=80000, team_total_gold=60000,
            damage_self_mitigated=8000, total_time_cc_dealt=30, total_heal=2000,
        )
        calc = PerformanceMetricsCalculator()
        m = calc.calculate_single_game(stats)
        assert m.kda > 0
        assert m.cs_per_minute > 0
        assert m.overall_rating.value in ["S+", "S", "A", "B", "C", "D", "F"]
        results["tests"].append({"name": "single_game_calc", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "single_game_calc", "status": "fail", "error": str(e)})
    try:
        calc = PerformanceMetricsCalculator()
        trend = calc.calculate_trend("kda", [2.0, 2.5, 3.0, 3.5, 4.0])
        assert trend.direction == TrendDirection.IMPROVING
        results["tests"].append({"name": "trend_analysis", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "trend_analysis", "status": "fail", "error": str(e)})
    try:
        calc = PerformanceMetricsCalculator()
        batch = []
        for i in range(10):
            batch.append(RawMatchStats(
                match_id=f"m{i}", participant_id=1, champion_id=1, role="MID",
                game_duration_seconds=1500 + i * 60, win=(i % 3 != 0),
                kills=5 + i, deaths=3, assists=7,
                total_damage_to_champions=20000 + i * 1000,
                total_damage_taken=10000, total_minions_killed=150 + i * 5,
                gold_earned=12000 + i * 200, vision_score=20,
                wards_placed=10, team_total_damage=70000, team_total_gold=55000,
            ))
        report = calc.generate_report("TestPlayer", batch)
        assert report.total_games == 10
        assert "kda" in report.trends
        results["tests"].append({"name": "full_report", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "full_report", "status": "fail", "error": str(e)})
    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results

if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))
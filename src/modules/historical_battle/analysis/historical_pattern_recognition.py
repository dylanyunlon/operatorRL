#!/usr/bin/env python3
"""
M816 - Historical Pattern Recognition
====================================
OperatorRL Historical Battle System - Behavioral Pattern Detection

查看机器学习模式识别在游戏数据中的应用方式,理解其模式,
特别是时间序列特征和行为模式是如何提取的。从统计特征开始,
遵循该模式实现历史模式识别引擎,使系统可以发现玩家的行为
习惯和战术偏好,并能预测其在特定情境下的决策倾向。

Core responsibilities:
- Extract behavioral feature vectors from match history
- Detect playstyle archetypes via statistical clustering
- Identify tilt indicators and performance patterns
- Analyze champion pool depth and flexibility
- Predict decision tendencies in specific game states
"""

import os, sys, json, time, math, logging, hashlib, statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.analysis.pattern")
logger.setLevel(logging.DEBUG)

MIN_GAMES_FOR_PATTERN = 10
PATTERN_CONFIDENCE_THRESHOLD = 0.7
BEHAVIORAL_FEATURES = [
    "aggression_index", "farming_priority", "roaming_tendency",
    "objective_focus", "vision_investment", "risk_tolerance",
    "early_aggression", "teamfight_engagement", "splitpush_tendency",
    "ward_placement_pattern", "recall_timing", "itemization_pattern",
]
CLUSTER_COUNT_DEFAULT = 5
SEQUENCE_WINDOW_SIZE = 3

class PatternType(Enum):
    PLAYSTYLE = "playstyle"
    CHAMPION_POOL = "champion_pool"
    ROLE_PREFERENCE = "role_preference"
    TILT_INDICATOR = "tilt_indicator"
    POWER_SPIKE = "power_spike"
    DECISION_BIAS = "decision_bias"
    ADAPTATION = "adaptation"

class PlaystyleArchetype(Enum):
    AGGRESSIVE = "aggressive"
    PASSIVE = "passive"
    FARM_FOCUSED = "farm_focused"
    ROAM_HEAVY = "roam_heavy"
    OBJECTIVE_FOCUSED = "objective_focused"
    VISION_ORIENTED = "vision_oriented"
    BALANCED = "balanced"
    CHAOTIC = "chaotic"

class TiltState(Enum):
    FRESH = "fresh"
    WARMING_UP = "warming_up"
    PEAK = "peak"
    DECLINING = "declining"
    TILTED = "tilted"
    RECOVERY = "recovery"

@dataclass
class BehavioralFeatureVector:
    """Multi-dimensional behavioral profile for a player."""
    player_id: str
    sample_size: int = 0
    aggression_index: float = 0.5
    farming_priority: float = 0.5
    roaming_tendency: float = 0.5
    objective_focus: float = 0.5
    vision_investment: float = 0.5
    risk_tolerance: float = 0.5
    early_aggression: float = 0.5
    teamfight_engagement: float = 0.5
    splitpush_tendency: float = 0.5
    consistency: float = 0.5

    def to_vector(self) -> List[float]:
        return [
            self.aggression_index, self.farming_priority,
            self.roaming_tendency, self.objective_focus,
            self.vision_investment, self.risk_tolerance,
            self.early_aggression, self.teamfight_engagement,
            self.splitpush_tendency, self.consistency,
        ]

    def distance_to(self, other: "BehavioralFeatureVector") -> float:
        v1, v2 = self.to_vector(), other.to_vector()
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(v1, v2)))

    def cosine_similarity(self, other: "BehavioralFeatureVector") -> float:
        v1, v2 = self.to_vector(), other.to_vector()
        dot = sum(a * b for a, b in zip(v1, v2))
        mag1 = math.sqrt(sum(a**2 for a in v1))
        mag2 = math.sqrt(sum(b**2 for b in v2))
        if mag1 == 0 or mag2 == 0:
            return 0.0
        return dot / (mag1 * mag2)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class DetectedPattern:
    """A pattern detected in player behavior."""
    pattern_id: str
    pattern_type: PatternType
    description: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    affected_metrics: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    first_seen_game: int = 0
    last_seen_game: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["pattern_type"] = self.pattern_type.value
        return result

@dataclass
class TiltAnalysis:
    """Analysis of player tilt/mental state over time."""
    player_id: str
    current_state: TiltState = TiltState.FRESH
    tilt_probability: float = 0.0
    loss_streak: int = 0
    win_streak: int = 0
    recent_performance_delta: float = 0.0
    death_rate_trend: float = 0.0
    game_duration_trend: float = 0.0
    indicators: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["current_state"] = self.current_state.value
        return result

@dataclass
class ChampionPoolProfile:
    """Analysis of a player's champion pool."""
    player_id: str
    total_unique_champions: int = 0
    most_played: List[Tuple[int, int]] = field(default_factory=list)
    highest_winrate: List[Tuple[int, float]] = field(default_factory=list)
    comfort_picks: List[int] = field(default_factory=list)
    flex_picks: List[int] = field(default_factory=list)
    pocket_picks: List[int] = field(default_factory=list)
    pool_diversity_score: float = 0.0
    role_flexibility: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id,
            "unique_champions": self.total_unique_champions,
            "most_played": self.most_played[:10],
            "comfort_picks": self.comfort_picks,
            "pool_diversity": round(self.pool_diversity_score, 3),
            "role_flexibility": self.role_flexibility,
        }

@dataclass
class PatternRecognitionReport:
    """Complete pattern recognition report for a player."""
    player_id: str
    games_analyzed: int = 0
    behavioral_profile: Optional[BehavioralFeatureVector] = None
    playstyle: PlaystyleArchetype = PlaystyleArchetype.BALANCED
    detected_patterns: List[DetectedPattern] = field(default_factory=list)
    tilt_analysis: Optional[TiltAnalysis] = None
    champion_pool: Optional[ChampionPoolProfile] = None
    prediction_accuracy: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_id": self.player_id, "games_analyzed": self.games_analyzed,
            "playstyle": self.playstyle.value,
            "patterns_found": len(self.detected_patterns),
            "patterns": [p.to_dict() for p in self.detected_patterns],
            "tilt": self.tilt_analysis.to_dict() if self.tilt_analysis else None,
            "champion_pool": self.champion_pool.to_dict() if self.champion_pool else None,
            "behavioral_profile": self.behavioral_profile.to_dict() if self.behavioral_profile else None,
        }


class HistoricalPatternRecognition:
    """Detects behavioral patterns, playstyle archetypes, tilt indicators,
    and champion pool characteristics from historical match data."""

    def __init__(self):
        self._pattern_counter = 0

    def _gen_pattern_id(self) -> str:
        self._pattern_counter += 1
        return f"pat_{self._pattern_counter:04d}"

    def analyze_player(self, player_id: str, match_stats: List[Dict[str, Any]]) -> PatternRecognitionReport:
        """Run full pattern analysis on a player's match history."""
        report = PatternRecognitionReport(player_id=player_id, games_analyzed=len(match_stats))
        if len(match_stats) < MIN_GAMES_FOR_PATTERN:
            return report
        report.behavioral_profile = self._build_behavioral_profile(player_id, match_stats)
        report.playstyle = self._classify_playstyle(report.behavioral_profile)
        report.detected_patterns = self._detect_patterns(match_stats, report.behavioral_profile)
        report.tilt_analysis = self._analyze_tilt(player_id, match_stats)
        report.champion_pool = self._analyze_champion_pool(player_id, match_stats)
        return report

    def _build_behavioral_profile(self, player_id: str, matches: List[Dict[str, Any]]) -> BehavioralFeatureVector:
        """Build a behavioral feature vector from match data."""
        profile = BehavioralFeatureVector(player_id=player_id, sample_size=len(matches))
        kills_list, deaths_list, cs_list, vision_list, obj_list = [], [], [], [], []
        for m in matches:
            duration = max(m.get("game_duration_seconds", 1800) / 60, 1)
            kills_list.append(m.get("kills", 0) / duration)
            deaths_list.append(m.get("deaths", 0) / duration)
            cs_list.append(m.get("total_cs", 0) / duration)
            vision_list.append(m.get("vision_score", 0) / duration)
            obj_list.append((m.get("dragon_kills", 0) + m.get("baron_kills", 0) + m.get("turret_kills", 0)) / duration)
        avg_kpm = statistics.mean(kills_list) if kills_list else 0
        avg_dpm = statistics.mean(deaths_list) if deaths_list else 0
        avg_csm = statistics.mean(cs_list) if cs_list else 0
        avg_vsm = statistics.mean(vision_list) if vision_list else 0
        avg_obj = statistics.mean(obj_list) if obj_list else 0
        profile.aggression_index = min(1.0, avg_kpm / 0.5)
        profile.farming_priority = min(1.0, avg_csm / 8.0)
        profile.vision_investment = min(1.0, avg_vsm / 1.5)
        profile.objective_focus = min(1.0, avg_obj / 0.15)
        profile.risk_tolerance = min(1.0, max(0, 1.0 - (avg_dpm * 5)))
        if len(kills_list) > 1:
            cv_kills = statistics.stdev(kills_list) / max(avg_kpm, 0.01)
            profile.consistency = max(0, 1.0 - cv_kills)
        return profile

    def _classify_playstyle(self, profile: BehavioralFeatureVector) -> PlaystyleArchetype:
        """Classify player into a playstyle archetype."""
        scores = {
            PlaystyleArchetype.AGGRESSIVE: profile.aggression_index * 0.6 + profile.risk_tolerance * 0.4,
            PlaystyleArchetype.PASSIVE: (1 - profile.aggression_index) * 0.5 + profile.farming_priority * 0.5,
            PlaystyleArchetype.FARM_FOCUSED: profile.farming_priority * 0.7 + (1 - profile.aggression_index) * 0.3,
            PlaystyleArchetype.OBJECTIVE_FOCUSED: profile.objective_focus * 0.7 + profile.vision_investment * 0.3,
            PlaystyleArchetype.VISION_ORIENTED: profile.vision_investment * 0.7 + profile.objective_focus * 0.3,
        }
        best = max(scores, key=scores.get)
        if scores[best] < 0.55:
            return PlaystyleArchetype.BALANCED
        return best

    def _detect_patterns(self, matches: List[Dict[str, Any]], profile: BehavioralFeatureVector) -> List[DetectedPattern]:
        """Detect specific behavioral patterns."""
        patterns = []
        win_results = [m.get("win", False) for m in matches]
        consecutive_losses = 0
        tilt_sequences = 0
        for i in range(1, len(win_results)):
            if not win_results[i] and not win_results[i-1]:
                consecutive_losses += 1
                if consecutive_losses >= 3:
                    tilt_sequences += 1
            else:
                consecutive_losses = 0
        if tilt_sequences >= 2:
            patterns.append(DetectedPattern(
                pattern_id=self._gen_pattern_id(), pattern_type=PatternType.TILT_INDICATOR,
                description=f"Prone to loss streaks ({tilt_sequences} sequences of 3+ losses)",
                confidence=min(1.0, tilt_sequences / 5),
                recommendations=["Take breaks after 2 consecutive losses", "Review mental game"],
            ))
        champ_counts = defaultdict(int)
        for m in matches:
            champ_counts[m.get("champion_id", 0)] += 1
        unique_ratio = len(champ_counts) / max(len(matches), 1)
        if unique_ratio < 0.2:
            patterns.append(DetectedPattern(
                pattern_id=self._gen_pattern_id(), pattern_type=PatternType.CHAMPION_POOL,
                description="Very narrow champion pool - one-trick tendency",
                confidence=0.8, recommendations=["Expand champion pool for flexibility"],
            ))
        elif unique_ratio > 0.7:
            patterns.append(DetectedPattern(
                pattern_id=self._gen_pattern_id(), pattern_type=PatternType.CHAMPION_POOL,
                description="Very wide champion pool - jack of all trades",
                confidence=0.7, recommendations=["Consider specializing in fewer champions"],
            ))
        early_kills = [m.get("kills_pre_14", m.get("kills", 0) * 0.4) for m in matches]
        if statistics.mean(early_kills) > 3:
            patterns.append(DetectedPattern(
                pattern_id=self._gen_pattern_id(), pattern_type=PatternType.PLAYSTYLE,
                description="High early game aggression", confidence=0.75,
            ))
        return patterns

    def _analyze_tilt(self, player_id: str, matches: List[Dict[str, Any]]) -> TiltAnalysis:
        """Analyze current tilt state."""
        analysis = TiltAnalysis(player_id=player_id)
        if not matches:
            return analysis
        recent = matches[-5:] if len(matches) >= 5 else matches
        recent_wins = sum(1 for m in recent if m.get("win", False))
        recent_losses = len(recent) - recent_wins
        for m in reversed(matches):
            if m.get("win", False):
                if analysis.loss_streak == 0:
                    analysis.win_streak += 1
                else:
                    break
            else:
                if analysis.win_streak == 0:
                    analysis.loss_streak += 1
                else:
                    break
        if analysis.loss_streak >= 4:
            analysis.current_state = TiltState.TILTED
            analysis.tilt_probability = 0.85
        elif analysis.loss_streak >= 2:
            analysis.current_state = TiltState.DECLINING
            analysis.tilt_probability = 0.5
        elif analysis.win_streak >= 3:
            analysis.current_state = TiltState.PEAK
            analysis.tilt_probability = 0.1
        else:
            analysis.current_state = TiltState.FRESH
            analysis.tilt_probability = 0.2
        if recent_losses > recent_wins:
            analysis.indicators.append(f"Recent record: {recent_wins}W/{recent_losses}L")
        return analysis

    def _analyze_champion_pool(self, player_id: str, matches: List[Dict[str, Any]]) -> ChampionPoolProfile:
        """Analyze champion pool characteristics."""
        pool = ChampionPoolProfile(player_id=player_id)
        champ_stats: Dict[int, Dict] = defaultdict(lambda: {"games": 0, "wins": 0})
        role_counts: Dict[str, int] = defaultdict(int)
        for m in matches:
            cid = m.get("champion_id", 0)
            champ_stats[cid]["games"] += 1
            if m.get("win", False):
                champ_stats[cid]["wins"] += 1
            role_counts[m.get("role", "FILL")] += 1
        pool.total_unique_champions = len(champ_stats)
        pool.most_played = sorted([(cid, s["games"]) for cid, s in champ_stats.items()], key=lambda x: x[1], reverse=True)[:10]
        pool.highest_winrate = sorted(
            [(cid, s["wins"] / s["games"]) for cid, s in champ_stats.items() if s["games"] >= 5],
            key=lambda x: x[1], reverse=True
        )[:10]
        pool.comfort_picks = [cid for cid, s in champ_stats.items() if s["games"] >= 10]
        pool.pocket_picks = [cid for cid, s in champ_stats.items() if s["games"] >= 5 and s["wins"] / s["games"] >= 0.6]
        pool.role_flexibility = dict(role_counts)
        if pool.total_unique_champions > 0:
            total = sum(s["games"] for s in champ_stats.values())
            entropy = 0
            for s in champ_stats.values():
                p = s["games"] / total
                if p > 0:
                    entropy -= p * math.log2(p)
            max_entropy = math.log2(pool.total_unique_champions) if pool.total_unique_champions > 1 else 1
            pool.pool_diversity_score = entropy / max_entropy if max_entropy > 0 else 0
        return pool



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
    results = {"module": "M816_historical_pattern_recognition", "tests": []}
    try:
        engine = HistoricalPatternRecognition()
        matches = [{"champion_id": 1 + (i % 5), "role": "MID", "game_duration_seconds": 1800,
                     "win": (i % 3 != 0), "kills": 5 + i % 4, "deaths": 3, "assists": 7,
                     "total_cs": 180, "vision_score": 20, "dragon_kills": 1, "baron_kills": 0,
                     "turret_kills": 1} for i in range(20)]
        report = engine.analyze_player("test_player", matches)
        assert report.games_analyzed == 20
        assert report.behavioral_profile is not None
        assert report.playstyle is not None
        results["tests"].append({"name": "full_analysis", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "full_analysis", "status": "fail", "error": str(e)})
    try:
        v1 = BehavioralFeatureVector(player_id="a", aggression_index=0.8)
        v2 = BehavioralFeatureVector(player_id="b", aggression_index=0.2)
        dist = v1.distance_to(v2)
        assert dist > 0
        sim = v1.cosine_similarity(v2)
        assert 0 <= sim <= 1
        results["tests"].append({"name": "feature_distance", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "feature_distance", "status": "fail", "error": str(e)})
    try:
        engine = HistoricalPatternRecognition()
        matches = [{"win": False, "champion_id": 1, "role": "MID", "game_duration_seconds": 1800,
                     "kills": 2, "deaths": 5, "total_cs": 100, "vision_score": 10} for _ in range(15)]
        report = engine.analyze_player("tilted_player", matches)
        assert report.tilt_analysis.current_state == TiltState.TILTED
        results["tests"].append({"name": "tilt_detection", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "tilt_detection", "status": "fail", "error": str(e)})
    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results

if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))
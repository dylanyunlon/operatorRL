#!/usr/bin/env python3
"""
M789: Match Analyzer
======================
查看 Seraphine 上现有对局数据分析的实现方式,理解其模式,
特别是统计计算和数据展示是如何分离的。
从 match detail 视图这个好例子开始。
然后,遵循该模式实现新的 MatchAnalyzer,
让 OperatorRL 可以深度分析每场对局,并能识别关键转折点。
接着引入 PerformanceScorer,使系统能够量化玩家表现,
同时优化数据聚合算法减少计算延迟。
随后整合 TeamfightDetector,令系统支持团战识别,
进而增强 ObjectiveTracker 的节奏分析精度。
最终完善 MatchInsightGenerator,确保分析结果兼容所有游戏模式,
全面升级分析引擎以达成专业级别的数据洞察。
"""

import sys
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from logging_system.core_logger import get_logger, EventCategory
except ImportError:
    get_logger = lambda x: None
    EventCategory = type('E', (), {'ANALYSIS': 'analysis'})()


# ============================================================================
# Constants
# ============================================================================

PERFORMANCE_WEIGHTS = {
    "kills": 3.0,
    "deaths": -2.5,
    "assists": 1.5,
    "cs_per_min": 1.2,
    "gold_per_min": 0.8,
    "damage_share": 2.0,
    "vision_score": 1.0,
    "kill_participation": 1.5,
    "objective_damage": 0.5,
    "damage_efficiency": 1.8,
}

ROLE_BENCHMARKS = {
    "TOP": {"cs_per_min": 7.5, "kda": 2.5, "damage_share": 0.22, "vision": 20},
    "JUNGLE": {"cs_per_min": 5.5, "kda": 3.0, "damage_share": 0.18, "vision": 25},
    "MID": {"cs_per_min": 8.0, "kda": 3.0, "damage_share": 0.25, "vision": 18},
    "ADC": {"cs_per_min": 8.5, "kda": 3.5, "damage_share": 0.28, "vision": 15},
    "SUPPORT": {"cs_per_min": 1.5, "kda": 3.5, "damage_share": 0.10, "vision": 45},
    "FILL": {"cs_per_min": 6.0, "kda": 3.0, "damage_share": 0.20, "vision": 20},
}

GAME_PHASES = {
    "early": (0, 14 * 60),      # 0-14 min
    "mid": (14 * 60, 25 * 60),  # 14-25 min
    "late": (25 * 60, 60 * 60), # 25+ min
}

OBJECTIVE_TYPES = [
    "BARON_NASHOR", "DRAGON", "RIFT_HERALD",
    "TOWER", "INHIBITOR", "ELDER_DRAGON",
]


class AnalysisGrade(Enum):
    S_PLUS = "S+"
    S = "S"
    A_PLUS = "A+"
    A = "A"
    B_PLUS = "B+"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class PlayerPerformanceScore:
    """Quantified player performance in a single match."""
    puuid: str
    summoner_name: str
    champion: str
    role: str
    grade: str = "C"
    raw_score: float = 0.0
    normalized_score: float = 0.0
    component_scores: Dict[str, float] = field(default_factory=dict)
    comparisons: Dict[str, float] = field(default_factory=dict)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    improvement_tips: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TeamfightEvent:
    """Detected teamfight in a match timeline."""
    timestamp_seconds: int
    location: Dict[str, float]
    blue_participants: List[str]
    red_participants: List[str]
    blue_kills: int = 0
    red_kills: int = 0
    duration_seconds: int = 0
    winning_team: int = 0
    significance: float = 0.0  # 0-1 how game-changing

    @property
    def display_time(self) -> str:
        m, s = divmod(self.timestamp_seconds, 60)
        return f"{m}:{s:02d}"

    @property
    def total_participants(self) -> int:
        return len(self.blue_participants) + len(self.red_participants)


@dataclass
class ObjectiveEvent:
    """Objective take event in a match."""
    timestamp_seconds: int
    objective_type: str
    team_id: int
    killer: str = ""
    stolen: bool = False
    sequence_number: int = 0

    @property
    def display_time(self) -> str:
        m, s = divmod(self.timestamp_seconds, 60)
        return f"{m}:{s:02d}"


@dataclass
class GoldDifferential:
    """Gold difference tracking at intervals."""
    timestamps: List[int] = field(default_factory=list)
    blue_gold: List[int] = field(default_factory=list)
    red_gold: List[int] = field(default_factory=list)
    differentials: List[int] = field(default_factory=list)
    max_lead_blue: int = 0
    max_lead_red: int = 0
    lead_changes: int = 0


@dataclass
class MatchInsight:
    """Complete analysis insight for a match."""
    game_id: int
    game_duration: int
    game_phase_at_end: str
    winning_team: int
    player_scores: List[PlayerPerformanceScore] = field(default_factory=list)
    teamfights: List[TeamfightEvent] = field(default_factory=list)
    objectives: List[ObjectiveEvent] = field(default_factory=list)
    gold_differential: Optional[GoldDifferential] = None
    key_moments: List[Dict[str, Any]] = field(default_factory=list)
    mvp: Optional[PlayerPerformanceScore] = None
    summary: str = ""
    analysis_timestamp: str = ""

    def __post_init__(self):
        if not self.analysis_timestamp:
            self.analysis_timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_id": self.game_id,
            "game_duration": self.game_duration,
            "winning_team": self.winning_team,
            "player_scores": [ps.to_dict() for ps in self.player_scores],
            "teamfight_count": len(self.teamfights),
            "objective_count": len(self.objectives),
            "key_moments": self.key_moments,
            "mvp": self.mvp.to_dict() if self.mvp else None,
            "summary": self.summary,
        }


# ============================================================================
# Performance Scorer
# ============================================================================

class PerformanceScorer:
    """
    Score player performance relative to role benchmarks.
    Uses weighted multi-factor scoring with role-adjusted normalization.
    """

    def __init__(self):
        self.weights = PERFORMANCE_WEIGHTS.copy()
        self.benchmarks = ROLE_BENCHMARKS.copy()

    def score_player(self, stats: Dict[str, Any], role: str,
                     game_duration_seconds: int,
                     team_stats: Dict[str, Any]) -> PlayerPerformanceScore:
        """Score a player's performance in a match."""
        duration_min = max(1, game_duration_seconds / 60)
        benchmark = self.benchmarks.get(role, self.benchmarks["FILL"])

        # Calculate component scores (0-10 scale)
        components = {}

        # KDA scoring
        kills = stats.get("kills", 0)
        deaths = stats.get("deaths", 0)
        assists = stats.get("assists", 0)
        kda = (kills + assists) / max(1, deaths)
        kda_benchmark = benchmark["kda"]
        components["kda"] = min(10, (kda / kda_benchmark) * 5)

        # CS scoring
        cs = stats.get("cs", 0)
        cs_per_min = cs / duration_min
        cs_benchmark = benchmark["cs_per_min"]
        components["cs_efficiency"] = min(10, (cs_per_min / cs_benchmark) * 5)

        # Damage share scoring
        damage = stats.get("damage_dealt", 0)
        team_damage = team_stats.get("total_damage", 1)
        damage_share = damage / max(1, team_damage)
        ds_benchmark = benchmark["damage_share"]
        components["damage_contribution"] = min(10, (damage_share / ds_benchmark) * 5)

        # Vision scoring
        vision = stats.get("vision_score", 0)
        vision_benchmark = benchmark["vision"]
        vision_per_min = vision / duration_min
        vision_benchmark_per_min = vision_benchmark / 30
        components["vision_control"] = min(10, (vision_per_min / max(0.1, vision_benchmark_per_min)) * 5)

        # Gold efficiency
        gold = stats.get("gold_earned", 0)
        gold_per_min = gold / duration_min
        components["gold_efficiency"] = min(10, gold_per_min / 400 * 5)

        # Kill participation
        team_kills = team_stats.get("total_kills", 1)
        kp = (kills + assists) / max(1, team_kills)
        components["kill_participation"] = min(10, kp / 0.6 * 5)

        # Damage efficiency (damage dealt vs taken ratio)
        damage_taken = stats.get("damage_taken", 1)
        damage_eff = damage / max(1, damage_taken)
        components["damage_efficiency"] = min(10, damage_eff * 3)

        # Objective damage
        obj_damage = stats.get("objective_damage", 0)
        components["objective_contribution"] = min(10, obj_damage / max(1, damage) * 20)

        # Calculate weighted score
        raw_score = sum(
            components.get(k, 0) * self.weights.get(k, 1.0)
            for k in components
        )
        max_possible = sum(10 * abs(w) for w in self.weights.values())
        normalized = (raw_score / max(1, max_possible)) * 100

        # Determine grade
        grade = self._compute_grade(normalized)

        # Identify strengths and weaknesses
        sorted_components = sorted(components.items(), key=lambda x: x[1], reverse=True)
        strengths = [name for name, score in sorted_components[:3] if score >= 6]
        weaknesses = [name for name, score in sorted_components[-3:] if score < 4]

        # Generate tips
        tips = self._generate_tips(components, role, stats)

        return PlayerPerformanceScore(
            puuid=stats.get("puuid", ""),
            summoner_name=stats.get("summoner_name", ""),
            champion=stats.get("champion_name", ""),
            role=role,
            grade=grade,
            raw_score=round(raw_score, 2),
            normalized_score=round(normalized, 2),
            component_scores={k: round(v, 2) for k, v in components.items()},
            comparisons={
                "kda_vs_benchmark": round(kda / kda_benchmark, 2),
                "cs_vs_benchmark": round(cs_per_min / cs_benchmark, 2),
                "vision_vs_benchmark": round(vision / max(1, vision_benchmark), 2),
            },
            strengths=strengths,
            weaknesses=weaknesses,
            improvement_tips=tips,
        )

    @staticmethod
    def _compute_grade(normalized_score: float) -> str:
        if normalized_score >= 90:
            return AnalysisGrade.S_PLUS.value
        elif normalized_score >= 80:
            return AnalysisGrade.S.value
        elif normalized_score >= 70:
            return AnalysisGrade.A_PLUS.value
        elif normalized_score >= 60:
            return AnalysisGrade.A.value
        elif normalized_score >= 50:
            return AnalysisGrade.B_PLUS.value
        elif normalized_score >= 40:
            return AnalysisGrade.B.value
        elif normalized_score >= 30:
            return AnalysisGrade.C.value
        elif normalized_score >= 20:
            return AnalysisGrade.D.value
        else:
            return AnalysisGrade.F.value

    @staticmethod
    def _generate_tips(components: Dict[str, float], role: str,
                       stats: Dict) -> List[str]:
        tips = []
        if components.get("vision_control", 0) < 3:
            tips.append(f"视野分数偏低, {role}位建议每分钟至少放置1个眼位")
        if components.get("cs_efficiency", 0) < 3 and role not in ("SUPPORT", "JUNGLE"):
            tips.append("补刀效率需要提升, 建议练习对线期补刀节奏")
        if components.get("kill_participation", 0) < 3:
            tips.append("参团率偏低, 注意地图意识和团战时机")
        if components.get("damage_efficiency", 0) < 3:
            tips.append("伤害效率偏低, 注意出装和技能释放时机")
        if stats.get("deaths", 0) > 8:
            tips.append("死亡次数过多, 注意安全意识和走位")
        return tips[:3]


# ============================================================================
# Teamfight Detector
# ============================================================================

class TeamfightDetector:
    """
    Detect teamfights from match timeline events.
    Uses proximity-based kill clustering to identify teamfight windows.
    """

    PROXIMITY_THRESHOLD = 2000  # units
    TIME_WINDOW = 15  # seconds
    MIN_PARTICIPANTS = 4

    def detect_teamfights(self, timeline_events: List[Dict]) -> List[TeamfightEvent]:
        """Analyze timeline events to detect teamfight clusters."""
        kill_events = [
            e for e in timeline_events
            if e.get("type") in ("CHAMPION_KILL", "champion_kill")
        ]

        if not kill_events:
            return []

        clusters = self._cluster_kills(kill_events)
        teamfights = []

        for cluster in clusters:
            if len(cluster) < 2:
                continue

            blue_parts = set()
            red_parts = set()
            blue_kills = 0
            red_kills = 0

            for kill in cluster:
                killer_team = kill.get("killerTeamId", kill.get("killer_team", 0))
                victim_team = kill.get("victimTeamId", kill.get("victim_team", 0))

                if killer_team == 100:
                    blue_kills += 1
                    blue_parts.add(kill.get("killerId", ""))
                    red_parts.add(kill.get("victimId", ""))
                else:
                    red_kills += 1
                    red_parts.add(kill.get("killerId", ""))
                    blue_parts.add(kill.get("victimId", ""))

                for assist_id in kill.get("assistingParticipantIds", []):
                    if killer_team == 100:
                        blue_parts.add(assist_id)
                    else:
                        red_parts.add(assist_id)

            total_participants = len(blue_parts) + len(red_parts)
            if total_participants < self.MIN_PARTICIPANTS:
                continue

            timestamps = [k.get("timestamp", 0) for k in cluster]
            min_ts = min(timestamps)
            max_ts = max(timestamps)
            duration = (max_ts - min_ts) // 1000

            positions = [k.get("position", {}) for k in cluster if k.get("position")]
            avg_x = sum(p.get("x", 0) for p in positions) / max(1, len(positions))
            avg_y = sum(p.get("y", 0) for p in positions) / max(1, len(positions))

            winning_team = 100 if blue_kills > red_kills else (
                200 if red_kills > blue_kills else 0
            )

            significance = min(1.0, (total_participants / 10) *
                              (blue_kills + red_kills) / 5)

            teamfights.append(TeamfightEvent(
                timestamp_seconds=min_ts // 1000,
                location={"x": avg_x, "y": avg_y},
                blue_participants=list(blue_parts),
                red_participants=list(red_parts),
                blue_kills=blue_kills,
                red_kills=red_kills,
                duration_seconds=duration,
                winning_team=winning_team,
                significance=round(significance, 2),
            ))

        return sorted(teamfights, key=lambda t: t.timestamp_seconds)

    def _cluster_kills(self, kills: List[Dict]) -> List[List[Dict]]:
        """Cluster kill events by time proximity."""
        if not kills:
            return []

        sorted_kills = sorted(kills, key=lambda k: k.get("timestamp", 0))
        clusters = [[sorted_kills[0]]]

        for kill in sorted_kills[1:]:
            prev_ts = clusters[-1][-1].get("timestamp", 0)
            curr_ts = kill.get("timestamp", 0)
            if (curr_ts - prev_ts) <= self.TIME_WINDOW * 1000:
                clusters[-1].append(kill)
            else:
                clusters.append([kill])

        return [c for c in clusters if len(c) >= 2]


# ============================================================================
# Objective Tracker
# ============================================================================

class ObjectiveTracker:
    """Track objective events and analyze objective control patterns."""

    DRAGON_TYPES = ["FIRE_DRAGON", "WATER_DRAGON", "EARTH_DRAGON",
                    "AIR_DRAGON", "ELDER_DRAGON", "HEXTECH_DRAGON",
                    "CHEMTECH_DRAGON"]

    def extract_objectives(self, timeline_events: List[Dict]) -> List[ObjectiveEvent]:
        objectives = []
        seq = 0

        for event in timeline_events:
            event_type = event.get("type", "")
            if event_type in ("ELITE_MONSTER_KILL", "BUILDING_KILL"):
                monster = event.get("monsterType", event.get("buildingType", ""))
                team_id = event.get("killerTeamId", event.get("teamId", 0))
                killer = event.get("killerId", "")
                stolen = event.get("stolen", False)

                obj_type = self._classify_objective(monster, event)
                if obj_type:
                    seq += 1
                    objectives.append(ObjectiveEvent(
                        timestamp_seconds=event.get("timestamp", 0) // 1000,
                        objective_type=obj_type,
                        team_id=team_id,
                        killer=str(killer),
                        stolen=stolen,
                        sequence_number=seq,
                    ))

        return objectives

    @staticmethod
    def _classify_objective(monster: str, event: Dict) -> Optional[str]:
        if "BARON" in monster.upper():
            return "BARON_NASHOR"
        if "DRAGON" in monster.upper():
            sub = event.get("monsterSubType", "")
            if "ELDER" in sub.upper():
                return "ELDER_DRAGON"
            return "DRAGON"
        if "HERALD" in monster.upper() or "RIFT_HERALD" in monster.upper():
            return "RIFT_HERALD"
        if "TOWER" in monster.upper() or "TURRET" in monster.upper():
            return "TOWER"
        if "INHIBITOR" in monster.upper():
            return "INHIBITOR"
        return None

    def compute_objective_control(self, objectives: List[ObjectiveEvent]) -> Dict[str, Any]:
        blue_count = sum(1 for o in objectives if o.team_id == 100)
        red_count = sum(1 for o in objectives if o.team_id == 200)
        by_type: Dict[str, Dict[str, int]] = defaultdict(lambda: {"blue": 0, "red": 0})

        for obj in objectives:
            key = "blue" if obj.team_id == 100 else "red"
            by_type[obj.objective_type][key] += 1

        stolen_count = sum(1 for o in objectives if o.stolen)
        first_dragon_team = None
        first_baron_team = None
        for obj in sorted(objectives, key=lambda o: o.timestamp_seconds):
            if obj.objective_type == "DRAGON" and first_dragon_team is None:
                first_dragon_team = obj.team_id
            if obj.objective_type == "BARON_NASHOR" and first_baron_team is None:
                first_baron_team = obj.team_id

        return {
            "total_objectives": len(objectives),
            "blue_objectives": blue_count,
            "red_objectives": red_count,
            "control_ratio": round(blue_count / max(1, blue_count + red_count), 2),
            "by_type": {k: dict(v) for k, v in by_type.items()},
            "stolen_objectives": stolen_count,
            "first_dragon": first_dragon_team,
            "first_baron": first_baron_team,
        }


# ============================================================================
# Match Insight Generator
# ============================================================================

class MatchInsightGenerator:
    """Generate comprehensive match insights by composing all analysis components."""

    def __init__(self):
        self.scorer = PerformanceScorer()
        self.teamfight_detector = TeamfightDetector()
        self.objective_tracker = ObjectiveTracker()
        self._logger = get_logger("M789") if get_logger else None

    def analyze_match(self, match_data: Dict,
                      timeline_data: Optional[Dict] = None) -> MatchInsight:
        """Full match analysis."""
        game_id = match_data.get("game_id", 0)
        duration = match_data.get("game_duration", 0)
        winning_team = match_data.get("winning_team", 0)

        # Determine game phase at end
        if duration < GAME_PHASES["early"][1]:
            phase = "early_surrender"
        elif duration < GAME_PHASES["mid"][1]:
            phase = "mid_game"
        else:
            phase = "late_game"

        # Score all participants
        participants = match_data.get("participants", [])
        team_stats_blue = self._compute_team_stats(participants, 100)
        team_stats_red = self._compute_team_stats(participants, 200)

        player_scores = []
        for p in participants:
            team_stats = team_stats_blue if p.get("team_id", 0) == 100 else team_stats_red
            role = p.get("role", "FILL")
            score = self.scorer.score_player(p, role, duration, team_stats)
            player_scores.append(score)

        # Determine MVP
        mvp = max(player_scores, key=lambda s: s.normalized_score) if player_scores else None

        # Analyze timeline if available
        teamfights = []
        objectives = []
        key_moments = []

        if timeline_data:
            events = timeline_data.get("frames", [])
            flat_events = []
            for frame in events:
                flat_events.extend(frame.get("events", []))

            teamfights = self.teamfight_detector.detect_teamfights(flat_events)
            objectives = self.objective_tracker.extract_objectives(flat_events)

            # Identify key moments
            key_moments = self._identify_key_moments(
                teamfights, objectives, player_scores
            )

        # Generate summary
        summary = self._generate_summary(
            duration, winning_team, player_scores, teamfights, objectives, phase
        )

        return MatchInsight(
            game_id=game_id,
            game_duration=duration,
            game_phase_at_end=phase,
            winning_team=winning_team,
            player_scores=player_scores,
            teamfights=teamfights,
            objectives=objectives,
            key_moments=key_moments,
            mvp=mvp,
            summary=summary,
        )

    @staticmethod
    def _compute_team_stats(participants: List[Dict], team_id: int) -> Dict[str, Any]:
        team = [p for p in participants if p.get("team_id") == team_id]
        return {
            "total_kills": sum(p.get("kills", 0) for p in team),
            "total_deaths": sum(p.get("deaths", 0) for p in team),
            "total_damage": sum(p.get("damage_dealt", 0) for p in team),
            "total_gold": sum(p.get("gold_earned", 0) for p in team),
        }

    @staticmethod
    def _identify_key_moments(teamfights: List[TeamfightEvent],
                               objectives: List[ObjectiveEvent],
                               scores: List[PlayerPerformanceScore]) -> List[Dict]:
        moments = []
        for tf in teamfights:
            if tf.significance >= 0.7:
                moments.append({
                    "type": "decisive_teamfight",
                    "time": tf.display_time,
                    "description": f"关键团战 ({tf.blue_kills}:{tf.red_kills})",
                    "significance": tf.significance,
                })
        for obj in objectives:
            if obj.objective_type in ("BARON_NASHOR", "ELDER_DRAGON"):
                prefix = "偷取" if obj.stolen else "击杀"
                moments.append({
                    "type": "major_objective",
                    "time": obj.display_time,
                    "description": f"{prefix}{obj.objective_type}",
                    "significance": 0.9 if obj.stolen else 0.7,
                })
        return sorted(moments, key=lambda m: m.get("significance", 0), reverse=True)[:10]

    @staticmethod
    def _generate_summary(duration: int, winning_team: int,
                           scores: List[PlayerPerformanceScore],
                           teamfights: List[TeamfightEvent],
                           objectives: List[ObjectiveEvent],
                           phase: str) -> str:
        m, s = divmod(duration, 60)
        team_str = "蓝方" if winning_team == 100 else "红方"
        mvp_str = ""
        if scores:
            best = max(scores, key=lambda s: s.normalized_score)
            mvp_str = f" MVP: {best.summoner_name} ({best.champion}, {best.grade})"

        tf_count = len(teamfights)
        obj_count = len(objectives)

        return (
            f"对局时长{m}分{s}秒, {team_str}胜利. "
            f"共发生{tf_count}次团战, {obj_count}次目标争夺. "
            f"游戏在{phase}阶段结束.{mvp_str}"
        )


# ============================================================================
# Module Self-Test
# ============================================================================

def self_test() -> Dict[str, Any]:
    results = {"module": "M789", "name": "match_analyzer", "tests": []}

    # Test 1: PerformanceScorer
    try:
        scorer = PerformanceScorer()
        stats = {
            "puuid": "test", "summoner_name": "TestPlayer",
            "champion_name": "Ahri", "kills": 8, "deaths": 3,
            "assists": 12, "cs": 220, "gold_earned": 13500,
            "damage_dealt": 28000, "damage_taken": 15000,
            "vision_score": 25, "objective_damage": 5000,
        }
        team_stats = {"total_kills": 30, "total_damage": 100000}
        score = scorer.score_player(stats, "MID", 1800, team_stats)
        assert score.grade in [g.value for g in AnalysisGrade]
        assert 0 <= score.normalized_score <= 100
        results["tests"].append({"name": "scorer", "status": "pass",
                                  "detail": f"Grade={score.grade}, Score={score.normalized_score}"})
    except Exception as e:
        results["tests"].append({"name": "scorer", "status": "fail", "error": str(e)})

    # Test 2: TeamfightDetector
    try:
        detector = TeamfightDetector()
        events = [
            {"type": "CHAMPION_KILL", "timestamp": 600000, "killerId": 1,
             "victimId": 6, "killerTeamId": 100, "position": {"x": 1000, "y": 1000},
             "assistingParticipantIds": [2, 3]},
            {"type": "CHAMPION_KILL", "timestamp": 605000, "killerId": 7,
             "victimId": 2, "killerTeamId": 200, "position": {"x": 1100, "y": 1000},
             "assistingParticipantIds": [8]},
            {"type": "CHAMPION_KILL", "timestamp": 608000, "killerId": 3,
             "victimId": 8, "killerTeamId": 100, "position": {"x": 1050, "y": 1050},
             "assistingParticipantIds": [1, 4, 5]},
        ]
        teamfights = detector.detect_teamfights(events)
        assert len(teamfights) >= 1
        results["tests"].append({"name": "teamfight_detector", "status": "pass",
                                  "detail": f"Detected {len(teamfights)} teamfights"})
    except Exception as e:
        results["tests"].append({"name": "teamfight_detector", "status": "fail", "error": str(e)})

    # Test 3: ObjectiveTracker
    try:
        tracker = ObjectiveTracker()
        events = [
            {"type": "ELITE_MONSTER_KILL", "timestamp": 900000,
             "monsterType": "DRAGON", "monsterSubType": "FIRE_DRAGON",
             "killerTeamId": 100, "killerId": 2},
            {"type": "ELITE_MONSTER_KILL", "timestamp": 1200000,
             "monsterType": "RIFT_HERALD", "killerTeamId": 100, "killerId": 2},
            {"type": "ELITE_MONSTER_KILL", "timestamp": 1800000,
             "monsterType": "BARON_NASHOR", "killerTeamId": 200,
             "killerId": 7, "stolen": True},
        ]
        objectives = tracker.extract_objectives(events)
        assert len(objectives) == 3
        control = tracker.compute_objective_control(objectives)
        assert control["total_objectives"] == 3
        assert control["stolen_objectives"] == 1
        results["tests"].append({"name": "objective_tracker", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "objective_tracker", "status": "fail", "error": str(e)})

    # Test 4: MatchInsightGenerator
    try:
        generator = MatchInsightGenerator()
        match_data = {
            "game_id": 123456, "game_duration": 1800, "winning_team": 100,
            "participants": [
                {"puuid": "p1", "summoner_name": "Player1", "champion_name": "Ahri",
                 "team_id": 100, "role": "MID", "kills": 10, "deaths": 2,
                 "assists": 8, "cs": 200, "gold_earned": 14000,
                 "damage_dealt": 30000, "damage_taken": 12000, "vision_score": 20},
                {"puuid": "p2", "summoner_name": "Player2", "champion_name": "Jinx",
                 "team_id": 200, "role": "ADC", "kills": 5, "deaths": 6,
                 "assists": 3, "cs": 180, "gold_earned": 11000,
                 "damage_dealt": 22000, "damage_taken": 18000, "vision_score": 10},
            ],
        }
        insight = generator.analyze_match(match_data)
        assert insight.game_id == 123456
        assert insight.mvp is not None
        assert len(insight.summary) > 0
        results["tests"].append({"name": "insight_generator", "status": "pass",
                                  "detail": f"MVP={insight.mvp.summoner_name}"})
    except Exception as e:
        results["tests"].append({"name": "insight_generator", "status": "fail", "error": str(e)})

    results["overall"] = "pass" if all(t["status"] == "pass" for t in results["tests"]) else "fail"
    return results


if __name__ == "__main__":
    print(json.dumps(self_test(), indent=2))

#!/usr/bin/env python3
"""
M817 - Opponent Scouting System
====================================
OperatorRL Historical Battle System - Pre-game Enemy Analysis

查看 Seraphine 等项目上现有的对手侦查功能实现方式,理解其模式,
特别是如何在选英阶段快速获取对手历史数据。从对手ID查询开始,
遵循该模式实现侦查系统,使玩家可以在游戏开始前了解每位对手
的强弱项,并能获得针对性策略建议。

Core: Pre-game enemy analysis, weakness detection, strategy suggestion
"""
import os, sys, json, time, math, logging, hashlib, statistics, struct
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.integration.scouting")
logger.setLevel(logging.DEBUG)

SCOUT_CACHE_TTL_SECONDS = 300
MAX_RECENT_MATCHES = 20
WEAKNESS_THRESHOLD = 0.4
STRENGTH_THRESHOLD = 0.65
CHAMPION_SELECT_TIMEOUT_SECONDS = 90

class ScoutingPriority(Enum):
    LANE_OPPONENT = auto()
    JUNGLE_THREAT = auto()
    CARRY_THREAT = auto()
    SUPPORT_THREAT = auto()

class WeaknessCategory(Enum):
    VISION = "vision"
    FARMING = "farming"
    DYING_EARLY = "dying_early"
    OBJECTIVE_CONTROL = "objective_control"
    CHAMPION_POOL = "champion_pool"
    TILT_PRONE = "tilt_prone"
    LATE_GAME = "late_game"

@dataclass
class PlayerWeakness:
    category: WeaknessCategory
    severity: float
    evidence: str
    exploitable: bool = True
    recommended_action: str = ""
    def to_dict(self) -> Dict[str, Any]:
        return {"category": self.category.value, "severity": round(self.severity, 3),
                "evidence": self.evidence, "exploitable": self.exploitable, "action": self.recommended_action}

@dataclass
class PlayerStrength:
    category: str
    score: float
    evidence: str
    def to_dict(self) -> Dict[str, Any]:
        return {"category": self.category, "score": round(self.score, 3), "evidence": self.evidence}

@dataclass
class OpponentProfile:
    summoner_name: str
    summoner_id: str
    rank_tier: str = "UNKNOWN"
    rank_division: str = ""
    lp: int = 0
    most_played_champions: List[Tuple[int, int, float]] = field(default_factory=list)
    recent_winrate: float = 0.5
    recent_games: int = 0
    strengths: List[PlayerStrength] = field(default_factory=list)
    weaknesses: List[PlayerWeakness] = field(default_factory=list)
    predicted_champion: Optional[int] = None
    predicted_champion_confidence: float = 0.0
    tilt_probability: float = 0.0
    overall_threat_level: float = 0.5
    def to_dict(self) -> Dict[str, Any]:
        return {
            "summoner": self.summoner_name, "rank": f"{self.rank_tier} {self.rank_division} {self.lp}LP",
            "recent_wr": round(self.recent_winrate, 3), "recent_games": self.recent_games,
            "threat_level": round(self.overall_threat_level, 3),
            "tilt_probability": round(self.tilt_probability, 3),
            "strengths": [s.to_dict() for s in self.strengths],
            "weaknesses": [w.to_dict() for w in self.weaknesses],
            "predicted_champion": self.predicted_champion,
        }

@dataclass
class ScoutingReport:
    match_lobby_id: Optional[str] = None
    my_team: List[str] = field(default_factory=list)
    enemy_profiles: List[OpponentProfile] = field(default_factory=list)
    team_analysis: Dict[str, Any] = field(default_factory=dict)
    strategy_recommendations: List[str] = field(default_factory=list)
    ban_suggestions: List[int] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)
    generation_time_ms: float = 0.0
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enemy_count": len(self.enemy_profiles),
            "enemies": [p.to_dict() for p in self.enemy_profiles],
            "strategy": self.strategy_recommendations,
            "ban_suggestions": self.ban_suggestions,
            "gen_time_ms": round(self.generation_time_ms, 1),
        }

class OpponentScoutingSystem:
    """Pre-game scouting system analyzing enemy players during champion select."""
    def __init__(self):
        self._cache: Dict[str, Tuple[float, OpponentProfile]] = {}
        self._scout_history: List[ScoutingReport] = []

    def scout_opponent(self, summoner_name: str, summoner_id: str,
                       match_history: List[Dict[str, Any]],
                       rank_info: Optional[Dict[str, Any]] = None) -> OpponentProfile:
        """Generate a scouting profile for a single opponent."""
        cache_key = summoner_id
        if cache_key in self._cache:
            ts, cached = self._cache[cache_key]
            if time.time() - ts < SCOUT_CACHE_TTL_SECONDS:
                return cached
        profile = OpponentProfile(summoner_name=summoner_name, summoner_id=summoner_id)
        if rank_info:
            profile.rank_tier = rank_info.get("tier", "UNKNOWN")
            profile.rank_division = rank_info.get("division", "")
            profile.lp = rank_info.get("lp", 0)
        recent = match_history[:MAX_RECENT_MATCHES]
        if recent:
            wins = sum(1 for m in recent if m.get("win", False))
            profile.recent_winrate = wins / len(recent)
            profile.recent_games = len(recent)
            champ_stats: Dict[int, Dict] = defaultdict(lambda: {"games": 0, "wins": 0})
            for m in recent:
                cid = m.get("champion_id", 0)
                champ_stats[cid]["games"] += 1
                if m.get("win"): champ_stats[cid]["wins"] += 1
            profile.most_played_champions = sorted(
                [(cid, s["games"], s["wins"]/s["games"]) for cid, s in champ_stats.items()],
                key=lambda x: x[1], reverse=True)[:5]
            if profile.most_played_champions:
                profile.predicted_champion = profile.most_played_champions[0][0]
                profile.predicted_champion_confidence = profile.most_played_champions[0][1] / len(recent)
            profile.weaknesses = self._detect_weaknesses(recent)
            profile.strengths = self._detect_strengths(recent)
            last_3 = recent[:3]
            losses = sum(1 for m in last_3 if not m.get("win", False))
            profile.tilt_probability = losses / 3
            tier_scores = {"IRON": 0.1, "BRONZE": 0.2, "SILVER": 0.3, "GOLD": 0.4,
                           "PLATINUM": 0.55, "EMERALD": 0.65, "DIAMOND": 0.75,
                           "MASTER": 0.85, "GRANDMASTER": 0.92, "CHALLENGER": 0.98}
            rank_score = tier_scores.get(profile.rank_tier, 0.5)
            profile.overall_threat_level = rank_score * 0.5 + profile.recent_winrate * 0.3 + (1 - profile.tilt_probability) * 0.2
        self._cache[cache_key] = (time.time(), profile)
        return profile

    def _detect_weaknesses(self, matches: List[Dict[str, Any]]) -> List[PlayerWeakness]:
        weaknesses = []
        avg_deaths = statistics.mean([m.get("deaths", 0) for m in matches]) if matches else 0
        avg_cs = statistics.mean([m.get("total_cs", 0) / max(m.get("game_duration_seconds", 1800)/60, 1) for m in matches]) if matches else 0
        avg_vision = statistics.mean([m.get("vision_score", 0) / max(m.get("game_duration_seconds", 1800)/60, 1) for m in matches]) if matches else 0
        if avg_deaths > 6:
            weaknesses.append(PlayerWeakness(category=WeaknessCategory.DYING_EARLY,
                severity=min(1.0, avg_deaths/10), evidence=f"Avg {avg_deaths:.1f} deaths/game",
                recommended_action="Play aggressive early, punish positioning"))
        if avg_cs < 5.0:
            weaknesses.append(PlayerWeakness(category=WeaknessCategory.FARMING,
                severity=min(1.0, (7.0-avg_cs)/5.0), evidence=f"Avg {avg_cs:.1f} CS/min",
                recommended_action="Deny CS in lane, freeze wave"))
        if avg_vision < 0.5:
            weaknesses.append(PlayerWeakness(category=WeaknessCategory.VISION,
                severity=min(1.0, (1.0-avg_vision)/1.0), evidence=f"Avg {avg_vision:.2f} vision/min",
                recommended_action="Exploit lack of vision with ganks"))
        recent_3 = matches[:3]
        if all(not m.get("win", False) for m in recent_3) and len(recent_3) == 3:
            weaknesses.append(PlayerWeakness(category=WeaknessCategory.TILT_PRONE,
                severity=0.7, evidence="3-game loss streak (possibly tilted)",
                recommended_action="Apply early pressure to tilt further"))
        return weaknesses

    def _detect_strengths(self, matches: List[Dict[str, Any]]) -> List[PlayerStrength]:
        strengths = []
        avg_kills = statistics.mean([m.get("kills", 0) for m in matches]) if matches else 0
        avg_kda = statistics.mean([(m.get("kills",0)+m.get("assists",0))/max(m.get("deaths",1),1) for m in matches]) if matches else 0
        if avg_kda > 4.0:
            strengths.append(PlayerStrength(category="combat", score=min(1.0, avg_kda/8), evidence=f"High KDA ({avg_kda:.1f})"))
        if avg_kills > 7:
            strengths.append(PlayerStrength(category="aggression", score=min(1.0, avg_kills/12), evidence=f"High kill average ({avg_kills:.1f})"))
        return strengths

    def generate_scouting_report(self, my_team: List[str], enemy_data: List[Dict[str, Any]]) -> ScoutingReport:
        """Generate a full pre-game scouting report for the enemy team."""
        start = time.time()
        report = ScoutingReport(my_team=my_team)
        for enemy in enemy_data:
            profile = self.scout_opponent(
                summoner_name=enemy.get("name", "Unknown"), summoner_id=enemy.get("id", ""),
                match_history=enemy.get("matches", []), rank_info=enemy.get("rank"))
            report.enemy_profiles.append(profile)
        high_threats = [p for p in report.enemy_profiles if p.overall_threat_level > 0.7]
        tilted = [p for p in report.enemy_profiles if p.tilt_probability > 0.6]
        if tilted:
            names = ", ".join(p.summoner_name for p in tilted)
            report.strategy_recommendations.append(f"Target tilted players: {names}")
        if high_threats:
            names = ", ".join(p.summoner_name for p in high_threats)
            report.strategy_recommendations.append(f"Watch out for high-threat: {names}")
        for p in sorted(report.enemy_profiles, key=lambda x: x.overall_threat_level, reverse=True):
            if p.predicted_champion and p.predicted_champion not in report.ban_suggestions:
                report.ban_suggestions.append(p.predicted_champion)
                if len(report.ban_suggestions) >= 5:
                    break
        report.generation_time_ms = (time.time() - start) * 1000
        self._scout_history.append(report)
        return report

    def get_threat_assessment(self, profiles: List[OpponentProfile]) -> Dict[str, Any]:
        """Aggregate threat assessment across enemy team."""
        if not profiles:
            return {"avg_threat": 0.5, "max_threat": 0.5, "tilted_count": 0}
        return {
            "avg_threat": statistics.mean([p.overall_threat_level for p in profiles]),
            "max_threat": max(p.overall_threat_level for p in profiles),
            "tilted_count": sum(1 for p in profiles if p.tilt_probability > 0.6),
            "total_weaknesses": sum(len(p.weaknesses) for p in profiles),
            "total_strengths": sum(len(p.strengths) for p in profiles),
        }

    def invalidate_cache(self, summoner_id: Optional[str] = None) -> int:
        """Invalidate cache entries."""
        if summoner_id:
            removed = 1 if summoner_id in self._cache else 0
            self._cache.pop(summoner_id, None)
            return removed
        count = len(self._cache)
        self._cache.clear()
        return count



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


class MatchHistoryAggregator:
    """Aggregates match history data for scouting analysis."""

    def __init__(self):
        self._aggregated: Dict[str, Dict[str, Any]] = {}

    def aggregate_player_stats(self, matches: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute aggregated statistics from match list."""
        if not matches:
            return {"games": 0}
        kills = [m.get("kills", 0) for m in matches]
        deaths = [m.get("deaths", 0) for m in matches]
        assists = [m.get("assists", 0) for m in matches]
        cs_per_min = [m.get("total_cs", 0) / max(m.get("game_duration_seconds", 1800)/60, 1) for m in matches]
        vision_per_min = [m.get("vision_score", 0) / max(m.get("game_duration_seconds", 1800)/60, 1) for m in matches]
        durations = [m.get("game_duration_seconds", 1800) / 60 for m in matches]
        wins = sum(1 for m in matches if m.get("win", False))
        return {
            "games": len(matches),
            "winrate": wins / len(matches),
            "avg_kills": statistics.mean(kills),
            "avg_deaths": statistics.mean(deaths),
            "avg_assists": statistics.mean(assists),
            "avg_kda": statistics.mean([(k+a)/max(d,1) for k,d,a in zip(kills, deaths, assists)]),
            "avg_cs_per_min": statistics.mean(cs_per_min),
            "avg_vision_per_min": statistics.mean(vision_per_min),
            "avg_game_duration_min": statistics.mean(durations),
            "std_kills": statistics.stdev(kills) if len(kills) > 1 else 0,
            "std_deaths": statistics.stdev(deaths) if len(deaths) > 1 else 0,
            "max_kills": max(kills),
            "max_deaths": max(deaths),
            "best_kda": max((k+a)/max(d,1) for k,d,a in zip(kills, deaths, assists)),
            "worst_kda": min((k+a)/max(d,1) for k,d,a in zip(kills, deaths, assists)),
        }

    def aggregate_by_champion(self, matches: List[Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """Aggregate stats grouped by champion."""
        by_champ: Dict[int, List[Dict]] = defaultdict(list)
        for m in matches:
            by_champ[m.get("champion_id", 0)].append(m)
        result = {}
        for cid, champ_matches in by_champ.items():
            result[cid] = self.aggregate_player_stats(champ_matches)
            result[cid]["champion_id"] = cid
        return result

    def aggregate_by_role(self, matches: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """Aggregate stats grouped by role."""
        by_role: Dict[str, List[Dict]] = defaultdict(list)
        for m in matches:
            by_role[m.get("role", "FILL")].append(m)
        result = {}
        for role, role_matches in by_role.items():
            result[role] = self.aggregate_player_stats(role_matches)
            result[role]["role"] = role
        return result

    def get_recent_form(self, matches: List[Dict[str, Any]], n: int = 5) -> Dict[str, Any]:
        """Analyze recent form from last N games."""
        recent = matches[:n]
        if not recent:
            return {"form": "unknown", "trend": "unknown"}
        wins = sum(1 for m in recent if m.get("win", False))
        wr = wins / len(recent)
        if wr >= 0.8:
            form = "hot_streak"
        elif wr >= 0.6:
            form = "good"
        elif wr >= 0.4:
            form = "average"
        elif wr >= 0.2:
            form = "poor"
        else:
            form = "cold_streak"
        older = matches[n:n*2]
        if older:
            older_wins = sum(1 for m in older if m.get("win", False))
            older_wr = older_wins / len(older)
            if wr > older_wr + 0.1:
                trend = "improving"
            elif wr < older_wr - 0.1:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "unknown"
        return {"form": form, "winrate": wr, "wins": wins, "games": len(recent), "trend": trend}


class StrategyAdvisor:
    """Generates strategic recommendations based on scouting data."""

    TIER_WEIGHTS = {
        "IRON": 1, "BRONZE": 2, "SILVER": 3, "GOLD": 4,
        "PLATINUM": 5, "EMERALD": 6, "DIAMOND": 7,
        "MASTER": 8, "GRANDMASTER": 9, "CHALLENGER": 10,
    }

    def __init__(self):
        self._strategy_cache: Dict[str, List[str]] = {}

    def generate_lane_strategy(self, my_role: str, opponent: OpponentProfile) -> List[str]:
        """Generate lane-specific strategy against an opponent."""
        strategies = []
        if opponent.tilt_probability > 0.6:
            strategies.append(f"[{my_role}] Opponent appears tilted - play aggressive early to snowball")
        farming_weak = any(w.category == WeaknessCategory.FARMING for w in opponent.weaknesses)
        if farming_weak:
            strategies.append(f"[{my_role}] Opponent has weak CS - zone them from minions and build lead")
        vision_weak = any(w.category == WeaknessCategory.VISION for w in opponent.weaknesses)
        if vision_weak:
            strategies.append(f"[{my_role}] Opponent lacks vision control - coordinate ganks and roams")
        dying_early = any(w.category == WeaknessCategory.DYING_EARLY for w in opponent.weaknesses)
        if dying_early:
            strategies.append(f"[{my_role}] Opponent dies frequently - punish aggressive positioning")
        combat_strong = any(s.category == "combat" for s in opponent.strengths)
        if combat_strong:
            strategies.append(f"[{my_role}] Opponent has strong combat stats - avoid extended trades unless ahead")
        if not strategies:
            strategies.append(f"[{my_role}] No clear weaknesses detected - play standard and focus on fundamentals")
        return strategies

    def generate_team_strategy(self, report: ScoutingReport) -> List[str]:
        """Generate team-level strategic recommendations."""
        strategies = list(report.strategy_recommendations)
        if not report.enemy_profiles:
            return strategies
        avg_threat = statistics.mean([p.overall_threat_level for p in report.enemy_profiles])
        if avg_threat > 0.7:
            strategies.append("Enemy team is high-skill - focus on macro play and objective control")
        elif avg_threat < 0.4:
            strategies.append("Enemy team is lower-ranked - play confidently but avoid overcommitting")
        tilted_count = sum(1 for p in report.enemy_profiles if p.tilt_probability > 0.5)
        if tilted_count >= 2:
            strategies.append(f"{tilted_count} enemies may be tilted - apply early pressure across the map")
        weak_vision = sum(1 for p in report.enemy_profiles
                         if any(w.category == WeaknessCategory.VISION for w in p.weaknesses))
        if weak_vision >= 3:
            strategies.append("Multiple enemies have poor vision - invade and control enemy jungle")
        return strategies

    def prioritize_targets(self, profiles: List[OpponentProfile]) -> List[Tuple[str, float, str]]:
        """Prioritize enemy players as targets based on weaknesses."""
        targets = []
        for p in profiles:
            priority = 0.0
            reasons = []
            if p.tilt_probability > 0.5:
                priority += 0.3
                reasons.append("tilted")
            if any(w.category == WeaknessCategory.DYING_EARLY for w in p.weaknesses):
                priority += 0.25
                reasons.append("dies frequently")
            if any(w.category == WeaknessCategory.FARMING for w in p.weaknesses):
                priority += 0.15
                reasons.append("weak farming")
            if p.overall_threat_level > 0.7:
                priority -= 0.1
                reasons.append("high-skill, risky target")
            targets.append((p.summoner_name, priority, ", ".join(reasons) if reasons else "no clear weakness"))
        targets.sort(key=lambda x: x[1], reverse=True)
        return targets


def _self_test() -> Dict[str, Any]:
    results = {"module": "M817_opponent_scouting_system", "tests": []}
    try:
        system = OpponentScoutingSystem()
        matches = [{"champion_id": 1, "win": True, "kills": 8, "deaths": 2, "assists": 10,
                     "total_cs": 200, "vision_score": 30, "game_duration_seconds": 1800} for _ in range(10)]
        profile = system.scout_opponent("TestEnemy", "id123", matches, {"tier": "DIAMOND", "division": "II", "lp": 50})
        assert profile.overall_threat_level > 0
        assert profile.rank_tier == "DIAMOND"
        results["tests"].append({"name": "scout_opponent", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "scout_opponent", "status": "fail", "error": str(e)})
    try:
        system = OpponentScoutingSystem()
        enemies = [{"name": f"Enemy{i}", "id": f"id{i}", "matches": [
            {"champion_id": i, "win": (i%2==0), "kills": 3, "deaths": 5, "assists": 4,
             "total_cs": 120, "vision_score": 10, "game_duration_seconds": 1800} for _ in range(5)],
            "rank": {"tier": "GOLD", "division": "I", "lp": 80}} for i in range(5)]
        report = system.generate_scouting_report(["Me"], enemies)
        assert len(report.enemy_profiles) == 5
        results["tests"].append({"name": "scouting_report", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "scouting_report", "status": "fail", "error": str(e)})
    try:
        system = OpponentScoutingSystem()
        system.scout_opponent("A", "1", [{"win": True, "kills": 5, "deaths": 2, "total_cs": 150, "vision_score": 20, "game_duration_seconds": 1800, "champion_id": 1} for _ in range(5)])
        assert system.invalidate_cache("1") == 1
        assert system.invalidate_cache("nonexistent") == 0
        results["tests"].append({"name": "cache_invalidation", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "cache_invalidation", "status": "fail", "error": str(e)})
    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results

if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))
#!/usr/bin/env python3
"""
M814 - Team Composition Analyzer
====================================
OperatorRL Historical Battle System - Champion Synergy & Counter Analysis

查看现有的英雄联盟阵容分析工具实现方式，理解其模式，
特别是英雄克制关系和协同效应是如何量化的。从英雄属性数据开始，
遵循该模式实现阵容分析器，使系统可以评估任意5v5阵容的强度，
并能给出Ban/Pick建议。接着引入协同矩阵计算，使分析能够
识别最优英雄组合，同时优化计算性能以支持实时选英阶段使用。

Core responsibilities:
- Analyze 5v5 team composition strength and weaknesses
- Calculate champion synergy scores (wombo combo, protect-the-carry, etc.)
- Evaluate counter-pick relationships from historical data
- Generate ban/pick recommendations during champion select
- Score team comps across dimensions (damage, CC, tankiness, etc.)
"""

import os, sys, json, time, math, logging, hashlib, statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from itertools import combinations

logger = logging.getLogger("operatorRL.historical_battle.analysis.composition")
logger.setLevel(logging.DEBUG)

MAX_TEAM_SIZE = 5
SYNERGY_MATRIX_SIZE = 200
MIN_GAMES_FOR_STAT = 30
WINRATE_BASELINE = 0.5
COUNTER_SIGNIFICANCE_THRESHOLD = 0.05
SYNERGY_SIGNIFICANCE_THRESHOLD = 0.03
ROLE_SLOTS = ["TOP", "JUNGLE", "MID", "ADC", "SUPPORT"]
COMP_ARCHETYPES = ["teamfight", "pick", "poke", "splitpush", "protect", "engage", "disengage", "siege", "skirmish", "scaling"]

class DamageType(Enum):
    PHYSICAL = "physical"
    MAGIC = "magic"
    TRUE = "true"
    MIXED = "mixed"

class ChampionRole(Enum):
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MID"
    ADC = "ADC"
    SUPPORT = "SUPPORT"
    FILL = "FILL"

class CompStrength(Enum):
    VERY_WEAK = 1
    WEAK = 2
    AVERAGE = 3
    STRONG = 4
    VERY_STRONG = 5

class SynergyType(Enum):
    WOMBO_COMBO = "wombo_combo"
    PROTECT_CARRY = "protect_carry"
    DIVE_COMP = "dive_comp"
    POKE_SIEGE = "poke_siege"
    SPLIT_PRESSURE = "split_pressure"
    PICK_COMP = "pick_comp"
    AOE_TEAMFIGHT = "aoe_teamfight"

@dataclass
class ChampionAttributes:
    champion_id: int
    name: str
    primary_role: ChampionRole
    secondary_roles: List[ChampionRole] = field(default_factory=list)
    damage_type: DamageType = DamageType.MIXED
    has_hard_cc: bool = False
    cc_duration_estimate: float = 0.0
    has_aoe_damage: bool = False
    has_engage: bool = False
    has_disengage: bool = False
    has_poke: bool = False
    has_sustain: bool = False
    is_tank: bool = False
    is_assassin: bool = False
    is_support_type: bool = False
    mobility_score: float = 0.5
    scaling_score: float = 0.5
    early_power: float = 0.5
    teamfight_score: float = 0.5
    splitpush_score: float = 0.5
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        r = asdict(self)
        r["primary_role"] = self.primary_role.value
        r["secondary_roles"] = [x.value for x in self.secondary_roles]
        r["damage_type"] = self.damage_type.value
        return r

@dataclass
class SynergyPair:
    champion_a: int
    champion_b: int
    synergy_score: float
    synergy_types: List[SynergyType] = field(default_factory=list)
    games_analyzed: int = 0
    combined_winrate: float = 0.5
    explanation: str = ""

@dataclass
class CounterRelation:
    champion: int
    counter: int
    winrate_delta: float
    games_analyzed: int = 0
    lane_specific: bool = True
    role: Optional[ChampionRole] = None
    confidence: float = 0.0

@dataclass
class CompositionScore:
    physical_damage: float = 0.0
    magic_damage: float = 0.0
    true_damage: float = 0.0
    crowd_control: float = 0.0
    tankiness: float = 0.0
    engage_power: float = 0.0
    disengage_power: float = 0.0
    poke_power: float = 0.0
    sustain: float = 0.0
    mobility: float = 0.0
    scaling: float = 0.0
    early_game: float = 0.0
    teamfight: float = 0.0
    splitpush: float = 0.0
    pick_potential: float = 0.0
    overall: float = 0.0

    def normalize(self) -> "CompositionScore":
        fields = ["physical_damage","magic_damage","true_damage","crowd_control","tankiness",
                   "engage_power","disengage_power","poke_power","sustain","mobility",
                   "scaling","early_game","teamfight","splitpush","pick_potential"]
        mx = max((getattr(self, f) for f in fields), default=1) or 1
        for f in fields:
            setattr(self, f, getattr(self, f) / mx)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {k: round(v, 3) for k, v in asdict(self).items()}

@dataclass
class CompositionAnalysis:
    team_champions: List[int]
    team_roles: Dict[int, ChampionRole]
    score: CompositionScore = field(default_factory=CompositionScore)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    archetype: str = ""
    synergy_pairs: List[SynergyPair] = field(default_factory=list)
    counter_vulnerabilities: List[CounterRelation] = field(default_factory=list)
    ban_recommendations: List[int] = field(default_factory=list)
    win_probability_estimate: float = 0.5
    confidence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "champions": self.team_champions,
            "roles": {str(k): v.value for k, v in self.team_roles.items()},
            "score": self.score.to_dict(),
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "archetype": self.archetype,
            "synergy_count": len(self.synergy_pairs),
            "vulnerability_count": len(self.counter_vulnerabilities),
            "ban_recommendations": self.ban_recommendations,
            "win_probability": round(self.win_probability_estimate, 4),
            "confidence": round(self.confidence, 3),
        }

class SynergyMatrix:
    def __init__(self):
        self._matrix: Dict[Tuple[int,int], SynergyPair] = {}
        self._counter_map: Dict[Tuple[int,int], CounterRelation] = {}

    def _key(self, a: int, b: int) -> Tuple[int,int]:
        return (min(a,b), max(a,b))

    def set_synergy(self, pair: SynergyPair) -> None:
        self._matrix[self._key(pair.champion_a, pair.champion_b)] = pair

    def get_synergy(self, a: int, b: int) -> Optional[SynergyPair]:
        return self._matrix.get(self._key(a, b))

    def set_counter(self, rel: CounterRelation) -> None:
        self._counter_map[(rel.champion, rel.counter)] = rel

    def get_counter(self, champion: int, counter: int) -> Optional[CounterRelation]:
        return self._counter_map.get((champion, counter))

    def get_team_synergy(self, champions: List[int]) -> float:
        total, count = 0.0, 0
        for a, b in combinations(champions, 2):
            pair = self.get_synergy(a, b)
            if pair:
                total += pair.synergy_score
                count += 1
        return total / count if count > 0 else 0.0

    def build_from_match_data(self, match_records: List[Dict[str, Any]]) -> None:
        pair_stats: Dict[Tuple[int,int], Dict] = defaultdict(lambda: {"wins": 0, "games": 0})
        for match in match_records:
            participants = match.get("participants", [])
            winning_team = match.get("winning_team", 0)
            for team_id in [100, 200]:
                champs = sorted(p["champion_id"] for p in participants if p.get("team_id") == team_id)
                won = (team_id == winning_team)
                for a, b in combinations(champs, 2):
                    pair_stats[(a,b)]["games"] += 1
                    if won:
                        pair_stats[(a,b)]["wins"] += 1
        for (a,b), s in pair_stats.items():
            if s["games"] >= MIN_GAMES_FOR_STAT:
                wr = s["wins"] / s["games"]
                self.set_synergy(SynergyPair(champion_a=a, champion_b=b, synergy_score=(wr - WINRATE_BASELINE)*2, games_analyzed=s["games"], combined_winrate=wr))

class TeamCompositionAnalyzer:
    """Analyzes team compositions for strengths, weaknesses, synergies and strategic recommendations."""

    def __init__(self, champion_db: Optional[Dict[int, ChampionAttributes]] = None):
        self._champion_db: Dict[int, ChampionAttributes] = champion_db or {}
        self._synergy_matrix = SynergyMatrix()

    def load_champion_data(self, data: Dict[int, ChampionAttributes]) -> None:
        self._champion_db = data

    def load_synergy_matrix(self, matrix: SynergyMatrix) -> None:
        self._synergy_matrix = matrix

    def analyze_composition(self, team: List[int], roles: Optional[Dict[int, ChampionRole]] = None, enemy_team: Optional[List[int]] = None) -> CompositionAnalysis:
        if roles is None:
            roles = self._auto_assign_roles(team)
        analysis = CompositionAnalysis(team_champions=team, team_roles=roles)
        analysis.score = self._calculate_scores(team)
        analysis.strengths = self._detect_strengths(analysis.score, team)
        analysis.weaknesses = self._detect_weaknesses(analysis.score, team)
        analysis.archetype = self._classify_archetype(analysis.score)
        for a, b in combinations(team, 2):
            pair = self._synergy_matrix.get_synergy(a, b)
            if pair:
                analysis.synergy_pairs.append(pair)
        if enemy_team:
            for ally in team:
                for enemy in enemy_team:
                    counter = self._synergy_matrix.get_counter(ally, enemy)
                    if counter and counter.winrate_delta < -COUNTER_SIGNIFICANCE_THRESHOLD:
                        analysis.counter_vulnerabilities.append(counter)
            analysis.ban_recommendations = self._recommend_bans(team, enemy_team)
        synergy_bonus = self._synergy_matrix.get_team_synergy(team)
        analysis.win_probability_estimate = self._estimate_win_probability(analysis.score, synergy_bonus, analysis.counter_vulnerabilities)
        analysis.confidence = min(1.0, len(analysis.synergy_pairs) / 10)
        return analysis

    def _auto_assign_roles(self, team: List[int]) -> Dict[int, ChampionRole]:
        assignments = {}
        available = set(ChampionRole)
        available.discard(ChampionRole.FILL)
        for cid in team:
            attrs = self._champion_db.get(cid)
            if attrs and attrs.primary_role in available:
                assignments[cid] = attrs.primary_role
                available.discard(attrs.primary_role)
            else:
                assignments[cid] = ChampionRole.FILL
        return assignments

    def _calculate_scores(self, team: List[int]) -> CompositionScore:
        score = CompositionScore()
        n = 0
        for cid in team:
            attrs = self._champion_db.get(cid)
            if not attrs:
                continue
            n += 1
            if attrs.damage_type in (DamageType.PHYSICAL, DamageType.MIXED):
                score.physical_damage += 1.0
            if attrs.damage_type in (DamageType.MAGIC, DamageType.MIXED):
                score.magic_damage += 1.0
            if attrs.damage_type == DamageType.TRUE:
                score.true_damage += 1.0
            if attrs.has_hard_cc:
                score.crowd_control += attrs.cc_duration_estimate
            if attrs.is_tank:
                score.tankiness += 1.0
            if attrs.has_engage:
                score.engage_power += 1.0
            if attrs.has_disengage:
                score.disengage_power += 1.0
            if attrs.has_poke:
                score.poke_power += 1.0
            if attrs.has_sustain:
                score.sustain += 1.0
            score.mobility += attrs.mobility_score
            score.scaling += attrs.scaling_score
            score.early_game += attrs.early_power
            score.teamfight += attrs.teamfight_score
            score.splitpush += attrs.splitpush_score
        if n > 0:
            for f in ["mobility","scaling","early_game","teamfight","splitpush"]:
                setattr(score, f, getattr(score, f) / n)
        score.overall = (score.crowd_control*0.15 + score.tankiness*0.1 + score.engage_power*0.15 + score.teamfight*0.2 + score.scaling*0.15 + score.mobility*0.1 + (score.physical_damage+score.magic_damage)*0.05 + score.sustain*0.05 + score.splitpush*0.05)
        return score

    def _detect_strengths(self, s: CompositionScore, team: List[int]) -> List[str]:
        r = []
        if s.crowd_control >= 3.0: r.append("Heavy CC chain potential")
        if s.engage_power >= 2.0: r.append("Strong engage tools")
        if s.physical_damage >= 2 and s.magic_damage >= 2: r.append("Balanced damage types")
        if s.scaling >= 0.7: r.append("Strong late-game scaling")
        if s.teamfight >= 0.7: r.append("Excellent teamfight potential")
        if s.poke_power >= 2.0: r.append("Strong poke/siege capability")
        if s.tankiness >= 2.0: r.append("Tanky frontline")
        if s.splitpush >= 0.7: r.append("Split push pressure")
        return r

    def _detect_weaknesses(self, s: CompositionScore, team: List[int]) -> List[str]:
        r = []
        if s.crowd_control < 1.0: r.append("Lacks reliable CC")
        if s.tankiness < 1.0: r.append("No frontline tank")
        if s.engage_power < 1.0: r.append("No engage tools")
        if s.physical_damage >= 3 and s.magic_damage < 1: r.append("Full AD - countered by armor")
        if s.magic_damage >= 3 and s.physical_damage < 1: r.append("Full AP - countered by MR")
        if s.early_game < 0.3: r.append("Weak early game")
        return r

    def _classify_archetype(self, s: CompositionScore) -> str:
        scores = {
            "teamfight": s.teamfight*0.4 + s.crowd_control*0.1 + s.engage_power*0.1,
            "pick": s.crowd_control*0.1 + s.mobility*0.2,
            "poke": s.poke_power*0.3 + s.disengage_power*0.1,
            "splitpush": s.splitpush*0.4 + s.mobility*0.1,
            "protect": s.sustain*0.2 + s.disengage_power*0.2,
            "engage": s.engage_power*0.3 + s.tankiness*0.1 + s.crowd_control*0.1,
            "scaling": s.scaling*0.4 + s.sustain*0.1,
        }
        return max(scores, key=scores.get)

    def _recommend_bans(self, team: List[int], enemy: List[int]) -> List[int]:
        ban_scores: Dict[int, float] = defaultdict(float)
        for cid, attrs in self._champion_db.items():
            if cid in team or cid in enemy:
                continue
            for ally in team:
                c = self._synergy_matrix.get_counter(ally, cid)
                if c:
                    ban_scores[cid] += abs(c.winrate_delta)
        return sorted(ban_scores, key=ban_scores.get, reverse=True)[:5]

    def _estimate_win_probability(self, score: CompositionScore, syn: float, vulns: List[CounterRelation]) -> float:
        base = 0.5 + score.overall * 0.05 + syn * 0.1
        base -= sum(abs(v.winrate_delta) for v in vulns) * 0.05
        return max(0.1, min(0.9, base))



class MatchupDatabase:
    """Historical matchup win rates for lane-specific counter analysis."""

    def __init__(self):
        self._matchups: Dict[Tuple[int, int, str], Dict[str, float]] = {}
        self._sample_counts: Dict[Tuple[int, int, str], int] = {}

    def record_matchup(self, champ_a: int, champ_b: int, role: str, a_won: bool) -> None:
        """Record a single matchup result."""
        key = (min(champ_a, champ_b), max(champ_a, champ_b), role)
        if key not in self._matchups:
            self._matchups[key] = {"wins_lower": 0, "total": 0}
            self._sample_counts[key] = 0
        self._matchups[key]["total"] += 1
        self._sample_counts[key] += 1
        if (a_won and champ_a == key[0]) or (not a_won and champ_b == key[0]):
            self._matchups[key]["wins_lower"] += 1

    def get_matchup_winrate(self, champ_a: int, champ_b: int, role: str) -> Optional[float]:
        """Get win rate for champ_a vs champ_b in specified role."""
        key = (min(champ_a, champ_b), max(champ_a, champ_b), role)
        data = self._matchups.get(key)
        if not data or data["total"] < MIN_GAMES_FOR_STAT:
            return None
        wr = data["wins_lower"] / data["total"]
        return wr if champ_a == key[0] else 1.0 - wr

    def get_worst_matchups(self, champion_id: int, role: str, top_n: int = 5) -> List[Tuple[int, float]]:
        """Get the worst matchups for a champion in a role."""
        results = []
        for (a, b, r), data in self._matchups.items():
            if r != role or data["total"] < MIN_GAMES_FOR_STAT:
                continue
            if a == champion_id:
                wr = data["wins_lower"] / data["total"]
                results.append((b, wr))
            elif b == champion_id:
                wr = 1.0 - data["wins_lower"] / data["total"]
                results.append((a, wr))
        results.sort(key=lambda x: x[1])
        return results[:top_n]

    def get_best_matchups(self, champion_id: int, role: str, top_n: int = 5) -> List[Tuple[int, float]]:
        """Get the best matchups for a champion in a role."""
        results = []
        for (a, b, r), data in self._matchups.items():
            if r != role or data["total"] < MIN_GAMES_FOR_STAT:
                continue
            if a == champion_id:
                wr = data["wins_lower"] / data["total"]
                results.append((b, wr))
            elif b == champion_id:
                wr = 1.0 - data["wins_lower"] / data["total"]
                results.append((a, wr))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_n]

    def build_from_matches(self, matches: List[Dict[str, Any]]) -> int:
        """Build matchup database from historical match records."""
        recorded = 0
        for match in matches:
            participants = match.get("participants", [])
            winning_team = match.get("winning_team", 0)
            role_map: Dict[str, Dict[int, int]] = defaultdict(dict)
            for p in participants:
                role = p.get("role", "FILL")
                team = p.get("team_id", 0)
                role_map[role][team] = p.get("champion_id", 0)
            for role, teams in role_map.items():
                if 100 in teams and 200 in teams:
                    c100, c200 = teams[100], teams[200]
                    self.record_matchup(c100, c200, role, winning_team == 100)
                    recorded += 1
        return recorded

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_matchups": len(self._matchups),
            "total_samples": sum(self._sample_counts.values()),
        }


class WinConditionAnalyzer:
    """Analyzes team compositions for specific win conditions."""

    WIN_CONDITIONS = [
        "early_snowball", "teamfight_dominance", "split_push_pressure",
        "poke_siege", "pick_and_catch", "scaling_advantage",
        "objective_control", "dive_backline",
    ]

    def __init__(self, champion_db: Dict[int, ChampionAttributes]):
        self._champion_db = champion_db

    def analyze_win_conditions(self, team: List[int], score: CompositionScore) -> Dict[str, float]:
        """Score each possible win condition for the team composition."""
        conditions = {}
        conditions["early_snowball"] = min(1.0, score.early_game * 1.5)
        conditions["teamfight_dominance"] = min(1.0, (score.teamfight * 0.4 + score.crowd_control * 0.1 + score.engage_power * 0.1) * 1.2)
        conditions["split_push_pressure"] = min(1.0, score.splitpush * 1.3)
        conditions["poke_siege"] = min(1.0, (score.poke_power * 0.5 + score.disengage_power * 0.2) * 0.8)
        conditions["pick_and_catch"] = min(1.0, (score.crowd_control * 0.15 + score.mobility * 0.3) * 1.2)
        conditions["scaling_advantage"] = min(1.0, score.scaling * 1.4)
        conditions["objective_control"] = min(1.0, (score.tankiness * 0.15 + score.sustain * 0.15 + score.crowd_control * 0.1) * 1.0)
        conditions["dive_backline"] = self._assess_dive_potential(team)
        return conditions

    def _assess_dive_potential(self, team: List[int]) -> float:
        """Evaluate backline dive potential."""
        divers = 0
        for cid in team:
            attrs = self._champion_db.get(cid)
            if attrs and (attrs.is_assassin or (attrs.has_engage and attrs.mobility_score >= 0.6)):
                divers += 1
        return min(1.0, divers * 0.35)

    def get_primary_win_condition(self, conditions: Dict[str, float]) -> Tuple[str, float]:
        """Get the strongest win condition."""
        if not conditions:
            return "balanced", 0.5
        best = max(conditions, key=conditions.get)
        return best, conditions[best]

    def generate_strategy_suggestions(self, conditions: Dict[str, float]) -> List[str]:
        """Generate strategic suggestions based on win conditions."""
        suggestions = []
        sorted_conditions = sorted(conditions.items(), key=lambda x: x[1], reverse=True)
        primary, primary_score = sorted_conditions[0] if sorted_conditions else ("balanced", 0.5)
        strategy_map = {
            "early_snowball": "Focus on early game aggression - invade, gank, and secure first blood/tower",
            "teamfight_dominance": "Group for 5v5 team fights around objectives - force Baron/Dragon fights",
            "split_push_pressure": "Apply split push pressure - 1-3-1 or 1-4 formations",
            "poke_siege": "Siege towers with poke - avoid hard engages and kite back",
            "pick_and_catch": "Control vision and look for picks on isolated targets",
            "scaling_advantage": "Play safe early, farm efficiently, and outscale in late game",
            "objective_control": "Prioritize neutral objectives - Dragon soul and Baron control",
            "dive_backline": "Look for flank angles to access enemy carries in fights",
        }
        suggestions.append(f"Primary strategy: {strategy_map.get(primary, 'Play to strengths')}")
        if len(sorted_conditions) >= 2:
            secondary = sorted_conditions[1]
            if secondary[1] >= 0.5:
                suggestions.append(f"Secondary option: {strategy_map.get(secondary[0], 'Adapt')}")
        weakest = sorted_conditions[-1] if sorted_conditions else None
        if weakest and weakest[1] < 0.3:
            avoid_map = {
                "early_snowball": "Avoid coinflip early game plays",
                "teamfight_dominance": "Avoid 5v5 team fights if possible",
                "split_push_pressure": "Don't attempt split push without vision",
                "poke_siege": "Don't try to siege against engage comps",
                "scaling_advantage": "Don't stall - you don't outscale",
            }
            if weakest[0] in avoid_map:
                suggestions.append(f"Avoid: {avoid_map[weakest[0]]}")
        return suggestions


def _self_test() -> Dict[str, Any]:
    results = {"module": "M814_team_composition_analyzer", "tests": []}
    try:
        attrs = ChampionAttributes(champion_id=1, name="Test", primary_role=ChampionRole.TOP)
        assert attrs.to_dict()["primary_role"] == "TOP"
        results["tests"].append({"name": "champion_attributes", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "champion_attributes", "status": "fail", "error": str(e)})
    try:
        m = SynergyMatrix()
        m.set_synergy(SynergyPair(champion_a=1, champion_b=2, synergy_score=0.5, games_analyzed=100))
        assert m.get_synergy(2, 1).synergy_score == 0.5
        results["tests"].append({"name": "synergy_matrix", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "synergy_matrix", "status": "fail", "error": str(e)})
    try:
        analyzer = TeamCompositionAnalyzer()
        db = {i: ChampionAttributes(champion_id=i, name=f"C{i}", primary_role=ChampionRole(ROLE_SLOTS[i-1]),
                has_hard_cc=(i%2==0), is_tank=(i==1), damage_type=DamageType.PHYSICAL if i<=3 else DamageType.MAGIC,
                teamfight_score=0.6, scaling_score=0.5, early_power=0.5, cc_duration_estimate=1.5 if i%2==0 else 0)
              for i in range(1,6)}
        analyzer.load_champion_data(db)
        a = analyzer.analyze_composition([1,2,3,4,5])
        assert a.archetype != ""
        results["tests"].append({"name": "composition_analysis", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "composition_analysis", "status": "fail", "error": str(e)})

    # Test matchup database
    try:
        mdb = MatchupDatabase()
        for i in range(50):
            mdb.record_matchup(1, 2, "MID", i % 3 != 0)
        wr = mdb.get_matchup_winrate(1, 2, "MID")
        assert wr is not None
        assert 0.0 <= wr <= 1.0
        worst = mdb.get_worst_matchups(1, "MID")
        assert isinstance(worst, list)
        results["tests"].append({"name": "matchup_database", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "matchup_database", "status": "fail", "error": str(e)})

    # Test win condition analyzer
    try:
        analyzer2 = TeamCompositionAnalyzer()
        db2 = {i: ChampionAttributes(champion_id=i, name=f"C{i}", primary_role=ChampionRole(ROLE_SLOTS[i-1]),
                has_hard_cc=(i%2==0), is_tank=(i==1), is_assassin=(i==3),
                damage_type=DamageType.PHYSICAL if i<=3 else DamageType.MAGIC,
                teamfight_score=0.6, scaling_score=0.5, early_power=0.5,
                mobility_score=0.7 if i==3 else 0.4, has_engage=(i==1),
                cc_duration_estimate=1.5 if i%2==0 else 0)
              for i in range(1,6)}
        analyzer2.load_champion_data(db2)
        score = analyzer2._calculate_scores([1,2,3,4,5])
        wca = WinConditionAnalyzer(db2)
        conditions = wca.analyze_win_conditions([1,2,3,4,5], score)
        assert len(conditions) == 8
        primary, primary_score = wca.get_primary_win_condition(conditions)
        assert primary in WinConditionAnalyzer.WIN_CONDITIONS
        suggestions = wca.generate_strategy_suggestions(conditions)
        assert len(suggestions) >= 1
        results["tests"].append({"name": "win_condition_analysis", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "win_condition_analysis", "status": "fail", "error": str(e)})

    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results

if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))

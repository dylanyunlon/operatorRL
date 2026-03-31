#!/usr/bin/env python3
"""
M792: Team Composition
======================
查看阵容分析和个体英雄数据是如何分离的。
从 team-stats aggregation 这个好例子开始。

Reference: operatorRL agentic system / Seraphine LCU patterns
"""

import os, sys, json, time, math, hashlib, sqlite3, threading, logging, struct, re
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union, Set, Callable
from dataclasses import dataclass, field, asdict
from collections import defaultdict, Counter, OrderedDict, deque

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from logging_system.core_logger import get_logger, EventCategory
except ImportError:
    get_logger = lambda x: logging.getLogger(x)
    EventCategory = type('E', (), dict(SYSTEM='system', DATA='data',
        NETWORK='network', PERF='performance'))()


# Constants
TEAM_SIZE = 5
COMPOSITION_TYPES = ["teamfight","poke","pick","split","protect","dive"]
DAMAGE_BALANCE_THRESHOLD = 0.35
ROLE_ASSIGNMENTS = ["TOP","JUNGLE","MIDDLE","BOTTOM","SUPPORT"]

class CompType(Enum):
    TEAMFIGHT = "teamfight"
    POKE = "poke"
    PICK = "pick"
    SPLIT = "split"
    PROTECT = "protect"
    DIVE = "dive"
    MIXED = "mixed"

class DmgProfile(Enum):
    PHYSICAL = "physical"
    MAGIC = "magic"
    BALANCED = "balanced"
    TRUE_HEAVY = "true_heavy"

@dataclass
class ChampionInComp:
    name: str = ""
    role: str = ""
    damage_type: str = "mixed"
    cc_score: float = 0.0
    tankiness: float = 0.0
    mobility: float = 0.0
    waveclear: float = 0.0
    poke: float = 0.0
    burst: float = 0.0
    dps: float = 0.0
    utility: float = 0.0
    engage: float = 0.0
    peel: float = 0.0
    scaling: str = "mid"

@dataclass
class CompositionAnalysis:
    team: List[ChampionInComp] = field(default_factory=list)
    comp_type: CompType = CompType.MIXED
    damage_profile: DmgProfile = DmgProfile.BALANCED
    total_cc: float = 0.0
    total_tankiness: float = 0.0
    total_engage: float = 0.0
    total_peel: float = 0.0
    waveclear_rating: str = "medium"
    scaling_rating: str = "medium"
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    win_conditions: List[str] = field(default_factory=list)
    power_spikes: Dict[str, str] = field(default_factory=dict)
    synergy_score: float = 50.0
    overall_score: float = 50.0

class ComboDetector:
    """Detects champion ability combos and synergies."""
    KNOWN_COMBOS = {
        ("Yasuo","Malphite"): {"combo":"Malphite R -> Yasuo R","impact":9.5,"type":"teamfight"},
        ("Orianna","Malphite"): {"combo":"Malphite R -> Orianna R","impact":9.0,"type":"teamfight"},
        ("Jinx","Thresh"): {"combo":"Thresh hook -> Jinx traps","impact":7.0,"type":"pick"},
        ("Leona","MissFortune"): {"combo":"Leona R -> MF R","impact":8.5,"type":"teamfight"},
        ("Jarvan IV","Rumble"): {"combo":"J4 R -> Rumble R","impact":8.0,"type":"teamfight"},
        ("Sejuani","Yasuo"): {"combo":"Sejuani R -> Yasuo R","impact":8.5,"type":"teamfight"},
        ("Twitch","Yuumi"): {"combo":"Stealth engage + heals","impact":7.5,"type":"protect"},
        ("Kog'Maw","Lulu"): {"combo":"Lulu buffs + Kog DPS","impact":8.0,"type":"protect"},
    }

    def __init__(self, logger=None):
        self._logger = logger

    def detect_combos(self, team: List[str]) -> List[Dict]:
        combos = []
        for i in range(len(team)):
            for j in range(i+1, len(team)):
                key1 = (team[i], team[j])
                key2 = (team[j], team[i])
                for key in (key1, key2):
                    if key in self.KNOWN_COMBOS:
                        combos.append({"champions": list(key), **self.KNOWN_COMBOS[key]})
        return combos

class WinConditionIdentifier:
    """Identifies win conditions for a team composition."""
    def __init__(self, logger=None):
        self._logger = logger

    def identify(self, analysis: CompositionAnalysis) -> List[str]:
        conditions = []
        if analysis.total_cc > 15:
            conditions.append("Win teamfights through chain CC")
        if analysis.total_engage > 15:
            conditions.append("Force engages on isolated targets")
        if analysis.scaling_rating == "late":
            conditions.append("Scale to late game, avoid early fights")
        if analysis.scaling_rating == "early":
            conditions.append("Snowball early leads, close before 25min")
        if analysis.waveclear_rating == "high":
            conditions.append("Control waves, siege towers methodically")
        any_split = any(c.mobility > 7 and c.waveclear > 7 for c in analysis.team)
        if any_split:
            conditions.append("Split push with mobile waveclear champion")
        if analysis.total_peel > 12:
            conditions.append("Protect carries in teamfights")
        if not conditions:
            conditions.append("Play standard, look for picks")
        return conditions

class TeamfightSimulator:
    """Simulates teamfight outcomes based on composition stats."""
    def __init__(self, logger=None):
        self._logger = logger

    def simulate(self, team_a: CompositionAnalysis, team_b: CompositionAnalysis) -> Dict:
        a_score = (team_a.total_cc * 2 + team_a.total_engage * 1.5 +
                   team_a.total_peel + team_a.total_tankiness * 0.5)
        b_score = (team_b.total_cc * 2 + team_b.total_engage * 1.5 +
                   team_b.total_peel + team_b.total_tankiness * 0.5)
        total = a_score + b_score or 1
        a_odds = round(a_score / total * 100, 1)
        return {
            "team_a_odds": a_odds,
            "team_b_odds": round(100 - a_odds, 1),
            "team_a_score": round(a_score, 1),
            "team_b_score": round(b_score, 1),
            "key_factor": "CC advantage" if abs(team_a.total_cc - team_b.total_cc) > 5
                          else ("Engage advantage" if abs(team_a.total_engage - team_b.total_engage) > 5
                                else "Balanced"),
        }

class DraftAdvisor:
    """Provides draft phase recommendations."""
    def __init__(self, logger=None):
        self._logger = logger
        self._role_priority = {"SUPPORT":1,"JUNGLE":2,"MIDDLE":3,"BOTTOM":4,"TOP":5}

    def suggest_pick(self, current_team: List[ChampionInComp],
                     available_roles: List[str],
                     enemy_team: List[str] = None) -> Dict:
        missing_roles = [r for r in ROLE_ASSIGNMENTS
                         if r not in [c.role for c in current_team]]
        needs = self._analyze_needs(current_team)
        return {
            "missing_roles": missing_roles,
            "priority_role": missing_roles[0] if missing_roles else None,
            "needs": needs,
            "suggestion": f"Pick a {needs[0]} for {missing_roles[0] if missing_roles else 'flex'}"
                          if needs else "Composition looks complete",
        }

    def _analyze_needs(self, team: List[ChampionInComp]) -> List[str]:
        needs = []
        total_cc = sum(c.cc_score for c in team)
        total_tank = sum(c.tankiness for c in team)
        total_engage = sum(c.engage for c in team)
        if total_cc < 8: needs.append("CC")
        if total_tank < 10: needs.append("frontline/tank")
        if total_engage < 5: needs.append("engage")
        if not any(c.damage_type == "magic" for c in team):
            needs.append("magic damage")
        if not any(c.damage_type == "physical" for c in team):
            needs.append("physical damage")
        return needs

    def suggest_bans(self, enemy_main_champions: List[Dict],
                     current_meta_ops: List[str] = None) -> List[str]:
        bans = []
        if current_meta_ops:
            bans.extend(current_meta_ops[:2])
        if enemy_main_champions:
            for champ in enemy_main_champions[:3]:
                name = champ.get("name", "")
                if name and name not in bans:
                    bans.append(name)
        return bans[:5]

class TeamCompositionAnalyzer:
    """Primary team composition analysis engine."""
    def __init__(self, logger=None):
        self._logger = logger or (get_logger("M792") if callable(get_logger)
                                  else logging.getLogger("M792"))
        self._combo_detector = ComboDetector(self._logger)
        self._wc_identifier = WinConditionIdentifier(self._logger)
        self._tf_simulator = TeamfightSimulator(self._logger)
        self._draft_advisor = DraftAdvisor(self._logger)
        self._champion_db: Dict[str, ChampionInComp] = {}
        self._analysis_count = 0

    def register_champion(self, name: str, **kwargs):
        self._champion_db[name.lower()] = ChampionInComp(name=name, **kwargs)

    def analyze(self, team_names: List[str],
                roles: Optional[List[str]] = None) -> CompositionAnalysis:
        self._analysis_count += 1
        team = []
        for i, name in enumerate(team_names):
            champ = self._champion_db.get(name.lower(), ChampionInComp(name=name))
            if roles and i < len(roles):
                champ.role = roles[i]
            team.append(champ)

        analysis = CompositionAnalysis(team=team)
        analysis.total_cc = sum(c.cc_score for c in team)
        analysis.total_tankiness = sum(c.tankiness for c in team)
        analysis.total_engage = sum(c.engage for c in team)
        analysis.total_peel = sum(c.peel for c in team)

        analysis.comp_type = self._classify_comp(team)
        analysis.damage_profile = self._analyze_damage(team)
        analysis.waveclear_rating = self._rate_waveclear(team)
        analysis.scaling_rating = self._rate_scaling(team)
        analysis.strengths, analysis.weaknesses = self._find_strengths_weaknesses(analysis)
        analysis.win_conditions = self._wc_identifier.identify(analysis)
        analysis.overall_score = self._calculate_score(analysis)
        return analysis

    def _classify_comp(self, team: List[ChampionInComp]) -> CompType:
        scores = {ct: 0.0 for ct in CompType}
        for c in team:
            if c.engage > 7: scores[CompType.TEAMFIGHT] += 2; scores[CompType.DIVE] += 1
            if c.poke > 7: scores[CompType.POKE] += 2
            if c.burst > 7: scores[CompType.PICK] += 2
            if c.waveclear > 7 and c.mobility > 6: scores[CompType.SPLIT] += 2
            if c.peel > 7: scores[CompType.PROTECT] += 2
        best = max(scores, key=scores.get)
        return best if scores[best] > 4 else CompType.MIXED

    def _analyze_damage(self, team):
        phys = sum(1 for c in team if c.damage_type == "physical")
        mag = sum(1 for c in team if c.damage_type == "magic")
        if phys >= 3 and mag <= 1: return DmgProfile.PHYSICAL
        if mag >= 3 and phys <= 1: return DmgProfile.MAGIC
        return DmgProfile.BALANCED

    def _rate_waveclear(self, team):
        avg = sum(c.waveclear for c in team) / max(len(team), 1)
        if avg > 7: return "high"
        if avg > 4: return "medium"
        return "low"

    def _rate_scaling(self, team):
        late = sum(1 for c in team if c.scaling == "late")
        early = sum(1 for c in team if c.scaling == "early")
        if late >= 3: return "late"
        if early >= 3: return "early"
        return "mid"

    def _find_strengths_weaknesses(self, a: CompositionAnalysis):
        s, w = [], []
        if a.total_cc > 15: s.append("Strong chain CC")
        if a.total_cc < 5: w.append("Lacks CC")
        if a.total_engage > 12: s.append("Multiple engage tools")
        if a.total_engage < 3: w.append("No reliable engage")
        if a.total_tankiness > 20: s.append("Very tanky frontline")
        if a.total_tankiness < 8: w.append("Squishy team")
        if a.damage_profile == DmgProfile.BALANCED: s.append("Balanced damage types")
        elif a.damage_profile in (DmgProfile.PHYSICAL, DmgProfile.MAGIC):
            w.append(f"Heavily {a.damage_profile.value} damage - easy to itemize against")
        return s, w

    def _calculate_score(self, a: CompositionAnalysis):
        score = 50.0
        score += len(a.strengths) * 5
        score -= len(a.weaknesses) * 5
        if a.damage_profile == DmgProfile.BALANCED: score += 5
        if a.total_cc > 10: score += 5
        if a.waveclear_rating == "high": score += 3
        return min(100, max(0, round(score, 1)))

    def compare_teams(self, team_a: List[str], team_b: List[str]) -> Dict:
        a_analysis = self.analyze(team_a)
        b_analysis = self.analyze(team_b)
        fight_result = self._tf_simulator.simulate(a_analysis, b_analysis)
        return {
            "team_a_analysis": asdict(a_analysis),
            "team_b_analysis": asdict(b_analysis),
            "teamfight_prediction": fight_result,
        }

    @property
    def analysis_count(self): return self._analysis_count
    @property
    def draft_advisor(self): return self._draft_advisor
    @property
    def combo_detector(self): return self._combo_detector

    def get_champion_info(self, name: str) -> Optional[ChampionInComp]:
        return self._champion_db.get(name.lower())

    def analyze_matchup(self, our_team: List[str], enemy_team: List[str]) -> Dict:
        our = self.analyze(our_team)
        enemy = self.analyze(enemy_team)
        fight = self._tf_simulator.simulate(our, enemy)
        our_combos = self._combo_detector.detect_combos(our_team)
        enemy_combos = self._combo_detector.detect_combos(enemy_team)
        return {
            "our_comp": {"type": our.comp_type.value, "score": our.overall_score,
                         "strengths": our.strengths, "weaknesses": our.weaknesses},
            "enemy_comp": {"type": enemy.comp_type.value, "score": enemy.overall_score,
                           "strengths": enemy.strengths, "weaknesses": enemy.weaknesses},
            "teamfight": fight,
            "our_combos": our_combos,
            "enemy_combos": enemy_combos,
            "win_conditions": our.win_conditions,
            "advantage": "our_team" if our.overall_score > enemy.overall_score else "enemy_team",
        }


# ============================================================================
# Power Spike Analyzer
# ============================================================================

class PowerSpikeAnalyzer:
    """Analyzes team power spikes across different game phases."""

    CHAMPION_SPIKES = {
        "yasuo": {"item_spikes": [2, 3], "level_spikes": [6, 13], "phase": "mid"},
        "jinx": {"item_spikes": [3, 4], "level_spikes": [6, 11], "phase": "late"},
        "leona": {"item_spikes": [1], "level_spikes": [2, 3, 6], "phase": "early"},
        "malphite": {"item_spikes": [2, 3], "level_spikes": [6, 11, 16], "phase": "mid"},
        "orianna": {"item_spikes": [2, 3], "level_spikes": [6, 9], "phase": "mid"},
        "renekton": {"item_spikes": [1, 2], "level_spikes": [3, 6], "phase": "early"},
        "kayle": {"item_spikes": [3, 4, 5], "level_spikes": [6, 11, 16], "phase": "late"},
        "lee sin": {"item_spikes": [1, 2], "level_spikes": [3, 6], "phase": "early"},
        "vayne": {"item_spikes": [2, 3, 4], "level_spikes": [6, 11], "phase": "late"},
        "thresh": {"item_spikes": [1], "level_spikes": [2, 6], "phase": "early"},
    }

    def __init__(self, logger=None):
        self._logger = logger

    def analyze_team_spikes(self, team: List[str]) -> Dict:
        early_power = 0
        mid_power = 0
        late_power = 0
        key_levels = []

        for champ in team:
            data = self.CHAMPION_SPIKES.get(champ.lower(), {})
            phase = data.get("phase", "mid")
            if phase == "early":
                early_power += 2
                mid_power += 1
            elif phase == "mid":
                early_power += 1
                mid_power += 2
                late_power += 1
            elif phase == "late":
                mid_power += 1
                late_power += 2
            for lvl in data.get("level_spikes", []):
                key_levels.append({"champion": champ, "level": lvl})

        total = max(early_power + mid_power + late_power, 1)
        peak_phase = "mid"
        if early_power > mid_power and early_power > late_power:
            peak_phase = "early"
        elif late_power > mid_power and late_power > early_power:
            peak_phase = "late"

        return {
            "early_power": round(early_power / total * 100, 1),
            "mid_power": round(mid_power / total * 100, 1),
            "late_power": round(late_power / total * 100, 1),
            "peak_phase": peak_phase,
            "key_level_spikes": sorted(key_levels, key=lambda x: x["level"]),
            "strategy_hint": self._get_strategy_hint(peak_phase),
        }

    def _get_strategy_hint(self, peak: str) -> str:
        hints = {
            "early": "Push advantages before 20 minutes. Force fights at dragon spawns.",
            "mid": "Look for picks after key items. Contest objectives actively.",
            "late": "Scale safely. Avoid unnecessary fights early. Farm for 3+ items.",
        }
        return hints.get(peak, "Play to your team's strengths.")


# ============================================================================
# Lane Matchup Predictor
# ============================================================================

class LaneMatchupPredictor:
    """Predicts individual lane matchup outcomes."""

    def __init__(self, logger=None):
        self._logger = logger

    def predict_lane(self, our_champ: ChampionInComp,
                     enemy_champ: ChampionInComp) -> Dict:
        our_score = (our_champ.dps * 0.25 + our_champ.burst * 0.2 +
                     our_champ.tankiness * 0.15 + our_champ.mobility * 0.15 +
                     our_champ.cc_score * 0.1 + our_champ.waveclear * 0.15)
        enemy_score = (enemy_champ.dps * 0.25 + enemy_champ.burst * 0.2 +
                       enemy_champ.tankiness * 0.15 + enemy_champ.mobility * 0.15 +
                       enemy_champ.cc_score * 0.1 + enemy_champ.waveclear * 0.15)
        total = max(our_score + enemy_score, 1)
        our_odds = round(our_score / total * 100, 1)

        advantage = "even"
        if our_odds > 58:
            advantage = "strong_advantage"
        elif our_odds > 52:
            advantage = "slight_advantage"
        elif our_odds < 42:
            advantage = "strong_disadvantage"
        elif our_odds < 48:
            advantage = "slight_disadvantage"

        tips = []
        if enemy_champ.burst > our_champ.tankiness:
            tips.append("Be cautious of burst trades. Build defensive early.")
        if our_champ.waveclear > enemy_champ.waveclear:
            tips.append("Push waves for roam/plate pressure.")
        if our_champ.mobility < enemy_champ.mobility:
            tips.append("Ward deep. Enemy has escape advantage.")

        return {
            "our_champion": our_champ.name,
            "enemy_champion": enemy_champ.name,
            "our_odds_pct": our_odds,
            "advantage": advantage,
            "tips": tips,
        }

    def predict_all_lanes(self, our_team: List[ChampionInComp],
                          enemy_team: List[ChampionInComp]) -> List[Dict]:
        results = []
        for our, enemy in zip(our_team, enemy_team):
            if our.role and enemy.role and our.role == enemy.role:
                results.append(self.predict_lane(our, enemy))
        return results


# ============================================================================
# Comp Archetype Database
# ============================================================================

class CompArchetypeDB:
    """Database of known team composition archetypes."""

    ARCHETYPES = {
        "wombo_combo": {
            "description": "Heavy AoE CC and damage. Win teamfights decisively.",
            "key_traits": {"cc": 15, "engage": 12},
            "examples": ["Malphite", "Orianna", "Yasuo", "MissFortune", "Leona"],
            "counter_strategy": "Avoid grouping. Split push. Build QSS/Cleanse.",
        },
        "protect_the_carry": {
            "description": "Peel-heavy team protecting a hypercarry.",
            "key_traits": {"peel": 15, "dps": 8},
            "examples": ["Shen", "Ivern", "Lulu", "Kog'Maw", "Janna"],
            "counter_strategy": "Hard engage. Dive the carry. Burst them before shields.",
        },
        "1-3-1_split": {
            "description": "Split push with strong side laners.",
            "key_traits": {"mobility": 12, "waveclear": 12},
            "examples": ["Fiora", "Lee Sin", "Twisted Fate", "Ezreal", "Bard"],
            "counter_strategy": "Force 5v5 fights. Don't let them set up splits.",
        },
        "early_aggro": {
            "description": "Snowball early. Close games before 25 minutes.",
            "key_traits": {"burst": 12, "engage": 10},
            "examples": ["Renekton", "Lee Sin", "Syndra", "Draven", "Leona"],
            "counter_strategy": "Play safe early. Scale. Survive to 3+ items.",
        },
    }

    def __init__(self):
        pass

    def match_archetype(self, analysis: CompositionAnalysis) -> Optional[Dict]:
        best_match = None
        best_score = 0
        for name, arch in self.ARCHETYPES.items():
            score = 0
            traits = arch["key_traits"]
            if "cc" in traits and analysis.total_cc >= traits["cc"]:
                score += 2
            if "engage" in traits and analysis.total_engage >= traits["engage"]:
                score += 2
            if "peel" in traits and analysis.total_peel >= traits["peel"]:
                score += 2
            if score > best_score:
                best_score = score
                best_match = {"archetype": name, "score": score, **arch}
        return best_match

    def get_counter_strategy(self, archetype_name: str) -> str:
        arch = self.ARCHETYPES.get(archetype_name, {})
        return arch.get("counter_strategy", "Play standard. Adapt to the game state.")


# ============================================================================
# Comp Exporter
# ============================================================================

class CompExporter:
    """Exports composition analysis for voice output and dashboard."""

    def __init__(self, logger=None):
        self._logger = logger

    def to_voice_brief(self, analysis: CompositionAnalysis) -> str:
        parts = []
        parts.append(f"阵容类型: {analysis.comp_type.value}")
        if analysis.strengths:
            parts.append(f"优势: {', '.join(analysis.strengths[:2])}")
        if analysis.weaknesses:
            parts.append(f"注意: {', '.join(analysis.weaknesses[:2])}")
        if analysis.win_conditions:
            parts.append(f"胜利条件: {analysis.win_conditions[0]}")
        return "。".join(parts)

    def to_dashboard_widget(self, analysis: CompositionAnalysis) -> Dict:
        return {
            "widget_type": "comp_analysis",
            "comp_type": analysis.comp_type.value,
            "damage_profile": analysis.damage_profile.value,
            "score": analysis.overall_score,
            "cc_total": analysis.total_cc,
            "engage_total": analysis.total_engage,
            "tankiness_total": analysis.total_tankiness,
            "peel_total": analysis.total_peel,
            "waveclear": analysis.waveclear_rating,
            "scaling": analysis.scaling_rating,
            "strengths": analysis.strengths,
            "weaknesses": analysis.weaknesses,
            "win_conditions": analysis.win_conditions,
        }


def _self_test():
    print("[M792] TeamCompositionAnalyzer self-test...")
    analyzer = TeamCompositionAnalyzer()
    analyzer.register_champion("Malphite", role="TOP", damage_type="magic",
        cc_score=8, tankiness=9, engage=9, peel=3, mobility=4, waveclear=5,
        poke=2, burst=5, dps=3, utility=4, scaling="mid")
    analyzer.register_champion("Yasuo", role="MIDDLE", damage_type="physical",
        cc_score=4, tankiness=3, engage=5, peel=2, mobility=8, waveclear=7,
        poke=1, burst=7, dps=8, utility=2, scaling="late")
    result = analyzer.analyze(["Malphite", "Yasuo"])
    assert result.total_cc == 12
    combos = analyzer.combo_detector.detect_combos(["Yasuo", "Malphite"])
    assert len(combos) >= 1
    print(f"  Comp type: {result.comp_type.value}")
    print(f"  Combos found: {len(combos)}")
    print(f"  Score: {result.overall_score}")
    print("[M792] All tests passed.\n")
    return True

if __name__ == "__main__":
    _self_test()

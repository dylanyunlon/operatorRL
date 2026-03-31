#!/usr/bin/env python3
"""
M894 — TeamCompositionEvaluator
=================================
Real-time team composition analysis during champion select. Evaluates team
synergy, damage distribution, power curve, and counter-pick opportunities.

Dependencies: M888, M890
Reference: dota2bot-OpenHyperAI strategy evaluation
"""
from __future__ import annotations
import asyncio, collections, json, logging, math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum, auto

logger = logging.getLogger("M894.TeamCompositionEvaluator")


class DamageType(Enum):
    PHYSICAL = "physical"
    MAGIC = "magic"
    TRUE = "true"
    MIXED = "mixed"


class ChampionRole(Enum):
    TANK = "tank"
    FIGHTER = "fighter"
    ASSASSIN = "assassin"
    MAGE = "mage"
    MARKSMAN = "marksman"
    SUPPORT = "support"
    SPECIALIST = "specialist"


class GamePhase(Enum):
    EARLY = "early"      # 0-14 min
    MID = "mid"          # 14-25 min
    LATE = "late"        # 25+ min


@dataclass
class ChampionProfile:
    champion_id: int
    name: str = ""
    primary_role: ChampionRole = ChampionRole.FIGHTER
    secondary_role: Optional[ChampionRole] = None
    damage_type: DamageType = DamageType.MIXED
    power_curve: Dict[str, float] = field(default_factory=lambda: {"early": 5.0, "mid": 5.0, "late": 5.0})
    cc_score: float = 5.0         # crowd control 0-10
    mobility_score: float = 5.0
    tankiness_score: float = 5.0
    burst_score: float = 5.0
    sustain_dps_score: float = 5.0
    peel_score: float = 5.0
    engage_score: float = 5.0
    split_push_score: float = 5.0
    waveclear_score: float = 5.0
    synergy_tags: List[str] = field(default_factory=list)  # e.g. ["wombo_combo", "poke", "dive"]


@dataclass
class CompositionScore:
    team_label: str  # "blue" or "red"
    champions: List[int] = field(default_factory=list)
    overall_score: float = 50.0
    damage_balance: Dict[str, float] = field(default_factory=dict)  # physical/magic split
    role_coverage: Dict[str, bool] = field(default_factory=dict)
    power_by_phase: Dict[str, float] = field(default_factory=dict)
    synergy_score: float = 5.0
    engage_score: float = 5.0
    teamfight_score: float = 5.0
    split_push_score: float = 5.0
    poke_score: float = 5.0
    pick_score: float = 5.0
    tankiness: float = 5.0
    cc_total: float = 0.0
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    recommended_strategy: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team": self.team_label, "champions": self.champions,
            "overall": round(self.overall_score, 1),
            "damage": self.damage_balance, "roles": self.role_coverage,
            "power_curve": self.power_by_phase,
            "synergy": round(self.synergy_score, 1),
            "engage": round(self.engage_score, 1),
            "teamfight": round(self.teamfight_score, 1),
            "strengths": self.strengths, "weaknesses": self.weaknesses,
            "strategy": self.recommended_strategy,
        }


@dataclass
class MatchupAnalysis:
    blue_score: CompositionScore
    red_score: CompositionScore
    blue_advantage_phases: List[str] = field(default_factory=list)
    red_advantage_phases: List[str] = field(default_factory=list)
    key_matchups: List[Dict[str, Any]] = field(default_factory=list)
    draft_suggestions: List[str] = field(default_factory=list)
    predicted_winner: str = ""
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blue": self.blue_score.to_dict(), "red": self.red_score.to_dict(),
            "blue_strong_phases": self.blue_advantage_phases,
            "red_strong_phases": self.red_advantage_phases,
            "key_matchups": self.key_matchups,
            "suggestions": self.draft_suggestions,
            "prediction": self.predicted_winner,
            "confidence": round(self.confidence, 2),
        }


class ChampionDatabase:
    """In-memory champion stats database. In production, loaded from data files."""

    def __init__(self):
        self._champions: Dict[int, ChampionProfile] = {}
        self._load_defaults()

    def _load_defaults(self):
        """Load baseline champion data. Production: loaded from Riot Data Dragon."""
        defaults = [
            (1, "Annie", ChampionRole.MAGE, DamageType.MAGIC, 4, 7, 8, {"early": 5, "mid": 7, "late": 6}),
            (12, "Amumu", ChampionRole.TANK, DamageType.MAGIC, 9, 4, 3, {"early": 3, "mid": 6, "late": 8}),
            (51, "Caitlyn", ChampionRole.MARKSMAN, DamageType.PHYSICAL, 2, 5, 3, {"early": 6, "mid": 5, "late": 8}),
            (86, "Garen", ChampionRole.FIGHTER, DamageType.MIXED, 3, 3, 2, {"early": 5, "mid": 6, "late": 7}),
            (99, "Lux", ChampionRole.MAGE, DamageType.MAGIC, 7, 3, 8, {"early": 4, "mid": 7, "late": 7}),
            (157, "Yasuo", ChampionRole.FIGHTER, DamageType.PHYSICAL, 4, 8, 5, {"early": 4, "mid": 6, "late": 9}),
            (238, "Zed", ChampionRole.ASSASSIN, DamageType.PHYSICAL, 1, 9, 9, {"early": 5, "mid": 8, "late": 6}),
            (412, "Thresh", ChampionRole.SUPPORT, DamageType.MAGIC, 9, 4, 2, {"early": 5, "mid": 7, "late": 6}),
        ]
        for cid, name, role, dmg, cc, mob, burst, curve in defaults:
            self._champions[cid] = ChampionProfile(
                champion_id=cid, name=name, primary_role=role, damage_type=dmg,
                cc_score=cc, mobility_score=mob, burst_score=burst,
                power_curve={"early": curve["early"], "mid": curve["mid"], "late": curve["late"]},
            )

    def get(self, champion_id: int) -> Optional[ChampionProfile]:
        return self._champions.get(champion_id)

    def get_or_default(self, champion_id: int) -> ChampionProfile:
        return self._champions.get(champion_id, ChampionProfile(champion_id=champion_id))


class TeamCompositionEvaluator:
    """
    Evaluates team compositions in real-time during champion select.

    Integrates with M888 ChampSelectPhaseTracker to receive pick/ban updates
    and recalculates composition scores after each change.

    Scoring dimensions:
    - Damage balance (physical vs magic)
    - Role coverage (tank, damage, support)
    - Power curve by game phase
    - Synergy between champions
    - Teamfight, split-push, pick, poke capabilities
    """

    def __init__(self, champ_db: Optional[ChampionDatabase] = None):
        self._db = champ_db or ChampionDatabase()
        self._current_analysis: Optional[MatchupAnalysis] = None
        self._analysis_history: List[MatchupAnalysis] = []
        self._stats = {"evaluations": 0, "champions_analyzed": 0}
        logger.info("TeamCompositionEvaluator initialized")

    def evaluate(self, blue_champions: List[int], red_champions: List[int]) -> MatchupAnalysis:
        """Evaluate two team compositions."""
        blue_score = self._score_team(blue_champions, "blue")
        red_score = self._score_team(red_champions, "red")

        blue_advantages = []
        red_advantages = []
        for phase in ["early", "mid", "late"]:
            bp = blue_score.power_by_phase.get(phase, 5)
            rp = red_score.power_by_phase.get(phase, 5)
            if bp > rp + 0.5:
                blue_advantages.append(phase)
            elif rp > bp + 0.5:
                red_advantages.append(phase)

        # Predict winner based on overall scores
        diff = blue_score.overall_score - red_score.overall_score
        if abs(diff) < 2:
            predicted = "even"
            confidence = 0.5
        elif diff > 0:
            predicted = "blue"
            confidence = min(0.85, 0.5 + diff / 20)
        else:
            predicted = "red"
            confidence = min(0.85, 0.5 + abs(diff) / 20)

        suggestions = self._generate_suggestions(blue_score, red_score)

        analysis = MatchupAnalysis(
            blue_score=blue_score, red_score=red_score,
            blue_advantage_phases=blue_advantages, red_advantage_phases=red_advantages,
            draft_suggestions=suggestions, predicted_winner=predicted, confidence=confidence,
        )
        self._current_analysis = analysis
        self._analysis_history.append(analysis)
        self._stats["evaluations"] += 1
        return analysis

    def _score_team(self, champion_ids: List[int], label: str) -> CompositionScore:
        """Score a single team composition."""
        profiles = [self._db.get_or_default(cid) for cid in champion_ids if cid > 0]
        self._stats["champions_analyzed"] += len(profiles)
        if not profiles:
            return CompositionScore(team_label=label, champions=champion_ids)

        # Damage balance
        phys = sum(1 for p in profiles if p.damage_type in (DamageType.PHYSICAL, DamageType.MIXED))
        magic = sum(1 for p in profiles if p.damage_type in (DamageType.MAGIC, DamageType.MIXED))
        total_dmg = max(phys + magic, 1)
        damage_balance = {"physical": round(phys / total_dmg * 100, 1), "magic": round(magic / total_dmg * 100, 1)}

        # Role coverage
        roles_present = set(p.primary_role for p in profiles)
        role_coverage = {r.value: r in roles_present for r in ChampionRole}

        # Power curve by phase
        power = {}
        for phase in ["early", "mid", "late"]:
            values = [p.power_curve.get(phase, 5.0) for p in profiles]
            power[phase] = sum(values) / len(values)

        # Aggregate scores
        cc_total = sum(p.cc_score for p in profiles)
        engage = sum(p.engage_score for p in profiles) / len(profiles)
        tank = sum(p.tankiness_score for p in profiles) / len(profiles)
        teamfight = (cc_total / 10 + engage) / 2 * 2

        # Strengths/weaknesses
        strengths, weaknesses = [], []
        if cc_total >= 30:
            strengths.append("strong_cc")
        elif cc_total <= 15:
            weaknesses.append("low_cc")
        if damage_balance["physical"] > 75:
            weaknesses.append("too_physical")
        if damage_balance["magic"] > 75:
            weaknesses.append("too_magic")
        if not role_coverage.get("tank", False):
            weaknesses.append("no_frontline")
        if power["late"] >= 7:
            strengths.append("strong_scaling")
        if power["early"] >= 7:
            strengths.append("strong_early")

        overall = (power["early"] + power["mid"] + power["late"]) / 3 * 10 + cc_total / 5
        strategy = "teamfight" if teamfight > 6 else ("split_push" if len(profiles) >= 2 else "balanced")

        return CompositionScore(
            team_label=label, champions=champion_ids,
            overall_score=round(overall, 1), damage_balance=damage_balance,
            role_coverage=role_coverage, power_by_phase={k: round(v, 1) for k, v in power.items()},
            engage_score=round(engage, 1), teamfight_score=round(teamfight, 1),
            tankiness=round(tank, 1), cc_total=round(cc_total, 1),
            strengths=strengths, weaknesses=weaknesses, recommended_strategy=strategy,
        )

    def _generate_suggestions(self, blue: CompositionScore, red: CompositionScore) -> List[str]:
        suggestions = []
        if "no_frontline" in blue.weaknesses:
            suggestions.append("Blue needs a tanky champion for frontline")
        if "too_physical" in blue.weaknesses:
            suggestions.append("Blue should add magic damage")
        if blue.power_by_phase.get("early", 5) < red.power_by_phase.get("early", 5):
            suggestions.append("Blue should play safe early, scale to mid/late")
        if blue.cc_total < red.cc_total - 10:
            suggestions.append("Blue lacks CC — consider pick with crowd control")
        return suggestions

    def get_current(self) -> Optional[MatchupAnalysis]:
        return self._current_analysis

    def export_stats(self) -> Dict[str, Any]:
        return {"evaluator_stats": self._stats}



# ---------------------------------------------------------------------------
# Extended TeamCompositionEvaluator utilities
# ---------------------------------------------------------------------------

class CounterPickSuggester:
    """Suggests counter-picks based on enemy draft."""

    COUNTER_MAP = {
        # assassin_id: [counter_ids]
        238: [1, 12],     # Zed countered by Annie, Amumu
        157: [99, 12],    # Yasuo countered by Lux, Amumu
        86: [51, 99],     # Garen countered by Caitlyn, Lux
    }

    @classmethod
    def suggest_counters(cls, enemy_champion_id: int) -> List[int]:
        return cls.COUNTER_MAP.get(enemy_champion_id, [])

    @classmethod
    def suggest_for_team(cls, enemy_ids: List[int]) -> Dict[int, List[int]]:
        result = {}
        for eid in enemy_ids:
            counters = cls.suggest_counters(eid)
            if counters:
                result[eid] = counters
        return result


class BanSuggester:
    """Suggests bans based on opponent history and current meta."""

    def __init__(self, analyzer=None):
        self._analyzer = analyzer
        self._meta_bans = [238, 157, 412]  # default meta bans

    def suggest_bans(self, enemy_puuids: Optional[List[str]] = None,
                     max_bans: int = 5) -> List[Dict[str, Any]]:
        suggestions = []

        # Priority 1: opponent one-tricks
        if self._analyzer and enemy_puuids:
            for puuid in enemy_puuids:
                profile = self._analyzer.get_opponent_profile(puuid)
                if profile and profile.champion_pool:
                    top = profile.champion_pool[0]
                    if top.games_played >= 5 and top.winrate >= 60:
                        suggestions.append({
                            "champion_id": top.champion_id,
                            "reason": f"One-trick ({top.winrate:.0f}% WR, {top.games_played} games)",
                            "priority": 1,
                        })

        # Priority 2: meta bans
        for cid in self._meta_bans:
            if not any(s["champion_id"] == cid for s in suggestions):
                suggestions.append({
                    "champion_id": cid,
                    "reason": "Meta ban",
                    "priority": 2,
                })

        suggestions.sort(key=lambda s: s["priority"])
        return suggestions[:max_bans]


class SynergyCalculator:
    """Calculates synergy scores between champion pairs."""

    SYNERGY_PAIRS = {
        (12, 157): 9.0,   # Amumu + Yasuo (ult combo)
        (1, 12): 8.0,     # Annie + Amumu (AoE stun combo)
        (99, 51): 7.0,    # Lux + Caitlyn (root + trap)
        (412, 51): 7.5,   # Thresh + Caitlyn (hook + trap)
    }

    @classmethod
    def compute_synergy(cls, champion_ids: List[int]) -> float:
        if len(champion_ids) < 2:
            return 5.0
        total_synergy = 0.0
        pairs = 0
        for i in range(len(champion_ids)):
            for j in range(i + 1, len(champion_ids)):
                pair = tuple(sorted([champion_ids[i], champion_ids[j]]))
                synergy = cls.SYNERGY_PAIRS.get(pair, 5.0)
                total_synergy += synergy
                pairs += 1
        return total_synergy / max(pairs, 1)

    @classmethod
    def find_best_addition(cls, team: List[int], candidates: List[int]) -> List[Tuple[int, float]]:
        """Rank candidates by synergy with existing team."""
        results = []
        for cid in candidates:
            extended = team + [cid]
            syn = cls.compute_synergy(extended)
            results.append((cid, syn))
        results.sort(key=lambda x: x[1], reverse=True)
        return results


class DraftPhaseAdvisor:
    """Provides pick/ban suggestions during each phase of the draft."""

    def __init__(self, evaluator: TeamCompositionEvaluator,
                 counter_picker: Optional[CounterPickSuggester] = None,
                 ban_suggester: Optional[BanSuggester] = None):
        self._evaluator = evaluator
        self._counter = counter_picker or CounterPickSuggester()
        self._ban = ban_suggester or BanSuggester()

    def advise(self, phase: str, blue: List[int], red: List[int],
               bans: List[int]) -> Dict[str, Any]:
        analysis = self._evaluator.evaluate(blue, red)
        advice = {
            "phase": phase,
            "current_analysis": analysis.to_dict(),
        }

        if phase == "ban":
            advice["ban_suggestions"] = self._ban.suggest_bans()
        elif phase == "pick":
            if red:
                counters = self._counter.suggest_for_team(red)
                advice["counter_picks"] = counters
            advice["synergy_score"] = SynergyCalculator.compute_synergy(blue)
            advice["weaknesses_to_address"] = analysis.blue_score.weaknesses

        return advice



# ---------------------------------------------------------------------------
# Extended TeamCompositionEvaluator utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class TeamCompositionEvaluatorMetrics:
    """Collects performance metrics for TeamCompositionEvaluator."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class TeamCompositionEvaluatorSerializer:
    """Serialization utilities for TeamCompositionEvaluator state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class TeamCompositionEvaluatorDiagnostics:
    """Diagnostic tools for TeamCompositionEvaluator troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "TeamCompositionEvaluator",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class TeamCompositionEvaluatorEventLogger:
    """Structured event logger for TeamCompositionEvaluator with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)

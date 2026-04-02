"""
DraftAnalyzer — Champion draft composition analysis and recommendation.
========================================================================

Evaluates champion select picks/bans with synergy matrices, counter
matchup tables, and team composition scoring to provide real-time
draft advice during champion select phase.

Architecture position:
    modules/prediction/draft/draft_analyzer.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (champ select phase data)
    ├─ Reads: /lol/events (pick/ban events)
    ├─ Publishes: /lol/draft_advice (DraftAdvice)
    └─ Consumed by: modules/planning/strategy/lane_advisor.py

Apollo reference:
    modules/prediction/evaluator/ — scenario evaluation
    modules/planning/scenarios/ — multi-scenario scoring

Design notes:
    - Synergy matrix: pairwise champion synergy scores [-1, +1]
    - Counter matrix: pairwise matchup advantage scores
    - Composition archetypes: teamfight, poke, pick, split, siege
    - Role balance check: ensures team has all 5 roles covered
    - Win-rate integration from historical match data
    - Ban recommendation based on enemy pattern + own weakness
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger

logger = get_logger("draft_analyzer")

# ─── Constants ───────────────────────────────────────────────────────────────

_DRAFT_INTERVAL_MS = 1000.0  # 1Hz during champ select
_MAX_RECOMMENDATIONS = 5
_SYNERGY_WEIGHT = 0.25
_COUNTER_WEIGHT = 0.30
_WINRATE_WEIGHT = 0.25
_COMP_WEIGHT = 0.20
_MIN_GAMES_THRESHOLD = 50


class Role(Enum):
    """Standard LoL roles."""
    TOP = auto()
    JUNGLE = auto()
    MID = auto()
    ADC = auto()
    SUPPORT = auto()
    UNKNOWN = auto()


class CompArchetype(Enum):
    """Team composition archetypes."""
    TEAMFIGHT = auto()    # AOE, engage
    POKE = auto()         # Long-range harass
    PICK = auto()         # Single-target catch
    SPLIT_PUSH = auto()   # 1-3-1 or 1-4
    SIEGE = auto()        # Tower pressure
    PROTECT = auto()      # Protect-the-carry
    BALANCED = auto()     # No dominant style


class DraftPhase(Enum):
    """Draft phases for ban/pick order."""
    BAN_PHASE_1 = auto()
    PICK_PHASE_1 = auto()
    BAN_PHASE_2 = auto()
    PICK_PHASE_2 = auto()
    COMPLETE = auto()


@dataclass
class ChampionProfile:
    """Static champion data for draft evaluation."""
    champion_id: int
    name: str
    roles: Set[Role] = field(default_factory=set)
    archetypes: Set[CompArchetype] = field(default_factory=set)
    base_winrate: float = 0.50
    pick_rate: float = 0.05
    ban_rate: float = 0.05
    damage_type: str = "mixed"  # "physical", "magic", "mixed"
    cc_score: float = 0.0       # crowd control rating [0, 10]
    waveclear_score: float = 0.0
    mobility_score: float = 0.0
    scaling_score: float = 0.0   # early vs late [0=early, 10=late]
    engage_score: float = 0.0


@dataclass
class MatchupData:
    """Pairwise champion matchup statistics."""
    champion_a_id: int
    champion_b_id: int
    winrate_a: float = 0.50
    games_count: int = 0
    advantage_score: float = 0.0  # [-1, +1] positive = A favored


@dataclass
class SynergyData:
    """Pairwise champion synergy statistics."""
    champion_a_id: int
    champion_b_id: int
    combined_winrate: float = 0.50
    games_count: int = 0
    synergy_score: float = 0.0  # [-1, +1]


@dataclass
class DraftState:
    """Current state of champion select."""
    phase: DraftPhase = DraftPhase.BAN_PHASE_1
    ally_bans: List[int] = field(default_factory=list)
    enemy_bans: List[int] = field(default_factory=list)
    ally_picks: Dict[Role, int] = field(default_factory=dict)
    enemy_picks: Dict[Role, int] = field(default_factory=dict)
    ally_hover: Optional[int] = None
    available_champions: Set[int] = field(default_factory=set)


@dataclass
class DraftRecommendation:
    """A single champion recommendation with reasoning."""
    champion_id: int
    champion_name: str
    role: Role
    score: float
    synergy_score: float = 0.0
    counter_score: float = 0.0
    winrate_score: float = 0.0
    comp_score: float = 0.0
    reasoning: str = ""


@dataclass
class DraftAdvice:
    """Published draft advice for the current state."""
    timestamp_ns: int
    phase: DraftPhase
    recommendations: List[DraftRecommendation]
    ban_suggestions: List[Tuple[int, str, str]] = field(default_factory=list)
    team_comp_archetype: CompArchetype = CompArchetype.BALANCED
    damage_balance: str = "balanced"
    comp_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase.name,
            "recommendations": [
                {
                    "champion": r.champion_name,
                    "role": r.role.name,
                    "score": round(r.score, 3),
                    "reasoning": r.reasoning,
                }
                for r in self.recommendations
            ],
            "ban_suggestions": [
                {"champion_id": b[0], "name": b[1], "reason": b[2]}
                for b in self.ban_suggestions
            ],
            "archetype": self.team_comp_archetype.name,
            "damage_balance": self.damage_balance,
            "warnings": self.comp_warnings,
        }


class ChampionDatabase:
    """In-memory champion profile and matchup database.

    In production, this would be loaded from DDragon + historical
    match data. Here we provide the interface and a baseline dataset.
    """

    def __init__(self) -> None:
        self._profiles: Dict[int, ChampionProfile] = {}
        self._matchups: Dict[Tuple[int, int], MatchupData] = {}
        self._synergies: Dict[Tuple[int, int], SynergyData] = {}

    def add_profile(self, profile: ChampionProfile) -> None:
        self._profiles[profile.champion_id] = profile

    def get_profile(self, champion_id: int) -> Optional[ChampionProfile]:
        return self._profiles.get(champion_id)

    def add_matchup(self, matchup: MatchupData) -> None:
        key = (min(matchup.champion_a_id, matchup.champion_b_id),
               max(matchup.champion_a_id, matchup.champion_b_id))
        self._matchups[key] = matchup

    def get_matchup(self, champ_a: int, champ_b: int) -> Optional[MatchupData]:
        key = (min(champ_a, champ_b), max(champ_a, champ_b))
        return self._matchups.get(key)

    def add_synergy(self, synergy: SynergyData) -> None:
        key = (min(synergy.champion_a_id, synergy.champion_b_id),
               max(synergy.champion_a_id, synergy.champion_b_id))
        self._synergies[key] = synergy

    def get_synergy(self, champ_a: int, champ_b: int) -> Optional[SynergyData]:
        key = (min(champ_a, champ_b), max(champ_a, champ_b))
        return self._synergies.get(key)

    def champions_for_role(self, role: Role) -> List[ChampionProfile]:
        return [p for p in self._profiles.values() if role in p.roles]

    @property
    def all_champion_ids(self) -> Set[int]:
        return set(self._profiles.keys())


class CompAnalyzer:
    """Analyzes team composition quality and archetype."""

    def __init__(self, db: ChampionDatabase) -> None:
        self._db = db

    def classify_archetype(
        self, picks: Dict[Role, int],
    ) -> CompArchetype:
        """Classify the dominant archetype of a team comp."""
        if not picks:
            return CompArchetype.BALANCED

        archetype_scores: Dict[CompArchetype, float] = defaultdict(float)

        for role, champ_id in picks.items():
            profile = self._db.get_profile(champ_id)
            if profile:
                for arch in profile.archetypes:
                    archetype_scores[arch] += 1.0

        if not archetype_scores:
            return CompArchetype.BALANCED

        best = max(archetype_scores, key=archetype_scores.get)
        if archetype_scores[best] >= 3:
            return best
        return CompArchetype.BALANCED

    def evaluate_damage_balance(
        self, picks: Dict[Role, int],
    ) -> str:
        """Check if damage types are balanced."""
        physical = 0
        magical = 0

        for champ_id in picks.values():
            profile = self._db.get_profile(champ_id)
            if not profile:
                continue
            if profile.damage_type == "physical":
                physical += 1
            elif profile.damage_type == "magic":
                magical += 1
            else:
                physical += 0.5
                magical += 0.5

        total = physical + magical
        if total == 0:
            return "unknown"
        if physical / total > 0.75:
            return "heavy_ad"
        if magical / total > 0.75:
            return "heavy_ap"
        return "balanced"

    def check_comp_warnings(
        self, picks: Dict[Role, int],
    ) -> List[str]:
        """Generate warnings about team composition weaknesses."""
        warnings = []

        if len(picks) < 2:
            return warnings

        # Check role coverage
        missing_roles = set(Role) - {Role.UNKNOWN} - set(picks.keys())
        if missing_roles:
            role_names = [r.name for r in missing_roles]
            warnings.append(f"Missing roles: {', '.join(role_names)}")

        # Check damage balance
        dmg = self.evaluate_damage_balance(picks)
        if dmg == "heavy_ad":
            warnings.append("Heavy physical damage — enemy can stack armor")
        elif dmg == "heavy_ap":
            warnings.append("Heavy magic damage — enemy can stack MR")

        # Check CC
        total_cc = 0.0
        for champ_id in picks.values():
            profile = self._db.get_profile(champ_id)
            if profile:
                total_cc += profile.cc_score
        if total_cc < 10.0 and len(picks) >= 3:
            warnings.append("Low crowd control — may struggle to engage")

        # Check engage
        total_engage = 0.0
        for champ_id in picks.values():
            profile = self._db.get_profile(champ_id)
            if profile:
                total_engage += profile.engage_score
        if total_engage < 8.0 and len(picks) >= 3:
            warnings.append("Weak engage tools — consider engage champion")

        return warnings

    def compute_synergy_total(
        self, picks: Dict[Role, int],
    ) -> float:
        """Compute total pairwise synergy score."""
        total = 0.0
        champ_ids = list(picks.values())
        count = 0

        for i in range(len(champ_ids)):
            for j in range(i + 1, len(champ_ids)):
                syn = self._db.get_synergy(champ_ids[i], champ_ids[j])
                if syn and syn.games_count >= _MIN_GAMES_THRESHOLD:
                    total += syn.synergy_score
                    count += 1

        return total / max(count, 1)


class DraftAnalyzer(TimerComponent):
    """Champion draft analysis and recommendation engine.

    Each ``Proc()`` cycle during champion select:
    1. Reads current draft state from ``/lol/game_state``
    2. Evaluates available champions against current picks
    3. Scores candidates on synergy, counters, winrate, composition
    4. Publishes DraftAdvice on ``/lol/draft_advice``
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="draft_analyzer",
                interval_ms=_DRAFT_INTERVAL_MS,
                warn_threshold_ms=800.0,
            ),
        )
        self.node = CyberNode("draft_analyzer")
        self._db = ChampionDatabase()
        self._comp_analyzer = CompAnalyzer(self._db)

        self._game_state_reader: Optional[Reader] = None
        self._events_reader: Optional[Reader] = None
        self._draft_writer: Optional[Writer] = None

        self._draft_state = DraftState()
        self._last_advice: Optional[DraftAdvice] = None
        self._in_champ_select: bool = False

    def Init(self) -> bool:
        try:
            self._game_state_reader = self.node.create_reader(
                "/lol/game_state", queue_size=4
            )
            self._events_reader = self.node.create_reader(
                "/lol/events", queue_size=32
            )
            self._draft_writer = self.node.create_writer("/lol/draft_advice")
            logger.info("DraftAnalyzer initialized")
            return True
        except Exception as exc:
            logger.error("DraftAnalyzer Init failed: %s", exc)
            return False

    def Proc(self) -> bool:
        try:
            game_state = (
                self._game_state_reader.get_latest()
                if self._game_state_reader else None
            )

            if not game_state:
                return True

            # Only active during champ select
            phase = getattr(game_state, "phase", None)
            phase_str = (
                phase.name if hasattr(phase, "name") else str(phase)
            ).upper()

            if "CHAMP_SELECT" not in phase_str:
                self._in_champ_select = False
                return True

            self._in_champ_select = True
            self._update_draft_state(game_state)
            advice = self._generate_advice()
            self._last_advice = advice

            if self._draft_writer:
                self._draft_writer.write(advice)

            return True
        except Exception as exc:
            logger.error("DraftAnalyzer Proc error: %s", exc)
            return False

    def _update_draft_state(self, game_state: Any) -> None:
        """Extract draft state from game snapshot."""
        # Extract picks and bans from game state
        champ_select = getattr(game_state, "champ_select", None)
        if not champ_select:
            return

        ally_picks = getattr(champ_select, "ally_picks", {})
        enemy_picks = getattr(champ_select, "enemy_picks", {})
        ally_bans = getattr(champ_select, "ally_bans", [])
        enemy_bans = getattr(champ_select, "enemy_bans", [])

        self._draft_state.ally_picks = (
            ally_picks if isinstance(ally_picks, dict)
            else {}
        )
        self._draft_state.enemy_picks = (
            enemy_picks if isinstance(enemy_picks, dict)
            else {}
        )
        self._draft_state.ally_bans = list(ally_bans)
        self._draft_state.enemy_bans = list(enemy_bans)

        # Compute available champions
        banned = set(ally_bans) | set(enemy_bans)
        picked = set(ally_picks.values()) | set(enemy_picks.values())
        self._draft_state.available_champions = (
            self._db.all_champion_ids - banned - picked
        )

    def _generate_advice(self) -> DraftAdvice:
        """Generate champion recommendations for current draft state."""
        state = self._draft_state

        # Determine which roles still need filling
        filled_roles = set(state.ally_picks.keys())
        needed_roles = {Role.TOP, Role.JUNGLE, Role.MID, Role.ADC, Role.SUPPORT} - filled_roles

        recommendations = []
        for role in needed_roles:
            candidates = self._db.champions_for_role(role)
            scored = []

            for profile in candidates:
                if profile.champion_id not in state.available_champions:
                    continue

                score_breakdown = self._score_candidate(
                    profile, role, state
                )
                scored.append(score_breakdown)

            scored.sort(key=lambda r: r.score, reverse=True)
            recommendations.extend(scored[:2])

        recommendations.sort(key=lambda r: r.score, reverse=True)
        recommendations = recommendations[:_MAX_RECOMMENDATIONS]

        # Ban suggestions
        ban_suggestions = self._suggest_bans(state)

        # Comp analysis
        archetype = self._comp_analyzer.classify_archetype(state.ally_picks)
        damage_balance = self._comp_analyzer.evaluate_damage_balance(
            state.ally_picks
        )
        warnings = self._comp_analyzer.check_comp_warnings(state.ally_picks)

        return DraftAdvice(
            timestamp_ns=time.time_ns(),
            phase=state.phase,
            recommendations=recommendations,
            ban_suggestions=ban_suggestions,
            team_comp_archetype=archetype,
            damage_balance=damage_balance,
            comp_warnings=warnings,
        )

    def _score_candidate(
        self,
        profile: ChampionProfile,
        role: Role,
        state: DraftState,
    ) -> DraftRecommendation:
        """Score a champion candidate for a given role."""
        # Synergy with existing ally picks
        synergy = 0.0
        syn_count = 0
        for ally_id in state.ally_picks.values():
            syn_data = self._db.get_synergy(profile.champion_id, ally_id)
            if syn_data and syn_data.games_count >= _MIN_GAMES_THRESHOLD:
                synergy += syn_data.synergy_score
                syn_count += 1
        synergy_score = synergy / max(syn_count, 1)

        # Counter score against enemy picks
        counter = 0.0
        cnt_count = 0
        for enemy_id in state.enemy_picks.values():
            matchup = self._db.get_matchup(profile.champion_id, enemy_id)
            if matchup and matchup.games_count >= _MIN_GAMES_THRESHOLD:
                if matchup.champion_a_id == profile.champion_id:
                    counter += matchup.advantage_score
                else:
                    counter -= matchup.advantage_score
                cnt_count += 1
        counter_score = counter / max(cnt_count, 1)

        # Base winrate score
        winrate_score = (profile.base_winrate - 0.50) * 2.0

        # Composition fit
        comp_score = self._evaluate_comp_fit(profile, state)

        # Weighted total
        total = (
            _SYNERGY_WEIGHT * synergy_score
            + _COUNTER_WEIGHT * counter_score
            + _WINRATE_WEIGHT * winrate_score
            + _COMP_WEIGHT * comp_score
        )

        reasons = []
        if synergy_score > 0.1:
            reasons.append("good synergy with team")
        if counter_score > 0.1:
            reasons.append("counters enemy picks")
        if winrate_score > 0.05:
            reasons.append("high winrate")
        if comp_score > 0.1:
            reasons.append("fills comp need")

        return DraftRecommendation(
            champion_id=profile.champion_id,
            champion_name=profile.name,
            role=role,
            score=total,
            synergy_score=synergy_score,
            counter_score=counter_score,
            winrate_score=winrate_score,
            comp_score=comp_score,
            reasoning="; ".join(reasons) if reasons else "solid pick",
        )

    def _evaluate_comp_fit(
        self, profile: ChampionProfile, state: DraftState,
    ) -> float:
        """Evaluate how well a champion fills composition gaps."""
        score = 0.0

        # Check damage balance
        dmg = self._comp_analyzer.evaluate_damage_balance(state.ally_picks)
        if dmg == "heavy_ad" and profile.damage_type == "magic":
            score += 0.3
        elif dmg == "heavy_ap" and profile.damage_type == "physical":
            score += 0.3

        # Check CC needs
        total_cc = sum(
            (self._db.get_profile(cid).cc_score
             if self._db.get_profile(cid) else 0)
            for cid in state.ally_picks.values()
        )
        if total_cc < 10.0 and profile.cc_score >= 5.0:
            score += 0.2

        # Check engage needs
        total_engage = sum(
            (self._db.get_profile(cid).engage_score
             if self._db.get_profile(cid) else 0)
            for cid in state.ally_picks.values()
        )
        if total_engage < 8.0 and profile.engage_score >= 5.0:
            score += 0.2

        return score

    def _suggest_bans(
        self, state: DraftState,
    ) -> List[Tuple[int, str, str]]:
        """Suggest champions to ban."""
        suggestions = []
        already_banned = set(state.ally_bans) | set(state.enemy_bans)

        # Ban high winrate + high pickrate champions
        for champ_id in self._db.all_champion_ids:
            if champ_id in already_banned:
                continue
            profile = self._db.get_profile(champ_id)
            if not profile:
                continue

            threat = (
                profile.base_winrate * 0.4
                + profile.pick_rate * 0.3
                + profile.ban_rate * 0.3
            )
            if threat > 0.3:
                suggestions.append(
                    (champ_id, profile.name, f"WR={profile.base_winrate:.0%}")
                )

        suggestions.sort(key=lambda x: x[0], reverse=True)
        return suggestions[:3]

    def get_advice(self) -> Optional[DraftAdvice]:
        return self._last_advice

    def status(self) -> Dict[str, Any]:
        base = super().status()
        base.update({
            "in_champ_select": self._in_champ_select,
            "ally_picks": len(self._draft_state.ally_picks),
            "enemy_picks": len(self._draft_state.enemy_picks),
            "available": len(self._draft_state.available_champions),
        })
        return base

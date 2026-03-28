"""
Team Composition Analyzer - Evaluates team comp strengths/weaknesses.

Analyzes damage profiles, engage/disengage capability, and scaling
to recommend team-wide strategy adjustments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from lol_fiddler_agent.network.live_client_data import LiveGameState, Player, Team
from lol_fiddler_agent.models.champion_db import ChampionDatabase, ChampionRole, DamageType
from lol_fiddler_agent.agents.strategy_agent import ActionType, StrategicAdvice, StrategyEvaluator, Urgency

logger = logging.getLogger(__name__)


class TeamStrength(str, Enum):
    ENGAGE = "engage"
    DISENGAGE = "disengage"
    POKE = "poke"
    SPLIT_PUSH = "split_push"
    TEAMFIGHT = "teamfight"
    PICK = "pick"
    SCALING = "scaling"
    EARLY_GAME = "early_game"


@dataclass
class CompositionProfile:
    """Aggregate profile of a team's composition."""
    team: Team
    physical_damage_count: int = 0
    magic_damage_count: int = 0
    tank_count: int = 0
    assassin_count: int = 0
    marksman_count: int = 0
    mage_count: int = 0
    support_count: int = 0
    fighter_count: int = 0
    ranged_count: int = 0
    melee_count: int = 0
    avg_difficulty: float = 5.0
    strengths: list[TeamStrength] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)

    @property
    def has_frontline(self) -> bool:
        return self.tank_count + self.fighter_count >= 1

    @property
    def has_carry(self) -> bool:
        return self.marksman_count + self.mage_count >= 1

    @property
    def damage_profile(self) -> str:
        if self.physical_damage_count >= 4:
            return "heavily_physical"
        elif self.magic_damage_count >= 4:
            return "heavily_magic"
        elif abs(self.physical_damage_count - self.magic_damage_count) <= 1:
            return "balanced"
        elif self.physical_damage_count > self.magic_damage_count:
            return "physical_heavy"
        return "magic_heavy"


class TeamCompAnalyzer(StrategyEvaluator):
    """Analyzes team compositions and suggests macro strategy."""

    def __init__(self, champion_db: Optional[ChampionDatabase] = None) -> None:
        self._db = champion_db or ChampionDatabase()
        if not self._db.is_loaded:
            self._db.load_defaults()

    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []
        if not state.game_data:
            return advice

        my_team = state.get_my_team()
        enemy_team_enum = Team.CHAOS if my_team == Team.ORDER else Team.ORDER

        ally_profile = self._build_profile(state, my_team)
        enemy_profile = self._build_profile(state, enemy_team_enum)

        # Damage balance advice
        damage_advice = self._evaluate_damage_balance(ally_profile, enemy_profile)
        if damage_advice:
            advice.extend(damage_advice)

        # Composition synergy advice
        synergy_advice = self._evaluate_synergy(ally_profile, enemy_profile, state)
        if synergy_advice:
            advice.extend(synergy_advice)

        return advice

    def _build_profile(self, state: LiveGameState, team: Team) -> CompositionProfile:
        players = [p for p in state.all_players if p.team_enum == team]
        profile = CompositionProfile(team=team)

        difficulties: list[int] = []
        for p in players:
            champ = self._db.get(p.champion_name)
            if champ:
                if champ.primary_damage == DamageType.PHYSICAL:
                    profile.physical_damage_count += 1
                elif champ.primary_damage == DamageType.MAGIC:
                    profile.magic_damage_count += 1
                else:
                    profile.physical_damage_count += 1  # mixed counts as physical

                for role in champ.roles:
                    if role == ChampionRole.TANK:
                        profile.tank_count += 1
                    elif role == ChampionRole.ASSASSIN:
                        profile.assassin_count += 1
                    elif role == ChampionRole.MARKSMAN:
                        profile.marksman_count += 1
                    elif role == ChampionRole.MAGE:
                        profile.mage_count += 1
                    elif role == ChampionRole.SUPPORT:
                        profile.support_count += 1
                    elif role == ChampionRole.FIGHTER:
                        profile.fighter_count += 1

                if champ.is_ranged:
                    profile.ranged_count += 1
                else:
                    profile.melee_count += 1

                difficulties.append(champ.difficulty)
            else:
                profile.physical_damage_count += 1

        if difficulties:
            profile.avg_difficulty = sum(difficulties) / len(difficulties)

        # Determine strengths
        if profile.assassin_count >= 2:
            profile.strengths.append(TeamStrength.PICK)
        if profile.tank_count >= 2:
            profile.strengths.append(TeamStrength.TEAMFIGHT)
            profile.strengths.append(TeamStrength.ENGAGE)
        if profile.has_frontline and profile.has_carry:
            profile.strengths.append(TeamStrength.TEAMFIGHT)
        if profile.fighter_count >= 2:
            profile.strengths.append(TeamStrength.SPLIT_PUSH)

        # Determine weaknesses
        if not profile.has_frontline:
            profile.weaknesses.append("No frontline - vulnerable to dive")
        if profile.damage_profile == "heavily_physical":
            profile.weaknesses.append("Mostly physical - enemy can stack armor")
        elif profile.damage_profile == "heavily_magic":
            profile.weaknesses.append("Mostly magic - enemy can stack MR")

        return profile

    def _evaluate_damage_balance(
        self, ally: CompositionProfile, enemy: CompositionProfile,
    ) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []

        if ally.damage_profile == "heavily_physical":
            advice.append(StrategicAdvice(
                action=ActionType.TRADE,
                urgency=Urgency.LOW,
                reason="Team mostly physical damage - enemies may stack armor. Build armor pen",
                confidence=0.65,
            ))
        elif ally.damage_profile == "heavily_magic":
            advice.append(StrategicAdvice(
                action=ActionType.TRADE,
                urgency=Urgency.LOW,
                reason="Team mostly magic damage - enemies may stack MR. Build magic pen",
                confidence=0.65,
            ))

        if enemy.damage_profile == "heavily_physical" and ally.tank_count > 0:
            advice.append(StrategicAdvice(
                action=ActionType.DEFEND,
                urgency=Urgency.LOW,
                reason="Enemy mostly physical - armor stacking very effective",
                confidence=0.70,
            ))

        return advice

    def _evaluate_synergy(
        self, ally: CompositionProfile, enemy: CompositionProfile,
        state: LiveGameState,
    ) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []

        # Teamfight comp vs pick comp
        if TeamStrength.TEAMFIGHT in ally.strengths:
            advice.append(StrategicAdvice(
                action=ActionType.GROUP,
                urgency=Urgency.MEDIUM,
                reason="Strong teamfight comp - group for 5v5 fights",
                confidence=0.70,
            ))
        elif TeamStrength.SPLIT_PUSH in ally.strengths:
            advice.append(StrategicAdvice(
                action=ActionType.SPLIT,
                urgency=Urgency.MEDIUM,
                reason="Strong split-push comp - apply side lane pressure",
                confidence=0.65,
            ))
        elif TeamStrength.PICK in ally.strengths:
            advice.append(StrategicAdvice(
                action=ActionType.TRADE,
                urgency=Urgency.MEDIUM,
                reason="Pick comp - catch enemies out of position with vision control",
                confidence=0.65,
            ))

        return advice

    def get_profile(self, state: LiveGameState, team: Team) -> CompositionProfile:
        """Public method to get a team composition profile."""
        return self._build_profile(state, team)


# ── Evolution Integration (M273 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'team_comp'


class EvolvableTeamCompAnalyzer(TeamCompAnalyzer):
    """TeamCompAnalyzer with self-evolution callback hooks."""

    def __init__(self, champion_db=None) -> None:
        super().__init__(champion_db)
        self._evolution_callback = None

    @property
    def evolution_callback(self):
        return self._evolution_callback

    @evolution_callback.setter
    def evolution_callback(self, cb):
        self._evolution_callback = cb

    def _fire_evolution(self, data: dict) -> None:
        import time as _time
        data.setdefault('module', _EVOLUTION_KEY)
        data.setdefault('timestamp', _time.time())
        if self._evolution_callback:
            try:
                self._evolution_callback(data)
            except Exception:
                pass

    def to_training_annotation(self, **kwargs) -> dict:
        annotation = {'module': _EVOLUTION_KEY}
        annotation.update(kwargs)
        return annotation

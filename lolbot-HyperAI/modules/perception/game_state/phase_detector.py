"""
modules/perception/game_state/phase_detector.py — Game phase transition detector.
===================================================================================
Claude18 · Extends perception with phase-change event detection

Current code classifies phase purely from game_time thresholds (EARLY < 840s
< MID < 1500s < LATE). This misses tempo-based transitions: a game with
15 kills at 10min is effectively "mid game" regardless of clock.

Solution: Detect phase transitions from a combination of game time, kill
density, tower count, and objective events. Publish PhaseTransition events
so planning can adjust strategy immediately when the game tempo shifts.

File location: lolbot-HyperAI/modules/perception/game_state/phase_detector.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DetailedPhase(Enum):
    """Fine-grained game phase classification."""
    LOADING = auto()
    LANING = auto()         # 0-8min: isolated lane play
    EARLY_SKIRMISH = auto() # 8-14min OR when kill density triggers
    MID_GAME = auto()       # 14-25min: grouping, first objectives
    LATE_MID = auto()       # 25-30min: baron dances
    LATE_GAME = auto()      # 30+min: one fight decides it
    ENDING = auto()         # Nexus under attack
    POST_GAME = auto()


@dataclass
class PhaseTransition:
    """A detected phase transition event."""
    from_phase: DetailedPhase
    to_phase: DetailedPhase
    game_time: float
    trigger_reason: str
    confidence: float  # How certain we are about this transition

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_phase.name,
            "to": self.to_phase.name,
            "game_time": round(self.game_time, 1),
            "trigger": self.trigger_reason,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class PhaseContext:
    """Context signals for phase detection."""
    game_time: float = 0.0
    total_kills: int = 0
    towers_destroyed: int = 0
    dragons_killed: int = 0
    barons_killed: int = 0
    inhibitors_destroyed: int = 0
    recent_kills_2min: int = 0  # Kills in last 2 minutes
    ace_occurred: bool = False


class PhaseDetector:
    """Detects game phase transitions from multi-signal analysis.

    Uses both time-based and tempo-based signals to classify game
    phase more accurately than pure game_time thresholds.

    Apollo reference: perception/fusion/multi_sensor_fusion.cc
    fuses multiple sensor inputs to determine scene classification.

    Usage::
        detector = PhaseDetector()
        # Each perception tick:
        ctx = PhaseContext(game_time=..., total_kills=..., ...)
        transition = detector.update(ctx)
        if transition:
            publish_phase_change(transition)
    """

    # Time thresholds (base, can be overridden by tempo signals)
    _TIME_LANING_END = 480.0       # 8min
    _TIME_EARLY_SKIRMISH_END = 840.0  # 14min
    _TIME_MID_END = 1500.0         # 25min
    _TIME_LATE_MID_END = 1800.0    # 30min

    # Tempo thresholds that can trigger EARLY phase transitions
    _KILLS_FOR_SKIRMISH = 6       # 6+ total kills → skirmish phase
    _KILLS_2MIN_FOR_TEAMFIGHT = 4 # 4+ kills in 2min → teamfight phase
    _TOWERS_FOR_MID = 2           # 2+ towers → mid game territory
    _INHIBS_FOR_ENDING = 1        # Any inhibitor → ending phase

    def __init__(self) -> None:
        self._current_phase = DetailedPhase.LOADING
        self._transition_history: List[PhaseTransition] = []
        self._update_count: int = 0

    @property
    def current_phase(self) -> DetailedPhase:
        return self._current_phase

    def update(self, ctx: PhaseContext) -> Optional[PhaseTransition]:
        """Update phase based on context. Returns transition if changed."""
        self._update_count += 1
        new_phase = self._classify(ctx)

        if new_phase == self._current_phase:
            return None

        # Phase can only advance forward (no regression)
        if new_phase.value <= self._current_phase.value:
            return None

        reason = self._explain_transition(ctx, new_phase)
        confidence = self._compute_confidence(ctx, new_phase)

        transition = PhaseTransition(
            from_phase=self._current_phase,
            to_phase=new_phase,
            game_time=ctx.game_time,
            trigger_reason=reason,
            confidence=confidence,
        )
        self._current_phase = new_phase
        self._transition_history.append(transition)

        logger.info(
            "Phase transition: %s → %s at %.0fs (%s, conf=%.2f)",
            transition.from_phase.name, transition.to_phase.name,
            ctx.game_time, reason, confidence,
        )
        return transition

    def _classify(self, ctx: PhaseContext) -> DetailedPhase:
        """Classify current phase from context signals."""
        gt = ctx.game_time

        if gt <= 0:
            return DetailedPhase.LOADING

        # Check ending signals first (they override everything)
        if ctx.inhibitors_destroyed >= self._INHIBS_FOR_ENDING:
            return DetailedPhase.ENDING

        # Time + tempo hybrid classification
        if gt < self._TIME_LANING_END:
            # Even in early game, high kill count → skirmish
            if ctx.total_kills >= self._KILLS_FOR_SKIRMISH:
                return DetailedPhase.EARLY_SKIRMISH
            return DetailedPhase.LANING

        if gt < self._TIME_EARLY_SKIRMISH_END:
            if ctx.towers_destroyed >= self._TOWERS_FOR_MID:
                return DetailedPhase.MID_GAME
            return DetailedPhase.EARLY_SKIRMISH

        if gt < self._TIME_MID_END:
            return DetailedPhase.MID_GAME

        if gt < self._TIME_LATE_MID_END:
            if ctx.barons_killed > 0:
                return DetailedPhase.LATE_GAME  # Baron = late game
            return DetailedPhase.LATE_MID

        return DetailedPhase.LATE_GAME

    def _explain_transition(
        self, ctx: PhaseContext, new_phase: DetailedPhase,
    ) -> str:
        """Human-readable reason for the phase transition."""
        reasons = {
            DetailedPhase.LANING: "Game started, laning phase",
            DetailedPhase.EARLY_SKIRMISH: (
                f"Skirmish phase: {ctx.total_kills} kills, "
                f"{ctx.towers_destroyed} towers"
            ),
            DetailedPhase.MID_GAME: (
                f"Mid game: {ctx.game_time/60:.0f}min, "
                f"{ctx.towers_destroyed} towers"
            ),
            DetailedPhase.LATE_MID: (
                f"Late-mid: {ctx.game_time/60:.0f}min, "
                f"baron dance phase"
            ),
            DetailedPhase.LATE_GAME: (
                f"Late game: {ctx.game_time/60:.0f}min"
                + (f", {ctx.barons_killed} barons" if ctx.barons_killed else "")
            ),
            DetailedPhase.ENDING: (
                f"Ending: {ctx.inhibitors_destroyed} inhibitors destroyed"
            ),
        }
        return reasons.get(new_phase, f"Transition to {new_phase.name}")

    def _compute_confidence(
        self, ctx: PhaseContext, new_phase: DetailedPhase,
    ) -> float:
        """Confidence in the phase classification."""
        # Pure time-based transitions are high confidence
        base = 0.8
        # Tempo-triggered transitions are slightly lower
        if (
            new_phase == DetailedPhase.EARLY_SKIRMISH
            and ctx.game_time < self._TIME_LANING_END
        ):
            base = 0.65  # Kill-triggered, less certain
        if new_phase == DetailedPhase.ENDING:
            base = 0.95  # Inhibitor destroyed = very certain

        return base

    @property
    def transition_history(self) -> List[PhaseTransition]:
        return list(self._transition_history)

    def stats(self) -> Dict[str, Any]:
        return {
            "current_phase": self._current_phase.name,
            "update_count": self._update_count,
            "transitions": len(self._transition_history),
            "history": [t.to_dict() for t in self._transition_history[-5:]],
        }

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


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended phase detector with sub-phase, tempo scoring, export
# ═══════════════════════════════════════════════════════════════════════════


class SubPhase(Enum):
    """Fine-grained sub-phases within each DetailedPhase.

    Claude20: Enables more granular strategy shifts within broad phases.
    """
    LANE_FARMING = auto()         # Pure CS focus
    LANE_TRADING = auto()         # Active trading windows
    FIRST_ROTATION = auto()       # First group movement
    SKIRMISH_RIVER = auto()       # River/scuttle fights
    TOWER_SIEGE = auto()          # Actively hitting towers
    BARON_DANCE = auto()          # Both teams posturing at baron
    DRAGON_FIGHT = auto()         # Dragon pit contest
    SPLIT_PRESSURE = auto()       # 1-3-1 or 4-1 split map pressure
    BASE_DEFENSE = auto()         # Defending inhibitors
    ALL_IN_PUSH = auto()          # All 5 pushing to end


@dataclass
class TempoScore:
    """Quantified game tempo measurement.

    Claude20: Measures how fast or slow the game is progressing
    relative to the expected pace. High tempo = lots of action.
    """
    kills_per_minute: float = 0.0
    objectives_per_10min: float = 0.0
    tower_plates_rate: float = 0.0
    tempo_rating: str = "normal"  # slow, normal, fast, chaotic

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kpm": round(self.kills_per_minute, 2),
            "obj_per_10": round(self.objectives_per_10min, 1),
            "tempo": self.tempo_rating,
        }


class PhaseDetectorV2(PhaseDetector):
    """Extended phase detector with sub-phases and tempo scoring.

    Claude20: Adds sub-phase detection, quantified tempo scoring,
    and phase prediction. All existing PhaseDetector logic preserved.

    Usage::
        detector = PhaseDetectorV2()
        ctx = PhaseContext(game_time=600, total_kills=12, ...)
        transition = detector.update(ctx)
        sub = detector.detect_sub_phase(ctx)
        tempo = detector.compute_tempo(ctx)
    """

    def __init__(self) -> None:
        super().__init__()
        self._sub_phase: SubPhase = SubPhase.LANE_FARMING
        self._tempo_history: List[TempoScore] = []
        self._prev_kills: int = 0
        self._prev_game_time: float = 0.0

    @property
    def sub_phase(self) -> SubPhase:
        return self._sub_phase

    def detect_sub_phase(self, ctx: PhaseContext) -> SubPhase:
        """Detect the current sub-phase within the broad phase.

        Claude20: Uses kill density, objective state, and tower count
        to determine what the game is actually doing right now.
        """
        phase = self.current_phase

        if phase == DetailedPhase.LANING:
            if ctx.recent_kills_2min >= 3:
                self._sub_phase = SubPhase.LANE_TRADING
            else:
                self._sub_phase = SubPhase.LANE_FARMING

        elif phase == DetailedPhase.EARLY_SKIRMISH:
            if ctx.towers_destroyed >= 1:
                self._sub_phase = SubPhase.FIRST_ROTATION
            else:
                self._sub_phase = SubPhase.SKIRMISH_RIVER

        elif phase in (DetailedPhase.MID_GAME, DetailedPhase.LATE_MID):
            if ctx.barons_killed > 0 or ctx.game_time > 1200:
                self._sub_phase = SubPhase.BARON_DANCE
            elif ctx.dragons_killed > 2:
                self._sub_phase = SubPhase.DRAGON_FIGHT
            else:
                self._sub_phase = SubPhase.TOWER_SIEGE

        elif phase == DetailedPhase.LATE_GAME:
            if ctx.inhibitors_destroyed > 0:
                self._sub_phase = SubPhase.ALL_IN_PUSH
            else:
                self._sub_phase = SubPhase.BARON_DANCE

        elif phase == DetailedPhase.ENDING:
            if ctx.ace_occurred:
                self._sub_phase = SubPhase.ALL_IN_PUSH
            else:
                self._sub_phase = SubPhase.BASE_DEFENSE

        return self._sub_phase

    def compute_tempo(self, ctx: PhaseContext) -> TempoScore:
        """Compute current game tempo score.

        Claude20: Quantifies how fast the game is developing.
        Used by prediction to weight time-sensitive features differently.
        """
        dt = ctx.game_time - self._prev_game_time if self._prev_game_time > 0 else 1.0
        dt = max(dt, 1.0)

        # Kills per minute (short window)
        kill_delta = ctx.total_kills - self._prev_kills
        kpm = (kill_delta / dt) * 60.0 if dt > 0 else 0.0

        # Overall KPM for the whole game
        overall_kpm = (ctx.total_kills / max(ctx.game_time, 1.0)) * 60.0

        # Objectives per 10 minutes
        total_objectives = ctx.dragons_killed + ctx.barons_killed + ctx.towers_destroyed
        obj_per_10 = (total_objectives / max(ctx.game_time, 1.0)) * 600.0

        # Classify tempo
        if overall_kpm > 1.5 and obj_per_10 > 3.0:
            rating = "chaotic"
        elif overall_kpm > 0.8 or obj_per_10 > 2.0:
            rating = "fast"
        elif overall_kpm < 0.3 and obj_per_10 < 1.0:
            rating = "slow"
        else:
            rating = "normal"

        score = TempoScore(
            kills_per_minute=overall_kpm,
            objectives_per_10min=obj_per_10,
            tempo_rating=rating,
        )

        self._tempo_history.append(score)
        self._prev_kills = ctx.total_kills
        self._prev_game_time = ctx.game_time
        return score

    def predict_next_phase(self, ctx: PhaseContext) -> Optional[str]:
        """Predict the next phase transition.

        Claude20: Based on current trajectory, estimate when the next
        phase transition will occur. Returns phase name or None.
        """
        current = self.current_phase
        if current == DetailedPhase.LANING:
            if ctx.total_kills >= 4:
                return DetailedPhase.EARLY_SKIRMISH.name
            if ctx.game_time > 400:
                return DetailedPhase.EARLY_SKIRMISH.name
        elif current == DetailedPhase.EARLY_SKIRMISH:
            if ctx.towers_destroyed >= 1 or ctx.game_time > 700:
                return DetailedPhase.MID_GAME.name
        elif current == DetailedPhase.MID_GAME:
            if ctx.game_time > 1400:
                return DetailedPhase.LATE_MID.name
        return None

    def extended_stats(self) -> Dict[str, Any]:
        base = self.stats()
        base["sub_phase"] = self._sub_phase.name
        if self._tempo_history:
            latest_tempo = self._tempo_history[-1]
            base["tempo"] = latest_tempo.to_dict()
        base["tempo_history_size"] = len(self._tempo_history)
        return base

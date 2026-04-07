"""
modules/perception/fusion/momentum_tracker.py — Team momentum state machine.
==============================================================================
Claude19 · Wires into PerceptionComponent.Proc() alongside GoldTrendAnalyzer

Tracks the flow of combat momentum between teams using a state-machine
approach inspired by Apollo's perception/fusion/async_fusion_component.cc
which fuses multiple sensor signals into a unified obstacle picture.

Here we fuse multiple game signals (kills, objectives, gold velocity,
tower plates) into a single Momentum enum:  SURGING / GAINING / NEUTRAL /
LOSING / COLLAPSING.  Published on /lol/momentum for prediction and
planning to consume.

File location: lolbot-HyperAI/modules/perception/fusion/momentum_tracker.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

# Momentum score boundaries (blue-team-centric: positive = blue advantage)
_SURGING_THRESHOLD = 6.0
_GAINING_THRESHOLD = 2.5
_LOSING_THRESHOLD = -2.5
_COLLAPSING_THRESHOLD = -6.0

# Decay: momentum decays toward neutral without new events
_DECAY_RATE_PER_SEC = 0.08

# Signal weights
_KILL_WEIGHT = 1.5         # Per kill in scoring window
_OBJECTIVE_WEIGHT = 3.0    # Dragon / baron / herald
_TOWER_WEIGHT = 2.0        # Tower take
_GOLD_VELOCITY_WEIGHT = 0.002  # Per gold/sec advantage
_ACE_WEIGHT = 5.0          # Team ace

# Scoring window: events older than this fade
_SCORING_WINDOW_SEC = 120.0
_MAX_EVENTS = 200


class MomentumState(Enum):
    """Five-state momentum model."""
    SURGING = auto()      # Dominant — keep pushing
    GAINING = auto()      # Slight advantage — press carefully
    NEUTRAL = auto()      # Even — look for openings
    LOSING = auto()       # Falling behind — play safe
    COLLAPSING = auto()   # Critical — avoid fights, farm


@dataclass
class MomentumEvent:
    """A single momentum-relevant event with a timestamp."""
    game_time: float
    event_type: str   # kill, objective, tower, ace, gold_spike
    team: str         # BLUE or RED
    value: float = 1.0  # Weight multiplier (e.g., baron = 3x)

    @property
    def blue_signed_value(self) -> float:
        """Return positive for blue advantage, negative for red."""
        return self.value if self.team == "BLUE" else -self.value


@dataclass
class MomentumReport:
    """Output of a single momentum evaluation tick."""
    state: MomentumState = MomentumState.NEUTRAL
    raw_score: float = 0.0
    smoothed_score: float = 0.0
    recent_kills_blue: int = 0
    recent_kills_red: int = 0
    recent_objectives_blue: int = 0
    recent_objectives_red: int = 0
    gold_velocity: float = 0.0
    transition_from: Optional[MomentumState] = None
    transition_reason: str = ""
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.name,
            "raw_score": round(self.raw_score, 3),
            "smoothed_score": round(self.smoothed_score, 3),
            "recent_kills_blue": self.recent_kills_blue,
            "recent_kills_red": self.recent_kills_red,
            "recent_objectives_blue": self.recent_objectives_blue,
            "recent_objectives_red": self.recent_objectives_red,
            "gold_velocity": round(self.gold_velocity, 1),
            "transition_from": (
                self.transition_from.name if self.transition_from else None
            ),
            "transition_reason": self.transition_reason,
            "game_time": round(self.game_time, 1),
        }


class MomentumTracker:
    """Fuses combat signals into a team momentum state.

    Usage::
        tracker = MomentumTracker()
        # In PerceptionComponent.Proc() after event detection:
        for event in new_events:
            tracker.record_event(event_type, team, game_time, weight)
        tracker.set_gold_velocity(gold_diff_per_min)
        report = tracker.evaluate(game_time)
    """

    def __init__(
        self,
        smoothing_alpha: float = 0.15,
        hysteresis: float = 0.8,
    ) -> None:
        self._events: Deque[MomentumEvent] = deque(maxlen=_MAX_EVENTS)
        self._smoothed_score: float = 0.0
        self._smoothing_alpha = smoothing_alpha
        self._hysteresis = hysteresis
        self._current_state = MomentumState.NEUTRAL
        self._gold_velocity: float = 0.0
        self._last_eval_time: float = 0.0
        self._eval_count: int = 0
        self._transition_count: int = 0

    # ─── Input recording ────────────────────────────────────────────

    def record_kill(
        self, team: str, game_time: float, is_shutdown: bool = False,
    ) -> None:
        """Record a champion kill."""
        weight = _KILL_WEIGHT * (1.5 if is_shutdown else 1.0)
        self._events.append(MomentumEvent(
            game_time=game_time,
            event_type="kill",
            team=team.upper(),
            value=weight,
        ))

    def record_objective(
        self, team: str, game_time: float, obj_type: str = "dragon",
    ) -> None:
        """Record an objective take (dragon, baron, herald, tower)."""
        weight_map = {
            "baron": _OBJECTIVE_WEIGHT * 1.5,
            "dragon": _OBJECTIVE_WEIGHT,
            "herald": _OBJECTIVE_WEIGHT * 0.8,
            "elder": _OBJECTIVE_WEIGHT * 2.0,
        }
        weight = weight_map.get(obj_type.lower(), _OBJECTIVE_WEIGHT)
        self._events.append(MomentumEvent(
            game_time=game_time,
            event_type="objective",
            team=team.upper(),
            value=weight,
        ))

    def record_tower(self, team: str, game_time: float) -> None:
        """Record a tower destruction."""
        self._events.append(MomentumEvent(
            game_time=game_time,
            event_type="tower",
            team=team.upper(),
            value=_TOWER_WEIGHT,
        ))

    def record_ace(self, team: str, game_time: float) -> None:
        """Record a team ace."""
        self._events.append(MomentumEvent(
            game_time=game_time,
            event_type="ace",
            team=team.upper(),
            value=_ACE_WEIGHT,
        ))

    def set_gold_velocity(self, gold_diff_per_min: float) -> None:
        """Set current gold velocity (positive = blue advantage)."""
        self._gold_velocity = gold_diff_per_min

    # ─── Evaluation ─────────────────────────────────────────────────

    def evaluate(self, game_time: float) -> MomentumReport:
        """Evaluate current momentum state.

        Combines recent events + gold velocity into a raw score,
        applies EMA smoothing, and classifies into MomentumState.
        Includes hysteresis to prevent rapid state flickering.
        """
        self._eval_count += 1
        self._last_eval_time = game_time

        # 1. Compute raw score from recent events
        cutoff = game_time - _SCORING_WINDOW_SEC
        recent_events = [e for e in self._events if e.game_time >= cutoff]

        raw_score = 0.0
        kills_blue = 0
        kills_red = 0
        obj_blue = 0
        obj_red = 0

        for evt in recent_events:
            # Time decay: more recent events weight more
            age_sec = game_time - evt.game_time
            time_weight = max(0.2, 1.0 - age_sec / _SCORING_WINDOW_SEC)
            raw_score += evt.blue_signed_value * time_weight

            if evt.event_type == "kill":
                if evt.team == "BLUE":
                    kills_blue += 1
                else:
                    kills_red += 1
            elif evt.event_type == "objective":
                if evt.team == "BLUE":
                    obj_blue += 1
                else:
                    obj_red += 1

        # 2. Add gold velocity contribution
        raw_score += self._gold_velocity * _GOLD_VELOCITY_WEIGHT * 60.0

        # 3. Natural decay toward neutral
        elapsed = game_time - self._last_eval_time if self._last_eval_time else 0
        if elapsed > 0 and abs(self._smoothed_score) > 0.1:
            decay = _DECAY_RATE_PER_SEC * elapsed
            if self._smoothed_score > 0:
                self._smoothed_score = max(0, self._smoothed_score - decay)
            else:
                self._smoothed_score = min(0, self._smoothed_score + decay)

        # 4. EMA smoothing
        self._smoothed_score = (
            self._smoothing_alpha * raw_score
            + (1 - self._smoothing_alpha) * self._smoothed_score
        )

        # 5. Classify with hysteresis
        new_state = self._classify_with_hysteresis(self._smoothed_score)

        # 6. Detect transition
        transition_from = None
        transition_reason = ""
        if new_state != self._current_state:
            transition_from = self._current_state
            transition_reason = self._explain_transition(
                self._current_state, new_state, kills_blue, kills_red,
                obj_blue, obj_red,
            )
            self._current_state = new_state
            self._transition_count += 1
            logger.info(
                "Momentum shift: %s → %s (score=%.2f) %s",
                transition_from.name, new_state.name,
                self._smoothed_score, transition_reason,
            )

        return MomentumReport(
            state=self._current_state,
            raw_score=raw_score,
            smoothed_score=self._smoothed_score,
            recent_kills_blue=kills_blue,
            recent_kills_red=kills_red,
            recent_objectives_blue=obj_blue,
            recent_objectives_red=obj_red,
            gold_velocity=self._gold_velocity,
            transition_from=transition_from,
            transition_reason=transition_reason,
            game_time=game_time,
        )

    def _classify_with_hysteresis(self, score: float) -> MomentumState:
        """Classify score with hysteresis to prevent flickering.

        When moving AWAY from a boundary, require the score to cross
        by `hysteresis` extra to trigger transition.
        """
        current = self._current_state
        h = self._hysteresis

        if current == MomentumState.SURGING:
            if score < _SURGING_THRESHOLD - h:
                return MomentumState.GAINING
            return MomentumState.SURGING
        elif current == MomentumState.GAINING:
            if score >= _SURGING_THRESHOLD:
                return MomentumState.SURGING
            if score < _GAINING_THRESHOLD - h:
                return MomentumState.NEUTRAL
            return MomentumState.GAINING
        elif current == MomentumState.NEUTRAL:
            if score >= _GAINING_THRESHOLD + h:
                return MomentumState.GAINING
            if score <= _LOSING_THRESHOLD - h:
                return MomentumState.LOSING
            return MomentumState.NEUTRAL
        elif current == MomentumState.LOSING:
            if score > _LOSING_THRESHOLD + h:
                return MomentumState.NEUTRAL
            if score <= _COLLAPSING_THRESHOLD:
                return MomentumState.COLLAPSING
            return MomentumState.LOSING
        elif current == MomentumState.COLLAPSING:
            if score > _COLLAPSING_THRESHOLD + h:
                return MomentumState.LOSING
            return MomentumState.COLLAPSING
        return MomentumState.NEUTRAL

    def _explain_transition(
        self,
        old: MomentumState,
        new: MomentumState,
        kb: int, kr: int,
        ob: int, or_: int,
    ) -> str:
        """Generate a human-readable reason for the transition."""
        parts = []
        kill_diff = kb - kr
        if abs(kill_diff) >= 2:
            team = "Blue" if kill_diff > 0 else "Red"
            parts.append(f"{team} +{abs(kill_diff)} kills")
        obj_diff = ob - or_
        if abs(obj_diff) >= 1:
            team = "Blue" if obj_diff > 0 else "Red"
            parts.append(f"{team} +{abs(obj_diff)} objectives")
        if abs(self._gold_velocity) > 200:
            team = "Blue" if self._gold_velocity > 0 else "Red"
            parts.append(f"{team} gold velocity {abs(self._gold_velocity):.0f}/min")
        return "; ".join(parts) if parts else "gradual shift"

    # ─── Status / Introspection ─────────────────────────────────────

    @property
    def current_state(self) -> MomentumState:
        return self._current_state

    @property
    def smoothed_score(self) -> float:
        return self._smoothed_score

    def stats(self) -> Dict[str, Any]:
        return {
            "state": self._current_state.name,
            "smoothed_score": round(self._smoothed_score, 3),
            "gold_velocity": round(self._gold_velocity, 1),
            "eval_count": self._eval_count,
            "transition_count": self._transition_count,
            "event_buffer_size": len(self._events),
        }

    def reset(self) -> None:
        """Reset all state (new game session)."""
        self._events.clear()
        self._smoothed_score = 0.0
        self._current_state = MomentumState.NEUTRAL
        self._gold_velocity = 0.0
        self._last_eval_time = 0.0
        self._eval_count = 0
        self._transition_count = 0

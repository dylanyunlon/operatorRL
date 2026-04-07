"""
Prediction layer output message types.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

class FightRecommendation(Enum):
    ENGAGE = "engage"
    DISENGAGE = "disengage"
    POKE = "poke"
    PICK = "pick"
    HOLD = "hold"

class TrendDirection(Enum):
    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"

@dataclass(frozen=True)
class TeamfightResult:
    our_win_probability: float
    recommended_action: FightRecommendation
    confidence: float
    rationale: str = ""
    feature_breakdown: Tuple[Tuple[str, float], ...] = ()
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "win_prob": round(self.our_win_probability, 3),
            "action": self.recommended_action.value,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "features": list(self.feature_breakdown),
            "game_time": round(self.game_time, 1),
        }

@dataclass(frozen=True)
class WinProbabilityDetail:
    blue_win_prob: float
    confidence: float
    trend: TrendDirection
    top_factors: Tuple[Tuple[str, float], ...] = ()
    what_if_scenarios: Tuple[Tuple[str, float], ...] = ()
    model_version: str = ""
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "win_prob": round(self.blue_win_prob, 4),
            "confidence": round(self.confidence, 3),
            "trend": self.trend.value,
            "factors": list(self.top_factors),
            "what_if": list(self.what_if_scenarios),
            "model": self.model_version,
            "game_time": round(self.game_time, 1),
        }

@dataclass(frozen=True)
class ObjectiveTimerState:
    dragon_timer_s: float = -1.0
    baron_timer_s: float = -1.0
    herald_timer_s: float = -1.0
    dragon_soul_progress_blue: int = 0
    dragon_soul_progress_red: int = 0
    elder_available: bool = False
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dragon_timer": round(self.dragon_timer_s, 0),
            "baron_timer": round(self.baron_timer_s, 0),
            "herald_timer": round(self.herald_timer_s, 0),
            "dragon_blue": self.dragon_soul_progress_blue,
            "dragon_red": self.dragon_soul_progress_red,
            "elder": self.elder_available,
            "game_time": round(self.game_time, 1),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended prediction proto with validation and rich types
# ═══════════════════════════════════════════════════════════════════════════

import math
from typing import Callable, List, Set


class PredictionValidationError(ValueError):
    """Raised when a prediction message fails validation."""
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"Prediction validation: {field} — {reason}")


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """Detailed confidence breakdown for win prediction.

    Claude20: Makes prediction transparency visible in dashboard.
    """
    data_quality: float = 0.0
    sample_size: float = 0.0
    model_agreement: float = 0.0
    temporal_stability: float = 0.0
    game_time_ramp: float = 0.0
    overall: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data_quality": round(self.data_quality, 3),
            "sample_size": round(self.sample_size, 3),
            "model_agreement": round(self.model_agreement, 3),
            "temporal_stability": round(self.temporal_stability, 3),
            "game_time_ramp": round(self.game_time_ramp, 3),
            "overall": round(self.overall, 3),
        }


@dataclass(frozen=True)
class MomentumSnapshot:
    """Momentum state at a point in time.

    Claude20: Published alongside win prediction for richer context.
    """
    state: str = "NEUTRAL"  # SURGING, GAINING, NEUTRAL, LOSING, COLLAPSING
    score: float = 0.0
    short_momentum: float = 0.0
    medium_momentum: float = 0.0
    gold_momentum: float = 0.0
    kill_momentum: float = 0.0
    last_shift_time: float = 0.0
    shift_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "score": round(self.score, 3),
            "short": round(self.short_momentum, 3),
            "medium": round(self.medium_momentum, 3),
            "gold": round(self.gold_momentum, 3),
            "kill": round(self.kill_momentum, 3),
        }


@dataclass(frozen=True)
class DeathTimerSnapshot:
    """Death timer summary for team fight readiness assessment.

    Claude20: Consumed by teamfight_predictor and objective_window_advisor.
    """
    blue_dead_count: int = 0
    red_dead_count: int = 0
    blue_total_respawn_s: float = 0.0
    red_total_respawn_s: float = 0.0
    blue_next_respawn_s: float = 0.0
    red_next_respawn_s: float = 0.0
    advantage_team: str = "even"
    advantage_magnitude: float = 0.0
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blue_dead": self.blue_dead_count,
            "red_dead": self.red_dead_count,
            "blue_respawn_s": round(self.blue_total_respawn_s, 1),
            "red_respawn_s": round(self.red_total_respawn_s, 1),
            "advantage": self.advantage_team,
            "advantage_mag": round(self.advantage_magnitude, 2),
        }


@dataclass(frozen=True)
class CompMatchupSnapshot:
    """Team composition matchup analysis snapshot.

    Claude20: Published alongside win prediction for comp-aware advice.
    """
    blue_archetype: str = "BALANCED"
    red_archetype: str = "BALANCED"
    blue_phase_advantage: float = 0.0
    matchup_description: str = ""
    blue_win_condition: str = ""
    red_win_condition: str = ""
    comp_adjustment: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blue_arch": self.blue_archetype,
            "red_arch": self.red_archetype,
            "phase_adv": round(self.blue_phase_advantage, 3),
            "matchup": self.matchup_description,
            "comp_adj": round(self.comp_adjustment, 4),
        }


@dataclass(frozen=True)
class PredictionBundle:
    """Complete prediction output bundle for a single tick.

    Claude20: Aggregates all prediction outputs into a single
    publishable message. Downstream components read one message
    instead of subscribing to 5 separate channels.

    Apollo reference: prediction/prediction_component.cc publishes
    a single PredictionObstacles message combining all obstacle types.
    """
    win_probability: WinProbabilityDetail = field(
        default_factory=lambda: WinProbabilityDetail(
            blue_win_prob=0.5, confidence=0.0, trend=TrendDirection.STABLE))
    teamfight: Optional[TeamfightResult] = None
    objectives: Optional[ObjectiveTimerState] = None
    momentum: Optional[MomentumSnapshot] = None
    death_timers: Optional[DeathTimerSnapshot] = None
    comp_matchup: Optional[CompMatchupSnapshot] = None
    confidence_breakdown: Optional[ConfidenceBreakdown] = None
    game_time: float = 0.0
    sequence: int = 0
    model_version: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "win": self.win_probability.to_dict(),
            "game_time": round(self.game_time, 1),
            "sequence": self.sequence,
            "model": self.model_version,
        }
        if self.teamfight:
            result["teamfight"] = self.teamfight.to_dict()
        if self.objectives:
            result["objectives"] = self.objectives.to_dict()
        if self.momentum:
            result["momentum"] = self.momentum.to_dict()
        if self.death_timers:
            result["death_timers"] = self.death_timers.to_dict()
        if self.comp_matchup:
            result["comp"] = self.comp_matchup.to_dict()
        if self.confidence_breakdown:
            result["confidence"] = self.confidence_breakdown.to_dict()
        return result


def validate_win_probability(prob: float) -> bool:
    """Validate that a win probability is in valid range."""
    if math.isnan(prob) or math.isinf(prob):
        return False
    return 0.0 <= prob <= 1.0


def clamp_probability(prob: float) -> float:
    """Clamp a probability to [0.001, 0.999] to avoid log(0) issues."""
    if math.isnan(prob) or math.isinf(prob):
        return 0.5
    return max(0.001, min(0.999, prob))

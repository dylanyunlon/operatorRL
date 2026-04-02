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

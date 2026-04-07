"""
Planning layer output message types.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

class MacroActionType(Enum):
    BARON = "baron"
    DRAGON = "dragon"
    GROUP = "group"
    SPLIT_PUSH = "split_push"
    DEFEND = "defend"
    RESET = "reset"
    VISION_CONTROL = "vision_control"
    IDLE = "idle"

class UrgencyLevel(Enum):
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    CRITICAL = auto()

class LaneAdviceCategory(Enum):
    CS_FARMING = "cs_farming"
    BACK_TIMING = "back_timing"
    TRADE_WINDOW = "trade_window"
    ROAM_OPPORTUNITY = "roam_opportunity"
    DANGER_WARNING = "danger_warning"
    WAVE_MANAGEMENT = "wave_management"
    POWER_SPIKE = "power_spike"

@dataclass(frozen=True)
class MacroDecisionResult:
    action: MacroActionType
    urgency: UrgencyLevel
    confidence: float
    rationale: str
    alternatives: Tuple[Tuple[str, float], ...] = ()
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "urgency": self.urgency.name,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "alternatives": list(self.alternatives),
            "game_time": round(self.game_time, 1),
        }

@dataclass(frozen=True)
class LaneAdviceResult:
    category: LaneAdviceCategory
    text: str
    confidence: float = 0.5
    aggressiveness: str = "neutral"
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category.value,
            "text": self.text,
            "confidence": round(self.confidence, 2),
            "aggro": self.aggressiveness,
            "game_time": round(self.game_time, 1),
        }

@dataclass(frozen=True)
class ItemBuildSuggestion:
    item_name: str
    item_id: int
    reason: str
    priority: int = 1
    gold_cost: int = 0
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item": self.item_name,
            "id": self.item_id,
            "reason": self.reason,
            "priority": self.priority,
            "cost": self.gold_cost,
            "game_time": round(self.game_time, 1),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended planning proto with validation and rich output types
# ═══════════════════════════════════════════════════════════════════════════

import math
from typing import Callable, List, Set


@dataclass(frozen=True)
class RecallTimingAdvice:
    """Recall timing recommendation from TempoModule.

    Claude20: Structured output from RecallAdvisor for voice narration.
    """
    urgency: str = "NOT_YET"
    reason: str = ""
    gold_available: float = 0.0
    best_buy: str = ""
    health_pct: float = 1.0
    mana_pct: float = 1.0
    objective_window_s: float = 0.0
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "urgency": self.urgency,
            "reason": self.reason,
            "gold": round(self.gold_available, 0),
            "best_buy": self.best_buy,
            "health": round(self.health_pct, 2),
            "mana": round(self.mana_pct, 2),
            "obj_window": round(self.objective_window_s, 0),
            "game_time": round(self.game_time, 1),
        }


@dataclass(frozen=True)
class PowerSpikeAlert:
    """Power spike detection result.

    Claude20: Typed output from PowerSpikeDetector for voice/overlay.
    """
    player_name: str = ""
    champion_name: str = ""
    spike_type: str = "LEVEL_SPIKE"
    impact: str = "MODERATE"
    is_ally: bool = False
    description: str = ""
    strategic_note: str = ""
    level: int = 0
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player": self.player_name,
            "champion": self.champion_name,
            "type": self.spike_type,
            "impact": self.impact,
            "ally": self.is_ally,
            "desc": self.description,
            "note": self.strategic_note,
        }


@dataclass(frozen=True)
class SpellWindowAlert:
    """Summoner spell cooldown window opportunity.

    Claude20: When enemy Flash/TP is down, signal to planning.
    """
    enemy_flash_down: Tuple[str, ...] = ()
    enemy_tp_down: Tuple[str, ...] = ()
    enemy_exhaust_down: Tuple[str, ...] = ()
    best_target: str = ""
    best_target_reason: str = ""
    window_quality: float = 0.0
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flash_down": list(self.enemy_flash_down),
            "tp_down": list(self.enemy_tp_down),
            "exhaust_down": list(self.enemy_exhaust_down),
            "target": self.best_target,
            "reason": self.best_target_reason,
            "quality": round(self.window_quality, 3),
        }


@dataclass(frozen=True)
class ObjectiveWindowAlert:
    """Objective spawn window recommendation.

    Claude20: Structured output from ObjectiveWindowAdvisor.
    """
    objective_name: str = ""
    urgency: str = "PLAN_AHEAD"
    seconds_until_spawn: float = 0.0
    strategic_priority: float = 0.0
    advice_text: str = ""
    voice_text: str = ""
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective": self.objective_name,
            "urgency": self.urgency,
            "seconds": round(self.seconds_until_spawn, 1),
            "priority": round(self.strategic_priority, 3),
            "advice": self.advice_text,
        }


@dataclass(frozen=True)
class PlanningBundle:
    """Complete planning output bundle for a single tick.

    Claude20: Aggregates all planning outputs. Downstream control
    component reads one message instead of multiple channels.

    Apollo reference: planning/planning_component.cc publishes a
    single ADCTrajectory message combining path, speed, and gear.
    """
    macro_decision: Optional[MacroDecisionResult] = None
    lane_advices: Tuple[LaneAdviceResult, ...] = ()
    recall_advice: Optional[RecallTimingAdvice] = None
    power_spikes: Tuple[PowerSpikeAlert, ...] = ()
    spell_windows: Optional[SpellWindowAlert] = None
    objective_windows: Tuple[ObjectiveWindowAlert, ...] = ()
    item_suggestions: Tuple[ItemBuildSuggestion, ...] = ()
    game_time: float = 0.0
    sequence: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "game_time": round(self.game_time, 1),
            "sequence": self.sequence,
        }
        if self.macro_decision:
            result["macro"] = self.macro_decision.to_dict()
        if self.lane_advices:
            result["lanes"] = [la.to_dict() for la in self.lane_advices]
        if self.recall_advice:
            result["recall"] = self.recall_advice.to_dict()
        if self.power_spikes:
            result["spikes"] = [s.to_dict() for s in self.power_spikes]
        if self.spell_windows:
            result["spell_windows"] = self.spell_windows.to_dict()
        if self.objective_windows:
            result["objectives"] = [o.to_dict() for o in self.objective_windows]
        if self.item_suggestions:
            result["items"] = [i.to_dict() for i in self.item_suggestions]
        return result

    @property
    def has_urgent_content(self) -> bool:
        """Check if this bundle contains anything worth announcing."""
        if self.macro_decision and self.macro_decision.urgency in (
            UrgencyLevel.HIGH, UrgencyLevel.CRITICAL):
            return True
        if self.power_spikes:
            return True
        if self.recall_advice and self.recall_advice.urgency in ("NOW", "SOON"):
            return True
        if self.objective_windows:
            for ow in self.objective_windows:
                if ow.urgency in ("CONTEST", "ACTIVE"):
                    return True
        return False


def validate_macro_decision(decision: MacroDecisionResult) -> List[str]:
    """Validate a macro decision message."""
    errors: List[str] = []
    if not (0.0 <= decision.confidence <= 1.0):
        errors.append(f"confidence out of range: {decision.confidence}")
    if not decision.rationale:
        errors.append("rationale must not be empty")
    return errors

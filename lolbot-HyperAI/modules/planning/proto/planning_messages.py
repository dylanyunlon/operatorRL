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

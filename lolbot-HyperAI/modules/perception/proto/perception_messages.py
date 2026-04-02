"""
Perception layer output message types.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

class KillPatternType(Enum):
    FIRST_BLOOD = "first_blood"
    DOUBLE_KILL = "double_kill"
    TRIPLE_KILL = "triple_kill"
    QUADRA_KILL = "quadra_kill"
    PENTA_KILL = "penta_kill"
    KILLING_SPREE = "killing_spree"
    SHUTDOWN = "shutdown"
    ACE = "ace"

class MapZone(Enum):
    TOP_LANE_NEAR = "top_near"
    TOP_LANE_MID = "top_mid"
    TOP_LANE_FAR = "top_far"
    MID_LANE_NEAR = "mid_near"
    MID_LANE_MID = "mid_mid"
    MID_LANE_FAR = "mid_far"
    BOT_LANE_NEAR = "bot_near"
    BOT_LANE_MID = "bot_mid"
    BOT_LANE_FAR = "bot_far"
    JUNGLE_TOP_BLUE = "jg_top_blue"
    JUNGLE_TOP_RED = "jg_top_red"
    JUNGLE_BOT_BLUE = "jg_bot_blue"
    JUNGLE_BOT_RED = "jg_bot_red"
    RIVER_TOP = "river_top"
    RIVER_BOT = "river_bot"
    BLUE_BASE = "blue_base"
    RED_BASE = "red_base"
    BARON_PIT = "baron_pit"
    DRAGON_PIT = "dragon_pit"

class LanePressure(Enum):
    PUSHED_TO_US = "pushed_to_us"
    SLOW_PUSH_TO_US = "slow_push_to_us"
    FROZEN = "frozen"
    SLOW_PUSH_TO_THEM = "slow_push_to_them"
    PUSHED_TO_THEM = "pushed_to_them"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class KillFeedResult:
    patterns: Tuple[Dict[str, Any], ...] = ()
    total_kills_blue: int = 0
    total_kills_red: int = 0
    active_sprees: int = 0
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patterns": list(self.patterns),
            "kills_blue": self.total_kills_blue,
            "kills_red": self.total_kills_red,
            "sprees": self.active_sprees,
            "game_time": round(self.game_time, 1),
        }

@dataclass(frozen=True)
class LaneState:
    lane: str
    pressure: LanePressure
    our_champions: int = 0
    their_champions: int = 0
    danger_level: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lane": self.lane,
            "pressure": self.pressure.value,
            "our": self.our_champions,
            "their": self.their_champions,
            "danger": round(self.danger_level, 2),
        }

@dataclass(frozen=True)
class MinimapAnalysisResult:
    lanes: Tuple[LaneState, ...] = ()
    jungle_control_ours: float = 0.5
    jungle_control_theirs: float = 0.5
    danger_zones: Tuple[str, ...] = ()
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lanes": [l.to_dict() for l in self.lanes],
            "jg_ours": round(self.jungle_control_ours, 2),
            "jg_theirs": round(self.jungle_control_theirs, 2),
            "danger": list(self.danger_zones),
            "game_time": round(self.game_time, 1),
        }

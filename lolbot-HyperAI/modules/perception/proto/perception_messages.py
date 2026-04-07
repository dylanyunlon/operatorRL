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


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended perception proto with validation and rich types
# ═══════════════════════════════════════════════════════════════════════════

import math
from typing import Callable, List, Set


@dataclass(frozen=True)
class GoldTrendSnapshot:
    """Gold trend analysis result snapshot.

    Claude20: Published alongside game_state for richer context.
    """
    current_gold_diff: float = 0.0
    short_momentum: float = 0.0
    medium_momentum: float = 0.0
    volatility: float = 0.0
    recent_spike: bool = False
    spike_direction: str = "none"
    spike_magnitude: float = 0.0
    advantage_team: str = "even"
    advantage_strength: str = "none"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gold_diff": round(self.current_gold_diff, 0),
            "short_mom": round(self.short_momentum, 1),
            "medium_mom": round(self.medium_momentum, 1),
            "volatility": round(self.volatility, 1),
            "spike": self.recent_spike,
            "advantage": f"{self.advantage_team}_{self.advantage_strength}",
        }


@dataclass(frozen=True)
class PhaseTransitionEvent:
    """Game phase transition event.

    Claude20: Published when PhaseDetector detects a phase change.
    """
    from_phase: str = "LOADING"
    to_phase: str = "LANING"
    game_time: float = 0.0
    trigger_reason: str = ""
    confidence: float = 0.8

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_phase,
            "to": self.to_phase,
            "game_time": round(self.game_time, 1),
            "trigger": self.trigger_reason,
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True)
class WardEvent:
    """Ward placement or destruction event.

    Claude20: Published by ward tracker for map awareness.
    """
    ward_type: str = "stealth"  # stealth, control, zombie
    action: str = "placed"      # placed, destroyed, expired
    x: float = 0.0
    y: float = 0.0
    zone: str = ""
    player_name: str = ""
    team: str = ""
    estimated_expiry_time: float = 0.0
    game_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.ward_type,
            "action": self.action,
            "position": (round(self.x, 1), round(self.y, 1)),
            "zone": self.zone,
            "player": self.player_name,
            "team": self.team,
            "expiry": round(self.estimated_expiry_time, 0),
        }


@dataclass(frozen=True)
class FusionStatusSnapshot:
    """Sensor fusion status for monitoring.

    Claude20: Shows which data source is active and health metrics.
    """
    active_source: str = "none"
    lcu_available: bool = False
    lcu_message_count: int = 0
    fiddler_available: bool = False
    fiddler_message_count: int = 0
    fused_count: int = 0
    dedup_count: int = 0
    fallback_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.active_source,
            "lcu_ok": self.lcu_available,
            "lcu_msgs": self.lcu_message_count,
            "fiddler_ok": self.fiddler_available,
            "fused": self.fused_count,
            "dedup": self.dedup_count,
            "fallbacks": self.fallback_count,
        }


@dataclass(frozen=True)
class PerceptionBundle:
    """Complete perception output bundle for a single tick.

    Claude20: Aggregates all perception outputs into one message.
    Downstream prediction and planning read one channel instead of many.
    """
    game_time: float = 0.0
    phase: str = "LOADING"
    gold_diff: float = 0.0
    blue_kills: int = 0
    red_kills: int = 0
    kill_feed: Optional[KillFeedResult] = None
    minimap: Optional[MinimapAnalysisResult] = None
    gold_trend: Optional[GoldTrendSnapshot] = None
    phase_transition: Optional[PhaseTransitionEvent] = None
    fusion_status: Optional[FusionStatusSnapshot] = None
    new_events_count: int = 0
    sequence: int = 0

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "game_time": round(self.game_time, 1),
            "phase": self.phase,
            "gold_diff": round(self.gold_diff, 0),
            "kills": {"blue": self.blue_kills, "red": self.red_kills},
            "new_events": self.new_events_count,
            "sequence": self.sequence,
        }
        if self.kill_feed:
            result["kill_feed"] = self.kill_feed.to_dict()
        if self.minimap:
            result["minimap"] = self.minimap.to_dict()
        if self.gold_trend:
            result["gold_trend"] = self.gold_trend.to_dict()
        if self.phase_transition:
            result["phase_change"] = self.phase_transition.to_dict()
        return result


def validate_game_time(game_time: float) -> bool:
    """Validate game time is reasonable."""
    if math.isnan(game_time) or math.isinf(game_time):
        return False
    return 0.0 <= game_time <= 7200.0


def validate_player_count(count: int) -> bool:
    """Validate player count is in expected range (5v5 = 10 max)."""
    return 0 <= count <= 10


def validate_gold_diff(gold_diff: float) -> bool:
    """Validate gold diff is in plausible range."""
    if math.isnan(gold_diff) or math.isinf(gold_diff):
        return False
    return abs(gold_diff) <= 100000  # 100k gold diff is extreme but possible

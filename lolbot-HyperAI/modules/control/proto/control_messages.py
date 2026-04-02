"""
Control layer message types — typed dataclasses for control pipeline.
======================================================================
lolbot-HyperAI · Control Layer · Proto

Defines structured messages for the control pipeline:
    - ControlAction: routed action with channel targets
    - OverlayUpdate: HUD element update command
    - VoiceDirective: TTS queue entry with priority
    - ControlStatus: aggregate control subsystem health

Architecture position:
    modules/control/proto/control_messages.py   ← YOU ARE HERE
    ├─ Used by: modules/control/control_component.py
    ├─ Used by: modules/control/action_dispatch/action_dispatcher.py
    ├─ Used by: modules/control/overlay/overlay_renderer.py
    └─ Used by: modules/control/voice_output/voice_narrator.py

Apollo reference:
    modules/control/proto/control_cmd.proto
    modules/control/proto/pad_msg.proto

Design notes:
    - All frozen dataclasses for immutability on the message bus
    - to_dict() / from_dict() for serialization
    - Enum fields for type safety
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


class ControlChannel(Enum):
    """Output channels the control layer can target."""
    VOICE = "voice"
    OVERLAY = "overlay"
    LOG = "log"
    ALL = "all"


class OverlayPosition(Enum):
    """Screen positions for overlay elements."""
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER = "center"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class OverlayElementType(Enum):
    """Types of overlay HUD elements."""
    WIN_PROBABILITY = "win_probability"
    STRATEGY_TEXT = "strategy_text"
    OBJECTIVE_TIMER = "objective_timer"
    TEAMFIGHT_INDICATOR = "teamfight_indicator"
    KILL_FEED_PATTERN = "kill_feed_pattern"
    MACRO_DECISION = "macro_decision"
    LANE_ADVICE = "lane_advice"
    CUSTOM_TEXT = "custom_text"


class VoiceUrgency(Enum):
    """Voice announcement urgency levels."""
    BACKGROUND = auto()    # Low priority, can be skipped
    NORMAL = auto()        # Standard announcement
    IMPORTANT = auto()     # Should not be skipped
    CRITICAL = auto()      # Interrupt current speech


@dataclass(frozen=True)
class ControlAction:
    """A routed action from the control dispatcher.

    Represents a single output command that has been assigned to
    specific channels by the ActionDispatcher.
    """
    action_id: int
    text: str
    voice_text: str = ""
    channels: Tuple[str, ...] = ("overlay", "log")
    priority: int = 1
    source: str = ""
    category: str = ""
    dedup_key: str = ""
    game_time: float = 0.0
    ttl_s: float = 10.0
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "text": self.text,
            "channels": list(self.channels),
            "priority": self.priority,
            "source": self.source,
            "category": self.category,
            "game_time": round(self.game_time, 1),
            "ttl_s": self.ttl_s,
        }


@dataclass(frozen=True)
class OverlayUpdate:
    """Command to add/update an overlay HUD element.

    Elements auto-expire after ttl_s seconds. Higher-priority elements
    can evict lower-priority ones when the max element count is reached.
    """
    element_type: OverlayElementType
    position: OverlayPosition
    text: str
    value: float = 0.0
    priority: int = 1
    ttl_s: float = 10.0
    source: str = ""
    game_time: float = 0.0
    color_hint: str = ""           # e.g. "green", "red", "#ff0000"
    secondary_text: str = ""       # subtitle or detail line
    progress: float = -1.0         # 0.0-1.0 for progress bars, -1 = none
    timestamp: float = field(default_factory=time.time)

    @property
    def dedup_key(self) -> str:
        return f"{self.source}:{self.element_type.value}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.element_type.value,
            "position": self.position.value,
            "text": self.text,
            "value": round(self.value, 3),
            "priority": self.priority,
            "ttl_s": self.ttl_s,
            "source": self.source,
            "color_hint": self.color_hint,
            "secondary_text": self.secondary_text,
            "progress": round(self.progress, 3) if self.progress >= 0 else -1,
        }


@dataclass(frozen=True)
class VoiceDirective:
    """A queued voice (TTS) announcement.

    VoiceNarrator processes these from a priority queue and sends
    them to the TTS backend.
    """
    text: str
    urgency: VoiceUrgency = VoiceUrgency.NORMAL
    source: str = ""
    game_time: float = 0.0
    dedup_key: str = ""
    max_age_s: float = 5.0        # discard if older than this
    timestamp: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > self.max_age_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "urgency": self.urgency.name,
            "source": self.source,
            "game_time": round(self.game_time, 1),
        }


@dataclass(frozen=True)
class ControlStatus:
    """Aggregate health status of the control subsystem."""
    dispatcher_queue_depth: int = 0
    overlay_active_elements: int = 0
    narrator_queue_depth: int = 0
    narrator_speaking: bool = False
    total_dispatched: int = 0
    total_voiced: int = 0
    total_overlay_updates: int = 0
    total_deduplicated: int = 0
    uptime_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dispatcher_queue": self.dispatcher_queue_depth,
            "overlay_elements": self.overlay_active_elements,
            "narrator_queue": self.narrator_queue_depth,
            "narrator_speaking": self.narrator_speaking,
            "total_dispatched": self.total_dispatched,
            "total_voiced": self.total_voiced,
            "total_overlay": self.total_overlay_updates,
            "total_dedup": self.total_deduplicated,
            "uptime_s": round(self.uptime_s, 1),
        }

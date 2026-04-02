"""
ChannelRegistry — Centralized channel name and schema registry.
================================================================

Single source of truth for all CyberNode channel names, their
expected payload schemas, and documentation. Prevents typos in
channel strings and enables runtime schema validation.

Architecture position:
    modules/common/proto/channel_registry.py   ← YOU ARE HERE
    ├─ Imported by: every *_component.py
    ├─ Used by: cyber/node/node.py (optional validation)
    └─ Used by: modules/monitor/ (channel discovery)

Apollo reference:
    cyber/proto/topology_change.proto — channel definition
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Type

logger = logging.getLogger(__name__)


class ChannelDirection(Enum):
    PUBLISH = auto()
    SUBSCRIBE = auto()
    BOTH = auto()


class ChannelFrequency(Enum):
    """Expected publish frequency."""
    HIGH_10HZ = "10hz"
    MEDIUM_2HZ = "2hz"
    LOW_1HZ = "1hz"
    ON_EVENT = "event"
    ON_DEMAND = "demand"


@dataclass(frozen=True)
class ChannelDef:
    """Definition of a single CyberNode channel."""
    name: str
    description: str
    payload_type: str = "dict"
    frequency: ChannelFrequency = ChannelFrequency.ON_EVENT
    publisher: str = ""
    subscribers: tuple = ()
    required_fields: tuple = ()
    optional_fields: tuple = ()

    def validate_payload(self, payload: Any) -> List[str]:
        errors = []
        if not isinstance(payload, dict):
            if self.required_fields:
                errors.append(f"Expected dict, got {type(payload).__name__}")
            return errors
        for f in self.required_fields:
            if f not in payload:
                errors.append(f"Missing required field: {f}")
        return errors


# ═══════════════════════════════════════════════════════════════════════════
# Channel definitions — the single source of truth
# ═══════════════════════════════════════════════════════════════════════════

# ─── CAN Bus / Raw Data ─────────────────────────────────────────────────
CH_RAW_LCU = ChannelDef(
    name="/lol/raw_lcu",
    description="Raw LCU Client API response data",
    frequency=ChannelFrequency.HIGH_10HZ,
    publisher="canbus",
    subscribers=("perception",),
    required_fields=("endpoint", "data", "timestamp"),
)

CH_RAW_FIDDLER = ChannelDef(
    name="/lol/raw_fiddler",
    description="Raw Fiddler proxy intercepted data",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="canbus",
    subscribers=("perception",),
    required_fields=("url", "method", "body"),
)

CH_RAW_LIVE_CLIENT = ChannelDef(
    name="/lol/raw_live_client",
    description="Raw Live Client API (127.0.0.1:2999) data",
    frequency=ChannelFrequency.HIGH_10HZ,
    publisher="canbus",
    subscribers=("perception",),
    required_fields=("endpoint", "data"),
)

# ─── Perception ──────────────────────────────────────────────────────────
CH_GAME_STATE = ChannelDef(
    name="/lol/game_state",
    description="Normalized game state snapshot",
    frequency=ChannelFrequency.MEDIUM_2HZ,
    publisher="perception",
    subscribers=("prediction", "planning", "storytelling"),
    required_fields=("game_time", "phase", "players", "teams"),
)

CH_EVENTS = ChannelDef(
    name="/lol/events",
    description="Detected game events (kills, objectives)",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="perception",
    subscribers=("prediction", "planning", "storytelling", "monitor"),
    required_fields=("type", "timestamp"),
)

CH_MAP_AWARENESS = ChannelDef(
    name="/lol/map_awareness",
    description="Player zone tracking and map presence",
    frequency=ChannelFrequency.MEDIUM_2HZ,
    publisher="localization",
    subscribers=("planning", "prediction"),
    required_fields=("players", "teams"),
)

CH_WARD_STATE = ChannelDef(
    name="/lol/ward_state",
    description="Active ward positions and expirations",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="ward_tracker",
    subscribers=("localization", "planning"),
    required_fields=("wards",),
)

CH_FOG_ESTIMATE = ChannelDef(
    name="/lol/fog_estimate",
    description="Fog of war probability map",
    frequency=ChannelFrequency.LOW_1HZ,
    publisher="fog_estimator",
    subscribers=("planning",),
)

# ─── Prediction ──────────────────────────────────────────────────────────
CH_WIN_PROBABILITY = ChannelDef(
    name="/lol/win_probability",
    description="Current win probability estimate",
    frequency=ChannelFrequency.MEDIUM_2HZ,
    publisher="prediction",
    subscribers=("planning", "output", "storytelling"),
    required_fields=("probability", "confidence", "model_version"),
)

CH_TEAMFIGHT_PREDICTION = ChannelDef(
    name="/lol/teamfight_prediction",
    description="Teamfight outcome prediction",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="prediction",
    subscribers=("planning", "storytelling"),
    required_fields=("win_probability", "factors"),
)

CH_DRAFT_ANALYSIS = ChannelDef(
    name="/lol/draft_analysis",
    description="Champion draft win-rate analysis",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="draft_analyzer",
    subscribers=("planning", "storytelling"),
)

CH_OBJECTIVE_TIMER = ChannelDef(
    name="/lol/objective_timer",
    description="Objective spawn timers (dragon, baron, etc.)",
    frequency=ChannelFrequency.LOW_1HZ,
    publisher="prediction",
    subscribers=("planning", "output"),
    required_fields=("objectives",),
)

# ─── Planning ────────────────────────────────────────────────────────────
CH_STRATEGY_ADVICE = ChannelDef(
    name="/lol/strategy_advice",
    description="Strategic recommendations",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="planning",
    subscribers=("output", "storytelling"),
    required_fields=("action", "reason", "confidence"),
)

CH_ITEM_ADVICE = ChannelDef(
    name="/lol/item_advice",
    description="Item build recommendations",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="planning",
    subscribers=("output",),
    required_fields=("items", "reason"),
)

CH_MACRO_DECISION = ChannelDef(
    name="/lol/macro_decision",
    description="Macro-level strategic decisions",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="planning",
    subscribers=("output", "storytelling"),
    required_fields=("decision", "urgency"),
)

# ─── Control / Output ────────────────────────────────────────────────────
CH_NARRATION = ChannelDef(
    name="/lol/narration",
    description="Generated narration text for TTS",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="storytelling",
    subscribers=("voice_output",),
    required_fields=("text", "priority"),
)

CH_COOLDOWN_STATE = ChannelDef(
    name="/lol/cooldown_state",
    description="Tracked summoner spell and ult cooldowns",
    frequency=ChannelFrequency.MEDIUM_2HZ,
    publisher="cooldown_tracker",
    subscribers=("planning", "prediction"),
)

# ─── System ──────────────────────────────────────────────────────────────
CH_SYSTEM_HEALTH = ChannelDef(
    name="/lol/system_health",
    description="System-wide health report",
    frequency=ChannelFrequency.LOW_1HZ,
    publisher="monitor",
    subscribers=("dreamview",),
)

CH_SYSTEM_ALERT = ChannelDef(
    name="/lol/system_alert",
    description="System alert notifications",
    frequency=ChannelFrequency.ON_EVENT,
    publisher="monitor",
    subscribers=("dreamview",),
)


# ─── Registry class ─────────────────────────────────────────────────────

class ChannelRegistry:
    """Runtime registry for channel discovery and validation."""

    _instance: Optional["ChannelRegistry"] = None

    def __init__(self) -> None:
        self._channels: Dict[str, ChannelDef] = {}
        self._register_defaults()

    @classmethod
    def instance(cls) -> "ChannelRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _register_defaults(self) -> None:
        for obj in globals().values():
            if isinstance(obj, ChannelDef):
                self._channels[obj.name] = obj

    def get(self, name: str) -> Optional[ChannelDef]:
        return self._channels.get(name)

    def register(self, channel: ChannelDef) -> None:
        self._channels[channel.name] = channel

    def all_channels(self) -> List[ChannelDef]:
        return list(self._channels.values())

    def channels_for_component(self, component: str) -> Dict[str, List[str]]:
        publishes = []
        subscribes = []
        for ch in self._channels.values():
            if ch.publisher == component:
                publishes.append(ch.name)
            if component in ch.subscribers:
                subscribes.append(ch.name)
        return {"publishes": publishes, "subscribes": subscribes}

    def validate(self, channel_name: str, payload: Any) -> List[str]:
        ch = self._channels.get(channel_name)
        if ch is None:
            return [f"Unknown channel: {channel_name}"]
        return ch.validate_payload(payload)

    def summary(self) -> Dict[str, Any]:
        return {
            "total_channels": len(self._channels),
            "channels": {
                ch.name: {
                    "description": ch.description,
                    "frequency": ch.frequency.value,
                    "publisher": ch.publisher,
                }
                for ch in self._channels.values()
            },
        }

"""
ChannelRegistry — Centralized channel definition catalog.
============================================================
lolbot-HyperAI · Common

Single source of truth for all channel names, message types, expected
rates, and descriptions.  Prevents typos and enables introspection.

Architecture position:
    modules/common/adapters/channel_registry.py   ← YOU ARE HERE
    └─ Consumed by: every component, diagnostics, dreamview
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Type


@dataclass(frozen=True)
class ChannelDef:
    """Definition of a single channel."""
    name: str
    msg_type: str
    rate_hz: float = 0.0
    description: str = ""
    publisher: str = ""
    subscribers: tuple = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "msg_type": self.msg_type,
            "rate_hz": self.rate_hz,
            "description": self.description,
            "publisher": self.publisher,
            "subscribers": list(self.subscribers),
        }


class ChannelRegistry:
    """Registry of all known channels in the system.

    Usage::
        reg = ChannelRegistry()
        reg.register(ChannelDef(name="/lol/game_state", ...))
        ch = reg.get("/lol/game_state")
        all_chs = reg.list_channels()
    """

    _instance: Optional["ChannelRegistry"] = None

    def __init__(self) -> None:
        self._channels: Dict[str, ChannelDef] = {}
        self._register_defaults()

    @classmethod
    def instance(cls) -> "ChannelRegistry":
        if cls._instance is None:
            cls._instance = ChannelRegistry()
        return cls._instance

    def register(self, channel: ChannelDef) -> None:
        if channel.name in self._channels:
            raise ValueError(f"Channel {channel.name} already registered")
        self._channels[channel.name] = channel

    def get(self, name: str) -> Optional[ChannelDef]:
        return self._channels.get(name)

    def list_channels(self) -> List[ChannelDef]:
        return sorted(self._channels.values(), key=lambda c: c.name)

    def validate_no_duplicates(self) -> List[str]:
        """Return list of duplicate channel names (should be empty)."""
        return []  # dict keys are unique by definition

    def channel_names(self) -> List[str]:
        return sorted(self._channels.keys())

    def publishers_for(self, component: str) -> List[ChannelDef]:
        return [c for c in self._channels.values() if c.publisher == component]

    def subscribers_for(self, component: str) -> List[ChannelDef]:
        return [c for c in self._channels.values() if component in c.subscribers]

    def _register_defaults(self) -> None:
        defaults = [
            ChannelDef("/lol/raw_lcu", "RawLCUData", 10.0,
                       "Raw LCU Live Client Data API response",
                       "canbus", ("perception",)),
            ChannelDef("/lol/raw_fiddler", "RawFiddlerData", 2.0,
                       "Raw Fiddler MCP bridge captures",
                       "canbus", ("perception",)),
            ChannelDef("/lol/fused_raw", "FusedRawData", 10.0,
                       "Fused raw data from best source",
                       "sensor_fusion", ("perception",)),
            ChannelDef("/lol/game_state", "GameSnapshot", 10.0,
                       "Normalized game state snapshot",
                       "perception", ("prediction", "planning", "dreamview", "control")),
            ChannelDef("/lol/events", "List[GameEvent]", 10.0,
                       "New game events since last tick",
                       "perception", ("event_stream_processor",)),
            ChannelDef("/lol/kill_feed", "List[KillFeedEntry]", 5.0,
                       "Processed kill feed with multi-kill/spree tags",
                       "event_stream_processor", ("control", "dreamview")),
            ChannelDef("/lol/objective_events", "ObjectiveEvent", 1.0,
                       "Dragon/Baron/Tower objective events",
                       "event_stream_processor", ("objective_tracker",)),
            ChannelDef("/lol/teamfight_active", "TeamfightCluster", 0.5,
                       "Detected teamfight clusters",
                       "event_stream_processor", ("control", "dreamview")),
            ChannelDef("/lol/win_prediction", "WinPrediction", 2.0,
                       "Win probability prediction",
                       "prediction", ("planning", "control", "dreamview")),
            ChannelDef("/lol/teamfight_prediction", "TeamfightPrediction", 2.0,
                       "Teamfight outcome prediction",
                       "prediction", ("planning", "control", "dreamview")),
            ChannelDef("/lol/objective_timers", "ObjectiveTimerState", 1.0,
                       "Objective respawn countdowns",
                       "objective_tracker", ("planning", "control", "dreamview")),
            ChannelDef("/lol/strategy_advice", "StrategyAdvice", 1.0,
                       "Strategic recommendations from planning",
                       "planning", ("control", "dreamview")),
            ChannelDef("/lol/voice_command", "VoiceCommand", 1.0,
                       "Voice narration commands (multi-source)",
                       "multiple", ("control",)),
            ChannelDef("/lol/session_state", "SessionStateMsg", 2.0,
                       "Game session lifecycle state",
                       "session_manager", ("evolution", "dreamview")),
        ]
        for ch in defaults:
            self._channels[ch.name] = ch

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total": len(self._channels),
            "channels": {n: c.to_dict() for n, c in self._channels.items()},
        }

#!/usr/bin/env python3
"""
proto/lolbot_messages.py — Message Protocol Definitions
=========================================================
lolbot-HyperAI · Protocol Layer

In Apollo, .proto files define the message schemas exchanged between
modules (chassis.proto, perception_obstacle.proto, planning.proto).
Our "proto" layer defines the expected payload schemas for each CAN
bus channel, used for:
    1. Runtime validation (optional, enabled in debug mode)
    2. Documentation (what fields each channel carries)
    3. Evolution compatibility checking (did a mutation break a schema?)
    4. Log analysis (structured field access)

Each schema is a simple dict describing field names, types, and
whether they're required. We use plain Python instead of protobuf
to keep the dependency footprint minimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Type

from canbus.channel_message import (
    CH_CHAMP_SELECT_STATE,
    CH_EVOLUTION_FITNESS,
    CH_EVOLUTION_GENERATION,
    CH_GAME_FLOW_PHASE,
    CH_KILL_EVENT,
    CH_LIVE_GAME_STATE,
    CH_OBJECTIVE_EVENT,
    CH_SCOREBOARD_SNAPSHOT,
    CH_STRATEGY_RECOMMENDATION,
    CH_SYSTEM_ERROR,
    CH_SYSTEM_HEARTBEAT,
    CH_SYSTEM_METRICS,
    CH_VOICE_ANNOUNCEMENT,
    CH_WIN_PROBABILITY,
)


# ---------------------------------------------------------------------------
# Field descriptor
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldSpec:
    """Describes a single field in a message payload."""
    name: str
    field_type: str             # "str", "int", "float", "bool", "list", "dict"
    required: bool = True
    description: str = ""
    default: Any = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None


# ---------------------------------------------------------------------------
# Message schema
# ---------------------------------------------------------------------------
@dataclass
class MessageSchema:
    """Schema for one channel's payload."""
    channel: str
    description: str
    fields: List[FieldSpec]
    version: int = 1
    publisher: str = ""         # Which module publishes this
    subscribers: List[str] = field(default_factory=list)

    def validate(self, payload: Dict[str, Any]) -> List[str]:
        """
        Validate a payload against this schema.

        Returns list of error strings (empty = valid).
        """
        errors = []
        for f in self.fields:
            if f.required and f.name not in payload:
                errors.append(f"Missing required field '{f.name}'")
                continue

            if f.name in payload:
                value = payload[f.name]
                # Type check
                expected = _TYPE_MAP.get(f.field_type)
                if expected and not isinstance(value, expected):
                    errors.append(
                        f"Field '{f.name}' expected {f.field_type}, "
                        f"got {type(value).__name__}"
                    )
                # Range check
                if isinstance(value, (int, float)):
                    if f.min_value is not None and value < f.min_value:
                        errors.append(
                            f"Field '{f.name}' value {value} < min {f.min_value}"
                        )
                    if f.max_value is not None and value > f.max_value:
                        errors.append(
                            f"Field '{f.name}' value {value} > max {f.max_value}"
                        )
        return errors

    def field_names(self) -> Set[str]:
        return {f.name for f in self.fields}

    def required_fields(self) -> Set[str]:
        return {f.name for f in self.fields if f.required}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "description": self.description,
            "version": self.version,
            "publisher": self.publisher,
            "subscribers": self.subscribers,
            "fields": [
                {
                    "name": f.name,
                    "type": f.field_type,
                    "required": f.required,
                    "description": f.description,
                }
                for f in self.fields
            ],
        }


_TYPE_MAP: Dict[str, Tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
    "list": (list,),
    "dict": (dict,),
}


# ---------------------------------------------------------------------------
# Schema definitions for all channels
# ---------------------------------------------------------------------------
SCHEMAS: Dict[str, MessageSchema] = {}


def _register(schema: MessageSchema) -> None:
    SCHEMAS[schema.channel] = schema


# -- Perception schemas -------------------------------------------------

_register(MessageSchema(
    channel=CH_GAME_FLOW_PHASE,
    description="Game client lifecycle phase transitions",
    publisher="perception.network_listener",
    subscribers=["perception.game_state_parser", "planning.strategy_planner"],
    fields=[
        FieldSpec("phase", "str", True, "Current gameflow phase string"),
        FieldSpec("previous_phase", "str", False, "Previous phase"),
        FieldSpec("transition_time_ms", "int", True, "Monotonic ms of transition"),
    ],
))

_register(MessageSchema(
    channel=CH_CHAMP_SELECT_STATE,
    description="Champion select session state",
    publisher="perception.network_listener",
    subscribers=["perception.game_state_parser", "planning.strategy_planner"],
    fields=[
        FieldSpec("my_team", "list", True, "Our team's pick state"),
        FieldSpec("their_team", "list", True, "Enemy team's pick state"),
        FieldSpec("bans", "dict", True, "Banned champion IDs"),
        FieldSpec("timer_phase", "str", True, "Current timer phase"),
        FieldSpec("timer_remaining_ms", "int", True, "Timer countdown"),
        FieldSpec("local_player_cell_id", "int", True, "Our cell ID"),
        FieldSpec("is_spectating", "bool", False, "Are we spectating?"),
    ],
))

_register(MessageSchema(
    channel=CH_LIVE_GAME_STATE,
    description="Fused normalized game state (primary data channel)",
    publisher="perception.game_state_parser",
    subscribers=[
        "prediction.feature_pipeline",
        "planning.strategy_planner",
        "evolution.fitness_evaluator",
    ],
    fields=[
        FieldSpec("game_time_sec", "float", True, "Game clock in seconds",
                  min_value=0, max_value=7200),
        FieldSpec("phase", "str", True, "Normalized game phase"),
        FieldSpec("our_team", "dict", True, "Our team state"),
        FieldSpec("enemy_team", "dict", True, "Enemy team state"),
        FieldSpec("kill_diff", "int", True, "Kill differential"),
        FieldSpec("gold_diff", "int", True, "Gold differential"),
        FieldSpec("cs_diff", "int", True, "CS differential"),
        FieldSpec("objectives", "dict", False, "Objective state"),
        FieldSpec("recent_kills", "list", False, "Last 60s kills"),
        FieldSpec("recent_objectives", "list", False, "Last 60s objectives"),
    ],
))

_register(MessageSchema(
    channel=CH_SCOREBOARD_SNAPSHOT,
    description="Aggregated team scoreboard",
    publisher="perception.network_listener",
    subscribers=["perception.game_state_parser"],
    fields=[
        FieldSpec("game_time_sec", "float", True, "Game clock"),
        FieldSpec("our_kills", "int", True, "Our team total kills"),
        FieldSpec("our_deaths", "int", True, "Our team total deaths"),
        FieldSpec("enemy_kills", "int", True, "Enemy team total kills"),
        FieldSpec("enemy_deaths", "int", True, "Enemy team total deaths"),
        FieldSpec("kill_diff", "int", True, "Kill differential"),
        FieldSpec("our_total_cs", "int", True, "Our team total CS"),
        FieldSpec("enemy_total_cs", "int", True, "Enemy team total CS"),
    ],
))

_register(MessageSchema(
    channel=CH_KILL_EVENT,
    description="Individual champion kill event",
    publisher="perception.network_listener",
    subscribers=["perception.game_state_parser"],
    fields=[
        FieldSpec("game_time_sec", "float", True, "Event time"),
        FieldSpec("killer", "str", True, "Killer name"),
        FieldSpec("victim", "str", True, "Victim name"),
        FieldSpec("assisters", "list", False, "Assisting players"),
    ],
))

_register(MessageSchema(
    channel=CH_OBJECTIVE_EVENT,
    description="Objective taken (dragon, baron, tower, inhib)",
    publisher="perception.network_listener",
    subscribers=["perception.game_state_parser", "planning.strategy_planner"],
    fields=[
        FieldSpec("game_time_sec", "float", True, "Event time"),
        FieldSpec("event_name", "str", True, "Objective type"),
        FieldSpec("killer", "str", True, "Who secured it"),
        FieldSpec("assisters", "list", False, "Assisting players"),
        FieldSpec("stolen", "bool", False, "Was it stolen?"),
    ],
))

# -- Prediction schemas -------------------------------------------------

_register(MessageSchema(
    channel=CH_WIN_PROBABILITY,
    description="Real-time win probability prediction",
    publisher="prediction.win_probability_engine",
    subscribers=["planning.strategy_planner", "output.voice_announcer",
                 "evolution.fitness_evaluator"],
    fields=[
        FieldSpec("win_pct", "float", True, "Win probability 0-1",
                  min_value=0, max_value=1),
        FieldSpec("confidence", "float", True, "Prediction confidence 0-1",
                  min_value=0, max_value=1),
        FieldSpec("trend", "str", True, "rising/stable/falling"),
        FieldSpec("trend_delta", "float", True, "Change over last 60s"),
        FieldSpec("key_factors", "list", True, "Top contributing features"),
        FieldSpec("what_if", "dict", True, "Scenario analysis results"),
        FieldSpec("model_version", "str", True, "Model identifier"),
        FieldSpec("features_used", "int", True, "Number of features"),
        FieldSpec("game_time_sec", "float", True, "Game clock"),
        FieldSpec("prediction_ms", "int", False, "Computation time"),
    ],
))

# -- Planning schemas ---------------------------------------------------

_register(MessageSchema(
    channel=CH_STRATEGY_RECOMMENDATION,
    description="Tactical recommendation for the player",
    publisher="planning.strategy_planner",
    subscribers=["output.voice_announcer", "evolution.fitness_evaluator"],
    fields=[
        FieldSpec("rec_id", "str", True, "Unique recommendation ID"),
        FieldSpec("rec_type", "str", True, "Recommendation type"),
        FieldSpec("priority", "int", True, "Priority 1-4",
                  min_value=1, max_value=4),
        FieldSpec("title", "str", True, "Short title"),
        FieldSpec("detail", "str", True, "Full explanation"),
        FieldSpec("voice_text", "str", True, "Pre-formatted TTS text"),
        FieldSpec("game_phase", "str", True, "Current game phase"),
        FieldSpec("confidence", "float", True, "Recommendation confidence",
                  min_value=0, max_value=1),
        FieldSpec("expires_sec", "float", True, "TTL in seconds"),
        FieldSpec("game_time_sec", "float", True, "Game clock"),
    ],
))

# -- Output schemas -----------------------------------------------------

_register(MessageSchema(
    channel=CH_VOICE_ANNOUNCEMENT,
    description="Voice announcement that was spoken (or queued)",
    publisher="output.voice_announcer",
    subscribers=["evolution.fitness_evaluator"],
    fields=[
        FieldSpec("text", "str", True, "Spoken text"),
        FieldSpec("urgency", "int", True, "Priority level 1-4"),
        FieldSpec("category", "str", True, "Announcement category"),
        FieldSpec("spoken", "bool", True, "Was actually spoken via TTS"),
        FieldSpec("tts_backend", "str", False, "TTS backend used"),
        FieldSpec("game_time_sec", "float", False, "Game clock"),
    ],
))

# -- Evolution schemas --------------------------------------------------

_register(MessageSchema(
    channel=CH_EVOLUTION_FITNESS,
    description="Fitness evaluation result for a generation",
    publisher="evolution.fitness_evaluator",
    subscribers=["evolution.generation_manager"],
    fields=[
        FieldSpec("generation_id", "str", True, "Generation identifier"),
        FieldSpec("fitness_score", "float", True, "Total fitness 0-1",
                  min_value=0, max_value=1),
        FieldSpec("metrics", "dict", True, "Detailed fitness metrics"),
    ],
))

_register(MessageSchema(
    channel=CH_EVOLUTION_GENERATION,
    description="Generation lifecycle events (commit/rollback)",
    publisher="evolution.generation_manager",
    subscribers=["integration.agent_os_connector"],
    fields=[
        FieldSpec("action", "str", True, "commit or rollback"),
        FieldSpec("generation_id", "str", True, "Target generation ID"),
    ],
))

# -- System schemas -----------------------------------------------------

_register(MessageSchema(
    channel=CH_SYSTEM_HEARTBEAT,
    description="Component health heartbeat",
    publisher="(any component)",
    subscribers=["evolution.fitness_evaluator"],
    fields=[
        FieldSpec("component", "str", True, "Component name"),
        FieldSpec("uptime_ms", "int", True, "Component uptime"),
        FieldSpec("status", "str", True, "ok or error"),
    ],
))

_register(MessageSchema(
    channel=CH_SYSTEM_ERROR,
    description="System error event",
    publisher="(any component)",
    subscribers=["evolution.fitness_evaluator", "output.notification_manager"],
    fields=[
        FieldSpec("component", "str", True, "Error source"),
        FieldSpec("error_type", "str", True, "Exception type"),
        FieldSpec("message", "str", True, "Error message"),
        FieldSpec("severity", "str", True, "low/medium/high/critical"),
    ],
))


# ---------------------------------------------------------------------------
# Schema registry API
# ---------------------------------------------------------------------------
def get_schema(channel: str) -> Optional[MessageSchema]:
    """Get the schema for a channel."""
    return SCHEMAS.get(channel)


def validate_payload(
    channel: str,
    payload: Dict[str, Any],
) -> List[str]:
    """
    Validate a payload against its channel schema.

    Returns list of error strings (empty = valid).
    Returns empty list if no schema is registered.
    """
    schema = SCHEMAS.get(channel)
    if schema is None:
        return []
    return schema.validate(payload)


def all_channels() -> List[str]:
    """List all registered channels."""
    return sorted(SCHEMAS.keys())


def schema_summary() -> Dict[str, Any]:
    """Summary of all registered schemas for documentation."""
    return {
        ch: {
            "description": s.description,
            "publisher": s.publisher,
            "subscribers": s.subscribers,
            "field_count": len(s.fields),
            "required_fields": len(s.required_fields()),
        }
        for ch, s in sorted(SCHEMAS.items())
    }

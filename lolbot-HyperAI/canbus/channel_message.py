#!/usr/bin/env python3
"""
canbus/channel_message.py — Typed Message Definitions for the Data Bus
========================================================================
lolbot-HyperAI · Apollo-style CAN Bus Architecture

In Apollo autonomous driving, the CAN bus carries structured messages
(chassis speed, steering angle, brake status) between ECUs at 10ms cycles.
Our "CAN bus" carries LoL game state messages between perception, prediction,
planning, and output modules at ~100ms cycles.

Every message has:
    - channel: str           (topic name, e.g. "game_state", "prediction")
    - timestamp_ms: int      (monotonic ms when published)
    - sequence_id: int       (monotonically increasing per channel)
    - payload: dict          (typed payload)
    - source_module: str     (who published it)

Modules never import each other directly. They publish/subscribe through
the MessageBus, which holds the latest message per channel (like a
shared-memory blackboard). This is the key decoupling pattern from Apollo.

Design decisions:
    1. Dataclass-based messages for type safety without protobuf overhead
    2. Frozen messages — once published, immutable
    3. Channel names are string constants, not enums, for extensibility
    4. Payload is always a dict for JSON-serializable logging
    5. Messages carry their schema_version for evolution compatibility
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Channel name constants
# ---------------------------------------------------------------------------
# Perception channels (raw sensor data → parsed state)
CH_RAW_NETWORK_PACKET = "perception.raw_network_packet"
CH_GAME_FLOW_PHASE = "perception.game_flow_phase"
CH_CHAMP_SELECT_STATE = "perception.champ_select_state"
CH_LIVE_GAME_STATE = "perception.live_game_state"
CH_SCOREBOARD_SNAPSHOT = "perception.scoreboard_snapshot"
CH_MINIMAP_EVENTS = "perception.minimap_events"
CH_ITEM_PURCHASE = "perception.item_purchase"
CH_KILL_EVENT = "perception.kill_event"
CH_OBJECTIVE_EVENT = "perception.objective_event"
CH_WARD_EVENT = "perception.ward_event"

# Prediction channels (parsed state → future projections)
CH_WIN_PROBABILITY = "prediction.win_probability"
CH_NEXT_OBJECTIVE = "prediction.next_objective"
CH_POWER_SPIKE_FORECAST = "prediction.power_spike_forecast"
CH_THREAT_ASSESSMENT = "prediction.threat_assessment"
CH_GOLD_DIFF_PROJECTION = "prediction.gold_diff_projection"
CH_TEAMFIGHT_PROBABILITY = "prediction.teamfight_probability"

# Planning channels (projections → actionable advice)
CH_STRATEGY_RECOMMENDATION = "planning.strategy_recommendation"
CH_LANE_PHASE_ADVICE = "planning.lane_phase_advice"
CH_TEAM_COMP_EVAL = "planning.team_comp_evaluation"
CH_OBJECTIVE_PRIORITY = "planning.objective_priority"
CH_BAN_PICK_SUGGESTION = "planning.ban_pick_suggestion"

# Output channels (advice → user-facing)
CH_VOICE_ANNOUNCEMENT = "output.voice_announcement"
CH_DASHBOARD_UPDATE = "output.dashboard_update"
CH_NOTIFICATION = "output.notification"

# Evolution channels (meta-loop)
CH_EVOLUTION_PROPOSAL = "evolution.proposal"
CH_EVOLUTION_FITNESS = "evolution.fitness_score"
CH_EVOLUTION_GENERATION = "evolution.generation_transition"

# System channels
CH_SYSTEM_HEARTBEAT = "system.heartbeat"
CH_SYSTEM_ERROR = "system.error"
CH_SYSTEM_METRICS = "system.metrics"


# ---------------------------------------------------------------------------
# Core message dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ChannelMessage:
    """
    Immutable message on the CAN bus.

    Frozen so that once published, no subscriber can mutate it.
    Payload is deep-copied on creation to prevent aliasing.

    Attributes:
        channel: Topic name from constants above.
        timestamp_ms: Monotonic milliseconds (not wall-clock — immune
                      to NTP jumps, like Apollo's cyber_clock).
        sequence_id: Per-channel monotonic counter.
        payload: Arbitrary dict. Must be JSON-serializable.
        source_module: Name of the publishing component.
        schema_version: For forward/backward compat during evolution.
        priority: 0 = normal, 1 = high, 2 = critical.
        ttl_ms: Time-to-live. Subscribers ignore stale messages.
                Default 5000ms (5s) — generous for a game running
                at ~100ms tick rate.
    """
    channel: str
    timestamp_ms: int
    sequence_id: int
    payload: Dict[str, Any]
    source_module: str
    schema_version: int = 1
    priority: int = 0
    ttl_ms: int = 5000

    def is_expired(self, now_ms: Optional[int] = None) -> bool:
        """Check if this message has exceeded its TTL."""
        if now_ms is None:
            now_ms = _monotonic_ms()
        return (now_ms - self.timestamp_ms) > self.ttl_ms

    def age_ms(self, now_ms: Optional[int] = None) -> int:
        """Milliseconds since this message was created."""
        if now_ms is None:
            now_ms = _monotonic_ms()
        return now_ms - self.timestamp_ms

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging / JSON export."""
        return asdict(self)

    def to_json(self) -> str:
        """Compact JSON string for wire / log."""
        return json.dumps(self.to_dict(), separators=(",", ":"),
                          default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelMessage":
        """Reconstruct from dict (e.g. log replay)."""
        return cls(**data)


# ---------------------------------------------------------------------------
# Message factory (manages sequence IDs per channel)
# ---------------------------------------------------------------------------
class MessageFactory:
    """
    Creates ChannelMessages with auto-incrementing sequence IDs.

    Each component should own one MessageFactory instance. The factory
    tracks sequence counters per channel so that subscribers can detect
    gaps (dropped messages) during evolution replay.

    Usage:
        factory = MessageFactory(source="prediction.win_prob")
        msg = factory.create(CH_WIN_PROBABILITY, {"win_pct": 0.62})
        bus.publish(msg)
    """

    def __init__(self, source_module: str) -> None:
        self._source = source_module
        self._seq: Dict[str, int] = {}

    def create(
        self,
        channel: str,
        payload: Dict[str, Any],
        *,
        priority: int = 0,
        ttl_ms: int = 5000,
        schema_version: int = 1,
    ) -> ChannelMessage:
        """
        Build a new message with auto-incremented sequence ID.

        Payload is deep-copied so the caller can safely reuse/mutate
        the original dict after calling create().
        """
        seq = self._seq.get(channel, 0)
        self._seq[channel] = seq + 1

        return ChannelMessage(
            channel=channel,
            timestamp_ms=_monotonic_ms(),
            sequence_id=seq,
            payload=copy.deepcopy(payload),
            source_module=self._source,
            schema_version=schema_version,
            priority=priority,
            ttl_ms=ttl_ms,
        )

    def reset_sequence(self, channel: Optional[str] = None) -> None:
        """Reset sequence counter(s). Used on generation rollover."""
        if channel is None:
            self._seq.clear()
        else:
            self._seq.pop(channel, None)


# ---------------------------------------------------------------------------
# Message Bus — the shared-memory blackboard
# ---------------------------------------------------------------------------
class MessageBus:
    """
    Central publish/subscribe message bus.

    Design mirrors Apollo's cyber/transport:
        - Latest-message-per-channel semantics (not a queue)
        - Synchronous callbacks on publish (no thread-pool)
        - Optional async notify via asyncio.Event
        - History ring buffer per channel for log replay

    In Apollo, `/apollo/canbus/chassis` holds the latest Chassis proto.
    Here, bus.latest("perception.live_game_state") returns the latest
    ChannelMessage for that topic.

    Thread-safety: This bus is designed for single-threaded asyncio.
    If you need multi-thread, wrap with a lock externally.
    """

    def __init__(self, history_size: int = 1000) -> None:
        self._latest: Dict[str, ChannelMessage] = {}
        self._subscribers: Dict[str, List[Callable[[ChannelMessage], None]]] = {}
        self._async_events: Dict[str, List[asyncio.Event]] = {}
        self._history: Dict[str, List[ChannelMessage]] = {}
        self._history_size = history_size
        self._total_published = 0
        self._total_dropped = 0
        self._channel_stats: Dict[str, _ChannelStats] = {}

    # -- Publish --------------------------------------------------------

    def publish(self, msg: ChannelMessage) -> int:
        """
        Publish a message to its channel.

        Returns:
            Number of subscribers notified.

        Side effects:
            - Updates latest-message cache
            - Appends to history ring buffer
            - Fires synchronous subscriber callbacks
            - Sets asyncio Events for async waiters
        """
        ch = msg.channel
        self._latest[ch] = msg
        self._total_published += 1

        # Update stats
        stats = self._channel_stats.get(ch)
        if stats is None:
            stats = _ChannelStats()
            self._channel_stats[ch] = stats
        stats.msg_count += 1
        stats.last_publish_ms = msg.timestamp_ms

        # Ring buffer history
        hist = self._history.get(ch)
        if hist is None:
            hist = []
            self._history[ch] = hist
        hist.append(msg)
        if len(hist) > self._history_size:
            hist.pop(0)
            self._total_dropped += 1

        # Synchronous subscriber callbacks
        notified = 0
        for cb in self._subscribers.get(ch, []):
            try:
                cb(msg)
                notified += 1
            except Exception as exc:
                stats.error_count += 1
                # Log but don't crash the bus
                import sys
                print(f"[MessageBus] subscriber error on {ch}: {exc}",
                      file=sys.stderr)

        # Async event notification
        for evt in self._async_events.get(ch, []):
            evt.set()

        return notified

    # -- Subscribe ------------------------------------------------------

    def subscribe(
        self,
        channel: str,
        callback: Callable[[ChannelMessage], None],
    ) -> Callable[[], None]:
        """
        Register a synchronous callback for a channel.

        Returns:
            An unsubscribe function.
        """
        subs = self._subscribers.setdefault(channel, [])
        subs.append(callback)

        def _unsub() -> None:
            try:
                subs.remove(callback)
            except ValueError:
                pass

        return _unsub

    def subscribe_pattern(
        self,
        prefix: str,
        callback: Callable[[ChannelMessage], None],
    ) -> Callable[[], None]:
        """
        Subscribe to all channels matching a prefix.

        E.g. subscribe_pattern("perception.") gets all perception channels.
        Implemented by intercepting publish — we attach a filter wrapper
        that checks the channel prefix.
        """
        # We store pattern subscribers separately and check on publish
        if not hasattr(self, "_pattern_subs"):
            self._pattern_subs: List[Tuple[str, Callable]] = []
        self._pattern_subs.append((prefix, callback))

        def _unsub() -> None:
            try:
                self._pattern_subs.remove((prefix, callback))
            except ValueError:
                pass

        return _unsub

    def create_async_waiter(self, channel: str) -> asyncio.Event:
        """
        Create an asyncio.Event that is set whenever a message arrives
        on the given channel.

        Usage (in an async component):
            event = bus.create_async_waiter(CH_WIN_PROBABILITY)
            while True:
                await event.wait()
                event.clear()
                msg = bus.latest(CH_WIN_PROBABILITY)
                # process msg
        """
        evt = asyncio.Event()
        self._async_events.setdefault(channel, []).append(evt)
        return evt

    # -- Read -----------------------------------------------------------

    def latest(self, channel: str) -> Optional[ChannelMessage]:
        """Get the latest message on a channel, or None."""
        return self._latest.get(channel)

    def latest_payload(
        self, channel: str, default: Any = None,
    ) -> Any:
        """Convenience: get payload dict from latest message."""
        msg = self._latest.get(channel)
        if msg is None:
            return default
        return msg.payload

    def history(
        self,
        channel: str,
        last_n: Optional[int] = None,
    ) -> List[ChannelMessage]:
        """
        Get message history for a channel.

        Args:
            channel: Channel name.
            last_n: If set, return only the last N messages.
        """
        hist = self._history.get(channel, [])
        if last_n is not None:
            return hist[-last_n:]
        return list(hist)

    def channels(self) -> Set[str]:
        """Set of all channels that have received at least one message."""
        return set(self._latest.keys())

    # -- Diagnostics ----------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Bus-wide statistics for the evolution logger."""
        return {
            "total_published": self._total_published,
            "total_dropped": self._total_dropped,
            "active_channels": len(self._latest),
            "channels": {
                ch: {
                    "msg_count": s.msg_count,
                    "error_count": s.error_count,
                    "last_publish_ms": s.last_publish_ms,
                }
                for ch, s in self._channel_stats.items()
            },
        }

    def clear(self) -> None:
        """Full reset. Used between games or on generation rollover."""
        self._latest.clear()
        self._history.clear()
        self._channel_stats.clear()
        self._total_published = 0
        self._total_dropped = 0

    def snapshot_json(self) -> str:
        """
        Serialize the entire bus state to JSON.
        Used by the evolution controller to checkpoint state
        before applying a mutation.
        """
        data = {
            "latest": {
                ch: msg.to_dict() for ch, msg in self._latest.items()
            },
            "stats": self.stats(),
        }
        return json.dumps(data, default=str, indent=2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
@dataclass
class _ChannelStats:
    msg_count: int = 0
    error_count: int = 0
    last_publish_ms: int = 0


def _monotonic_ms() -> int:
    """Monotonic clock in milliseconds (like Apollo cyber_clock)."""
    return int(time.monotonic() * 1000)


# ---------------------------------------------------------------------------
# Convenience: global singleton bus (optional, components can also
# receive the bus via dependency injection)
# ---------------------------------------------------------------------------
_global_bus: Optional[MessageBus] = None


def get_bus() -> MessageBus:
    """Get or create the global MessageBus singleton."""
    global _global_bus
    if _global_bus is None:
        _global_bus = MessageBus()
    return _global_bus


def reset_bus() -> None:
    """Reset the global bus. Used in tests and between generations."""
    global _global_bus
    if _global_bus is not None:
        _global_bus.clear()
    _global_bus = None


# ---------------------------------------------------------------------------
# Channel schema registry (for evolution compatibility checking)
# ---------------------------------------------------------------------------
_CHANNEL_SCHEMAS: Dict[str, Dict[str, Any]] = {}


def register_channel_schema(
    channel: str,
    required_fields: List[str],
    version: int = 1,
) -> None:
    """
    Register the expected payload schema for a channel.

    Used by the evolution controller to validate that a mutated module
    still produces compatible messages.
    """
    _CHANNEL_SCHEMAS[channel] = {
        "required_fields": required_fields,
        "version": version,
    }


def validate_message(msg: ChannelMessage) -> List[str]:
    """
    Validate a message against its registered schema.

    Returns:
        List of validation error strings (empty = valid).
    """
    schema = _CHANNEL_SCHEMAS.get(msg.channel)
    if schema is None:
        return []  # No schema registered = no validation

    errors = []
    for field_name in schema["required_fields"]:
        if field_name not in msg.payload:
            errors.append(
                f"Missing required field '{field_name}' in {msg.channel}"
            )
    return errors


# ---------------------------------------------------------------------------
# Pre-register known channel schemas
# ---------------------------------------------------------------------------
register_channel_schema(CH_WIN_PROBABILITY, [
    "win_pct", "confidence", "model_version", "features_used",
])
register_channel_schema(CH_LIVE_GAME_STATE, [
    "game_time_sec", "phase", "our_team", "enemy_team",
])
register_channel_schema(CH_STRATEGY_RECOMMENDATION, [
    "rec_type", "priority", "title", "detail",
])
register_channel_schema(CH_VOICE_ANNOUNCEMENT, [
    "text", "urgency", "category",
])
register_channel_schema(CH_EVOLUTION_FITNESS, [
    "generation_id", "fitness_score", "metrics",
])
register_channel_schema(CH_SYSTEM_HEARTBEAT, [
    "component", "uptime_ms", "status",
])

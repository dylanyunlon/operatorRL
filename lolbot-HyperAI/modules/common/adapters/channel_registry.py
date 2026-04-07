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


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: ChannelRegistryV2 — typed channel declarations, dependency
# graph, health monitoring, and automatic wire-up validation
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ChannelDeclaration:
    """Typed declaration of a channel in the system.

    Claude21: Each channel declares its message type, expected publisher,
    expected subscribers, and QoS requirements. At startup, the registry
    validates that all declared channels have publishers before allowing
    the system to enter RUNNING state.

    Apollo reference: cyber/conf/topology_config.h — channel topology
    declarations with expected readers/writers.
    """
    channel_name: str
    message_type: str             # Python class name of the message
    publisher: str                # Component name that writes to this channel
    subscribers: List[str] = field(default_factory=list)
    frequency_hz: float = 0.0    # Expected publish rate (0 = irregular)
    max_pending: int = 16        # Queue size for slow consumers
    required: bool = True        # System won't start without this channel
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel_name,
            "type": self.message_type,
            "publisher": self.publisher,
            "subscribers": self.subscribers,
            "hz": self.frequency_hz,
            "required": self.required,
        }


@dataclass
class ChannelHealth:
    """Runtime health of a channel.

    Claude21: Tracked by the channel monitor to detect stale channels,
    backpressure, and message rate anomalies.
    """
    channel_name: str
    publisher: str
    message_count: int = 0
    last_publish_time: float = 0.0
    avg_interval_ms: float = 0.0
    backpressure_count: int = 0
    error_count: int = 0
    is_healthy: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel_name,
            "publisher": self.publisher,
            "messages": self.message_count,
            "avg_ms": round(self.avg_interval_ms, 2),
            "backpressure": self.backpressure_count,
            "healthy": self.is_healthy,
        }


# ── Core channel declarations ──────────────────────────────────────────

CORE_CHANNELS: List[ChannelDeclaration] = [
    ChannelDeclaration(
        channel_name="/lol/raw_lcu",
        message_type="RawLCUData",
        publisher="canbus",
        subscribers=["perception"],
        frequency_hz=10.0,
        description="Raw LCU Live Client API data",
    ),
    ChannelDeclaration(
        channel_name="/lol/game_state",
        message_type="GameSnapshot",
        publisher="perception",
        subscribers=["prediction", "planning", "control", "monitor"],
        frequency_hz=10.0,
        description="Typed game state snapshot",
    ),
    ChannelDeclaration(
        channel_name="/lol/win_prediction",
        message_type="WinPrediction",
        publisher="prediction",
        subscribers=["planning", "control"],
        frequency_hz=2.0,
        description="Win probability prediction",
    ),
    ChannelDeclaration(
        channel_name="/lol/strategy",
        message_type="StrategyAdvice",
        publisher="planning",
        subscribers=["control"],
        frequency_hz=2.0,
        description="Strategic recommendations",
    ),
    ChannelDeclaration(
        channel_name="/lol/events",
        message_type="List[GameEvent]",
        publisher="perception",
        subscribers=["prediction", "planning", "control"],
        frequency_hz=10.0,
        required=False,
        description="New game events since last tick",
    ),
    ChannelDeclaration(
        channel_name="/lol/phase_transition",
        message_type="PhaseTransition",
        publisher="perception",
        subscribers=["planning"],
        frequency_hz=0.0,
        required=False,
        description="Game phase change events",
    ),
    ChannelDeclaration(
        channel_name="/lol/monitor_status",
        message_type="MonitorReport",
        publisher="monitor",
        subscribers=[],
        frequency_hz=0.5,
        required=False,
        description="System health monitoring",
    ),
]


class ChannelRegistryV2(ChannelRegistry):
    """Production-grade channel registry with typed declarations,
    dependency validation, and health monitoring.

    Claude21: Extends ChannelRegistry with:
    - Typed channel declarations with publisher/subscriber info
    - Startup validation: all required channels have publishers
    - Dependency graph: which components depend on which channels
    - Runtime health tracking per channel
    - Diagnostic dump for debugging pipeline issues

    Usage::
        registry = ChannelRegistryV2()
        registry.load_declarations(CORE_CHANNELS)
        errors = registry.validate_topology()
        if errors:
            raise SystemError(f"Channel topology invalid: {errors}")
    """

    def __init__(self) -> None:
        super().__init__()
        self._declarations: Dict[str, ChannelDeclaration] = {}
        self._health: Dict[str, ChannelHealth] = {}

    def load_declarations(self, declarations: List[ChannelDeclaration]) -> None:
        """Load channel declarations."""
        for decl in declarations:
            self._declarations[decl.channel_name] = decl
            self._health[decl.channel_name] = ChannelHealth(
                channel_name=decl.channel_name,
                publisher=decl.publisher,
            )

    def validate_topology(
        self, registered_components: Optional[List[str]] = None,
    ) -> List[str]:
        """Validate that all required channels have registered publishers.

        Returns list of error messages (empty = valid).
        """
        errors = []
        components = set(registered_components or [])

        for name, decl in self._declarations.items():
            if not decl.required:
                continue
            if components and decl.publisher not in components:
                errors.append(
                    f"Channel {name}: publisher '{decl.publisher}' not registered"
                )
            for sub in decl.subscribers:
                if components and sub not in components:
                    errors.append(
                        f"Channel {name}: subscriber '{sub}' not registered"
                    )

        return errors

    def get_component_dependencies(
        self, component_name: str,
    ) -> Dict[str, List[str]]:
        """Get channels a component publishes to and subscribes from.

        Claude21: Used to determine startup order — a component should
        not start until its input channels' publishers are running.
        """
        publishes = []
        subscribes = []
        for name, decl in self._declarations.items():
            if decl.publisher == component_name:
                publishes.append(name)
            if component_name in decl.subscribers:
                subscribes.append(name)
        return {"publishes": publishes, "subscribes": subscribes}

    def record_publish(self, channel_name: str) -> None:
        """Record a message publication for health tracking."""
        health = self._health.get(channel_name)
        if not health:
            return
        now = time.time()
        if health.last_publish_time > 0:
            interval = (now - health.last_publish_time) * 1000
            # Exponential moving average
            alpha = 0.2
            health.avg_interval_ms = (
                alpha * interval + (1 - alpha) * health.avg_interval_ms
            )
        health.last_publish_time = now
        health.message_count += 1

    def check_channel_health(self) -> Dict[str, ChannelHealth]:
        """Check health of all declared channels."""
        now = time.time()
        for name, health in self._health.items():
            decl = self._declarations.get(name)
            if not decl or decl.frequency_hz <= 0:
                continue
            expected_interval_s = 1.0 / decl.frequency_hz
            stale_threshold = expected_interval_s * 3.0
            if health.last_publish_time > 0:
                age = now - health.last_publish_time
                health.is_healthy = age < stale_threshold
            else:
                health.is_healthy = not decl.required
        return dict(self._health)

    def diagnostic_dump(self) -> Dict[str, Any]:
        """Full diagnostic dump of the channel system."""
        return {
            "declarations": {
                n: d.to_dict() for n, d in self._declarations.items()
            },
            "health": {
                n: h.to_dict() for n, h in self._health.items()
            },
            "topology_errors": self.validate_topology(),
        }

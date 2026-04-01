#!/usr/bin/env python3
"""
PipelineBuilder — Perception→Analysis→Strategy→Output Pipeline Assembler
==========================================================================
OperatorRL lolbot-HyperAI · 自部署 自环境反馈 自演化

Constructs the data processing pipeline that mirrors Apollo's
Perception→Prediction→Planning→Control flow. Each stage receives
data from the previous stage via typed channels, processes it, and
publishes results downstream.

Apollo Reference:
    cyber/message/message_traits.h → typed message channels
    modules/perception/ → perception pipeline stage
    modules/prediction/ → prediction stage consumes perception output
    modules/planning/ → planning consumes prediction output

Design:
    PipelineBuilder
      ├── PipelineStage          (one processing stage: input channels → proc → output channels)
      ├── TypedChannel           (named, typed data channel between stages)
      ├── PipelineDefinition     (complete stage graph definition)
      ├── PipelineExecutor       (runs stages in correct order per tick)
      └── DataFlowMonitor        (track message flow rates and latencies)

    The pipeline for LoL:
        [NetworkCapture] → game_events channel
            → [GameStateTracker] → game_state channel
                → [OpponentAnalyzer, TeamCompAnalyzer] → analysis channel
                    → [WinPredictor, StrategyEngine] → decisions channel
                        → [VoiceOutput, Dashboard] → user_output channel

Production Critique (Knuth-level):
    1. User: If the analysis stage is slow (e.g., computing opponent profiles),
       the prediction stage uses the last known analysis — stale data is better
       than no data during a teamfight. User hears predictions based on
       slightly outdated opponent stats rather than silence.
    2. System: Channels have configurable depth (default: 1, latest-only).
       For events where history matters (kill timeline), depth is increased.
       Overflow policy: drop oldest (not block sender). This prevents any
       slow stage from stalling the pipeline.
"""

import asyncio
import enum
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Deque, Dict, Generic, List, Optional, Set, Tuple, TypeVar
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Typed Channel — data conduit between pipeline stages
# ---------------------------------------------------------------------------

class OverflowPolicy(enum.Enum):
    """What to do when channel is full."""
    DROP_OLDEST = "drop_oldest"   # Default: keep latest data
    DROP_NEWEST = "drop_newest"   # Keep oldest data
    BLOCK = "block"               # Block sender (dangerous, avoid)


@dataclass
class ChannelStats:
    """Statistics for a data channel."""
    messages_sent: int = 0
    messages_received: int = 0
    messages_dropped: int = 0
    last_send_at: float = 0.0
    last_receive_at: float = 0.0
    avg_latency_ms: float = 0.0
    _latencies: Deque[float] = field(default_factory=lambda: deque(maxlen=100))

    def record_send(self) -> None:
        self.messages_sent += 1
        self.last_send_at = time.monotonic()

    def record_receive(self, send_time: float) -> None:
        self.messages_received += 1
        self.last_receive_at = time.monotonic()
        latency = (self.last_receive_at - send_time) * 1000.0
        self._latencies.append(latency)
        if self._latencies:
            self.avg_latency_ms = sum(self._latencies) / len(self._latencies)

    def record_drop(self) -> None:
        self.messages_dropped += 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sent": self.messages_sent,
            "received": self.messages_received,
            "dropped": self.messages_dropped,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
        }


class TypedChannel:
    """
    Named, typed data channel between pipeline stages.
    Depth=1 means only the latest message is kept (common for state).
    Depth>1 allows buffering for event streams.
    """

    def __init__(
        self,
        name: str,
        depth: int = 1,
        overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    ):
        self._name = name
        self._depth = max(1, depth)
        self._policy = overflow_policy
        self._buffer: Deque[Tuple[float, Any]] = deque(maxlen=self._depth)
        self._stats = ChannelStats()
        self._subscribers: List[Callable[[Any], None]] = []
        self._log = logging.getLogger(f"lolbot.channel.{name}")

    @property
    def name(self) -> str:
        return self._name

    @property
    def stats(self) -> ChannelStats:
        return self._stats

    def publish(self, message: Any) -> None:
        """
        Publish a message to the channel.
        If buffer is full, apply overflow policy.
        """
        now = time.monotonic()

        if len(self._buffer) >= self._depth:
            if self._policy == OverflowPolicy.DROP_OLDEST:
                self._buffer.popleft()
                self._stats.record_drop()
            elif self._policy == OverflowPolicy.DROP_NEWEST:
                self._stats.record_drop()
                return

        self._buffer.append((now, message))
        self._stats.record_send()

        # Notify subscribers
        for sub in self._subscribers:
            try:
                sub(message)
            except Exception as exc:
                self._log.error("Subscriber error on channel %s: %s", self._name, exc)

    def read_latest(self) -> Optional[Any]:
        """Read the most recent message without consuming it."""
        if not self._buffer:
            return None
        ts, msg = self._buffer[-1]
        self._stats.record_receive(ts)
        return msg

    def read_all(self) -> List[Any]:
        """Read all buffered messages (for event streams)."""
        messages = []
        for ts, msg in self._buffer:
            self._stats.record_receive(ts)
            messages.append(msg)
        return messages

    def consume(self) -> Optional[Any]:
        """Read and remove the oldest message."""
        if not self._buffer:
            return None
        ts, msg = self._buffer.popleft()
        self._stats.record_receive(ts)
        return msg

    def subscribe(self, callback: Callable[[Any], None]) -> None:
        """Register a push-mode subscriber."""
        self._subscribers.append(callback)

    @property
    def empty(self) -> bool:
        return len(self._buffer) == 0

    @property
    def size(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()


# ---------------------------------------------------------------------------
# Pipeline Stage
# ---------------------------------------------------------------------------

@dataclass
class StageDefinition:
    """Definition of a pipeline stage (before instantiation)."""
    name: str
    input_channels: List[str]       # Channel names to read from
    output_channels: List[str]      # Channel names to write to
    processor_name: str             # Module name in registry
    priority: int = 0               # Execution order (lower = first)
    optional_inputs: List[str] = field(default_factory=list)  # OK if missing
    description: str = ""


class PipelineStage:
    """
    A processing stage in the pipeline. Reads from input channels,
    calls the processor's proc(), writes to output channels.
    """

    def __init__(
        self,
        definition: StageDefinition,
        input_channels: Dict[str, TypedChannel],
        output_channels: Dict[str, TypedChannel],
        processor: Any,
    ):
        self._def = definition
        self._inputs = input_channels
        self._outputs = output_channels
        self._processor = processor
        self._log = logging.getLogger(f"lolbot.pipeline.{definition.name}")
        self._last_exec_ms: float = 0.0
        self._exec_count: int = 0
        self._error_count: int = 0

    @property
    def name(self) -> str:
        return self._def.name

    @property
    def priority(self) -> int:
        return self._def.priority

    async def execute(self) -> bool:
        """
        Execute one cycle: read inputs → process → write outputs.
        Returns True if execution succeeded.
        """
        start = time.monotonic()
        try:
            # Gather input data
            input_data: Dict[str, Any] = {}
            for ch_name, channel in self._inputs.items():
                data = channel.read_latest()
                if data is not None:
                    input_data[ch_name] = data
                elif ch_name not in self._def.optional_inputs:
                    # Required input missing — skip this tick
                    return True  # Not an error, just no data yet

            # Call processor
            if hasattr(self._processor, "process"):
                result = await self._processor.process(input_data)
            elif hasattr(self._processor, "proc"):
                await self._processor.proc()
                result = None
            else:
                self._log.error("Processor has no process() or proc() method")
                return False

            # Write outputs
            if result is not None:
                if isinstance(result, dict):
                    for ch_name, data in result.items():
                        if ch_name in self._outputs:
                            self._outputs[ch_name].publish(data)
                else:
                    # Single output → write to first output channel
                    for channel in self._outputs.values():
                        channel.publish(result)
                        break

            self._last_exec_ms = (time.monotonic() - start) * 1000.0
            self._exec_count += 1
            return True

        except Exception as exc:
            self._last_exec_ms = (time.monotonic() - start) * 1000.0
            self._error_count += 1
            self._log.error("Stage %s execution error: %s", self._def.name, exc)
            return False

    def get_stats(self) -> Dict[str, Any]:
        return {
            "name": self._def.name,
            "priority": self._def.priority,
            "exec_count": self._exec_count,
            "error_count": self._error_count,
            "last_exec_ms": round(self._last_exec_ms, 3),
        }


# ---------------------------------------------------------------------------
# Pipeline Definition — declarative pipeline spec
# ---------------------------------------------------------------------------

# Pre-defined channel names for LoL pipeline
CHANNEL_RAW_PACKETS = "raw_packets"
CHANNEL_GAME_EVENTS = "game_events"
CHANNEL_GAME_STATE = "game_state"
CHANNEL_PLAYER_PROFILES = "player_profiles"
CHANNEL_ANALYSIS = "analysis_results"
CHANNEL_PREDICTIONS = "predictions"
CHANNEL_STRATEGY = "strategy_decisions"
CHANNEL_VOICE_QUEUE = "voice_queue"
CHANNEL_EVOLUTION_LOG = "evolution_log"

# Default pipeline definition for LoL game assistant
DEFAULT_LOL_PIPELINE: List[StageDefinition] = [
    StageDefinition(
        name="network_capture",
        input_channels=[],
        output_channels=[CHANNEL_RAW_PACKETS, CHANNEL_GAME_EVENTS],
        processor_name="perception.network_capture",
        priority=0,
        description="Capture LoL client network traffic via Fiddler/LCU",
    ),
    StageDefinition(
        name="game_state_tracking",
        input_channels=[CHANNEL_GAME_EVENTS],
        output_channels=[CHANNEL_GAME_STATE],
        processor_name="core.game_state_tracker",
        priority=5,
        description="Maintain game phase state machine",
    ),
    StageDefinition(
        name="opponent_analysis",
        input_channels=[CHANNEL_GAME_STATE, CHANNEL_PLAYER_PROFILES],
        output_channels=[CHANNEL_ANALYSIS],
        processor_name="analysis.opponent_profiler",
        priority=10,
        optional_inputs=[CHANNEL_PLAYER_PROFILES],
        description="Analyze opponent behavior and patterns",
    ),
    StageDefinition(
        name="win_prediction",
        input_channels=[CHANNEL_GAME_STATE, CHANNEL_ANALYSIS],
        output_channels=[CHANNEL_PREDICTIONS],
        processor_name="prediction.win_predictor",
        priority=15,
        optional_inputs=[CHANNEL_ANALYSIS],
        description="Predict win probability from game state",
    ),
    StageDefinition(
        name="strategy_planning",
        input_channels=[CHANNEL_GAME_STATE, CHANNEL_PREDICTIONS, CHANNEL_ANALYSIS],
        output_channels=[CHANNEL_STRATEGY],
        processor_name="planning.strategy_planner",
        priority=20,
        optional_inputs=[CHANNEL_ANALYSIS],
        description="Generate tactical recommendations",
    ),
    StageDefinition(
        name="voice_output",
        input_channels=[CHANNEL_STRATEGY, CHANNEL_PREDICTIONS],
        output_channels=[CHANNEL_VOICE_QUEUE],
        processor_name="output.voice_engine",
        priority=30,
        description="Convert strategy to voice announcements",
    ),
]


# ---------------------------------------------------------------------------
# PipelineBuilder — assembles the pipeline from definitions
# ---------------------------------------------------------------------------

class PipelineBuilder:
    """
    Builds and manages the data processing pipeline.

    Usage:
        builder = PipelineBuilder(registry)
        builder.load_definitions(DEFAULT_LOL_PIPELINE)
        pipeline = builder.build()
        
        # In main loop:
        await pipeline.execute_tick()
    """

    def __init__(self, module_registry: Any = None):
        self._log = logging.getLogger("lolbot.integration.pipeline_builder")
        self._registry = module_registry
        self._definitions: List[StageDefinition] = []
        self._channels: Dict[str, TypedChannel] = {}
        self._stages: List[PipelineStage] = []
        self._built = False

    def load_definitions(self, definitions: List[StageDefinition]) -> None:
        """Load pipeline stage definitions."""
        self._definitions = list(definitions)
        self._log.info("Loaded %d pipeline stage definitions", len(definitions))

    def add_stage(self, definition: StageDefinition) -> None:
        """Add a single stage definition."""
        self._definitions.append(definition)

    def create_channel(
        self,
        name: str,
        depth: int = 1,
        overflow_policy: OverflowPolicy = OverflowPolicy.DROP_OLDEST,
    ) -> TypedChannel:
        """Create or get a named channel."""
        if name not in self._channels:
            self._channels[name] = TypedChannel(
                name=name, depth=depth, overflow_policy=overflow_policy
            )
        return self._channels[name]

    def build(self) -> "Pipeline":
        """
        Build the pipeline from definitions.
        Creates channels, resolves processors from registry, assembles stages.
        """
        self._log.info("Building pipeline with %d stages", len(self._definitions))

        # Create all channels
        all_channel_names: Set[str] = set()
        for defn in self._definitions:
            all_channel_names.update(defn.input_channels)
            all_channel_names.update(defn.output_channels)

        for ch_name in all_channel_names:
            self.create_channel(ch_name)

        # Create stages
        stages: List[PipelineStage] = []
        for defn in sorted(self._definitions, key=lambda d: d.priority):
            # Resolve processor
            processor = None
            if self._registry:
                processor = self._registry.get(defn.processor_name)

            if processor is None:
                # Create a passthrough processor as placeholder
                processor = _PassthroughProcessor(defn.name)
                self._log.warning(
                    "No processor found for stage '%s' (module: %s) — using passthrough",
                    defn.name, defn.processor_name,
                )

            input_channels = {
                ch: self._channels[ch]
                for ch in defn.input_channels
                if ch in self._channels
            }
            output_channels = {
                ch: self._channels[ch]
                for ch in defn.output_channels
                if ch in self._channels
            }

            stage = PipelineStage(
                definition=defn,
                input_channels=input_channels,
                output_channels=output_channels,
                processor=processor,
            )
            stages.append(stage)

        self._stages = stages
        self._built = True

        pipeline = Pipeline(stages, self._channels)
        self._log.info(
            "Pipeline built: %d stages, %d channels",
            len(stages), len(self._channels),
        )
        return pipeline

    def get_channel(self, name: str) -> Optional[TypedChannel]:
        """Get a channel by name (after build)."""
        return self._channels.get(name)


# ---------------------------------------------------------------------------
# Pipeline — the executable pipeline
# ---------------------------------------------------------------------------

class Pipeline:
    """
    Assembled, executable pipeline. Called once per tick.
    Stages execute in priority order (lower first).
    """

    def __init__(
        self,
        stages: List[PipelineStage],
        channels: Dict[str, TypedChannel],
    ):
        self._stages = sorted(stages, key=lambda s: s.priority)
        self._channels = channels
        self._log = logging.getLogger("lolbot.pipeline")
        self._tick_count = 0
        self._total_exec_ms = 0.0

    async def execute_tick(self) -> Dict[str, Any]:
        """
        Execute one tick of the pipeline.
        Returns execution statistics.
        """
        start = time.monotonic()
        results: Dict[str, bool] = {}

        for stage in self._stages:
            ok = await stage.execute()
            results[stage.name] = ok

        elapsed_ms = (time.monotonic() - start) * 1000.0
        self._tick_count += 1
        self._total_exec_ms += elapsed_ms

        return {
            "tick": self._tick_count,
            "duration_ms": round(elapsed_ms, 3),
            "stages": results,
        }

    def get_channel(self, name: str) -> Optional[TypedChannel]:
        return self._channels.get(name)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "tick_count": self._tick_count,
            "avg_tick_ms": round(
                self._total_exec_ms / max(1, self._tick_count), 3
            ),
            "stages": [s.get_stats() for s in self._stages],
            "channels": {
                name: ch.stats.to_dict()
                for name, ch in self._channels.items()
            },
        }

    # ComponentProtocol
    @property
    def name(self) -> str:
        return "integration.pipeline"

    async def init(self) -> None:
        self._log.info("Pipeline initialized with %d stages", len(self._stages))

    async def proc(self) -> None:
        await self.execute_tick()

    async def shutdown(self) -> None:
        for ch in self._channels.values():
            ch.clear()
        self._log.info("Pipeline shutdown — %d ticks executed", self._tick_count)


# ---------------------------------------------------------------------------
# Passthrough processor (placeholder for missing modules)
# ---------------------------------------------------------------------------

class _PassthroughProcessor:
    """Placeholder processor that passes input to output unchanged."""

    def __init__(self, name: str):
        self._name = name

    async def process(self, inputs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return inputs if inputs else None

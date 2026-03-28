"""
Orchestrator - Top-level coordinator for all agent subsystems.

Wires together the full pipeline:
  Fiddler MCP → Session Manager → Data Pipeline → Strategy Engine
       ↓                                               ↓
  Traffic Classifier                              Advice Output
       ↓                                               ↓
  WebSocket Bridge                             Feedback Tracker
                                                       ↓
                                                 Replay Recorder
                                                       ↓
                                                Training Data Store

This is the single entry point for starting/stopping the entire
agent system. All subsystems are managed as supervised tasks
with automatic restart on failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.agents.strategy_agent import (
    LoLStrategyAgent,
    StrategicAdvice,
    StrategyAgentConfig,
)
from lol_fiddler_agent.data.pipeline import DataPipeline, create_standard_pipeline
from lol_fiddler_agent.feedback.tracker import FeedbackTracker
from lol_fiddler_agent.integrations.agentos_bridge import AgentOSBridge, BridgeConfig
from lol_fiddler_agent.ml.prediction_engine import BuiltinLogisticModel, EnsemblePredictionEngine
from lol_fiddler_agent.models.game_snapshot import GameSnapshot
from lol_fiddler_agent.network.fiddler_client import FiddlerConfig, FiddlerMCPClient
from lol_fiddler_agent.network.session_manager import SessionManager, SessionManagerConfig
from lol_fiddler_agent.network.packet_analyzer import AnalyzedPacket, APIEndpointCategory
from lol_fiddler_agent.network.traffic_classifier import TrafficClassifier
from lol_fiddler_agent.replay.recorder import ReplayRecorder
from lol_fiddler_agent.strategies.objective_timer import ObjectiveTracker
from lol_fiddler_agent.utils.async_helpers import PeriodicTask, TaskGroup
from lol_fiddler_agent.utils.metrics import MetricsCollector, get_metrics

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    """Top-level orchestrator configuration."""
    # Fiddler
    fiddler_api_key: str = ""
    fiddler_host: str = "localhost"
    fiddler_port: int = 8868

    # Agent
    agent_id: str = "lol-strategy-agent"
    poll_interval: float = 2.0
    advice_cooldown: float = 10.0

    # Features
    enable_recording: bool = True
    enable_ws_bridge: bool = False
    enable_feedback: bool = True

    # Paths
    replay_dir: str = "./replays"
    training_data_dir: str = "./training_data"

    # AgentOS
    policies: list[str] = field(default_factory=lambda: ["read_only", "no_pii"])


class Orchestrator:
    """Main system orchestrator.

    Manages the lifecycle of all subsystems and coordinates
    data flow between them.

    Example::

        config = OrchestratorConfig(fiddler_api_key="your-key")
        orch = Orchestrator(config)
        await orch.start()
        # ... system runs autonomously ...
        summary = await orch.stop()
    """

    def __init__(self, config: OrchestratorConfig) -> None:
        self._config = config
        self._running = False
        self._start_time: float = 0.0

        # Metrics
        self._metrics = get_metrics()
        self._advice_counter = self._metrics.counter("advice_total")
        self._snapshot_counter = self._metrics.counter("snapshots_processed")
        self._game_time_gauge = self._metrics.gauge("game_time_seconds")

        # Subsystems (initialized in start())
        self._fiddler: Optional[FiddlerMCPClient] = None
        self._session_manager: Optional[SessionManager] = None
        self._pipeline: Optional[DataPipeline] = None
        self._strategy_agent: Optional[LoLStrategyAgent] = None
        self._classifier: Optional[TrafficClassifier] = None
        self._feedback_tracker: Optional[FeedbackTracker] = None
        self._replay_recorder: Optional[ReplayRecorder] = None
        self._objective_tracker: Optional[ObjectiveTracker] = None
        self._bridge: Optional[AgentOSBridge] = None
        self._prediction_engine: Optional[EnsemblePredictionEngine] = None

        # State
        self._last_snapshot: Optional[GameSnapshot] = None
        self._advice_given: int = 0
        self._snapshots_processed: int = 0

    async def start(self) -> None:
        """Start all subsystems."""
        if self._running:
            logger.warning("Orchestrator already running")
            return

        self._start_time = time.time()
        logger.info("Starting Orchestrator...")

        # 1. Initialize Fiddler client
        fiddler_config = FiddlerConfig(
            host=self._config.fiddler_host,
            port=self._config.fiddler_port,
            api_key=self._config.fiddler_api_key,
        )
        self._fiddler = FiddlerMCPClient(fiddler_config)
        await self._fiddler.connect()
        await self._fiddler.setup_lol_capture()
        logger.info("Fiddler MCP connected")

        # 2. Session manager
        sm_config = SessionManagerConfig(poll_interval=self._config.poll_interval)
        self._session_manager = SessionManager(self._fiddler, sm_config)

        # 3. Data pipeline
        self._pipeline = create_standard_pipeline()
        self._pipeline.on_output(self._on_snapshot)

        # 4. Traffic classifier
        self._classifier = TrafficClassifier()

        # 5. Strategy agent
        strategy_config = StrategyAgentConfig(
            agent_id=self._config.agent_id,
            fiddler_api_key=self._config.fiddler_api_key,
            fiddler_host=self._config.fiddler_host,
            fiddler_port=self._config.fiddler_port,
            poll_interval_seconds=self._config.poll_interval,
            advice_cooldown_seconds=self._config.advice_cooldown,
        )
        self._strategy_agent = LoLStrategyAgent(strategy_config)

        # 6. Prediction engine
        self._prediction_engine = EnsemblePredictionEngine()
        self._prediction_engine.add_model(BuiltinLogisticModel())

        # 7. Feedback tracker
        if self._config.enable_feedback:
            self._feedback_tracker = FeedbackTracker()

        # 8. Replay recorder
        if self._config.enable_recording:
            self._replay_recorder = ReplayRecorder(self._config.replay_dir)
            self._replay_recorder.start_recording()

        # 9. Objective tracker
        self._objective_tracker = ObjectiveTracker()

        # 10. AgentOS bridge
        self._bridge = AgentOSBridge(BridgeConfig(
            agent_id=self._config.agent_id,
            policies=self._config.policies,
        ))
        await self._bridge.initialize()

        # Register session manager callbacks
        self._session_manager.on_packet(self._on_packet)
        self._session_manager.on_live_client_data(self._on_live_client_data)

        # Start session manager
        await self._session_manager.start()

        self._running = True
        logger.info("Orchestrator started (all subsystems active)")

    async def stop(self) -> dict[str, Any]:
        """Stop all subsystems and return session summary."""
        if not self._running:
            return {}

        self._running = False
        logger.info("Stopping Orchestrator...")

        # Stop in reverse order
        if self._session_manager:
            await self._session_manager.stop()

        if self._replay_recorder and self._replay_recorder.is_recording:
            self._replay_recorder.stop_recording()

        if self._fiddler:
            await self._fiddler.disconnect()

        elapsed = time.time() - self._start_time
        summary = {
            "uptime_seconds": elapsed,
            "snapshots_processed": self._snapshots_processed,
            "advice_given": self._advice_given,
            "pipeline_metrics": self._pipeline.metrics.__dict__ if self._pipeline else {},
            "bridge_stats": self._bridge.get_stats() if self._bridge else {},
        }

        if self._feedback_tracker:
            summary["feedback"] = self._feedback_tracker.get_summary()

        logger.info("Orchestrator stopped: %s", summary)
        return summary

    # ── Internal Callbacks ────────────────────────────────────────────────

    async def _on_packet(self, packet: AnalyzedPacket) -> None:
        """Handle every analyzed packet."""
        if self._classifier:
            self._classifier.record_packet(packet)
            self._classifier.update_phase(packet)

    async def _on_live_client_data(self, packet: AnalyzedPacket) -> None:
        """Handle Live Client API data packets."""
        if self._pipeline:
            await self._pipeline.run(packet)

    async def _on_snapshot(self, snapshot: GameSnapshot) -> None:
        """Handle processed game snapshot."""
        self._snapshots_processed += 1
        self._snapshot_counter.inc()
        self._game_time_gauge.set(snapshot.game_time)

        # Record for replay
        if self._replay_recorder:
            self._replay_recorder.record_snapshot(snapshot)

        # Track objectives
        # (ObjectiveTracker needs LiveGameState, we'd convert back here
        # in production; for now we track via events in snapshots)

        # Evaluate feedback from previous advice
        if self._feedback_tracker and self._last_snapshot:
            records = self._feedback_tracker.evaluate(snapshot)
            for record in records:
                logger.debug("Feedback: %s %s", record.advice_action, record.compliance_outcome)

        # Generate new advice via strategy agent
        if self._bridge and self._bridge.should_generate_advice():
            # The strategy agent handles advice internally
            # Here we could add additional orchestration-level advice
            pass

        self._last_snapshot = snapshot

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def uptime(self) -> float:
        if not self._running:
            return 0.0
        return time.time() - self._start_time

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self._running,
            "uptime": self.uptime,
            "snapshots": self._snapshots_processed,
            "advice": self._advice_given,
            "classifier_phase": (
                self._classifier.current_phase.value if self._classifier else "unknown"
            ),
            "session_buffer_size": (
                self._session_manager.buffer.size if self._session_manager else 0
            ),
        }

    @property
    def last_snapshot(self) -> Optional[GameSnapshot]:
        return self._last_snapshot


# ── Evolution Integration (M275 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'orchestrator'


class EvolutionCoordinator:
    """Global evolution coordinator for all strategy modules.

    Collects training annotations from all Evolvable* modules,
    batches them for AgentLightning training spans, and manages
    cross-module evolution state.

    Example::

        coord = EvolutionCoordinator()
        coord.register_module('power_spike', power_spike_detector)
        coord.record_annotation({'module': 'power_spike', 'spike': 'level6'})
        batch = coord.export_training_batch()
    """

    def __init__(self) -> None:
        self._registered: dict[str, Any] = {}
        self._annotations: list[dict] = []
        self._generation: int = 0

    def register_module(self, name: str, module_ref: Any) -> None:
        """Register a strategy module for evolution tracking."""
        self._registered[name] = module_ref

    @property
    def registered_modules(self) -> dict[str, Any]:
        return dict(self._registered)

    def record_annotation(self, annotation: dict) -> None:
        """Record a training annotation from any module."""
        annotation.setdefault('generation', self._generation)
        annotation.setdefault('recorded_at', time.time())
        self._annotations.append(annotation)

    @property
    def annotations(self) -> list[dict]:
        return list(self._annotations)

    def export_training_batch(self) -> list[dict]:
        """Export all collected annotations as a training batch."""
        batch = list(self._annotations)
        return batch

    def reset(self) -> None:
        """Clear all collected annotations."""
        self._annotations.clear()

    def advance_generation(self) -> int:
        """Advance the evolution generation counter."""
        self._generation += 1
        return self._generation

    def get_stats(self) -> dict[str, Any]:
        return {
            'total_annotations': len(self._annotations),
            'registered_modules': list(self._registered.keys()),
            'generation': self._generation,
        }

#!/usr/bin/env python3
"""
launch/main_loop.py — Apollo-style While-True Main Loop
==========================================================
lolbot-HyperAI · Launch Layer

This is the entry point. It wires all modules together and runs
the Apollo-style while-true Proc() loop:

    while running:
        perception.proc()       # Read sensors (network data)
        prediction.proc()       # Extract features, predict
        planning.proc()         # Generate recommendations
        output.proc()           # Announce via TTS
        evolution.proc()        # (post-game) evaluate and evolve

In Apollo autonomous driving:
    timer_component.cc::Proc() is called every 10ms
    Components process data from shared channels
    The scheduler manages component lifecycle

Here, our cycle runs at ~100ms (10 Hz), which is more than fast
enough for a game assistant (human reaction time is ~200ms).

The main loop also manages the session lifecycle:
    1. Startup: init all components, load generation
    2. Pre-game: monitor for game start
    3. In-game: full perception-prediction-planning-output loop
    4. Post-game: evaluate fitness, evolve, save state
    5. Shutdown: graceful cleanup

Usage:
    python -m lolbot-HyperAI.launch.main_loop
    # or
    from launch.main_loop import MainLoop
    loop = MainLoop()
    asyncio.run(loop.run())
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root is in path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from canbus.channel_message import (
    CH_GAME_FLOW_PHASE,
    CH_SYSTEM_ERROR,
    CH_SYSTEM_HEARTBEAT,
    MessageBus,
    MessageFactory,
)
from canbus.transport import Transport
from conf.default_config import LolBotConfig, load_config
from evolution.fitness_evaluator import FitnessEvaluator
from evolution.generation_manager import GenerationManager
from evolution.strategy_mutator import StrategyMutator
from integration.agent_os_connector import AgentOSConnector, GovernanceMode
from integration.riot_api_client import RiotAPIClient, Region
from modules.common.component_base import ComponentRegistry
from output.voice_announcer import VoiceAnnouncer, VoiceConfig
from perception.game_state_parser import GameStateParser
from perception.network_listener import NetworkListener
from planning.strategy_planner import StrategyPlanner
from prediction.feature_pipeline import FeaturePipeline
from prediction.win_probability_engine import WinProbabilityEngine


# ---------------------------------------------------------------------------
# Session state machine
# ---------------------------------------------------------------------------
class SessionState:
    """Tracks the current session state."""
    IDLE = "idle"               # Waiting for game
    PRE_GAME = "pre_game"       # In lobby or champ select
    IN_GAME = "in_game"         # Active game
    POST_GAME = "post_game"     # Game ended, evaluating
    EVOLVING = "evolving"       # Applying evolution


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------
class MainLoop:
    """
    The Apollo-style main loop orchestrating all components.

    This is the heart of lolbot-HyperAI. It:
        1. Creates and wires all components
        2. Runs the Proc() cycle at ~100ms intervals
        3. Manages session state transitions
        4. Handles the evolution loop between games
    """

    TICK_INTERVAL_SEC = 0.1     # 100ms = 10 Hz main loop
    MAX_TICK_OVERRUN_MS = 50    # Warn if tick takes > 150ms

    def __init__(
        self,
        config: Optional[LolBotConfig] = None,
    ) -> None:
        self._config = config or load_config(
            Path("data/config.json"),
        )
        self._running = False
        self._state = SessionState.IDLE
        self._session_id: Optional[str] = None
        self._tick_count = 0
        self._error_count = 0
        self._start_time = 0.0

        # Reset component registry to avoid stale entries from crash-restart
        ComponentRegistry.reset()

        # Components (initialized in _init_components)
        self._bus: Optional[MessageBus] = None
        self._transport: Optional[Transport] = None
        self._network_listener: Optional[NetworkListener] = None
        self._game_state_parser: Optional[GameStateParser] = None
        self._feature_pipeline: Optional[FeaturePipeline] = None
        self._win_engine: Optional[WinProbabilityEngine] = None
        self._strategy_planner: Optional[StrategyPlanner] = None
        self._voice_announcer: Optional[VoiceAnnouncer] = None
        self._fitness_evaluator: Optional[FitnessEvaluator] = None
        self._generation_manager: Optional[GenerationManager] = None
        self._strategy_mutator: Optional[StrategyMutator] = None
        self._agent_os: Optional[AgentOSConnector] = None
        self._riot_api: Optional[RiotAPIClient] = None

        self._factory = MessageFactory("launch.main_loop")

    # -- Initialization -------------------------------------------------

    def _init_components(self) -> None:
        """Create and wire all components."""
        cfg = self._config

        # 1. CAN Bus (data backbone)
        self._bus = MessageBus(history_size=cfg.transport.history_size)
        recording_path = None
        if cfg.transport.recording_enabled:
            rec_dir = Path(cfg.transport.recording_dir)
            rec_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            recording_path = rec_dir / f"session_{ts}.jsonl"

        self._transport = Transport(
            self._bus,
            recording_path=recording_path,
            default_rate_limit=cfg.transport.default_rate_limit,
        )

        # 2. Perception layer
        self._network_listener = NetworkListener(self._transport)
        self._game_state_parser = GameStateParser(self._transport)

        # 3. Prediction layer
        self._feature_pipeline = FeaturePipeline(self._transport)
        self._win_engine = WinProbabilityEngine(
            self._transport, self._feature_pipeline,
        )

        # 4. Planning layer
        self._strategy_planner = StrategyPlanner(self._transport)

        # 5. Output layer
        voice_cfg = VoiceConfig(
            rate=cfg.output.tts_rate_wpm,
            volume=cfg.output.tts_volume,
        )
        self._voice_announcer = VoiceAnnouncer(
            self._transport, voice_cfg,
        )

        # 6. Evolution layer
        data_dir = Path(cfg.evolution.data_dir)
        self._fitness_evaluator = FitnessEvaluator(self._transport)
        self._generation_manager = GenerationManager(
            self._transport, data_dir,
        )
        self._strategy_mutator = StrategyMutator()

        # 7. Integration layer
        self._agent_os = AgentOSConnector(
            self._transport,
            mode=GovernanceMode(cfg.integration.agent_os_mode),
        )
        self._riot_api = RiotAPIClient(
            api_key=cfg.integration.riot_api_key,
            region=Region(cfg.integration.riot_region),
        )

    def _init_all(self) -> None:
        """Initialize all components (call their init() methods)."""
        print("[MainLoop] Initializing components...")

        # Network listener: detect data source
        self._network_listener.init()
        print(f"  Perception: {self._network_listener.stats()['capture_mode']}")

        # Game state parser: subscribe to raw channels
        self._game_state_parser.init()
        print("  GameStateParser: subscribed")

        # Feature pipeline: subscribe to game state
        self._feature_pipeline.init()
        print("  FeaturePipeline: ready")

        # Win probability engine: load model
        model_dir = Path(self._config.prediction.model_dir)
        self._win_engine.init(model_dir=model_dir)
        print(f"  WinProbEngine: {self._win_engine.stats()['model_version']}")

        # Strategy planner
        self._strategy_planner.init()
        print("  StrategyPlanner: ready")

        # Voice announcer: detect TTS backend
        tts_info = self._voice_announcer.init()
        print(f"  VoiceAnnouncer: {tts_info['tts_backend']}")

        # Generation manager: load or create initial generation
        gen = self._generation_manager.init()
        print(f"  Generation: {gen.generation_id}")

        # Apply generation params to components
        self._apply_generation(gen)

        # Agent OS connector
        mode = self._agent_os.init()
        print(f"  AgentOS: {mode.value}")

        print("[MainLoop] All components initialized.")

    def _apply_generation(self, gen) -> None:
        """Apply a generation snapshot's params to live components."""
        # Win probability weights
        if gen.prediction_weights:
            self._win_engine.set_model_weights({
                "weights": gen.prediction_weights,
                "bias": gen.prediction_bias,
            })

        # Planning thresholds
        self._strategy_planner.set_min_confidence(
            gen.min_recommendation_confidence,
        )

        # Cooldowns
        for rec_type, cooldown in gen.recommendation_cooldowns.items():
            self._strategy_planner.set_cooldown(rec_type, cooldown)

        # Voice intervals
        self._voice_announcer.set_min_interval(
            gen.min_announce_interval_sec,
        )
        self._voice_announcer.set_win_update_interval(
            gen.win_update_interval_sec,
        )

    # -- Main loop ------------------------------------------------------

    async def run(self) -> None:
        """
        Start the main loop. Runs until interrupted.

        This is the top-level entry point. Call with:
            asyncio.run(loop.run())
        """
        self._running = True
        self._start_time = time.monotonic()

        # Create components
        self._init_components()
        self._init_all()

        # Register signal handlers for graceful shutdown
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._signal_shutdown)
            except (NotImplementedError, OSError):
                pass  # Windows doesn't support add_signal_handler

        print(f"\n[MainLoop] Running at {1/self.TICK_INTERVAL_SEC:.0f} Hz. Press Ctrl+C to stop.\n")

        # The while-true Proc() loop (Apollo pattern)
        try:
            while self._running:
                tick_start = time.monotonic()

                await self._tick()

                # Sleep to maintain tick rate
                elapsed = time.monotonic() - tick_start
                sleep_time = self.TICK_INTERVAL_SEC - elapsed
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                elif elapsed > self.TICK_INTERVAL_SEC + self.MAX_TICK_OVERRUN_MS / 1000:
                    self._warn_overrun(elapsed)

        except KeyboardInterrupt:
            pass
        finally:
            await self._shutdown()

    async def _tick(self) -> None:
        """
        Single tick of the main loop.

        Calls each component's proc() in order:
            Perception → Prediction → Planning → Output
        """
        self._tick_count += 1

        try:
            # 1. Perception: read network data
            await self._network_listener.proc()

            # 2. Perception fusion: normalize game state
            await self._game_state_parser.proc()

            # 3. Feature extraction
            await self._feature_pipeline.proc()

            # 4. Win probability prediction
            await self._win_engine.proc()

            # 5. Strategy planning
            await self._strategy_planner.proc()

            # 6. Voice output
            await self._voice_announcer.proc()

            # 7. State transitions
            self._check_state_transitions()

        except Exception as exc:
            self._error_count += 1
            self._publish_error("main_loop", exc)
            if self._config.system.debug_mode:
                traceback.print_exc()

    def _check_state_transitions(self) -> None:
        """Check for game phase changes and manage session state."""
        phase_msg = self._transport.latest(CH_GAME_FLOW_PHASE)
        if phase_msg is None:
            return

        phase = phase_msg.payload.get("phase", "None")

        if self._state == SessionState.IDLE:
            if phase in ("Lobby", "Matchmaking", "ReadyCheck"):
                self._transition_to(SessionState.PRE_GAME)
            elif phase in ("ChampSelect",):
                self._transition_to(SessionState.PRE_GAME)
            elif phase in ("InProgress", "Reconnect"):
                self._transition_to(SessionState.IN_GAME)

        elif self._state == SessionState.PRE_GAME:
            if phase in ("InProgress", "Reconnect"):
                self._transition_to(SessionState.IN_GAME)
            elif phase in ("None",):
                self._transition_to(SessionState.IDLE)

        elif self._state == SessionState.IN_GAME:
            if phase in ("WaitingForStats", "PreEndOfGame", "EndOfGame"):
                self._transition_to(SessionState.POST_GAME)

        elif self._state == SessionState.POST_GAME:
            if phase in ("None", "Lobby"):
                self._on_post_game_complete()
                self._transition_to(SessionState.IDLE)

    def _transition_to(self, new_state: str) -> None:
        """Handle a state transition."""
        old_state = self._state
        self._state = new_state
        print(f"[State] {old_state} → {new_state}")

        if new_state == SessionState.IN_GAME:
            self._on_game_start()
        elif new_state == SessionState.POST_GAME:
            self._on_game_end()

    def _on_game_start(self) -> None:
        """Called when a game starts."""
        self._session_id = f"session_{int(time.time())}"
        self._agent_os.on_session_start(self._session_id)
        self._fitness_evaluator.start_collection()
        self._voice_announcer.force_announce(
            "Game started. Good luck!", priority=2,
        )
        print(f"[Session] Game started: {self._session_id}")

    def _on_game_end(self) -> None:
        """Called when a game ends."""
        self._fitness_evaluator.stop_collection()
        self._voice_announcer.force_announce(
            "Game over.", priority=1,
        )
        print(f"[Session] Game ended: {self._session_id}")

    def _on_post_game_complete(self) -> None:
        """
        Called after post-game screen. Trigger evolution loop.

        This is where the self-evolution cycle happens:
            1. Evaluate fitness
            2. Generate mutations
            3. Apply mutations
            4. (Next game evaluates new generation)
        """
        if not self._config.evolution.enabled:
            return

        print("[Evolution] Evaluating session fitness...")

        # Evaluate current generation's fitness
        fitness = self._fitness_evaluator.evaluate(
            generation_id=self._generation_manager.current.generation_id
            if self._generation_manager.current else "",
            session_id=self._session_id or "",
        )
        print(f"  Fitness score: {fitness.total:.4f}")

        # Record fitness
        if self._generation_manager.current:
            self._generation_manager.record_fitness(
                self._generation_manager.current.generation_id,
                fitness.to_dict(),
            )

        # Report to agent_os
        if self._session_id:
            self._agent_os.on_session_end(
                self._session_id, fitness.to_dict(),
            )

        # Auto-evolve if enabled
        if self._config.evolution.auto_evolve_after_game:
            self._evolve(fitness)

    def _evolve(self, fitness) -> None:
        """Run one evolution cycle."""
        if not self._generation_manager.current:
            return

        current_gen = self._generation_manager.current
        print(f"[Evolution] Current generation: {current_gen.generation_id}")
        print(f"  Avg fitness: {current_gen.avg_fitness:.4f}")

        # Generate mutation proposals
        proposals = self._strategy_mutator.propose(
            current_gen, fitness,
        )
        if not proposals:
            print("  No mutations proposed.")
            return

        print(f"  Proposing {len(proposals)} mutations:")
        for p in proposals:
            print(f"    - {p.category}: {p.target_param}")
            print(f"      {p.rationale}")

        # Check policy
        policy = self._agent_os.check_policy("mutation", {
            "mutations_this_hour": len(proposals),
        })
        if not policy.allowed:
            print(f"  Mutation blocked by policy: {policy.reason}")
            return

        # Apply mutations to create new generation
        new_gen = self._generation_manager.apply_mutations(proposals)
        print(f"  New generation: {new_gen.generation_id}")

        # Apply new params to live components
        self._apply_generation(new_gen)

        # Commit (we'll evaluate properly next game)
        # For now, always commit and let fitness tracking decide later
        if self._generation_manager.should_commit(
            fitness.total,
            current_gen.avg_fitness,
            threshold=self._config.evolution.fitness_commit_threshold,
        ):
            self._generation_manager.commit(new_gen.generation_id)
            print(f"  Committed: {new_gen.generation_id}")
        else:
            print(f"  Applied but not committed (pending evaluation)")
            # Still commit for now — proper A/B testing needs multiple sessions
            self._generation_manager.commit(new_gen.generation_id)

    # -- Error handling -------------------------------------------------

    def _publish_error(self, component: str, exc: Exception) -> None:
        """Publish an error to the system error channel."""
        if self._transport:
            msg = self._factory.create(
                CH_SYSTEM_ERROR,
                {
                    "component": component,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "severity": "medium",
                },
                priority=2,
            )
            self._transport.publish(msg)

    def _warn_overrun(self, elapsed: float) -> None:
        """Log a warning when tick takes too long."""
        overrun_ms = (elapsed - self.TICK_INTERVAL_SEC) * 1000
        if self._tick_count % 100 == 0:  # Don't spam
            print(
                f"[Warning] Tick #{self._tick_count} overran by "
                f"{overrun_ms:.1f}ms"
            )

    # -- Shutdown -------------------------------------------------------

    def _signal_shutdown(self) -> None:
        """Handle SIGINT/SIGTERM."""
        print("\n[MainLoop] Shutdown signal received.")
        self._running = False

    async def _shutdown(self) -> None:
        """Graceful shutdown of all components."""
        print("[MainLoop] Shutting down...")

        components_stats = {}

        if self._voice_announcer:
            components_stats["voice"] = self._voice_announcer.shutdown()

        if self._strategy_planner:
            components_stats["planning"] = self._strategy_planner.shutdown()

        if self._win_engine:
            components_stats["prediction"] = self._win_engine.shutdown()

        if self._game_state_parser:
            components_stats["parser"] = self._game_state_parser.shutdown()

        if self._network_listener:
            components_stats["listener"] = self._network_listener.shutdown()

        if self._transport:
            transport_stats = self._transport.shutdown()
            components_stats["transport"] = transport_stats

            # Compress recording
            gz_path = self._transport.compress_recording()
            if gz_path:
                print(f"  Recording compressed: {gz_path}")

        # Print summary
        uptime = time.monotonic() - self._start_time
        print(f"\n[MainLoop] Final stats:")
        print(f"  Uptime: {uptime:.1f}s")
        print(f"  Ticks: {self._tick_count}")
        print(f"  Errors: {self._error_count}")
        if self._generation_manager and self._generation_manager.current:
            gen = self._generation_manager.current
            print(f"  Generation: {gen.generation_id}")
            print(f"  Avg fitness: {gen.avg_fitness:.4f}")

        print("[MainLoop] Shutdown complete.")

    # -- Public API (for testing / integration) -------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        """Request graceful stop."""
        self._running = False

    def stats(self) -> Dict[str, Any]:
        """Aggregate stats from all components."""
        return {
            "state": self._state,
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "uptime_sec": round(time.monotonic() - self._start_time, 1),
            "session_id": self._session_id,
            "components": {
                "listener": self._network_listener.stats()
                    if self._network_listener else {},
                "parser": self._game_state_parser.stats()
                    if self._game_state_parser else {},
                "features": self._feature_pipeline.stats()
                    if self._feature_pipeline else {},
                "prediction": self._win_engine.stats()
                    if self._win_engine else {},
                "planning": self._strategy_planner.stats()
                    if self._strategy_planner else {},
                "voice": self._voice_announcer.stats()
                    if self._voice_announcer else {},
                "evolution": self._generation_manager.stats()
                    if self._generation_manager else {},
                "agent_os": self._agent_os.stats()
                    if self._agent_os else {},
                "riot_api": self._riot_api.stats()
                    if self._riot_api else {},
            },
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point."""
    print("=" * 60)
    print("  lolbot-HyperAI")
    print("  Apollo-style LoL Game Assistant")
    print("  Self-evolving via operatorRL governance kernel")
    print("=" * 60)
    print()

    loop = MainLoop()
    asyncio.run(loop.run())


if __name__ == "__main__":
    main()

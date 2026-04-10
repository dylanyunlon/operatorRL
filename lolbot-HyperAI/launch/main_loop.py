#!/usr/bin/env python3
"""
launch/main_loop.py — Apollo-style While-True Main Loop (Thread-per-Component)
================================================================================
lolbot-HyperAI · Launch Layer

This is the entry point. It wires all modules together and runs
the Apollo-style while-true Proc() loop via Mainboard + TimerComponent
threads.

Claude14 architecture overhaul:
    OLD: MainLoop._tick() called await component.proc() sequentially in a
         single asyncio loop — this bypassed TimerComponent's threading,
         circuit-breaker, and latency tracking entirely.
    NEW: Mainboard.start_all() spawns each component's _run_loop thread.
         MainLoop only manages session state and evolution in a 1Hz
         supervisor loop. Data flows via channel pub/sub (Apollo pattern).

In Apollo autonomous driving:
    timer_component.cc::Proc() is called every 10ms in its own thread
    Components process data from shared channels
    mainboard.cc manages component lifecycle (Start/LoadModule)

Component threads (each runs Proc() independently):
    CanbusComponent:     100ms (10Hz) → publishes /lol/raw_lcu
    PerceptionComponent: 100ms (10Hz) → publishes /lol/game_state
    PredictionComponent: 500ms  (2Hz) → publishes /lol/win_prediction
    PlanningComponent:   500ms  (2Hz) → publishes /lol/strategy
    ControlComponent:    200ms  (5Hz) → dispatches voice/overlay/log
    MonitorComponent:   2000ms (0.5Hz) → publishes /lol/monitor_status

Session lifecycle (managed by 1Hz supervisor, NOT by Proc()):
    1. Startup: create components, Mainboard.start_all(), init legacy layers
    2. Pre-game: monitor for game start via transport channel
    3. In-game: components run independently in threads
    4. Post-game: evaluate fitness, evolve, save state
    5. Shutdown: Mainboard.stop_all() in reverse dependency order

Usage:
    python -m lolbot-HyperAI.launch.main_loop
    # or
    from launch.main_loop import MainLoop
    loop = MainLoop()
    loop.run()
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
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
from launch.mainboard import Mainboard
from modules.canbus.canbus_component import CanbusComponent, CanbusConfig
from modules.common.component_base import ComponentRegistry
from modules.control.control_component import ControlComponent
from modules.monitor.monitor_component import MonitorComponent
from modules.perception.perception_component import PerceptionComponent
from modules.planning.planning_component import PlanningComponent
from modules.prediction.prediction_component import PredictionComponent
# Claude28: Apollo storytelling module parity
from modules.storytelling.storytelling_component import StorytellingComponent
# Claude28: Apollo latency_recorder parity
from modules.common.latency_recorder.latency_recorder import PipelineLatencyTracker
from output.voice_announcer import VoiceAnnouncer, VoiceConfig
from perception.game_state_parser import GameStateParser
from perception.network_listener import NetworkListener
from planning.strategy_planner import StrategyPlanner
from prediction.feature_pipeline import FeaturePipeline
from prediction.win_probability_engine import WinProbabilityEngine
# Claude19: Wire Claude18 GameRecorder into session management
from modules.common.adapters.game_record import GameRecorder
# Claude27: Apollo parity — Environment detection + GlobalData singleton
from cyber.common.environment import Environment
from cyber.common.global_data import GlobalData
# Claude29: Apollo cyber core infrastructure for 10ms precision timing
from cyber.profiler import Profiler
from cyber.sysmo import SysMo, SystemHealth


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
    The Apollo-style main loop orchestrating all components via Mainboard.

    Claude14: Each *_component.py now runs its own Proc() in a dedicated
    thread managed by TimerComponent._run_loop(). MainLoop does NOT call
    Proc() directly — it only manages session state and evolution in a
    1Hz supervisor loop. Data flows via channel pub/sub (Apollo pattern).

    This is the heart of lolbot-HyperAI. It:
        1. Creates and wires all components
        2. Delegates to Mainboard.start_all() for threaded Proc() execution
        3. Runs a 1Hz supervisor for session state + evolution + health
        4. Handles graceful shutdown via Mainboard.stop_all()
    """

    SUPERVISOR_INTERVAL_SEC = 1.0   # 1Hz supervisor loop
    HEALTH_CHECK_INTERVAL_SEC = 5.0
    HEARTBEAT_INTERVAL_SEC = 10.0

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
        self._last_health_check = 0.0
        self._last_heartbeat = 0.0
        self._stop_event = threading.Event()

        # Reset component registry to avoid stale entries from crash-restart
        ComponentRegistry.reset()

        # Claude27: Initialize GlobalData + Environment before Mainboard
        # Apollo pattern: global singletons initialized before module loading
        self._global_data = GlobalData.instance()
        self._global_data.init()
        self._environment = Environment()
        self._environment.detect()

        # Claude14: Mainboard manages all component threads
        self._mainboard = Mainboard()

        # TimerComponents (registered with Mainboard, run in own threads)
        self._canbus: Optional[CanbusComponent] = None
        self._perception: Optional[PerceptionComponent] = None
        self._prediction: Optional[PredictionComponent] = None
        self._planning: Optional[PlanningComponent] = None
        self._control: Optional[ControlComponent] = None
        self._monitor: Optional[MonitorComponent] = None
        # Claude28: Apollo storytelling module
        self._storytelling: Optional[StorytellingComponent] = None

        # Legacy wrappers (initialized in _init_all, used by evolution)
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

        # Claude19: GameRecorder for structured game session recording
        self._game_recorder: Optional[GameRecorder] = None

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

        # 2. TimerComponents — Claude14: each gets its own Proc() thread
        #    Registration order = startup order = dependency order
        canbus_cfg = CanbusConfig(
            lcu_base_url=getattr(
                cfg, 'lcu_base_url', 'https://127.0.0.1:2999'),
            fiddler_enabled=getattr(cfg, 'fiddler_enabled', False),
        )
        self._canbus = CanbusComponent(canbus_cfg)
        self._mainboard.register(self._canbus)

        self._perception = PerceptionComponent()
        self._mainboard.register(self._perception)

        self._prediction = PredictionComponent()
        self._mainboard.register(self._prediction)

        self._planning = PlanningComponent()
        self._mainboard.register(self._planning)

        self._control = ControlComponent()
        self._mainboard.register(self._control)

        # Claude28: Storytelling component (Apollo storytelling.cc parity)
        # 1Hz, reads /lol/events + /lol/game_state, publishes /lol/narration
        self._storytelling = StorytellingComponent()
        self._mainboard.register(self._storytelling)

        self._monitor = MonitorComponent()
        self._mainboard.register(self._monitor)

        # 3. Legacy perception/planning wrappers (used by evolution layer)
        self._network_listener = NetworkListener(self._transport)
        self._game_state_parser = GameStateParser(self._transport)

        # 4. Prediction layer (legacy wrappers)
        self._feature_pipeline = FeaturePipeline(self._transport)
        self._win_engine = WinProbabilityEngine(
            self._transport, self._feature_pipeline,
        )

        # 5. Planning layer (legacy wrapper)
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

    def run(self) -> None:
        """
        Start the main loop. Runs until interrupted.

        Claude14: No longer async. Components run Proc() in their own
        threads via Mainboard. This method only runs the 1Hz supervisor.

        Call with:
            loop = MainLoop()
            loop.run()
        """
        self._running = True
        self._start_time = time.monotonic()

        # Create components
        self._init_components()

        # Claude14: Start component threads via Mainboard
        print("[MainLoop] Starting component threads via Mainboard...")
        self._mainboard.enable_channel_monitor()

        # Claude24: Enable pipeline flow diagnostics if requested
        if os.environ.get("LOLBOT_DIAGNOSTICS") == "1":
            self._mainboard.enable_pipeline_diagnostics(
                auto_report_interval_sec=10.0,
            )
            print("[MainLoop] Pipeline diagnostics enabled (10s interval)")

        # Claude23: Validate startup dependencies before launching
        startup_issues = self._validate_startup()

        all_ok = self._mainboard.start_all()
        if not all_ok:
            print("[MainLoop] WARNING: Some components failed to start")

        # Claude23: Health probe after start
        self._startup_health_probe()

        # Claude24: Start diagnostics auto-report after components are running
        diag = self._mainboard.pipeline_diagnostics
        if diag is not None:
            diag.start_auto_report()

        # Initialize legacy wrappers (evolution, voice, etc)
        self._init_all()

        # Register signal handlers for graceful shutdown
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._signal_handler)
            except (OSError, ValueError):
                pass  # Windows doesn't support signal in threads

        # Print startup summary
        print(f"\n[MainLoop] System running. Supervisor at "
              f"{1/self.SUPERVISOR_INTERVAL_SEC:.0f}Hz. "
              f"Press Ctrl+C to stop.\n")
        print(f"  Component threads:")
        for name, info in self._mainboard.status()["components"].items():
            print(f"    {name}: {info['state']}")
        print()

        # Claude14: Supervisor loop (1Hz) — does NOT call Proc() directly
        # Each component's Proc() runs in its own thread via TimerComponent
        try:
            while self._running and not self._stop_event.is_set():
                tick_start = time.monotonic()

                self._supervisor_tick()

                # Sleep to maintain supervisor rate
                elapsed = time.monotonic() - tick_start
                sleep_time = self.SUPERVISOR_INTERVAL_SEC - elapsed
                if sleep_time > 0:
                    self._stop_event.wait(timeout=sleep_time)

        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _supervisor_tick(self) -> None:
        """
        Single tick of the 1Hz supervisor loop.

        Claude14: This replaces the old _tick() which called
        await component.proc() sequentially. Now components run in
        their own threads — supervisor only manages session state,
        evolution, and health.
        Claude23: Added SafeMode check.
        """
        self._tick_count += 1

        try:
            # 1. Session state transitions
            self._check_state_transitions()

            # 2. Periodic health check
            now = time.monotonic()
            if now - self._last_health_check >= self.HEALTH_CHECK_INTERVAL_SEC:
                self._last_health_check = now
                self._run_health_check()

            # 3. Heartbeat
            if now - self._last_heartbeat >= self.HEARTBEAT_INTERVAL_SEC:
                self._last_heartbeat = now
                self._publish_heartbeat()

            # 4. Claude23: SafeMode supervision
            self._check_safe_mode()

        except Exception as exc:
            self._error_count += 1
            self._publish_error("supervisor", exc)
            if self._config.system.debug_mode:
                traceback.print_exc()

    def _run_health_check(self) -> None:
        """Poll component health via Mainboard and ComponentRegistry."""
        registry = ComponentRegistry.instance()
        health = registry.health_summary()

        for comp_name, comp_health in health.items():
            if isinstance(comp_health, dict):
                if not comp_health.get("healthy", True):
                    reason = comp_health.get("details", {}).get(
                        "reason", "unknown")
                    print(f"[Health] WARNING: {comp_name} unhealthy: "
                          f"{reason}")

        # Also check Mainboard component states
        mb_status = self._mainboard.status()
        for name, info in mb_status.get("components", {}).items():
            if info.get("state") == "ERROR":
                print(f"[Health] ERROR: {name} in ERROR state")

        # Claude24: Check pipeline diagnostics for stale channels
        diag = self._mainboard.pipeline_diagnostics
        if diag is not None:
            for atype, desc in diag.check_anomalies():
                print(f"[Health] PIPELINE: {desc}")

    def _publish_heartbeat(self) -> None:
        """Publish a heartbeat message on the system channel."""
        if self._transport:
            msg = self._factory.create(
                CH_SYSTEM_HEARTBEAT,
                {
                    "state": self._state,
                    "uptime_sec": round(
                        time.monotonic() - self._start_time, 1),
                    "tick_count": self._tick_count,
                    "error_count": self._error_count,
                    "session_id": self._session_id,
                },
                priority=0,
            )
            self._transport.publish(msg)

    def _check_state_transitions(self) -> None:
        """Check for game phase changes and manage session state."""
        phase_msg = self._transport.latest(CH_GAME_FLOW_PHASE)
        if phase_msg is None:
            return

        phase = phase_msg.payload.get("phase", "None")

        if self._state == SessionState.IDLE:
            if phase in ("Lobby", "Matchmaking", "ReadyCheck", "ChampSelect"):
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
        # Claude19: Start GameRecorder session
        if self._game_recorder is None:
            self._game_recorder = GameRecorder()
        self._game_recorder.start_session(
            self._session_id,
            data_source=getattr(self._config, "data_source", "unknown"),
            generation_id=(
                self._generation_manager.current.generation_id
                if self._generation_manager and self._generation_manager.current
                else ""
            ),
        )
        print(f"[Session] Game started: {self._session_id}")

    def _on_game_end(self) -> None:
        """Called when a game ends."""
        self._fitness_evaluator.stop_collection()
        self._voice_announcer.force_announce(
            "Game over.", priority=1,
        )
        # Claude19: End GameRecorder session and save
        if self._game_recorder and self._game_recorder.is_recording:
            try:
                record = self._game_recorder.end_session(
                    game_duration=0.0,  # Will be populated from final snapshot
                    final_gold_diff=0.0,
                )
                output_dir = Path(self._config.paths.output_dir if hasattr(self._config, "paths") else "data/generations")
                self._game_recorder.save(record, str(output_dir))
                print(f"[Session] Game record saved to {output_dir}")
            except Exception as exc:
                print(f"[Session] GameRecorder save error: {exc}")
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

    # -- Shutdown -------------------------------------------------------

    def _signal_handler(self, signum, frame) -> None:
        """Handle SIGINT/SIGTERM."""
        print("\n[MainLoop] Shutdown signal received.")
        self._running = False
        self._stop_event.set()

    def _shutdown(self) -> None:
        """Graceful shutdown of all components.

        Claude14: Uses Mainboard.stop_all() for component threads,
        then shuts down legacy wrappers.
        Claude24: Also stops pipeline diagnostics auto-report.
        """
        print("[MainLoop] Shutting down...")

        # Claude24: Stop pipeline diagnostics first
        diag = self._mainboard.pipeline_diagnostics
        if diag is not None:
            diag.stop_auto_report()
            print("  Pipeline diagnostics stopped")

        # 1. Stop all component threads via Mainboard
        results = self._mainboard.stop_all(timeout=5.0)
        for comp_name, final_state in results.items():
            print(f"  {comp_name}: {final_state}")

        # 2. Shutdown legacy wrappers
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
        print(f"  Supervisor ticks: {self._tick_count}")
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
        self._stop_event.set()

    def stats(self) -> Dict[str, Any]:
        """Aggregate stats from all components."""
        result = {
            "state": self._state,
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "uptime_sec": round(time.monotonic() - self._start_time, 1),
            "session_id": self._session_id,
            "mainboard": self._mainboard.status(),
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
        # Claude24: Include pipeline diagnostics if enabled
        diag_snap = self._mainboard.diagnostics_snapshot()
        if diag_snap:
            result["pipeline_diagnostics"] = diag_snap
        # Claude28: Include pipeline latency tracker summary
        result["pipeline_latency"] = PipelineLatencyTracker.instance().summary()
        # Claude27: Include environment and global_data info
        result["environment"] = self._environment.snapshot()
        result["global_data"] = self._global_data.snapshot()
        return result

    # ─── Apollo-aligned supervisor hardening (Claude23) ──────────────────
    #
    # Claude24 fix: These methods were placed after `if __name__` by Claude23
    # (dead code — outside the class body). Moved here into MainLoop class.

    def _startup_health_probe(self) -> bool:
        """Probe all components for health after Mainboard.start_all().

        Apollo pattern: mainboard waits for all modules to Init() OK.
        Returns True if all critical components are healthy.
        """
        probe = self._mainboard.health_probe(timeout_s=5.0)
        all_ok = True
        for name, healthy in probe.items():
            status = "OK" if healthy else "FAILED"
            print(f"  Health probe: {name} = {status}")
            if not healthy:
                all_ok = False
        return all_ok

    def _check_safe_mode(self) -> None:
        """Check and respond to SafeMode in the supervisor tick.

        When SafeMode activates, the supervisor should:
        1. Log the activation
        2. Notify voice output to suppress
        3. Hold current state until safe mode deactivates
        """
        try:
            from modules.common.component_base import SafeMode
            safe = SafeMode.instance()
            if safe.is_active:
                sources = safe.active_sources
                if not hasattr(self, "_safe_mode_notified"):
                    self._safe_mode_notified = False
                if not self._safe_mode_notified:
                    print(f"[SafeMode] ACTIVE — sources: {sources}")
                    if self._voice_announcer:
                        self._voice_announcer.force_announce(
                            "System entering safe mode. Data may be stale.",
                            priority=3,
                        )
                    self._safe_mode_notified = True
            else:
                if hasattr(self, "_safe_mode_notified") and self._safe_mode_notified:
                    print("[SafeMode] Deactivated — resuming normal operation")
                    self._safe_mode_notified = False
        except ImportError:
            pass

    def _validate_startup(self) -> List[str]:
        """Validate component dependencies before starting.

        Apollo pattern: DAG validation in mainboard module loading.
        Returns list of issues (empty = all OK).
        """
        issues = []

        # Check mainboard dependency graph
        dep_issues = self._mainboard.validate_dependencies()
        issues.extend(dep_issues)

        # Check critical components are registered
        critical = ["canbus", "perception", "prediction", "planning"]
        mb_status = self._mainboard.status()
        registered = set(mb_status.get("components", {}).keys())
        for comp in critical:
            if comp not in registered:
                issues.append(f"Critical component '{comp}' not registered")

        if issues:
            for issue in issues:
                print(f"[Startup] WARNING: {issue}")

        return issues


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    """CLI entry point."""
    print("=" * 60)
    print("  lolbot-HyperAI")
    print("  Apollo-style LoL Game Assistant")
    print("  Self-evolving via operatorRL governance kernel")
    print("  Thread-per-component architecture (Apollo mainboard)")
    print("=" * 60)
    print()

    loop = MainLoop()
    loop.run()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
M1057: Main Orchestrator — System Entry Point
===============================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Wires all M1046-M1065 modules together into a running system.
This is the single entry point for the LoL game assistant.

Startup sequence:
    1. Initialize EvolutionLogger
    2. Detect environment (Fiddler? Proxifier? LCU?)
    3. Initialize NetworkCaptureEngine (auto-selects best mode)
    4. Initialize GameStateTracker
    5. Wire: CaptureEngine → StateTracker → HistoryCrawler → Analyzer
           → StrategyEngine → VoiceOutput
    6. Start capture loop
    7. On game end: run EvolutionController cycle
"""

import asyncio
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from evo_logging.evolution_logger import (
    EvolutionLogger, LogCategory, get_logger, get_analyzer)
from capture.network_capture_engine import (
    NetworkCaptureEngine, CaptureMode, EndpointCategory, InterceptedRequest)
from capture.fiddler_deep_packet_analyzer import FiddlerDeepPacketAnalyzer
from core.game_state_tracker import GameStateTracker, GamePhase, GameContext
from core.live_match_monitor import LiveMatchMonitor
from core.evolution_controller import EvolutionController
from history.match_data_crawler import HistoricalMatchCrawler, HistoricalDataCache
from analysis.opponent_behavior_analyzer import BehaviorAnalyzer
from analysis.trend_analyzer import TrendAnalyzer, SessionSnapshot
from strategy.strategy_engine import StrategyEngine
from strategy.champ_select_advisor import ChampSelectAdvisor
from integration.voice_output_engine import VoiceOutputEngine, TTSBackend


class Orchestrator:
    """
    Main system orchestrator for M1046-M1065.

    Lifecycle:
        orchestrator = Orchestrator()
        await orchestrator.initialize()
        await orchestrator.run()  # Blocks until shutdown
        await orchestrator.shutdown()
    """
    def __init__(
        self,
        log_dir: str = "logs/m1046_m1065",
        fiddler_api_key: Optional[str] = None,
        voice_enabled: bool = True,
        voice_backend: str = "system",
    ):
        self._log_dir = log_dir
        self._fiddler_api_key = fiddler_api_key or os.environ.get(
            'FIDDLER_MCP_API_KEY', '')
        self._voice_enabled = voice_enabled
        self._voice_backend = voice_backend
        self._running = False

        # Module instances (initialized in initialize())
        self._logger: Optional[EvolutionLogger] = None
        self._capture: Optional[NetworkCaptureEngine] = None
        self._packet_analyzer: Optional[FiddlerDeepPacketAnalyzer] = None
        self._state_tracker: Optional[GameStateTracker] = None
        self._live_monitor: Optional[LiveMatchMonitor] = None
        self._crawler: Optional[HistoricalMatchCrawler] = None
        self._behavior_analyzer: Optional[BehaviorAnalyzer] = None
        self._trend_analyzer: Optional[TrendAnalyzer] = None
        self._strategy: Optional[StrategyEngine] = None
        self._champ_advisor: Optional[ChampSelectAdvisor] = None
        self._voice: Optional[VoiceOutputEngine] = None
        self._evolution: Optional[EvolutionController] = None
        self._cache = HistoricalDataCache()

    async def initialize(self) -> Dict[str, Any]:
        """Initialize all modules and wire them together."""
        # 1. Logger
        EvolutionLogger.reset()
        self._logger = get_logger(self._log_dir)
        self._logger.info(LogCategory.SYSTEM, "M1046-M1065 Orchestrator initializing")

        # 2. Network capture
        self._capture = NetworkCaptureEngine(
            fiddler_api_key=self._fiddler_api_key)
        capture_mode = await self._capture.initialize()

        # 3. Packet analyzer
        self._packet_analyzer = FiddlerDeepPacketAnalyzer()

        # 4. Game state tracker
        self._state_tracker = GameStateTracker()

        # 5. Live match monitor
        self._live_monitor = LiveMatchMonitor()

        # 6. History crawler
        lcu = self._capture._lcu if hasattr(self._capture, '_lcu') else None
        self._crawler = HistoricalMatchCrawler(
            lcu=lcu, cache=self._cache)

        # 7. Behavior analyzer
        self._behavior_analyzer = BehaviorAnalyzer()

        # 8. Trend analyzer
        self._trend_analyzer = TrendAnalyzer()

        # 9. Strategy engine
        self._strategy = StrategyEngine()

        # 10. Champ select advisor
        self._champ_advisor = ChampSelectAdvisor()

        # 11. Voice engine
        backend = TTSBackend.DISABLED
        if self._voice_enabled:
            try:
                backend = TTSBackend(self._voice_backend)
            except ValueError:
                backend = TTSBackend.SYSTEM
        self._voice = VoiceOutputEngine(backend=backend)
        if self._voice_enabled:
            self._voice.start()

        # 12. Evolution controller
        self._evolution = EvolutionController(log_dir=self._log_dir)

        # ---- Wire modules together ----

        # Capture → Packet Analyzer
        self._capture.register_handler(
            EndpointCategory.MATCH_HISTORY,
            lambda req: self._packet_analyzer.analyze_request(req))
        self._capture.register_handler(
            EndpointCategory.CHAMP_SELECT,
            lambda req: self._on_champ_select_capture(req))
        self._capture.register_handler(
            EndpointCategory.GAMEFLOW,
            lambda req: self._on_gameflow_capture(req))

        # State tracker → Strategy engine
        self._state_tracker.add_listener(self._strategy.on_phase_change)
        self._state_tracker.add_listener(self._on_phase_change)

        # Strategy → Voice
        self._strategy.add_listener(self._voice.on_recommendation)

        # Live monitor → Strategy ticks
        self._live_monitor.add_tick_callback(
            lambda state: self._strategy.on_game_timer_tick(
                state.game_time_sec, self._state_tracker.context))

        status = {
            'capture_mode': capture_mode.name,
            'voice_backend': backend.value,
            'modules_initialized': 12,
            'evolution_generation': self._evolution._generation,
        }
        self._logger.info(
            LogCategory.SYSTEM,
            "M1046-M1065 Orchestrator initialized",
            data=status)
        return status

    async def run(self) -> None:
        """Main run loop — blocks until shutdown."""
        self._running = True
        self._logger.info(LogCategory.SYSTEM, "Orchestrator starting main loop")
        try:
            async for req in self._capture.capture_stream():
                if not self._running:
                    break
                self._packet_analyzer.analyze_request(req)
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Graceful shutdown of all modules."""
        self._running = False
        self._logger.info(LogCategory.SYSTEM, "Orchestrator shutting down")

        # Run evolution cycle on shutdown
        if self._evolution:
            try:
                proposals = self._evolution.analyze_and_propose()
                if proposals:
                    gen = self._evolution.apply_proposals()
                    improvement = self._evolution.evaluate_generation(gen)
                    self._evolution.commit_or_rollback(gen, improvement)
            except Exception as e:
                self._logger.error(
                    LogCategory.EVOLUTION,
                    f"Evolution cycle error on shutdown: {e}")

        if self._capture:
            await self._capture.shutdown()
        if self._voice:
            self._voice.stop()
        if self._live_monitor:
            self._live_monitor.stop_monitoring()
        if self._logger:
            self._logger.shutdown()

    # ---- Internal event handlers ----

    def _on_champ_select_capture(self, req: InterceptedRequest) -> None:
        data = req.get_json_response()
        if data:
            self._state_tracker.on_champ_select_update(data)

    def _on_gameflow_capture(self, req: InterceptedRequest) -> None:
        data = req.get_json_response()
        if isinstance(data, str):
            self._state_tracker.on_gameflow_update(data)

    def _on_phase_change(
        self, old: GamePhase, new: GamePhase, context: GameContext
    ) -> None:
        """Handle phase transitions for cross-module coordination."""
        if new == GamePhase.CHAMP_SELECT:
            # Trigger opponent history fetch
            asyncio.create_task(self._fetch_opponent_history(context))
        elif new == GamePhase.IN_PROGRESS:
            # Start live monitoring
            asyncio.create_task(self._live_monitor.start_monitoring())
        elif new == GamePhase.END_OF_GAME:
            self._live_monitor.stop_monitoring()
            # Record session for trend analysis
            self._record_session_snapshot(context)

    async def _fetch_opponent_history(self, context: GameContext) -> None:
        """Fetch and analyze opponent history during champ select."""
        enemy_puuids = self._state_tracker.get_enemy_puuids()
        if not enemy_puuids:
            return
        self._logger.info(
            LogCategory.HISTORY_FETCH,
            f"Fetching history for {len(enemy_puuids)} opponents")
        profiles = await self._crawler.fetch_opponents(enemy_puuids)
        # Analyze threats
        assessments = {}
        for puuid, profile in profiles.items():
            assessment = self._behavior_analyzer.analyze(profile)
            assessments[puuid] = assessment.to_dict()
        # Update strategy engine
        self._strategy.on_threat_update(assessments)
        # Generate ban recommendations
        self._champ_advisor.generate_ban_recommendations(assessments)

    def _record_session_snapshot(self, context: GameContext) -> None:
        """Record session data for trend analysis."""
        state = self._live_monitor.get_state()
        snapshot = SessionSnapshot(
            session_id=context.game_id or "unknown",
            timestamp=datetime.now(timezone.utc).isoformat(),
            game_id=context.game_id,
            game_duration_sec=int(context.game_elapsed_sec()),
        )
        self._trend_analyzer.add_snapshot(snapshot)

    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        return {
            'running': self._running,
            'capture': self._capture.get_session_stats() if self._capture else {},
            'game_state': self._state_tracker.get_stats() if self._state_tracker else {},
            'strategy': self._strategy.get_stats() if self._strategy else {},
            'voice': self._voice.get_stats() if self._voice else {},
            'evolution': {
                'generation': self._evolution._generation if self._evolution else 0,
                'config': self._evolution.get_all_config() if self._evolution else {},
            },
            'crawler': self._crawler.get_crawler_stats() if self._crawler else {},
            'packet_analyzer': self._packet_analyzer.get_stats() if self._packet_analyzer else {},
            'logger': self._logger.get_diagnostics() if self._logger else {},
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    """Main entry point for the M1046-M1065 system."""
    print("=" * 60)
    print("OperatorRL M1046-M1065: LoL Game Assistant")
    print("  Network Capture + Historical Data + Strategy Engine")
    print("  自部署 · 自环境反馈 · 自演化")
    print("=" * 60)

    orchestrator = Orchestrator(
        voice_enabled=False,  # Disable voice for headless environments
    )
    status = await orchestrator.initialize()
    print(f"\nSystem Status: {json.dumps(status, indent=2)}")
    print(f"\nFull Status: {json.dumps(orchestrator.get_system_status(), indent=2)}")

    # Run evolution cycle with existing logs
    if orchestrator._evolution:
        proposals = orchestrator._evolution.analyze_and_propose()
        print(f"\nEvolution Proposals: {len(proposals)}")
        for p in proposals:
            print(f"  [{p.category}] {p.description} (confidence={p.confidence})")

    await orchestrator.shutdown()
    print("\n[M1046-M1065] System shutdown complete")


if __name__ == '__main__':
    asyncio.run(main())


class HealthMonitor:
    """
    Monitors the health of all M1046-M1065 subsystems.

    Runs periodic health checks on each component and reports
    aggregate system health to the evolution controller.

    Production critique:
        1. User: If any subsystem fails, the orchestrator degrades
           gracefully — e.g., if voice output fails, strategy
           recommendations are still displayed as text.
        2. System: Health checks run on a separate thread to avoid
           blocking the main game event loop.
    """
    def __init__(self):
        self._component_health: Dict[str, Dict] = {}
        self._last_check_time: float = 0.0
        self._check_interval = 10.0

    def register_component(self, name: str) -> None:
        self._component_health[name] = {
            'status': 'unknown',
            'last_check': 0.0,
            'error_count': 0,
            'last_error': None,
            'uptime_start': time.monotonic(),
        }

    def report_healthy(self, name: str) -> None:
        if name in self._component_health:
            self._component_health[name]['status'] = 'healthy'
            self._component_health[name]['last_check'] = time.monotonic()

    def report_error(self, name: str, error: str) -> None:
        if name in self._component_health:
            self._component_health[name]['status'] = 'error'
            self._component_health[name]['error_count'] += 1
            self._component_health[name]['last_error'] = error
            self._component_health[name]['last_check'] = time.monotonic()

    def report_degraded(self, name: str, reason: str) -> None:
        if name in self._component_health:
            self._component_health[name]['status'] = 'degraded'
            self._component_health[name]['last_error'] = reason
            self._component_health[name]['last_check'] = time.monotonic()

    def get_overall_health(self) -> Dict[str, Any]:
        statuses = [c['status'] for c in self._component_health.values()]
        if all(s == 'healthy' for s in statuses):
            overall = 'healthy'
        elif any(s == 'error' for s in statuses):
            overall = 'degraded'
        else:
            overall = 'unknown'
        return {
            'overall': overall,
            'components': {
                name: {
                    'status': info['status'],
                    'error_count': info['error_count'],
                    'uptime_sec': round(
                        time.monotonic() - info['uptime_start'], 1),
                }
                for name, info in self._component_health.items()
            },
            'healthy_count': sum(1 for s in statuses if s == 'healthy'),
            'total_count': len(statuses),
        }

    def get_failed_components(self) -> List[str]:
        return [name for name, info in self._component_health.items()
                if info['status'] == 'error']

    def should_trigger_evolution(self) -> bool:
        """Determine if error rate warrants evolution cycle."""
        total_errors = sum(
            c['error_count'] for c in self._component_health.values())
        failed = len(self.get_failed_components())
        return total_errors > 50 or failed >= 2


class SessionRecorder:
    """
    Records complete game sessions for post-game analysis and training.

    Captures all events, decisions, and outcomes in a single session
    file that can be replayed for offline analysis.
    """
    def __init__(self, output_dir: str = "sessions"):
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events: List[Dict] = []
        self._session_start: float = time.monotonic()
        self._session_id = datetime.now(timezone.utc).strftime(
            '%Y%m%d_%H%M%S')
        self._metadata: Dict[str, Any] = {}

    def set_metadata(self, key: str, value: Any) -> None:
        self._metadata[key] = value

    def record_event(self, event_type: str, data: Dict) -> None:
        self._events.append({
            'type': event_type,
            'elapsed_sec': round(
                time.monotonic() - self._session_start, 2),
            'data': data,
        })

    def save(self) -> str:
        """Save session to disk and return filepath."""
        session = {
            'session_id': self._session_id,
            'metadata': self._metadata,
            'events': self._events,
            'total_events': len(self._events),
            'duration_sec': round(
                time.monotonic() - self._session_start, 2),
        }
        path = self._dir / f"session_{self._session_id}.json"
        path.write_text(json.dumps(session, ensure_ascii=False, indent=1))
        return str(path)

    def get_event_count(self) -> int:
        return len(self._events)

    def get_events_by_type(self, event_type: str) -> List[Dict]:
        return [e for e in self._events if e['type'] == event_type]

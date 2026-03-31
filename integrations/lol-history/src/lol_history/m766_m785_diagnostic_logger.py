"""
M766-M785 Diagnostic Logger — System for generating runtime logs across all modules.

This logger system initializes each M766-M785 module, runs diagnostic scenarios,
and collects structured logs for analysis and code improvement.

Architecture:
  - Instantiates all 20 modules (M766-M785)
  - Runs each through a simulated game lifecycle
  - Collects timing, error, and coverage logs
  - Outputs structured JSON diagnostics per module
"""
from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime
from io import StringIO
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("m766_m785_diagnostic")


class DiagnosticLogger:
    """Collects and reports diagnostics across all M766-M785 modules."""

    def __init__(self) -> None:
        self._logs: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._timings: Dict[str, List[float]] = defaultdict(list)
        self._errors: Dict[str, List[str]] = defaultdict(list)
        self._coverage: Dict[str, Dict[str, bool]] = {}

    def log_event(self, module: str, event: str, data: Any = None) -> None:
        self._logs[module].append({
            "ts": datetime.utcnow().isoformat(),
            "event": event,
            "data": data,
        })

    def log_timing(self, module: str, op: str, elapsed: float) -> None:
        self._timings[f"{module}.{op}"].append(elapsed)
        self.log_event(module, f"timing.{op}", {"elapsed_ms": elapsed * 1000})

    def log_error(self, module: str, error: str) -> None:
        self._errors[module].append(error)
        self.log_event(module, "error", {"error": error})

    def log_coverage(self, module: str, method: str, covered: bool) -> None:
        if module not in self._coverage:
            self._coverage[module] = {}
        self._coverage[module][method] = covered

    def get_report(self) -> Dict[str, Any]:
        report = {}
        for mod, logs in self._logs.items():
            report[mod] = {
                "total_events": len(logs),
                "errors": len(self._errors.get(mod, [])),
                "coverage": self._coverage.get(mod, {}),
                "last_event": logs[-1] if logs else None,
            }
        return report

    def dump_json(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump({
                "generated_at": datetime.utcnow().isoformat(),
                "logs": {k: v for k, v in self._logs.items()},
                "timings": {k: v for k, v in self._timings.items()},
                "errors": {k: v for k, v in self._errors.items()},
                "coverage": self._coverage,
                "summary": self.get_report(),
            }, f, indent=2, default=str)


def _timed(diag: DiagnosticLogger, module_name: str, op_name: str, fn, *args, **kwargs):
    """Run fn with timing and error capture."""
    t0 = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.monotonic() - t0
        diag.log_timing(module_name, op_name, elapsed)
        diag.log_coverage(module_name, op_name, True)
        return result
    except Exception as e:
        elapsed = time.monotonic() - t0
        diag.log_timing(module_name, op_name, elapsed)
        diag.log_error(module_name, f"{op_name}: {e}")
        diag.log_coverage(module_name, op_name, False)
        return None


# ─── Simulated game data for diagnostic runs ─────────────────────────────────

MOCK_PLAYER_DATA = {
    "summoner_name": "TestPlayer",
    "champion": "Yasuo",
    "champion_id": 157,
    "team": "ORDER",
    "position": "MIDDLE",
    "level": 10,
    "gold": 5200,
    "xp": 8500,
    "kills": 3,
    "deaths": 1,
    "assists": 5,
    "cs": 120,
    "items": [3031, 3046, 1001],
}

MOCK_EVENT_DATA = {
    "Events": [
        {"EventID": 1, "EventName": "GameStart", "EventTime": 0.0},
        {"EventID": 2, "EventName": "ChampionKill", "EventTime": 120.5,
         "KillerName": "TestPlayer", "VictimName": "Enemy1"},
        {"EventID": 3, "EventName": "DragonKill", "EventTime": 300.0,
         "DragonType": "Infernal", "KillerName": "TestPlayer"},
        {"EventID": 4, "EventName": "TurretKilled", "EventTime": 450.0,
         "TurretKilled": "Turret_T2_R_03"},
        {"EventID": 5, "EventName": "BaronKill", "EventTime": 1200.0,
         "KillerName": "TestPlayer"},
    ]
}

MOCK_GAME_STATE = {
    "game_time": 900.0,
    "phase": "ingame",
    "players": [MOCK_PLAYER_DATA],
    "events": MOCK_EVENT_DATA["Events"],
    "map_number": 11,
}

MOCK_HISTORY_DATA = {
    "matches": [
        {"game_id": f"game_{i}", "win": i % 3 != 0, "champion": "Yasuo",
         "kills": 5 + i, "deaths": 3, "assists": 7, "duration": 1800 + i * 60}
        for i in range(20)
    ],
    "rank": {"tier": "DIAMOND", "division": "II", "lp": 65},
}

MOCK_SUGGESTIONS = [
    {"id": "s1", "type": "macro", "text": "推线到对面二塔", "priority": "high",
     "timestamp": 300.0, "adhered": True, "outcome": "positive"},
    {"id": "s2", "type": "objective", "text": "做龙", "priority": "critical",
     "timestamp": 600.0, "adhered": True, "outcome": "positive"},
    {"id": "s3", "type": "recall", "text": "回城补装备", "priority": "medium",
     "timestamp": 900.0, "adhered": False, "outcome": "neutral"},
]


def run_diagnostics():
    """Run diagnostic scenarios for all M766-M785 modules and produce logs."""
    diag = DiagnosticLogger()
    modules_status = {}

    # === M766: LiveClientDataPoller ===
    logger.info("=== Diagnosing M766: LiveClientDataPoller ===")
    try:
        from lol_history.live_client_data_poller import LiveClientDataPoller
        poller = LiveClientDataPoller()
        _timed(diag, "M766", "init", lambda: poller)
        _timed(diag, "M766", "poll_once", poller.poll_once)
        _timed(diag, "M766", "check_game_active", poller.check_game_active)
        _timed(diag, "M766", "get_all_game_data", poller.get_all_game_data)
        _timed(diag, "M766", "get_active_player", poller.get_active_player)
        _timed(diag, "M766", "get_player_list", poller.get_player_list)
        _timed(diag, "M766", "get_event_data", poller.get_event_data)
        _timed(diag, "M766", "get_stats", poller.get_stats)
        modules_status["M766"] = "OK"
    except Exception as e:
        diag.log_error("M766", traceback.format_exc())
        modules_status["M766"] = f"FAIL: {e}"

    # === M767: LiveGameEventStreamProcessor ===
    logger.info("=== Diagnosing M767: LiveGameEventStreamProcessor ===")
    try:
        from lol_history.live_game_event_stream_processor import LiveGameEventStreamProcessor
        esp = LiveGameEventStreamProcessor()
        _timed(diag, "M767", "init", lambda: esp)
        _timed(diag, "M767", "process_events", esp.process_events, MOCK_EVENT_DATA["Events"])
        _timed(diag, "M767", "get_event_summary", esp.get_event_summary)
        _timed(diag, "M767", "get_events_by_type", esp.get_events_by_type, "ChampionKill")
        _timed(diag, "M767", "get_recent_events", esp.get_recent_events, 60.0)
        _timed(diag, "M767", "get_stats", esp.get_stats)
        modules_status["M767"] = "OK"
    except Exception as e:
        diag.log_error("M767", traceback.format_exc())
        modules_status["M767"] = f"FAIL: {e}"

    # === M768: RealtimeGoldXpTracker ===
    logger.info("=== Diagnosing M768: RealtimeGoldXpTracker ===")
    try:
        from lol_history.realtime_gold_xp_tracker import RealtimeGoldXpTracker
        gxt = RealtimeGoldXpTracker()
        _timed(diag, "M768", "init", lambda: gxt)
        _timed(diag, "M768", "update_player", gxt.update_player, "TestPlayer", 5200, 8500, 300.0)
        _timed(diag, "M768", "update_player_2", gxt.update_player, "TestPlayer", 5800, 9200, 360.0)
        _timed(diag, "M768", "get_player_trend", gxt.get_player_trend, "TestPlayer")
        _timed(diag, "M768", "get_team_gold_diff", gxt.get_team_gold_diff, ["TestPlayer"], ["Enemy1"])
        _timed(diag, "M768", "detect_gold_spike", gxt.detect_gold_spike, "TestPlayer")
        _timed(diag, "M768", "get_stats", gxt.get_stats)
        modules_status["M768"] = "OK"
    except Exception as e:
        diag.log_error("M768", traceback.format_exc())
        modules_status["M768"] = f"FAIL: {e}"

    # === M769: RealtimeMinimapTracker ===
    logger.info("=== Diagnosing M769: RealtimeMinimapTracker ===")
    try:
        from lol_history.realtime_minimap_tracker import RealtimeMinimapTracker
        mmt = RealtimeMinimapTracker()
        _timed(diag, "M769", "init", lambda: mmt)
        _timed(diag, "M769", "update_position", mmt.update_position, "TestPlayer", 7000.0, 7000.0, 300.0)
        _timed(diag, "M769", "update_position_2", mmt.update_position, "TestPlayer", 7500.0, 7200.0, 310.0)
        _timed(diag, "M769", "get_trajectory", mmt.get_trajectory, "TestPlayer")
        _timed(diag, "M769", "detect_mia", mmt.detect_mia, ["TestPlayer", "Enemy1"], 330.0)
        _timed(diag, "M769", "get_clustering", mmt.get_clustering, 330.0)
        _timed(diag, "M769", "get_stats", mmt.get_stats)
        modules_status["M769"] = "OK"
    except Exception as e:
        diag.log_error("M769", traceback.format_exc())
        modules_status["M769"] = f"FAIL: {e}"

    # === M770: IngameDecisionSuggestionEngine ===
    logger.info("=== Diagnosing M770: IngameDecisionSuggestionEngine ===")
    try:
        from lol_history.ingame_decision_suggestion_engine import IngameDecisionSuggestionEngine
        dse = IngameDecisionSuggestionEngine()
        _timed(diag, "M770", "init", lambda: dse)
        _timed(diag, "M770", "generate_suggestions", dse.generate_suggestions, MOCK_GAME_STATE, MOCK_HISTORY_DATA)
        _timed(diag, "M770", "get_top_suggestion", dse.get_top_suggestion, MOCK_GAME_STATE, MOCK_HISTORY_DATA)
        _timed(diag, "M770", "get_stats", dse.get_stats)
        modules_status["M770"] = "OK"
    except Exception as e:
        diag.log_error("M770", traceback.format_exc())
        modules_status["M770"] = f"FAIL: {e}"

    # === M771: IngameVoiceNarrator ===
    logger.info("=== Diagnosing M771: IngameVoiceNarrator ===")
    try:
        from lol_history.ingame_voice_narrator import IngameVoiceNarrator
        ivn = IngameVoiceNarrator()
        _timed(diag, "M771", "init", lambda: ivn)
        suggestion = {"type": "objective", "text": "做龙", "priority": "critical", "timestamp": 600.0}
        _timed(diag, "M771", "narrate_suggestion", ivn.narrate_suggestion, suggestion)
        _timed(diag, "M771", "check_cooldown", ivn.check_cooldown)
        _timed(diag, "M771", "format_for_tts", ivn.format_for_tts, "做龙，优先级最高。")
        _timed(diag, "M771", "get_stats", ivn.get_stats)
        modules_status["M771"] = "OK"
    except Exception as e:
        diag.log_error("M771", traceback.format_exc())
        modules_status["M771"] = f"FAIL: {e}"

    # === M772: PostgameDataCollector ===
    logger.info("=== Diagnosing M772: PostgameDataCollector ===")
    try:
        from lol_history.postgame_data_collector import PostgameDataCollector
        pdc = PostgameDataCollector()
        _timed(diag, "M772", "init", lambda: pdc)
        _timed(diag, "M772", "collect_endgame", pdc.collect_endgame, MOCK_GAME_STATE, MOCK_EVENT_DATA)
        _timed(diag, "M772", "compare_predictions", pdc.compare_predictions,
               {"win_probability": 0.65}, {"actual_win": True})
        _timed(diag, "M772", "export_training_record", pdc.export_training_record, "game_123")
        _timed(diag, "M772", "get_stats", pdc.get_stats)
        modules_status["M772"] = "OK"
    except Exception as e:
        diag.log_error("M772", traceback.format_exc())
        modules_status["M772"] = f"FAIL: {e}"

    # === M773: EvolutionFeedbackSignalRouter ===
    logger.info("=== Diagnosing M773: EvolutionFeedbackSignalRouter ===")
    try:
        from lol_history.evolution_feedback_signal_router import EvolutionFeedbackSignalRouter
        efsr = EvolutionFeedbackSignalRouter()
        _timed(diag, "M773", "init", lambda: efsr)
        _timed(diag, "M773", "route_signal", efsr.route_signal,
               "prediction_accuracy", {"accuracy": 0.72, "game_id": "g1"})
        _timed(diag, "M773", "register_handler", efsr.register_handler,
               "prediction_accuracy", lambda s: None)
        _timed(diag, "M773", "flush_batch", efsr.flush_batch)
        _timed(diag, "M773", "get_stats", efsr.get_stats)
        modules_status["M773"] = "OK"
    except Exception as e:
        diag.log_error("M773", traceback.format_exc())
        modules_status["M773"] = f"FAIL: {e}"

    # === M774: ModelVersionRollbackManager ===
    logger.info("=== Diagnosing M774: ModelVersionRollbackManager ===")
    try:
        from lol_history.model_version_rollback_manager import ModelVersionRollbackManager
        mvrm = ModelVersionRollbackManager()
        _timed(diag, "M774", "init", lambda: mvrm)
        _timed(diag, "M774", "register_version", mvrm.register_version,
               "v1.0", {"type": "intel_model", "weights": "mock"})
        _timed(diag, "M774", "register_version_2", mvrm.register_version,
               "v1.1", {"type": "intel_model", "weights": "mock_v2"})
        _timed(diag, "M774", "record_performance", mvrm.record_performance,
               "v1.1", {"winrate": 0.45, "accuracy": 0.60, "latency_ms": 50})
        _timed(diag, "M774", "check_rollback_needed", mvrm.check_rollback_needed, "v1.1")
        _timed(diag, "M774", "get_version_history", mvrm.get_version_history)
        _timed(diag, "M774", "get_stats", mvrm.get_stats)
        modules_status["M774"] = "OK"
    except Exception as e:
        diag.log_error("M774", traceback.format_exc())
        modules_status["M774"] = f"FAIL: {e}"

    # === M775: FiddlerProtocolLiveEnricher ===
    logger.info("=== Diagnosing M775: FiddlerProtocolLiveEnricher ===")
    try:
        from lol_history.fiddler_protocol_live_enricher import FiddlerProtocolLiveEnricher
        fple = FiddlerProtocolLiveEnricher()
        _timed(diag, "M775", "init", lambda: fple)
        _timed(diag, "M775", "enrich_from_packet", fple.enrich_from_packet,
               {"url": "https://127.0.0.1:2999/liveclientdata/allgamedata",
                "method": "GET", "status": 200, "body": json.dumps(MOCK_GAME_STATE),
                "timestamp": time.time()})
        _timed(diag, "M775", "fuse_lcd_fiddler", fple.fuse_lcd_fiddler,
               MOCK_GAME_STATE, {"extra_timing": 15.2})
        _timed(diag, "M775", "get_stats", fple.get_stats)
        modules_status["M775"] = "OK"
    except Exception as e:
        diag.log_error("M775", traceback.format_exc())
        modules_status["M775"] = f"FAIL: {e}"

    # === M776: E2eGameSessionOrchestrator ===
    logger.info("=== Diagnosing M776: E2eGameSessionOrchestrator ===")
    try:
        from lol_history.e2e_game_session_orchestrator import E2eGameSessionOrchestrator
        egso = E2eGameSessionOrchestrator()
        _timed(diag, "M776", "init", lambda: egso)
        _timed(diag, "M776", "transition_phase", egso.transition_phase, "pregame")
        _timed(diag, "M776", "transition_phase_ingame", egso.transition_phase, "ingame")
        _timed(diag, "M776", "register_module", egso.register_module, "test_mod", type("M", (), {"get_stats": lambda s: {}})(), ["pregame"])
        _timed(diag, "M776", "get_active_modules", egso.get_active_modules)
        _timed(diag, "M776", "get_stats", egso.get_stats)
        modules_status["M776"] = "OK"
    except Exception as e:
        diag.log_error("M776", traceback.format_exc())
        modules_status["M776"] = f"FAIL: {e}"

    # === M777: SuggestionAdherenceTracker ===
    logger.info("=== Diagnosing M777: SuggestionAdherenceTracker ===")
    try:
        from lol_history.suggestion_adherence_tracker import SuggestionAdherenceTracker
        sat = SuggestionAdherenceTracker()
        _timed(diag, "M777", "init", lambda: sat)
        for s in MOCK_SUGGESTIONS:
            _timed(diag, "M777", f"record_{s['id']}", sat.record_suggestion,
                   s["id"], s["type"], s["text"], s["priority"], s["timestamp"])
        _timed(diag, "M777", "record_adherence", sat.record_adherence, "s1", True, "positive")
        _timed(diag, "M777", "get_adherence_rate", sat.get_adherence_rate)
        _timed(diag, "M777", "get_per_type_stats", sat.get_per_type_stats)
        _timed(diag, "M777", "get_stats", sat.get_stats)
        modules_status["M777"] = "OK"
    except Exception as e:
        diag.log_error("M777", traceback.format_exc())
        modules_status["M777"] = f"FAIL: {e}"

    # === M778: PipelineLatencyProfiler ===
    logger.info("=== Diagnosing M778: PipelineLatencyProfiler ===")
    try:
        from lol_history.pipeline_latency_profiler import PipelineLatencyProfiler
        plp = PipelineLatencyProfiler()
        _timed(diag, "M778", "init", lambda: plp)
        _timed(diag, "M778", "start_span", plp.start_span, "data_fetch")
        time.sleep(0.01)
        _timed(diag, "M778", "end_span", plp.end_span, "data_fetch")
        _timed(diag, "M778", "get_latency_report", plp.get_latency_report)
        _timed(diag, "M778", "check_sla", plp.check_sla, {"data_fetch": 100.0})
        _timed(diag, "M778", "get_stats", plp.get_stats)
        modules_status["M778"] = "OK"
    except Exception as e:
        diag.log_error("M778", traceback.format_exc())
        modules_status["M778"] = f"FAIL: {e}"

    # === M779: TrainingDataQualityValidator ===
    logger.info("=== Diagnosing M779: TrainingDataQualityValidator ===")
    try:
        from lol_history.training_data_quality_validator import TrainingDataQualityValidator
        tdqv = TrainingDataQualityValidator()
        _timed(diag, "M779", "init", lambda: tdqv)
        sample = {"game_id": "g1", "champion": "Yasuo", "kills": 5, "deaths": 2,
                  "assists": 7, "win": True, "duration": 1800, "gold": 12000}
        _timed(diag, "M779", "validate_record", tdqv.validate_record, sample)
        _timed(diag, "M779", "validate_batch", tdqv.validate_batch, [sample, sample])
        _timed(diag, "M779", "get_rejection_reasons", tdqv.get_rejection_reasons)
        _timed(diag, "M779", "get_stats", tdqv.get_stats)
        modules_status["M779"] = "OK"
    except Exception as e:
        diag.log_error("M779", traceback.format_exc())
        modules_status["M779"] = f"FAIL: {e}"

    # === M780: CrossGameIntelTransferAdapter ===
    logger.info("=== Diagnosing M780: CrossGameIntelTransferAdapter ===")
    try:
        from lol_history.cross_game_intel_transfer_adapter import CrossGameIntelTransferAdapter
        cgita = CrossGameIntelTransferAdapter()
        _timed(diag, "M780", "init", lambda: cgita)
        _timed(diag, "M780", "register_game", cgita.register_game, "lol",
               {"opponent_profile": True, "prediction": True, "suggestion": True, "feedback": True})
        _timed(diag, "M780", "transfer_pattern", cgita.transfer_pattern, "lol", "dota2", "opponent_profile")
        _timed(diag, "M780", "get_compatible_games", cgita.get_compatible_games)
        _timed(diag, "M780", "get_stats", cgita.get_stats)
        modules_status["M780"] = "OK"
    except Exception as e:
        diag.log_error("M780", traceback.format_exc())
        modules_status["M780"] = f"FAIL: {e}"

    # === M781: ObservabilityMetricsExporter ===
    logger.info("=== Diagnosing M781: ObservabilityMetricsExporter ===")
    try:
        from lol_history.observability_metrics_exporter import ObservabilityMetricsExporter
        ome = ObservabilityMetricsExporter()
        _timed(diag, "M781", "init", lambda: ome)
        _timed(diag, "M781", "record_counter", ome.record_counter, "requests_total", 1,
               {"module": "M766", "phase": "ingame"})
        _timed(diag, "M781", "record_histogram", ome.record_histogram, "latency_ms", 42.5,
               {"module": "M766"})
        _timed(diag, "M781", "export_prometheus", ome.export_prometheus)
        _timed(diag, "M781", "export_opentelemetry", ome.export_opentelemetry)
        _timed(diag, "M781", "get_stats", ome.get_stats)
        modules_status["M781"] = "OK"
    except Exception as e:
        diag.log_error("M781", traceback.format_exc())
        modules_status["M781"] = f"FAIL: {e}"

    # === M782: UserPreferenceLearner ===
    logger.info("=== Diagnosing M782: UserPreferenceLearner ===")
    try:
        from lol_history.user_preference_learner import UserPreferenceLearner
        upl = UserPreferenceLearner()
        _timed(diag, "M782", "init", lambda: upl)
        for s in MOCK_SUGGESTIONS:
            _timed(diag, "M782", f"observe_{s['id']}", upl.observe_decision,
                   s["type"], s["priority"], s["adhered"], s.get("outcome", "neutral"), "ingame")
        _timed(diag, "M782", "get_preference_profile", upl.get_preference_profile)
        _timed(diag, "M782", "adjust_suggestion_priority", upl.adjust_suggestion_priority,
               {"type": "macro", "priority": "high"})
        _timed(diag, "M782", "get_stats", upl.get_stats)
        modules_status["M782"] = "OK"
    except Exception as e:
        diag.log_error("M782", traceback.format_exc())
        modules_status["M782"] = f"FAIL: {e}"

    # === M783: ResilienceCircuitBreaker ===
    logger.info("=== Diagnosing M783: ResilienceCircuitBreaker ===")
    try:
        from lol_history.resilience_circuit_breaker import ResilienceCircuitBreaker
        rcb = ResilienceCircuitBreaker()
        _timed(diag, "M783", "init", lambda: rcb)
        _timed(diag, "M783", "register_dependency", rcb.register_dependency, "riot_api",
               {"failure_threshold": 5, "recovery_timeout": 30.0})
        _timed(diag, "M783", "record_success", rcb.record_success, "riot_api")
        _timed(diag, "M783", "record_failure", rcb.record_failure, "riot_api")
        _timed(diag, "M783", "is_available", rcb.is_available, "riot_api")
        _timed(diag, "M783", "get_all_states", rcb.get_all_states)
        _timed(diag, "M783", "get_stats", rcb.get_stats)
        modules_status["M783"] = "OK"
    except Exception as e:
        diag.log_error("M783", traceback.format_exc())
        modules_status["M783"] = f"FAIL: {e}"

    # === M784: SessionReplayExporter ===
    logger.info("=== Diagnosing M784: SessionReplayExporter ===")
    try:
        from lol_history.session_replay_exporter import SessionReplayExporter
        sre = SessionReplayExporter()
        _timed(diag, "M784", "init", lambda: sre)
        _timed(diag, "M784", "start_session", sre.start_session, "game_123")
        _timed(diag, "M784", "record_decision", sre.record_decision,
               300.0, {"type": "macro", "text": "推线"}, {"action": "push_lane"}, "positive")
        _timed(diag, "M784", "end_session", sre.end_session, "game_123", True)
        _timed(diag, "M784", "export_timeline", sre.export_timeline, "game_123")
        _timed(diag, "M784", "get_stats", sre.get_stats)
        modules_status["M784"] = "OK"
    except Exception as e:
        diag.log_error("M784", traceback.format_exc())
        modules_status["M784"] = f"FAIL: {e}"

    # === M785: E2eRealtimeAssistPipelineOrchestrator ===
    logger.info("=== Diagnosing M785: E2eRealtimeAssistPipelineOrchestrator ===")
    try:
        from lol_history.e2e_realtime_assist_pipeline_orchestrator import E2eRealtimeAssistPipelineOrchestrator
        eraop = E2eRealtimeAssistPipelineOrchestrator()
        _timed(diag, "M785", "init", lambda: eraop)
        _timed(diag, "M785", "register_module", eraop.register_module,
               "poller", type("P", (), {"get_stats": lambda s: {}})(), "data_collection")
        _timed(diag, "M785", "initialize_pipeline", eraop.initialize_pipeline)
        _timed(diag, "M785", "get_pipeline_health", eraop.get_pipeline_health)
        _timed(diag, "M785", "get_sla_status", eraop.get_sla_status)
        _timed(diag, "M785", "get_stats", eraop.get_stats)
        modules_status["M785"] = "OK"
    except Exception as e:
        diag.log_error("M785", traceback.format_exc())
        modules_status["M785"] = f"FAIL: {e}"

    # === Final Report ===
    logger.info("=" * 80)
    logger.info("DIAGNOSTIC SUMMARY")
    logger.info("=" * 80)
    for mod, status in sorted(modules_status.items()):
        logger.info(f"  {mod}: {status}")

    ok_count = sum(1 for s in modules_status.values() if s == "OK")
    logger.info(f"\n  TOTAL: {ok_count}/{len(modules_status)} modules OK")

    # Dump full diagnostics
    diag.dump_json("/home/claude/operatorRL/m766_m785_diagnostics.json")
    logger.info("Full diagnostics written to m766_m785_diagnostics.json")

    return diag, modules_status


if __name__ == "__main__":
    run_diagnostics()

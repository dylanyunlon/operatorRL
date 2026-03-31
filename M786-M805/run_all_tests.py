#!/usr/bin/env python3
"""Run all M786-M805 module self-tests."""
import sys, importlib, traceback
from pathlib import Path

MODULES = {
    "M786": "logging_system.core_logger",
    "M787": "historical_battle_data.historical_battle_data",
    "M788": "lcu_connector.lcu_connector",
    "M789": "match_analyzer.match_analyzer",
    "M790": "player_profiler.player_profiler",
    "M791": "champion_stats.champion_stats",
    "M792": "team_composition.team_composition",
    "M793": "win_prediction.win_prediction",
    "M794": "data_pipeline.data_pipeline",
    "M795": "network_capture.network_capture",
    "M796": "fiddler_integration.fiddler_integration",
    "M797": "proxy_config.proxy_config",
    "M798": "realtime_dashboard.realtime_dashboard",
    "M799": "feedback_engine.feedback_engine",
    "M800": "voice_output.voice_output",
    "M801": "game_state_tracker.game_state_tracker",
    "M802": "strategy_advisor.strategy_advisor",
    "M803": "replay_parser.replay_parser",
    "M804": "performance_metrics.performance_metrics",
    "M805": "plan_update.plan_update",
}

sys.path.insert(0, str(Path(__file__).parent))
passed = failed = 0
for mid, mod_path in MODULES.items():
    try:
        mod = importlib.import_module(mod_path)
        if hasattr(mod, '_self_test'):
            if mod._self_test():
                passed += 1
            else:
                failed += 1
    except Exception as e:
        print(f"[{mid}] ERROR: {e}")
        failed += 1
print(f"\nResults: {passed} passed, {failed} failed, {len(MODULES)} total")

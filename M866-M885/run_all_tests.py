#!/usr/bin/env python3
"""Run basic import tests for all M866-M885 modules."""
import importlib, sys, os
sys.path.insert(0, os.path.dirname(__file__))
modules = [
    "fiddler_traffic_interceptor", "lcu_websocket_bridge", "match_history_aggregator",
    "champion_meta_tracker", "player_behavior_predictor", "draft_phase_analyzer",
    "lane_matchup_predictor", "objective_timing_engine", "teamfight_outcome_predictor",
    "win_probability_model", "item_build_path_optimizer", "rune_page_recommender",
    "proxifier_rule_engine", "network_packet_classifier", "replay_analysis_engine",
    "strategy_feedback_loop", "voice_coach_narrator", "performance_heatmap_generator",
    "cross_game_intel_fusion", "system_health_dashboard",
]
passed = failed = 0
for m in modules:
    try:
        importlib.import_module(m)
        passed += 1
    except Exception as e:
        print(f"FAIL: {m}: {e}")
        failed += 1
print(f"\n{passed}/{passed+failed} modules imported OK")
sys.exit(1 if failed else 0)

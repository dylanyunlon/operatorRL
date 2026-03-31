#!/usr/bin/env python3
"""
M846-M865 Master Test Runner & Log Generator
=============================================
Runs self-tests on all modules, collects logs, and reports results.
"""

import json
import logging
import os
import sys
import time
import importlib.util
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# Configure comprehensive logging
log_file = LOG_DIR / f"test_run_{int(time.time())}.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("M846-M865-TestRunner")

MODULES = [
    ("M846", "logging_orchestrator", "logging_orchestrator"),
    ("M847", "historical_match_crawler", "historical_match_crawler"),
    ("M848", "summoner_deep_profiler", "summoner_deep_profiler"),
    ("M849", "match_timeline_reconstructor", "match_timeline_reconstructor"),
    ("M850", "champion_mastery_analyzer", "champion_mastery_analyzer"),
    ("M851", "team_comp_historical_evaluator", "team_comp_historical_evaluator"),
    ("M852", "opponent_scouting_engine", "opponent_scouting_engine"),
    ("M853", "ranked_progression_tracker", "ranked_progression_tracker"),
    ("M854", "game_flow_session_monitor", "game_flow_session_monitor"),
    ("M855", "rune_item_build_optimizer", "rune_item_build_optimizer"),
    ("M856", "ban_pick_suggestion_engine", "ban_pick_suggestion_engine"),
    ("M857", "vision_score_analyzer", "vision_score_analyzer"),
    ("M858", "objective_control_predictor", "objective_control_predictor"),
    ("M859", "network_protocol_decoder", "network_protocol_decoder"),
    ("M860", "cross_match_pattern_miner", "cross_match_pattern_miner"),
    ("M861", "realtime_strategy_recommender", "realtime_strategy_recommender"),
    ("M862", "voice_alert_system_tts", "voice_alert_system_tts"),
    ("M863", "performance_regression_detector", "performance_regression_detector"),
    ("M864", "dashboard_data_aggregation_api", "dashboard_data_aggregation_api"),
    ("M865", "plan_update_project_integrator", "plan_update_project_integrator"),
]


def load_module(mid, dir_name, file_name):
    """Dynamically load a module and return its self-test function."""
    module_path = BASE_DIR / dir_name / f"{file_name}.py"
    if not module_path.exists():
        raise FileNotFoundError(f"Module file not found: {module_path}")

    mod_name = f"m846m865.{file_name}"
    spec = importlib.util.spec_from_file_location(mod_name, module_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def main():
    logger.info("=" * 70)
    logger.info("OperatorRL M846-M865 Master Test Runner")
    logger.info(f"Log file: {log_file}")
    logger.info("=" * 70)

    all_results = {
        "run_timestamp": time.time(),
        "total_passed": 0,
        "total_failed": 0,
        "module_results": [],
    }

    for mid, dir_name, file_name in MODULES:
        logger.info(f"\n{'='*40}")
        logger.info(f"Testing {mid}: {dir_name}")
        logger.info(f"{'='*40}")

        try:
            mod = load_module(mid, dir_name, file_name)
            if hasattr(mod, "run_self_test"):
                result = mod.run_self_test()
                all_results["total_passed"] += result["passed"]
                all_results["total_failed"] += result["failed"]
                all_results["module_results"].append(result)

                for t in result["tests"]:
                    status = "✓" if t["status"] == "PASS" else "✗"
                    logger.info(f"  {status} {t['name']}")
                    if t.get("error"):
                        logger.error(f"    Error: {t['error']}")

                logger.info(f"  → {result['passed']} passed, {result['failed']} failed")
            else:
                logger.warning(f"  No run_self_test() found in {mid}")
        except Exception as exc:
            logger.error(f"  LOAD ERROR: {exc}")
            all_results["total_failed"] += 1
            all_results["module_results"].append({
                "module": mid,
                "error": str(exc),
                "passed": 0,
                "failed": 1,
            })

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info(f"Total Passed: {all_results['total_passed']}")
    logger.info(f"Total Failed: {all_results['total_failed']}")
    logger.info(f"Log written to: {log_file}")

    # Write JSON results
    results_path = LOG_DIR / "test_results.json"
    with open(results_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"Results written to: {results_path}")

    return all_results


if __name__ == "__main__":
    results = main()
    sys.exit(0 if results["total_failed"] == 0 else 1)

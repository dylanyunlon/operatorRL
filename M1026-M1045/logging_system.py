#!/usr/bin/env python3
"""
M1026-M1045 Logging System
===========================
生成日志 → 诊断报告 → 驱动模块改进循环

Author: dylanyunlong <dylanyunlong@gmail.com>
"""

import json
import logging
import os
import sys
import time
import importlib
import traceback
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "M1026-M1045.log"
DIAG_FILE = LOG_DIR / "M1026-M1045_diagnostic_report.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="w", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("M1026-M1045.LoggingSystem")

MODULE_REGISTRY = [
    ("M1026", "match_history_deep_fetcher", "MatchHistoryDeepFetcher"),
    ("M1027", "summoner_profile_aggregator", "SummonerProfileAggregator"),
    ("M1028", "champion_mastery_analyzer", "ChampionMasteryAnalyzer"),
    ("M1029", "ranked_stats_tracker", "RankedStatsTracker"),
    ("M1030", "match_timeline_parser", "MatchTimelineParser"),
    ("M1031", "player_behavior_profiler", "PlayerBehaviorProfiler"),
    ("M1032", "team_history_correlator", "TeamHistoryCorrelator"),
    ("M1033", "opponent_pattern_miner", "OpponentPatternMiner"),
    ("M1034", "win_streak_momentum_engine", "WinStreakMomentumEngine"),
    ("M1035", "role_performance_decomposer", "RolePerformanceDecomposer"),
    ("M1036", "item_build_history_analyzer", "ItemBuildHistoryAnalyzer"),
    ("M1037", "death_heatmap_generator", "DeathHeatmapGenerator"),
    ("M1038", "cs_efficiency_tracker", "CsEfficiencyTracker"),
    ("M1039", "vision_score_history_engine", "VisionScoreHistoryEngine"),
    ("M1040", "duo_partner_detector", "DuoPartnerDetector"),
    ("M1041", "tilt_detection_engine", "TiltDetectionEngine"),
    ("M1042", "meta_compliance_scorer", "MetaComplianceScorer"),
    ("M1043", "historical_matchup_matrix", "HistoricalMatchupMatrix"),
    ("M1044", "pregame_intelligence_fuser", "PregameIntelligenceFuser"),
    ("M1045", "historical_intelligence_gateway", "HistoricalIntelligenceGateway"),
]


def run_diagnostics():
    """运行全部模块的语法/导入/实例化/方法签名检测"""
    results = {}
    total_lines = 0
    success_count = 0
    fail_count = 0

    for mid, pkg, cls_name in MODULE_REGISTRY:
        mod_path = Path(__file__).parent / pkg / f"{pkg}.py"
        entry = {
            "module_id": mid,
            "package": pkg,
            "class": cls_name,
            "file_exists": mod_path.exists(),
            "line_count": 0,
            "syntax_ok": False,
            "import_ok": False,
            "instantiate_ok": False,
            "methods": [],
            "errors": [],
        }

        if not mod_path.exists():
            entry["errors"].append(f"File not found: {mod_path}")
            fail_count += 1
            results[mid] = entry
            continue

        # Line count
        with open(mod_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        entry["line_count"] = len(lines)
        total_lines += len(lines)

        # Syntax check
        try:
            compile(open(mod_path, "r", encoding="utf-8").read(), str(mod_path), "exec")
            entry["syntax_ok"] = True
        except SyntaxError as e:
            entry["errors"].append(f"SyntaxError: {e}")
            fail_count += 1
            results[mid] = entry
            continue

        # Import check
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            mod = importlib.import_module(f"{pkg}.{pkg}")
            entry["import_ok"] = True
        except Exception as e:
            entry["errors"].append(f"ImportError: {e}")
            fail_count += 1
            results[mid] = entry
            continue

        # Instantiate check
        try:
            klass = getattr(mod, cls_name)
            instance = klass()
            entry["instantiate_ok"] = True
            entry["methods"] = [
                m for m in dir(instance)
                if not m.startswith("_") and callable(getattr(instance, m))
            ]
            success_count += 1
        except Exception as e:
            entry["errors"].append(f"InstantiateError: {e}")
            fail_count += 1

        results[mid] = entry
        logger.info(f"{mid} {cls_name}: syntax={'OK' if entry['syntax_ok'] else 'FAIL'} "
                     f"import={'OK' if entry['import_ok'] else 'FAIL'} "
                     f"init={'OK' if entry['instantiate_ok'] else 'FAIL'} "
                     f"lines={entry['line_count']}")

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "milestone": "M1026-M1045",
        "instance": "#38",
        "total_modules": len(MODULE_REGISTRY),
        "success": success_count,
        "fail": fail_count,
        "total_lines": total_lines,
        "avg_lines": total_lines // max(len(MODULE_REGISTRY), 1),
        "modules": results,
    }

    with open(DIAG_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(f"=== Diagnostic Complete: {success_count}/{len(MODULE_REGISTRY)} OK, "
                f"{total_lines} total lines, report → {DIAG_FILE} ===")
    return report


if __name__ == "__main__":
    run_diagnostics()

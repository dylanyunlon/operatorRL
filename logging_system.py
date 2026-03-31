#!/usr/bin/env python3
"""
OperatorRL - Historical Battle System Logging & Diagnostics
============================================================
M806-M825 Task Logging System
Generates diagnostic logs for each module, validates interfaces,
checks dependency chains, and outputs improvement recommendations.
"""

import os
import sys
import json
import time
import hashlib
import inspect
import importlib
import traceback
import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict

# ─── Configuration ───────────────────────────────────────────────────────────

LOG_DIR = Path(__file__).parent / "logs"
SRC_DIR = Path(__file__).parent / "src" / "modules" / "historical_battle"
CONFIG_DIR = Path(__file__).parent / "config"
LOG_DIR.mkdir(parents=True, exist_ok=True)

TASK_REGISTRY = {
    "M806": {"name": "historical_battle_core", "category": "core",
             "desc": "Historical Battle Data Core - data models, schemas, validation"},
    "M807": {"name": "lcu_api_client", "category": "core",
             "desc": "LCU API Client - League Client Update API integration"},
    "M808": {"name": "match_history_collector", "category": "core",
             "desc": "Match History Collector - batch/stream match data collection"},
    "M809": {"name": "player_profile_analyzer", "category": "analysis",
             "desc": "Player Profile Analyzer - multi-dimensional player profiling"},
    "M810": {"name": "champion_statistics_engine", "category": "analysis",
             "desc": "Champion Statistics Engine - win/pick/ban rates, tier analysis"},
    "M811": {"name": "network_capture_layer", "category": "network",
             "desc": "Network Capture Layer - Fiddler/mitmproxy integration"},
    "M812": {"name": "protocol_decoder", "category": "network",
             "desc": "Protocol Decoder - game protocol parsing, packet analysis"},
    "M813": {"name": "battle_timeline_reconstructor", "category": "analysis",
             "desc": "Battle Timeline Reconstructor - event sequence rebuilding"},
    "M814": {"name": "team_composition_analyzer", "category": "analysis",
             "desc": "Team Composition Analyzer - synergy/counter analysis"},
    "M815": {"name": "performance_metrics_calculator", "category": "analysis",
             "desc": "Performance Metrics Calculator - KDA, vision, CS, objectives"},
    "M816": {"name": "historical_pattern_recognition", "category": "analysis",
             "desc": "Historical Pattern Recognition - behavioral pattern detection"},
    "M817": {"name": "opponent_scouting_system", "category": "integration",
             "desc": "Opponent Scouting System - pre-game enemy analysis"},
    "M818": {"name": "realtime_data_bridge", "category": "integration",
             "desc": "Real-time Data Bridge - historical↔realtime data fusion"},
    "M819": {"name": "data_persistence_layer", "category": "persistence",
             "desc": "Data Persistence Layer - storage, caching, indexing"},
    "M820": {"name": "analytics_dashboard_backend", "category": "integration",
             "desc": "Analytics Dashboard Backend - REST API for analytics"},
    "M821": {"name": "replay_parser", "category": "core",
             "desc": "Replay Parser - .rofl replay file parsing and extraction"},
    "M822": {"name": "meta_analysis_engine", "category": "analysis",
             "desc": "Meta Analysis Engine - current meta trends, patch analysis"},
    "M823": {"name": "prediction_model_integration", "category": "integration",
             "desc": "Prediction Model Integration - win probability from history"},
    "M824": {"name": "report_generator", "category": "integration",
             "desc": "Report Generator - structured analysis report creation"},
    "M825": {"name": "system_orchestrator", "category": "orchestration",
             "desc": "System Orchestrator - module coordination and lifecycle"},
}


@dataclass
class ModuleLogEntry:
    """Structured log entry for module diagnostics."""
    task_id: str
    module_name: str
    timestamp: str
    status: str  # "initialized", "checked", "warning", "error", "improved"
    category: str
    line_count: int = 0
    class_count: int = 0
    function_count: int = 0
    interface_compliance: float = 0.0
    dependency_check: Dict[str, bool] = field(default_factory=dict)
    code_quality_notes: List[str] = field(default_factory=list)
    improvement_suggestions: List[str] = field(default_factory=list)
    error_details: Optional[str] = None


class DiagnosticLogger:
    """
    Central diagnostic logger for the Historical Battle module system.
    Scans each module file, validates structure, checks interfaces,
    and generates improvement logs.
    """

    def __init__(self):
        self.log_entries: List[ModuleLogEntry] = []
        self.start_time = datetime.datetime.now()
        self.session_id = hashlib.md5(
            str(self.start_time).encode()
        ).hexdigest()[:12]

    def scan_module_file(self, task_id: str, info: Dict) -> ModuleLogEntry:
        """Scan a single module file and produce diagnostics."""
        module_name = info["name"]
        category = info["category"]
        file_path = SRC_DIR / category / f"{module_name}.py"

        entry = ModuleLogEntry(
            task_id=task_id,
            module_name=module_name,
            timestamp=datetime.datetime.now().isoformat(),
            status="initialized",
            category=category,
        )

        if not file_path.exists():
            entry.status = "missing"
            entry.error_details = f"Module file not found: {file_path}"
            entry.improvement_suggestions.append(
                f"CREATE {file_path} with ≥500 lines implementing {info['desc']}"
            )
            self.log_entries.append(entry)
            return entry

        try:
            content = file_path.read_text(encoding="utf-8")
            lines = content.split("\n")
            entry.line_count = len(lines)

            # Count classes and functions
            entry.class_count = sum(1 for l in lines if l.strip().startswith("class "))
            entry.function_count = sum(
                1 for l in lines
                if l.strip().startswith("def ") or l.strip().startswith("async def ")
            )

            # Interface compliance checks
            checks = {
                "has_docstring": '"""' in content or "'''" in content,
                "has_type_hints": "->" in content or ": " in content,
                "has_error_handling": "try:" in content or "except" in content,
                "has_logging": "logger" in content.lower() or "logging" in content.lower(),
                "has_dataclass_or_model": "@dataclass" in content or "BaseModel" in content,
                "has_async_support": "async " in content or "await " in content,
                "has_tests_reference": "test" in content.lower(),
                "has_init_method": "__init__" in content,
                "meets_line_target": len(lines) >= 500,
                "has_constants": any(l.strip() and l.strip()[0].isupper() and "=" in l and l.split("=")[0].strip().isupper() for l in lines),
            }
            entry.dependency_check = checks
            passing = sum(1 for v in checks.values() if v)
            entry.interface_compliance = passing / len(checks)

            # Quality notes
            if entry.line_count < 500:
                entry.code_quality_notes.append(
                    f"Below 500-line target: {entry.line_count} lines (need {500 - entry.line_count} more)"
                )
            if entry.class_count < 3:
                entry.code_quality_notes.append(
                    f"Low class count: {entry.class_count} (recommend ≥3 for production)"
                )
            if not checks["has_error_handling"]:
                entry.code_quality_notes.append("Missing try/except error handling")
            if not checks["has_async_support"]:
                entry.code_quality_notes.append(
                    "No async support - consider adding for I/O bound operations"
                )

            # Improvement suggestions based on template pattern
            if not checks["has_logging"]:
                entry.improvement_suggestions.append(
                    "Add structured logging with module-specific logger"
                )
            if not checks["has_dataclass_or_model"]:
                entry.improvement_suggestions.append(
                    "Introduce dataclass/Pydantic models for data validation"
                )
            if entry.line_count < 500:
                entry.improvement_suggestions.append(
                    f"Expand implementation to ≥500 lines following Seraphine-style patterns"
                )

            entry.status = "checked"

        except Exception as e:
            entry.status = "error"
            entry.error_details = traceback.format_exc()

        self.log_entries.append(entry)
        return entry

    def run_full_scan(self) -> str:
        """Scan all registered modules and produce a full diagnostic report."""
        print(f"{'='*72}")
        print(f"  OperatorRL Historical Battle System - Diagnostic Scan")
        print(f"  Session: {self.session_id}")
        print(f"  Time: {self.start_time.isoformat()}")
        print(f"{'='*72}\n")

        for task_id in sorted(TASK_REGISTRY.keys()):
            info = TASK_REGISTRY[task_id]
            print(f"  [{task_id}] Scanning {info['name']}...", end=" ")
            entry = self.scan_module_file(task_id, info)
            status_icon = {
                "checked": "✓", "missing": "✗", "error": "⚠",
                "initialized": "○"
            }.get(entry.status, "?")
            print(f"{status_icon} ({entry.status}, {entry.line_count} lines)")

        # Write log file
        log_path = LOG_DIR / f"diagnostic_{self.session_id}.json"
        log_data = {
            "session_id": self.session_id,
            "timestamp": self.start_time.isoformat(),
            "total_modules": len(TASK_REGISTRY),
            "scanned": len(self.log_entries),
            "missing": sum(1 for e in self.log_entries if e.status == "missing"),
            "checked": sum(1 for e in self.log_entries if e.status == "checked"),
            "errors": sum(1 for e in self.log_entries if e.status == "error"),
            "total_lines": sum(e.line_count for e in self.log_entries),
            "avg_compliance": (
                sum(e.interface_compliance for e in self.log_entries) / len(self.log_entries)
                if self.log_entries else 0
            ),
            "entries": [asdict(e) for e in self.log_entries],
        }

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)

        # Print summary
        print(f"\n{'─'*72}")
        print(f"  Summary:")
        print(f"    Total modules: {log_data['total_modules']}")
        print(f"    Missing: {log_data['missing']}")
        print(f"    Checked: {log_data['checked']}")
        print(f"    Errors: {log_data['errors']}")
        print(f"    Total lines: {log_data['total_lines']}")
        print(f"    Avg compliance: {log_data['avg_compliance']:.1%}")
        print(f"    Log saved: {log_path}")
        print(f"{'─'*72}\n")

        # Generate improvement plan
        plan_path = LOG_DIR / f"improvement_plan_{self.session_id}.md"
        self._write_improvement_plan(plan_path)
        print(f"  Improvement plan: {plan_path}\n")

        return str(log_path)

    def _write_improvement_plan(self, path: Path):
        """Generate a markdown improvement plan from diagnostics."""
        lines = [
            "# M806-M825 Improvement Plan",
            f"\nGenerated: {self.start_time.isoformat()}",
            f"Session: {self.session_id}\n",
            "## Module Status Overview\n",
            "| Task | Module | Status | Lines | Compliance |",
            "|------|--------|--------|-------|------------|",
        ]
        for e in self.log_entries:
            lines.append(
                f"| {e.task_id} | {e.module_name} | {e.status} | "
                f"{e.line_count} | {e.interface_compliance:.0%} |"
            )

        lines.append("\n## Required Actions\n")
        for e in self.log_entries:
            if e.improvement_suggestions:
                lines.append(f"### {e.task_id}: {e.module_name}\n")
                for s in e.improvement_suggestions:
                    lines.append(f"- {s}")
                lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))


if __name__ == "__main__":
    logger = DiagnosticLogger()
    log_path = logger.run_full_scan()
    print(f"Diagnostics complete. Log: {log_path}")

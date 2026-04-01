#!/usr/bin/env python3
"""
M986-M1005 Logging System — Historical Battle Intelligence Acquisition
第三十六位 Claude (Instance #36)

Centralized logging for all 20 modules in the Historical Battle Intelligence layer.
Captures module generation, initialization, runtime diagnostics, and self-test results.
"""

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "M986-M1005.log"
DIAG_FILE = LOG_DIR / "M986-M1005_diagnostic_report.json"

# ─── Structured Logger ───────────────────────────────────────────────────────

class StructuredFormatter(logging.Formatter):
    """JSON-structured log lines for machine parsing + human readability."""

    def format(self, record):
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": getattr(record, "module_id", record.module),
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exception"] = traceback.format_exception(*record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def get_logger(module_id: str) -> logging.Logger:
    """Return a logger bound to a specific module ID (e.g., 'M986')."""
    logger = logging.getLogger(f"M986-M1005.{module_id}")
    if not logger.handlers:
        # File handler — structured JSON
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(StructuredFormatter())
        logger.addHandler(fh)
        # Console handler — human readable
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter(
            f"[%(asctime)s] [{module_id}] %(levelname)s: %(message)s",
            datefmt="%H:%M:%S"
        ))
        logger.addHandler(ch)
        logger.setLevel(logging.DEBUG)
    # Attach module_id for structured formatter
    old_factory = logging.getLogRecordFactory()
    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.module_id = module_id
        return record
    logging.setLogRecordFactory(record_factory)
    return logger


# ─── Diagnostic Collector ────────────────────────────────────────────────────

class DiagnosticCollector:
    """Collects generation and self-test diagnostics across all 20 modules."""

    def __init__(self):
        self.start_time = time.monotonic()
        self.modules = {}
        self.errors = []
        self.warnings = []

    def record_module(self, module_id: str, name: str, lines: int,
                      syntax_ok: bool, self_test_ok: bool, duration_ms: float):
        self.modules[module_id] = {
            "name": name,
            "lines": lines,
            "syntax_ok": syntax_ok,
            "self_test_ok": self_test_ok,
            "duration_ms": round(duration_ms, 2),
        }

    def record_error(self, module_id: str, error: str):
        self.errors.append({"module": module_id, "error": error})

    def record_warning(self, module_id: str, warning: str):
        self.warnings.append({"module": module_id, "warning": warning})

    def finalize(self) -> dict:
        elapsed = time.monotonic() - self.start_time
        total_lines = sum(m["lines"] for m in self.modules.values())
        report = {
            "instance": "#36",
            "milestone": "M986-M1005",
            "title": "Historical Battle Intelligence Acquisition for Live Matches",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "summary": {
                "total_modules": len(self.modules),
                "total_lines": total_lines,
                "avg_lines_per_module": round(total_lines / max(len(self.modules), 1)),
                "syntax_errors": sum(1 for m in self.modules.values() if not m["syntax_ok"]),
                "self_test_failures": sum(1 for m in self.modules.values() if not m["self_test_ok"]),
                "warnings": len(self.warnings),
                "errors": len(self.errors),
            },
            "modules": self.modules,
            "errors": self.errors,
            "warnings": self.warnings,
        }
        # Write diagnostic report
        with open(DIAG_FILE, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        return report


# ─── Quick self-test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    log = get_logger("logging_system")
    log.info("Logging system self-test: OK")
    dc = DiagnosticCollector()
    dc.record_module("TEST", "SelfTest", 100, True, True, 0.5)
    report = dc.finalize()
    log.info(f"Diagnostic report written: {DIAG_FILE}")
    print(json.dumps(report, indent=2))

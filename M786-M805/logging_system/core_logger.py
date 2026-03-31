#!/usr/bin/env python3
"""
OperatorRL M786-M805 Core Logging System
=========================================
Production-grade logging infrastructure for the Historical Battle Data
Integration subsystem. Based on Seraphine (Zzaphkiel/Seraphine) LCU API
patterns for LoL match history retrieval.

Architecture:
  - Structured JSON logging with rotation
  - Module-level isolation with correlation IDs
  - Performance timing decorators
  - Error classification and escalation
  - Log aggregation for cross-module analysis

References:
  - Seraphine LCU API connector patterns
  - operatorRL agentic feedback loop architecture
  - Fiddler MCP server network capture integration
"""

import os
import sys
import json
import time
import uuid
import logging
import hashlib
import traceback
import threading
import functools
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable, Union
from dataclasses import dataclass, field, asdict
from collections import defaultdict, deque
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler


# ============================================================================
# Constants & Configuration
# ============================================================================

LOG_BASE_DIR = Path(__file__).parent.parent / "logs"
LOG_BASE_DIR.mkdir(parents=True, exist_ok=True)

MAX_LOG_SIZE = 50 * 1024 * 1024  # 50MB per file
MAX_LOG_FILES = 10
LOG_FORMAT_VERSION = "2.0.0"
CORRELATION_HEADER = "X-OperatorRL-Correlation-ID"

MODULE_REGISTRY = {
    "M786": "logging_system",
    "M787": "historical_battle_data",
    "M788": "lcu_connector",
    "M789": "match_analyzer",
    "M790": "player_profiler",
    "M791": "champion_stats",
    "M792": "team_composition",
    "M793": "win_prediction",
    "M794": "data_pipeline",
    "M795": "network_capture",
    "M796": "fiddler_integration",
    "M797": "proxy_config",
    "M798": "realtime_dashboard",
    "M799": "feedback_engine",
    "M800": "voice_output",
    "M801": "game_state_tracker",
    "M802": "strategy_advisor",
    "M803": "replay_parser",
    "M804": "performance_metrics",
    "M805": "plan_update",
}


class LogLevel(Enum):
    TRACE = 5
    DEBUG = 10
    INFO = 20
    WARN = 30
    ERROR = 40
    CRITICAL = 50
    FATAL = 60


class EventCategory(Enum):
    SYSTEM = "system"
    LCU_API = "lcu_api"
    NETWORK = "network"
    MATCH_DATA = "match_data"
    PLAYER_DATA = "player_data"
    ANALYSIS = "analysis"
    PREDICTION = "prediction"
    FEEDBACK = "feedback"
    PERFORMANCE = "performance"
    INTEGRATION = "integration"


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class LogEntry:
    """Structured log entry with full context tracking."""
    timestamp: str
    level: str
    module_id: str
    module_name: str
    correlation_id: str
    event_category: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None
    source_file: Optional[str] = None
    source_line: Optional[int] = None
    thread_name: Optional[str] = None
    format_version: str = LOG_FORMAT_VERSION

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result = {k: v for k, v in result.items() if v is not None}
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


@dataclass
class ModuleHealthReport:
    """Health status for a specific module."""
    module_id: str
    module_name: str
    status: str  # healthy, degraded, error, offline
    last_heartbeat: str
    error_count: int = 0
    warning_count: int = 0
    total_events: int = 0
    avg_response_ms: float = 0.0
    uptime_seconds: float = 0.0
    memory_usage_mb: float = 0.0
    active_connections: int = 0
    last_error: Optional[str] = None


@dataclass
class PerformanceSnapshot:
    """Performance metrics for timing analysis."""
    operation: str
    module_id: str
    start_time: float
    end_time: float = 0.0
    duration_ms: float = 0.0
    success: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def complete(self, success: bool = True) -> 'PerformanceSnapshot':
        self.end_time = time.monotonic()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        self.success = success
        return self


# ============================================================================
# JSON Formatter for Structured Logging
# ============================================================================

class StructuredJsonFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON log lines."""

    def __init__(self, module_id: str = "SYSTEM", module_name: str = "core"):
        super().__init__()
        self.module_id = module_id
        self.module_name = module_name
        self._hostname = os.environ.get("HOSTNAME", "localhost")

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module_id": getattr(record, 'module_id', self.module_id),
            "module_name": getattr(record, 'module_name', self.module_name),
            "correlation_id": getattr(record, 'correlation_id', ''),
            "event_category": getattr(record, 'event_category', 'system'),
            "message": record.getMessage(),
            "source_file": record.pathname,
            "source_line": record.lineno,
            "thread_name": record.threadName,
            "hostname": self._hostname,
            "format_version": LOG_FORMAT_VERSION,
        }

        if hasattr(record, 'data') and record.data:
            entry["data"] = record.data

        if record.exc_info and record.exc_info[1]:
            entry["error"] = {
                "type": type(record.exc_info[1]).__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        if hasattr(record, 'duration_ms'):
            entry["duration_ms"] = record.duration_ms

        return json.dumps(entry, ensure_ascii=False, default=str)


# ============================================================================
# Log Aggregator - Cross-Module Analysis
# ============================================================================

class LogAggregator:
    """
    Aggregates logs across all M786-M805 modules for cross-cutting analysis.
    Maintains rolling windows for error rate calculation, latency percentiles,
    and module dependency health tracking.
    """

    def __init__(self, window_size: int = 1000):
        self._lock = threading.Lock()
        self._window_size = window_size
        self._entries: deque = deque(maxlen=window_size)
        self._error_counts: Dict[str, int] = defaultdict(int)
        self._module_latencies: Dict[str, List[float]] = defaultdict(list)
        self._event_category_counts: Dict[str, int] = defaultdict(int)
        self._module_health: Dict[str, ModuleHealthReport] = {}
        self._start_time = time.monotonic()

    def ingest(self, entry: LogEntry) -> None:
        with self._lock:
            self._entries.append(entry)
            self._event_category_counts[entry.event_category] += 1

            if entry.level in ("ERROR", "CRITICAL", "FATAL"):
                self._error_counts[entry.module_id] += 1

            if entry.duration_ms is not None:
                self._module_latencies[entry.module_id].append(entry.duration_ms)
                if len(self._module_latencies[entry.module_id]) > self._window_size:
                    self._module_latencies[entry.module_id] = \
                        self._module_latencies[entry.module_id][-self._window_size:]

    def get_error_rate(self, module_id: str) -> float:
        with self._lock:
            total = sum(1 for e in self._entries if e.module_id == module_id)
            errors = self._error_counts.get(module_id, 0)
            return (errors / total * 100) if total > 0 else 0.0

    def get_latency_percentile(self, module_id: str, percentile: float = 95.0) -> float:
        with self._lock:
            latencies = self._module_latencies.get(module_id, [])
            if not latencies:
                return 0.0
            sorted_lat = sorted(latencies)
            idx = int(len(sorted_lat) * percentile / 100)
            idx = min(idx, len(sorted_lat) - 1)
            return sorted_lat[idx]

    def get_module_health(self, module_id: str) -> Dict[str, Any]:
        with self._lock:
            module_name = MODULE_REGISTRY.get(module_id, "unknown")
            entries = [e for e in self._entries if e.module_id == module_id]
            error_count = self._error_counts.get(module_id, 0)
            latencies = self._module_latencies.get(module_id, [])
            avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

            if error_count > 10:
                status = "error"
            elif error_count > 3:
                status = "degraded"
            elif len(entries) == 0:
                status = "offline"
            else:
                status = "healthy"

            return {
                "module_id": module_id,
                "module_name": module_name,
                "status": status,
                "total_events": len(entries),
                "error_count": error_count,
                "avg_latency_ms": round(avg_latency, 2),
                "p95_latency_ms": round(self.get_latency_percentile(module_id, 95), 2),
                "p99_latency_ms": round(self.get_latency_percentile(module_id, 99), 2),
                "uptime_seconds": round(time.monotonic() - self._start_time, 2),
            }

    def get_system_overview(self) -> Dict[str, Any]:
        overview = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_entries": len(self._entries),
            "modules": {},
            "event_categories": dict(self._event_category_counts),
            "system_uptime_seconds": round(time.monotonic() - self._start_time, 2),
        }
        for mid in MODULE_REGISTRY:
            overview["modules"][mid] = self.get_module_health(mid)
        return overview


# ============================================================================
# Module Logger - Per-Module Logging Interface
# ============================================================================

class ModuleLogger:
    """
    Per-module logger with correlation tracking, timing decorators,
    and structured event emission. Each M786-M805 module instantiates
    one ModuleLogger.
    
    Usage:
        logger = ModuleLogger("M787", "historical_battle_data")
        logger.info("Fetching match history", category=EventCategory.MATCH_DATA,
                     data={"summoner": "Player1", "count": 20})
    """

    def __init__(self, module_id: str, module_name: str,
                 aggregator: Optional[LogAggregator] = None):
        self.module_id = module_id
        self.module_name = module_name
        self._aggregator = aggregator
        self._correlation_id = str(uuid.uuid4())[:12]

        self._logger = logging.getLogger(f"operatorRL.{module_id}")
        self._logger.setLevel(logging.DEBUG)

        if not self._logger.handlers:
            log_file = LOG_BASE_DIR / f"{module_id}_{module_name}.log"
            handler = RotatingFileHandler(
                str(log_file),
                maxBytes=MAX_LOG_SIZE,
                backupCount=MAX_LOG_FILES,
                encoding='utf-8'
            )
            handler.setFormatter(StructuredJsonFormatter(module_id, module_name))
            self._logger.addHandler(handler)

            console = logging.StreamHandler(sys.stdout)
            console.setLevel(logging.INFO)
            console.setFormatter(StructuredJsonFormatter(module_id, module_name))
            self._logger.addHandler(console)

        self._perf_snapshots: List[PerformanceSnapshot] = []

    def _emit(self, level: int, message: str,
              category: EventCategory = EventCategory.SYSTEM,
              data: Optional[Dict] = None,
              error: Optional[Exception] = None,
              duration_ms: Optional[float] = None) -> LogEntry:
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            level=logging.getLevelName(level),
            module_id=self.module_id,
            module_name=self.module_name,
            correlation_id=self._correlation_id,
            event_category=category.value,
            message=message,
            data=data or {},
            duration_ms=duration_ms,
            thread_name=threading.current_thread().name,
        )

        if error:
            entry.error = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exc(),
            }

        record = logging.LogRecord(
            name=self._logger.name,
            level=level,
            pathname=__file__,
            lineno=0,
            msg=message,
            args=(),
            exc_info=None
        )
        record.module_id = self.module_id
        record.module_name = self.module_name
        record.correlation_id = self._correlation_id
        record.event_category = category.value
        record.data = data
        if duration_ms:
            record.duration_ms = duration_ms

        self._logger.handle(record)

        if self._aggregator:
            self._aggregator.ingest(entry)

        return entry

    def trace(self, message: str, **kwargs) -> LogEntry:
        return self._emit(5, message, **kwargs)

    def debug(self, message: str, **kwargs) -> LogEntry:
        return self._emit(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs) -> LogEntry:
        return self._emit(logging.INFO, message, **kwargs)

    def warn(self, message: str, **kwargs) -> LogEntry:
        return self._emit(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs) -> LogEntry:
        return self._emit(logging.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs) -> LogEntry:
        return self._emit(logging.CRITICAL, message, **kwargs)

    def start_timer(self, operation: str) -> PerformanceSnapshot:
        snap = PerformanceSnapshot(
            operation=operation,
            module_id=self.module_id,
            start_time=time.monotonic()
        )
        self._perf_snapshots.append(snap)
        return snap

    def stop_timer(self, snap: PerformanceSnapshot, success: bool = True,
                   log_result: bool = True) -> PerformanceSnapshot:
        snap.complete(success)
        if log_result:
            level = logging.INFO if success else logging.ERROR
            self._emit(
                level,
                f"Operation '{snap.operation}' completed in {snap.duration_ms:.2f}ms",
                category=EventCategory.PERFORMANCE,
                data={"operation": snap.operation, "success": success},
                duration_ms=snap.duration_ms
            )
        return snap

    def timed(self, operation: str = None,
              category: EventCategory = EventCategory.PERFORMANCE):
        """Decorator for timing function execution."""
        def decorator(func: Callable) -> Callable:
            op_name = operation or func.__qualname__

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                snap = self.start_timer(op_name)
                try:
                    result = func(*args, **kwargs)
                    self.stop_timer(snap, success=True)
                    return result
                except Exception as e:
                    self.stop_timer(snap, success=False)
                    self.error(
                        f"Operation '{op_name}' failed: {e}",
                        category=category,
                        error=e
                    )
                    raise
            return wrapper
        return decorator

    def new_correlation(self) -> str:
        self._correlation_id = str(uuid.uuid4())[:12]
        return self._correlation_id

    def set_correlation(self, correlation_id: str) -> None:
        self._correlation_id = correlation_id

    def get_performance_summary(self) -> Dict[str, Any]:
        if not self._perf_snapshots:
            return {"operations": 0}

        completed = [s for s in self._perf_snapshots if s.end_time > 0]
        if not completed:
            return {"operations": 0, "pending": len(self._perf_snapshots)}

        durations = [s.duration_ms for s in completed]
        successes = sum(1 for s in completed if s.success)

        return {
            "operations": len(completed),
            "success_rate": round(successes / len(completed) * 100, 2),
            "avg_duration_ms": round(sum(durations) / len(durations), 2),
            "min_duration_ms": round(min(durations), 2),
            "max_duration_ms": round(max(durations), 2),
            "total_duration_ms": round(sum(durations), 2),
        }


# ============================================================================
# Global Logger Factory
# ============================================================================

_global_aggregator = LogAggregator()
_module_loggers: Dict[str, ModuleLogger] = {}


def get_logger(module_id: str) -> ModuleLogger:
    """Factory function to get or create a ModuleLogger for a given module ID."""
    if module_id not in _module_loggers:
        module_name = MODULE_REGISTRY.get(module_id, "unknown")
        _module_loggers[module_id] = ModuleLogger(
            module_id, module_name, _global_aggregator
        )
    return _module_loggers[module_id]


def get_aggregator() -> LogAggregator:
    """Get the global log aggregator."""
    return _global_aggregator


def get_system_health() -> Dict[str, Any]:
    """Get health overview for all registered modules."""
    return _global_aggregator.get_system_overview()


# ============================================================================
# Log Replay & Analysis
# ============================================================================

class LogReplayEngine:
    """
    Replays structured log files for post-hoc analysis.
    Supports filtering by module, time range, error level, and category.
    """

    def __init__(self, log_dir: Path = LOG_BASE_DIR):
        self.log_dir = log_dir

    def load_logs(self, module_id: Optional[str] = None,
                  level_filter: Optional[str] = None,
                  category_filter: Optional[str] = None,
                  limit: int = 10000) -> List[Dict[str, Any]]:
        entries = []
        pattern = f"{module_id}_*.log" if module_id else "*.log"

        for log_file in sorted(self.log_dir.glob(pattern)):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if level_filter and entry.get("level") != level_filter:
                            continue
                        if category_filter and entry.get("event_category") != category_filter:
                            continue

                        entries.append(entry)
                        if len(entries) >= limit:
                            return entries
            except (IOError, OSError):
                continue

        return entries

    def get_error_timeline(self, module_id: Optional[str] = None) -> List[Dict]:
        errors = self.load_logs(
            module_id=module_id,
            level_filter="ERROR",
            limit=500
        )
        return [
            {
                "timestamp": e.get("timestamp"),
                "module": e.get("module_id"),
                "message": e.get("message"),
                "error_type": e.get("error", {}).get("type") if e.get("error") else None,
            }
            for e in errors
        ]

    def compute_statistics(self, module_id: Optional[str] = None) -> Dict[str, Any]:
        entries = self.load_logs(module_id=module_id)
        if not entries:
            return {"total": 0}

        level_counts = defaultdict(int)
        category_counts = defaultdict(int)
        durations = []

        for e in entries:
            level_counts[e.get("level", "UNKNOWN")] += 1
            category_counts[e.get("event_category", "unknown")] += 1
            if "duration_ms" in e and e["duration_ms"] is not None:
                durations.append(e["duration_ms"])

        stats = {
            "total": len(entries),
            "by_level": dict(level_counts),
            "by_category": dict(category_counts),
        }

        if durations:
            sorted_d = sorted(durations)
            stats["latency"] = {
                "avg_ms": round(sum(sorted_d) / len(sorted_d), 2),
                "p50_ms": round(sorted_d[len(sorted_d) // 2], 2),
                "p95_ms": round(sorted_d[int(len(sorted_d) * 0.95)], 2),
                "p99_ms": round(sorted_d[int(len(sorted_d) * 0.99)], 2),
                "max_ms": round(sorted_d[-1], 2),
            }

        return stats


# ============================================================================
# Self-Diagnostics Runner
# ============================================================================

def run_diagnostics() -> Dict[str, Any]:
    """
    Run self-diagnostics on the logging system.
    Validates file permissions, disk space, and module registration.
    """
    diag = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "log_dir": str(LOG_BASE_DIR),
        "log_dir_exists": LOG_BASE_DIR.exists(),
        "log_dir_writable": os.access(str(LOG_BASE_DIR), os.W_OK),
        "registered_modules": len(MODULE_REGISTRY),
        "active_loggers": len(_module_loggers),
        "checks": [],
    }

    # Check 1: Directory writability
    try:
        test_file = LOG_BASE_DIR / ".diag_test"
        test_file.write_text("test")
        test_file.unlink()
        diag["checks"].append({"name": "dir_writable", "status": "pass"})
    except Exception as e:
        diag["checks"].append({"name": "dir_writable", "status": "fail", "error": str(e)})

    # Check 2: All modules registered
    for mid, mname in MODULE_REGISTRY.items():
        diag["checks"].append({
            "name": f"module_{mid}",
            "status": "registered",
            "module_name": mname,
            "logger_active": mid in _module_loggers,
        })

    # Check 3: Log file sizes
    total_size = 0
    for log_file in LOG_BASE_DIR.glob("*.log"):
        size = log_file.stat().st_size
        total_size += size
        if size > MAX_LOG_SIZE:
            diag["checks"].append({
                "name": f"file_size_{log_file.name}",
                "status": "warn",
                "size_mb": round(size / 1024 / 1024, 2),
            })

    diag["total_log_size_mb"] = round(total_size / 1024 / 1024, 2)
    diag["overall_status"] = "healthy" if all(
        c.get("status") != "fail" for c in diag["checks"]
    ) else "degraded"

    return diag


# ============================================================================
# Main Entry - System Bootstrap & Log Generation
# ============================================================================

def main():
    """Bootstrap the logging system and generate initial diagnostic logs."""
    print("=" * 70)
    print("OperatorRL M786-M805 Logging System Bootstrap")
    print("=" * 70)

    # Initialize all module loggers
    for module_id, module_name in MODULE_REGISTRY.items():
        logger = get_logger(module_id)
        logger.info(
            f"Module {module_id} ({module_name}) logger initialized",
            category=EventCategory.SYSTEM,
            data={"module_id": module_id, "module_name": module_name}
        )

    # Run diagnostics
    diag = run_diagnostics()
    system_logger = get_logger("M786")
    system_logger.info(
        "System diagnostics completed",
        category=EventCategory.SYSTEM,
        data=diag
    )

    # Simulate module interactions for log generation
    simulations = [
        ("M787", EventCategory.MATCH_DATA, "Historical battle data fetch initiated",
         {"source": "Seraphine/LCU", "endpoint": "/lol-match-history/v1/products/lol"}),
        ("M788", EventCategory.LCU_API, "LCU connector established",
         {"port": 2999, "protocol": "https", "auth": "riot-basic"}),
        ("M789", EventCategory.ANALYSIS, "Match analysis pipeline started",
         {"match_count": 20, "mode": "ranked_solo"}),
        ("M790", EventCategory.PLAYER_DATA, "Player profile aggregation",
         {"summoner_count": 10, "region": "NA1"}),
        ("M791", EventCategory.ANALYSIS, "Champion statistics computation",
         {"champions": 168, "patch": "26.6"}),
        ("M792", EventCategory.ANALYSIS, "Team composition evaluation",
         {"team_size": 5, "roles": ["top", "jungle", "mid", "adc", "support"]}),
        ("M793", EventCategory.PREDICTION, "Win prediction model loaded",
         {"model_version": "3.2.1", "accuracy": 0.73}),
        ("M794", EventCategory.SYSTEM, "Data pipeline orchestration",
         {"stages": ["extract", "transform", "load", "validate"]}),
        ("M795", EventCategory.NETWORK, "Network capture initialized",
         {"method": "fiddler_proxy", "protocol": "HTTP/HTTPS"}),
        ("M796", EventCategory.INTEGRATION, "Fiddler MCP integration active",
         {"fiddler_version": "5.x", "mcp_server": True}),
        ("M797", EventCategory.NETWORK, "Proxy configuration applied",
         {"proxifier": True, "global_proxy": "127.0.0.1:8888"}),
        ("M798", EventCategory.SYSTEM, "Realtime dashboard streaming",
         {"websocket": True, "update_interval_ms": 1000}),
        ("M799", EventCategory.FEEDBACK, "Feedback engine calibrated",
         {"feedback_types": ["action", "decision", "timing"]}),
        ("M800", EventCategory.SYSTEM, "Voice output synthesizer ready",
         {"engine": "edge-tts", "language": "zh-CN"}),
        ("M801", EventCategory.MATCH_DATA, "Game state tracker armed",
         {"capture_fps": 14, "state_buffer_size": 1800}),
        ("M802", EventCategory.ANALYSIS, "Strategy advisor model loaded",
         {"strategies": ["macro", "micro", "objective", "teamfight"]}),
        ("M803", EventCategory.MATCH_DATA, "Replay parser initialized",
         {"supported_formats": [".rofl", ".lrf"]}),
        ("M804", EventCategory.PERFORMANCE, "Performance metrics collector",
         {"metrics": ["fps", "latency", "cpu", "memory", "gpu"]}),
        ("M805", EventCategory.SYSTEM, "Plan update scheduler active",
         {"target_file": "plan.md", "file_count": 100}),
    ]

    for module_id, category, message, data in simulations:
        logger = get_logger(module_id)
        timer = logger.start_timer(f"{module_id}_init")
        time.sleep(0.001)  # Simulate work
        logger.info(message, category=category, data=data)
        logger.stop_timer(timer)

    # Generate system health report
    health = get_system_health()
    system_logger.info(
        "System health report generated",
        category=EventCategory.SYSTEM,
        data=health
    )

    # Output summary
    print(f"\nLog directory: {LOG_BASE_DIR}")
    print(f"Modules initialized: {len(_module_loggers)}")
    print(f"System status: {diag['overall_status']}")
    print(f"Total log entries: {health['total_entries']}")

    # Save diagnostics report
    report_path = LOG_BASE_DIR / "diagnostics_report.json"
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump({
            "diagnostics": diag,
            "health": health,
            "performance": {
                mid: get_logger(mid).get_performance_summary()
                for mid in MODULE_REGISTRY
            }
        }, f, indent=2, ensure_ascii=False, default=str)

    print(f"Diagnostics report: {report_path}")
    return health


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, indent=2, default=str))

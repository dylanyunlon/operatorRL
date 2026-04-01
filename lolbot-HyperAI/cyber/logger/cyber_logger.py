"""
CyberLogger — Structured logging system for lolbot-HyperAI.
=============================================================

Provides module-scoped loggers with JSON-structured output, log-level
filtering, rotating file handlers, and a centralized LogCollector
for real-time dashboard consumption.

Architecture position:
    cyber/logger/cyber_logger.py   ← YOU ARE HERE
    ├─ Used by: every component via ``get_logger(module_name)``
    ├─ LogCollector aggregates for dreamview dashboard
    └─ Supports both file and stdout sinks

Apollo reference:
    cyber/logger/logger.h         — AINFO / AWARN / AERROR macros
    cyber/logger/log_file_object.h — rotating file backend

Design notes:
    - JSON lines format for machine parsing
    - Per-module log files under logs/<module_name>/
    - Configurable rotation (size + count)
    - Thread-safe LogCollector with bounded buffer for live tailing
    - Correlation ID propagation via contextvars
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_LOG_DIR = Path("logs")
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024   # 10 MB per file
_DEFAULT_BACKUP_COUNT = 5
_DEFAULT_LEVEL = logging.INFO
_COLLECTOR_MAX_ENTRIES = 10000
_LOG_FORMAT_CONSOLE = (
    "%(asctime)s [%(levelname)-5s] [%(module_tag)s] %(message)s"
)
_LOG_FORMAT_JSON_KEYS = (
    "timestamp", "level", "module", "message", "correlation_id",
    "seq", "extra",
)

# Context variable for correlation ID propagation across async boundaries
_correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def set_correlation_id(cid: str) -> contextvars.Token:
    """Set the correlation ID for the current context.

    Returns a token that can be used to reset.
    """
    return _correlation_id_var.set(cid)


def get_correlation_id() -> str:
    """Get the current correlation ID."""
    return _correlation_id_var.get()


# ─── JSON Formatter ─────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects.

    Each line contains: timestamp, level, module, message,
    correlation_id, seq, and any extra fields.
    """

    def __init__(self) -> None:
        super().__init__()
        self._seq = 0
        self._lock = threading.Lock()

    def format(self, record: logging.LogRecord) -> str:
        with self._lock:
            self._seq += 1
            seq = self._seq

        # Extract module_tag from our custom filter, fallback to logger name
        module_tag = getattr(record, "module_tag", record.name)

        entry: Dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.") +
                         f"{int(record.msecs):03d}Z",
            "level": record.levelname,
            "module": module_tag,
            "message": record.getMessage(),
            "correlation_id": _correlation_id_var.get(""),
            "seq": seq,
        }

        # Include exception info if present
        if record.exc_info and record.exc_info[1] is not None:
            entry["exception"] = self.formatException(record.exc_info)

        # Include extra fields passed via logger.info("msg", extra={...})
        extra = {}
        for key, val in record.__dict__.items():
            if key not in logging.LogRecord(
                "", 0, "", 0, "", (), None
            ).__dict__ and key not in ("module_tag",):
                try:
                    json.dumps(val)  # ensure serializable
                    extra[key] = val
                except (TypeError, ValueError):
                    extra[key] = str(val)

        if extra:
            entry["extra"] = extra

        try:
            return json.dumps(entry, ensure_ascii=False)
        except (TypeError, ValueError):
            entry["message"] = str(entry.get("message", ""))
            return json.dumps(entry, ensure_ascii=False, default=str)


class _ConsoleFormatter(logging.Formatter):
    """Human-readable console formatter with color support."""

    COLORS = {
        "DEBUG": "\033[36m",    # cyan
        "INFO": "\033[32m",     # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",    # red
        "CRITICAL": "\033[41m", # red bg
    }
    RESET = "\033[0m"

    def __init__(self, use_color: bool = True) -> None:
        super().__init__(_LOG_FORMAT_CONSOLE)
        self._use_color = use_color and sys.stderr.isatty()

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "module_tag"):
            record.module_tag = record.name  # type: ignore[attr-defined]

        if self._use_color:
            color = self.COLORS.get(record.levelname, "")
            record.levelname = f"{color}{record.levelname}{self.RESET}"

        return super().format(record)


# ─── Module Tag Filter ───────────────────────────────────────────────────────

class _ModuleTagFilter(logging.Filter):
    """Injects ``module_tag`` into every LogRecord."""

    def __init__(self, tag: str) -> None:
        super().__init__()
        self._tag = tag

    def filter(self, record: logging.LogRecord) -> bool:
        record.module_tag = self._tag  # type: ignore[attr-defined]
        return True


# ─── Log Collector (live tail buffer) ────────────────────────────────────────

@dataclass
class LogEntry:
    """Single collected log entry for dashboard consumption."""
    timestamp: float
    level: str
    module: str
    message: str
    correlation_id: str = ""
    seq: int = 0


class LogCollector:
    """Thread-safe bounded buffer collecting log entries from all modules.

    The dreamview dashboard polls ``get_recent()`` to display live logs.
    """

    def __init__(self, max_entries: int = _COLLECTOR_MAX_ENTRIES) -> None:
        self._buffer: Deque[LogEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._callbacks: List[Callable[[LogEntry], None]] = []
        self._seq = 0

    def append(self, entry: LogEntry) -> None:
        """Add a log entry to the buffer."""
        with self._lock:
            self._seq += 1
            entry.seq = self._seq
            self._buffer.append(entry)

        for cb in self._callbacks:
            try:
                cb(entry)
            except Exception:
                pass  # never let callback errors disrupt logging

    def get_recent(self, count: int = 100) -> List[LogEntry]:
        """Return the most recent ``count`` entries."""
        with self._lock:
            items = list(self._buffer)
        return items[-count:]

    def get_since(self, seq: int) -> List[LogEntry]:
        """Return all entries with seq > given value (for incremental poll)."""
        with self._lock:
            return [e for e in self._buffer if e.seq > seq]

    def subscribe(self, callback: Callable[[LogEntry], None]) -> None:
        """Register a real-time callback for new entries."""
        self._callbacks.append(callback)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buffer)


# Global collector instance
_collector = LogCollector()


def get_collector() -> LogCollector:
    """Return the global LogCollector instance."""
    return _collector


# ─── Collector Handler (bridges logging → LogCollector) ──────────────────────

class _CollectorHandler(logging.Handler):
    """Logging handler that pushes records into the LogCollector."""

    def __init__(self, collector: LogCollector, module_tag: str) -> None:
        super().__init__()
        self._collector = collector
        self._module_tag = module_tag

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = LogEntry(
                timestamp=record.created,
                level=record.levelname,
                module=self._module_tag,
                message=record.getMessage(),
                correlation_id=_correlation_id_var.get(""),
            )
            self._collector.append(entry)
        except Exception:
            self.handleError(record)


# ─── Logger Configuration ────────────────────────────────────────────────────

@dataclass
class LogConfig:
    """Configuration for the logging system.

    Attributes:
        log_dir: Base directory for log files.
        level: Minimum log level.
        max_bytes: Max size per log file before rotation.
        backup_count: Number of rotated files to keep.
        console_output: Whether to log to stderr.
        json_file_output: Whether to write JSON log files.
        use_color: Whether to use ANSI colors in console.
        collect: Whether to push entries to LogCollector.
    """
    log_dir: Path = field(default_factory=lambda: _DEFAULT_LOG_DIR)
    level: int = _DEFAULT_LEVEL
    max_bytes: int = _DEFAULT_MAX_BYTES
    backup_count: int = _DEFAULT_BACKUP_COUNT
    console_output: bool = True
    json_file_output: bool = True
    use_color: bool = True
    collect: bool = True


_global_config = LogConfig()
_configured_loggers: Dict[str, logging.Logger] = {}
_config_lock = threading.Lock()


def configure(config: LogConfig) -> None:
    """Set the global logging configuration.

    Should be called once at startup before any ``get_logger()`` calls.
    """
    global _global_config
    with _config_lock:
        _global_config = config
        # Reconfigure already-created loggers
        for tag, lg in _configured_loggers.items():
            _apply_config(lg, tag, config)


def get_logger(module_tag: str) -> logging.Logger:
    """Get or create a module-scoped logger.

    Args:
        module_tag: Short identifier (e.g. "canbus", "perception").

    Returns:
        A configured ``logging.Logger`` instance.
    """
    with _config_lock:
        if module_tag in _configured_loggers:
            return _configured_loggers[module_tag]

        lg = logging.getLogger(f"lolbot.{module_tag}")
        _apply_config(lg, module_tag, _global_config)
        _configured_loggers[module_tag] = lg
        return lg


def _apply_config(
    lg: logging.Logger, module_tag: str, config: LogConfig
) -> None:
    """Apply configuration to a logger (add/replace handlers)."""
    lg.setLevel(config.level)
    lg.handlers.clear()
    lg.propagate = False

    tag_filter = _ModuleTagFilter(module_tag)

    # ── Console handler ──────────────────────────────────────────────
    if config.console_output:
        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(_ConsoleFormatter(use_color=config.use_color))
        ch.addFilter(tag_filter)
        lg.addHandler(ch)

    # ── JSON file handler ────────────────────────────────────────────
    if config.json_file_output:
        log_dir = config.log_dir / module_tag
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / f"{module_tag}.jsonl",
            maxBytes=config.max_bytes,
            backupCount=config.backup_count,
            encoding="utf-8",
        )
        fh.setFormatter(_JsonFormatter())
        fh.addFilter(tag_filter)
        lg.addHandler(fh)

    # ── Collector handler ────────────────────────────────────────────
    if config.collect:
        col_handler = _CollectorHandler(_collector, module_tag)
        col_handler.addFilter(tag_filter)
        lg.addHandler(col_handler)


# ─── Convenience: traced decorator ──────────────────────────────────────────

def traced(module_tag: str):
    """Decorator that logs entry/exit and timing for a function.

    Usage::

        @traced("canbus")
        def fetch_game_state():
            ...
    """
    import functools

    def decorator(fn):
        _lg = get_logger(module_tag)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            _lg.debug("→ %s()", fn.__name__)
            t0 = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                elapsed = (time.monotonic() - t0) * 1000
                _lg.debug("← %s() %.1fms", fn.__name__, elapsed)
                return result
            except Exception as exc:
                elapsed = (time.monotonic() - t0) * 1000
                _lg.error(
                    "✗ %s() %.1fms — %s: %s",
                    fn.__name__, elapsed,
                    type(exc).__name__, exc,
                )
                raise

        return wrapper
    return decorator

"""
Logging Setup - Structured logging configuration.

Provides JSON-structured logging for production, and colored
console output for development. Integrates with AgentOS
observability when available.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Optional


class JSONFormatter(logging.Formatter):
    """JSON-structured log formatter for production."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


class ColorFormatter(logging.Formatter):
    """Colored console formatter for development."""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.RESET)
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)


class MetricsHandler(logging.Handler):
    """Counts log events by level for metrics."""

    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[str, int] = {
            "DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 0, "CRITICAL": 0,
        }

    def emit(self, record: logging.LogRecord) -> None:
        self.counts[record.levelname] = self.counts.get(record.levelname, 0) + 1

    def get_counts(self) -> dict[str, int]:
        return dict(self.counts)


_metrics_handler: Optional[MetricsHandler] = None


def setup_logging(
    level: str = "INFO",
    json_output: bool = False,
    log_file: Optional[str] = None,
) -> MetricsHandler:
    """Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        json_output: Use JSON-structured output
        log_file: Optional file path for log output
    """
    global _metrics_handler

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    if json_output:
        console.setFormatter(JSONFormatter())
    else:
        fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        console.setFormatter(ColorFormatter(fmt))
    root.addHandler(console)

    # File handler
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JSONFormatter())
        root.addHandler(file_handler)

    # Metrics handler
    _metrics_handler = MetricsHandler()
    root.addHandler(_metrics_handler)

    # Silence noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return _metrics_handler


def get_log_metrics() -> dict[str, int]:
    """Get log event counts."""
    if _metrics_handler:
        return _metrics_handler.get_counts()
    return {}

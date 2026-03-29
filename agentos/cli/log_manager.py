"""
Log Manager — Leveled logging with rotation and console coloring.

Provides structured log management with level filtering, in-memory
history, ANSI coloring, and export capabilities.

Location: agentos/cli/log_manager.py

Reference (拿来主义):
  - DI-star/distar/ctools/utils/log_helper.py: build_logger, log formatting
  - PARL logging utilities: level-based filtering
  - agentos/governance/notification_service.py: history tracking pattern
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

_LEVEL_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}

_ANSI_COLORS = {
    "DEBUG": "\033[36m",     # Cyan
    "INFO": "\033[32m",      # Green
    "WARNING": "\033[33m",   # Yellow
    "ERROR": "\033[31m",     # Red
    "CRITICAL": "\033[35m",  # Magenta
}
_ANSI_RESET = "\033[0m"


class LogManager:
    """Manages structured logging with level filtering and history.

    Attributes:
        max_history: Maximum log entries to retain in memory.
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._level: str = "INFO"
        self._history: deque[dict[str, Any]] = deque(maxlen=max_history)

    def set_level(self, level: str) -> None:
        """Set the minimum log level.

        Args:
            level: Log level string (DEBUG/INFO/WARNING/ERROR/CRITICAL).
        """
        if level in _LEVEL_ORDER:
            self._level = level
        else:
            self._level = "INFO"

    def get_level(self) -> str:
        """Return current log level."""
        return self._level

    def log(self, level: str, message: str) -> None:
        """Log a message at the specified level.

        Messages below the current level are filtered out.

        Args:
            level: Log level string.
            message: Log message text.
        """
        if _LEVEL_ORDER.get(level, 1) < _LEVEL_ORDER.get(self._level, 1):
            return

        entry = {
            "level": level,
            "message": message,
            "timestamp": time.time(),
        }
        self._history.append(entry)

    def get_recent(self, count: int) -> list[dict[str, Any]]:
        """Get the most recent log entries.

        Args:
            count: Maximum number of entries to return.

        Returns:
            List of log entry dicts (newest last).
        """
        items = list(self._history)
        return items[-count:] if count < len(items) else items

    def colorize(self, level: str, message: str) -> str:
        """Apply ANSI color to a log message.

        Args:
            level: Log level for color selection.
            message: Message text.

        Returns:
            Colored message string.
        """
        color = _ANSI_COLORS.get(level, "")
        return f"{color}{message}{_ANSI_RESET}" if color else message

    def clear(self) -> None:
        """Clear all log history."""
        self._history.clear()

    def export(self) -> str:
        """Export all log history as a formatted string.

        Returns:
            Multi-line log string.
        """
        lines = []
        for entry in self._history:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["timestamp"]))
            lines.append(f"[{ts}] [{entry['level']}] {entry['message']}")
        return "\n".join(lines)

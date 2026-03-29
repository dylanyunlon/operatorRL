"""
Status Reporter — Real-time status formatting with progress bars.

Formats agent status, progress bars, elapsed time, and multi-section
reports for CLI output.

Location: agentos/cli/status_reporter.py

Reference (拿来主义):
  - agentos/governance/cross_game_dashboard.py: panel rendering pattern
  - DI-star/distar/ctools/utils/log_helper.py: log formatting
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class StatusReporter:
    """Formats agent status information for CLI display."""

    def __init__(self) -> None:
        self._sections: dict[str, dict[str, Any]] = {}

    def format_status(self, status: dict[str, Any]) -> str:
        """Format a status dict as human-readable text.

        Args:
            status: Status dict with game, state, uptime, etc.

        Returns:
            Formatted status string.
        """
        parts = []
        for key, value in status.items():
            parts.append(f"{key}: {value}")
        return " | ".join(parts)

    def format_progress(self, current: int, total: int) -> str:
        """Format a progress bar string.

        Args:
            current: Current progress value.
            total: Total target value.

        Returns:
            Progress bar string with percentage.
        """
        if total <= 0:
            return "[----------] 0%"

        pct = min(100, int(current / total * 100))
        filled = pct // 10
        bar = "#" * filled + "-" * (10 - filled)
        return f"[{bar}] {pct}%"

    def format_elapsed(self, seconds: int) -> str:
        """Format elapsed time as human-readable string.

        Args:
            seconds: Total elapsed seconds.

        Returns:
            Formatted time string (e.g., "1h 23m 45s").
        """
        if seconds <= 0:
            return "0s"

        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if secs > 0 or not parts:
            parts.append(f"{secs}s")

        return " ".join(parts)

    def add_section(self, name: str, data: dict[str, Any]) -> None:
        """Add a display section.

        Args:
            name: Section name.
            data: Section data dict.
        """
        self._sections[name] = data

    def get_sections(self) -> dict[str, dict[str, Any]]:
        """Return all sections."""
        return dict(self._sections)

    def clear_sections(self) -> None:
        """Clear all sections."""
        self._sections.clear()

    def render_all(self) -> str:
        """Render all sections as formatted text."""
        lines = []
        for name, data in self._sections.items():
            lines.append(f"=== {name} ===")
            for key, value in data.items():
                lines.append(f"  {key}: {value}")
        return "\n".join(lines)

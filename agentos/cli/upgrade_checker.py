"""
Upgrade Checker — Semantic version comparison and update notifications.

Compares current version against available versions, generates
update messages and changelog placeholders.

Location: agentos/cli/upgrade_checker.py

Reference (拿来主义):
  - agentos/governance/model_versioner.py: version tracking + diff
  - PARL version management patterns
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class UpgradeChecker:
    """Semantic version checker with update notifications.

    Attributes:
        current_version: Currently installed version string.
    """

    def __init__(self, current_version: str = "0.0.0") -> None:
        self.current_version: str = current_version

    def parse_version(self, version: str) -> tuple[int, int, int]:
        """Parse a version string into (major, minor, patch).

        Args:
            version: Version string (e.g., "1.2.3" or "1.0").

        Returns:
            Tuple of (major, minor, patch).
        """
        parts = version.split(".")
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)

    def compare(self, remote_version: str) -> int:
        """Compare current version with a remote version.

        Args:
            remote_version: Version to compare against.

        Returns:
            Positive if remote is newer, 0 if equal, negative if older.
        """
        current = self.parse_version(self.current_version)
        remote = self.parse_version(remote_version)

        for c, r in zip(current, remote):
            if r > c:
                return 1
            if r < c:
                return -1
        return 0

    def needs_update(self, remote_version: str) -> bool:
        """Check if an update is available.

        Args:
            remote_version: Latest available version.

        Returns:
            True if remote is newer than current.
        """
        return self.compare(remote_version) > 0

    def format_update_message(self, remote_version: str) -> str:
        """Format a user-friendly update notification.

        Args:
            remote_version: Available version.

        Returns:
            Update message string.
        """
        if self.needs_update(remote_version):
            return (
                f"Update available: {self.current_version} → {remote_version}. "
                f"Run 'agentos upgrade' to update."
            )
        return f"You are on the latest version ({self.current_version})."

    def get_changelog(self, remote_version: str) -> str:
        """Get changelog for a version (placeholder).

        Args:
            remote_version: Target version.

        Returns:
            Changelog string.
        """
        return f"Changelog for {remote_version}: See https://github.com/dylanyunlon/operatorRL/releases"

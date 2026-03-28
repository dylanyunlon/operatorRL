"""
Patch Timeline — Track champion and item changes across game patches.

Maintains a versioned history of champion and item changes for
cross-patch analysis and meta detection.

Location: integrations/lol-history/src/lol_history/patch_timeline.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.patch_timeline.v1"


class PatchTimeline:
    """Track champion and item changes across game patches.

    Stores patch entries with champion_changes and item_changes,
    supports cross-patch comparison and champion history queries.
    """

    def __init__(self) -> None:
        self._patches: dict[str, dict[str, Any]] = {}
        self._order: list[str] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_patch(self, version: str, data: dict[str, Any]) -> None:
        """Add a patch entry.

        Args:
            version: Patch version string (e.g., "14.10").
            data: Patch data with champion_changes and/or item_changes.
        """
        self._patches[version] = data
        if version not in self._order:
            self._order.append(version)
            self._order.sort(key=lambda v: [int(x) for x in v.split(".")])

    def get_patch(self, version: str) -> Optional[dict[str, Any]]:
        """Get patch data by version.

        Args:
            version: Patch version string.

        Returns:
            Patch data dict or None.
        """
        return self._patches.get(version)

    def get_champion_history(self, champion: str) -> list[dict[str, Any]]:
        """Get all changes for a specific champion across patches.

        Args:
            champion: Champion name.

        Returns:
            List of {version, changes} dicts.
        """
        history: list[dict[str, Any]] = []
        for version in self._order:
            patch = self._patches[version]
            champ_changes = patch.get("champion_changes", {})
            if champion in champ_changes:
                history.append({
                    "version": version,
                    "changes": champ_changes[champion],
                })
        return history

    def compare_patches(
        self, version_a: str, version_b: str
    ) -> dict[str, Any]:
        """Compare two patches.

        Args:
            version_a: First patch version.
            version_b: Second patch version.

        Returns:
            Diff dict with added/removed/changed champions.
        """
        a = self._patches.get(version_a, {}).get("champion_changes", {})
        b = self._patches.get(version_b, {}).get("champion_changes", {})
        all_champs = set(a.keys()) | set(b.keys())
        diff: dict[str, Any] = {}
        for champ in all_champs:
            in_a = champ in a
            in_b = champ in b
            if in_a and not in_b:
                diff[champ] = {"status": "removed_in_b", "a": a[champ]}
            elif in_b and not in_a:
                diff[champ] = {"status": "added_in_b", "b": b[champ]}
            else:
                if a[champ] != b[champ]:
                    diff[champ] = {"status": "changed", "a": a[champ], "b": b[champ]}
        return diff

    def latest_patch_version(self) -> Optional[str]:
        """Get the latest patch version.

        Returns:
            Latest version string or None if empty.
        """
        if not self._order:
            return None
        return self._order[-1]

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "patch_timeline",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

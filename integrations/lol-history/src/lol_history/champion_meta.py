"""
Champion Meta — Version-aware champion strength detection.

Analyzes match data across patches to detect:
- Current tier lists (strongest champions)
- Meta shifts between patches
- Pick/ban rates
- Win rate trends

Location: integrations/lol-history/src/lol_history/champion_meta.py
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.champion_meta.v1"


class ChampionMeta:
    """Champion meta analysis engine.

    Tracks champion performance across patches and produces
    tier lists, meta shift detection, and pick rate analysis.
    """

    def __init__(self, current_patch: str = "") -> None:
        self.current_patch = current_patch
        # {patch: {champion: {"wins": int, "total": int}}}
        self._data: dict[str, dict[str, dict[str, int]]] = defaultdict(
            lambda: defaultdict(lambda: {"wins": 0, "total": 0})
        )
        self._match_count: int = 0

    @property
    def match_count(self) -> int:
        return self._match_count

    def add_match(self, champion: str, win: bool, patch: str = "") -> None:
        """Record a match result for a champion.

        Args:
            champion: Champion name.
            win: Whether the champion won.
            patch: Game patch version (e.g., "14.10").
        """
        patch = patch or self.current_patch or "unknown"
        self._data[patch][champion]["total"] += 1
        if win:
            self._data[patch][champion]["wins"] += 1
        self._match_count += 1

    def win_rate(self, champion: str, patch: str = "") -> float:
        """Get win rate for a champion on a specific patch."""
        patch = patch or self.current_patch or "unknown"
        stats = self._data.get(patch, {}).get(champion, {"wins": 0, "total": 0})
        total = stats["total"]
        if total == 0:
            return 0.0
        return stats["wins"] / total

    def pick_rate(self, champion: str, patch: str = "") -> float:
        """Get pick rate for a champion on a specific patch."""
        patch = patch or self.current_patch or "unknown"
        patch_data = self._data.get(patch, {})
        total_matches = sum(s["total"] for s in patch_data.values())
        if total_matches == 0:
            return 0.0
        champ_matches = patch_data.get(champion, {"total": 0})["total"]
        return champ_matches / total_matches

    def tier_list(
        self,
        patch: str = "",
        min_games: int = 1,
    ) -> list[dict[str, Any]]:
        """Generate tier list sorted by win rate.

        Args:
            patch: Patch to analyze.
            min_games: Minimum games for inclusion.

        Returns:
            Sorted list of {champion, win_rate, games} dicts.
        """
        patch = patch or self.current_patch or "unknown"
        patch_data = self._data.get(patch, {})

        entries = []
        for champ, stats in patch_data.items():
            if stats["total"] >= min_games:
                entries.append({
                    "champion": champ,
                    "win_rate": stats["wins"] / stats["total"],
                    "games": stats["total"],
                })

        entries.sort(key=lambda e: e["win_rate"], reverse=True)
        return entries

    def detect_shift(
        self,
        champion: str,
        from_patch: str,
        to_patch: str,
    ) -> float:
        """Detect win rate shift between patches.

        Returns:
            Win rate delta (positive = got stronger).
        """
        wr_from = self.win_rate(champion, from_patch)
        wr_to = self.win_rate(champion, to_patch)
        return wr_to - wr_from

    def strongest(self, patch: str = "", top_n: int = 5) -> list[str]:
        """Get strongest champions by win rate.

        Args:
            patch: Patch to analyze.
            top_n: Number of champions to return.

        Returns:
            List of champion names.
        """
        tiers = self.tier_list(patch=patch, min_games=1)
        return [t["champion"] for t in tiers[:top_n]]

    def to_dict(self) -> dict[str, Any]:
        """Serialize meta data."""
        champions: dict[str, Any] = {}
        for patch, patch_data in self._data.items():
            for champ, stats in patch_data.items():
                if champ not in champions:
                    champions[champ] = {}
                champions[champ][patch] = dict(stats)
        return {
            "champions": champions,
            "match_count": self._match_count,
            "current_patch": self.current_patch,
        }

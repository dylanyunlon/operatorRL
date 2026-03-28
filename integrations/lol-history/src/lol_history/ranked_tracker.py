"""
Ranked Tracker — Rank and LP change trend tracking.

Tracks tier/division/LP history over time to detect:
- Win/loss streaks
- LP trend direction (climbing vs falling)
- Tier transitions (promotions/demotions)

Location: integrations/lol-history/src/lol_history/ranked_tracker.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_history.ranked_tracker.v1"

_TIER_ORDER = {
    "Iron": 0, "Bronze": 1, "Silver": 2, "Gold": 3,
    "Platinum": 4, "Emerald": 5, "Diamond": 6,
    "Master": 7, "Grandmaster": 8, "Challenger": 9,
}


@dataclass
class RankRecord:
    """Single rank data point."""
    tier: str
    division: int
    lp: int
    win: Optional[bool] = None
    timestamp: float = field(default_factory=time.time)

    def to_absolute_lp(self) -> int:
        """Convert to absolute LP for trend computation."""
        tier_base = _TIER_ORDER.get(self.tier, 0) * 400
        division_base = (4 - self.division) * 100
        return tier_base + division_base + self.lp


class RankedTracker:
    """Tracks ranked progression over time.

    Usage:
        tracker = RankedTracker()
        tracker.record("Gold", 2, 75, win=True)
        tracker.record("Gold", 2, 90, win=True)
        trend = tracker.lp_trend()  # > 0 = climbing
        streak = tracker.current_streak()  # 2 = two wins
    """

    def __init__(self) -> None:
        self._records: list[RankRecord] = []

    @property
    def history_count(self) -> int:
        return len(self._records)

    def record(
        self,
        tier: str,
        division: int = 1,
        lp: int = 0,
        win: Optional[bool] = None,
    ) -> None:
        """Record a new rank data point."""
        self._records.append(RankRecord(
            tier=tier,
            division=division,
            lp=lp,
            win=win,
        ))

    def lp_trend(self, window: int = 10) -> float:
        """Compute LP trend over recent records.

        Returns:
            Positive = climbing, negative = falling, 0 = flat.
        """
        records = self._records[-window:]
        if len(records) < 2:
            return 0.0

        lps = [r.to_absolute_lp() for r in records]
        # Simple: last - first
        return float(lps[-1] - lps[0])

    def current_streak(self) -> int:
        """Compute current win/loss streak.

        Returns:
            Positive int = win streak, negative int = loss streak, 0 = no streak.
        """
        if not self._records:
            return 0

        streak = 0
        last_win = None

        for r in reversed(self._records):
            if r.win is None:
                break
            if last_win is None:
                last_win = r.win
            if r.win == last_win:
                streak += 1 if r.win else -1
            else:
                break
            last_win = r.win

        return streak

    def to_dict(self) -> dict[str, Any]:
        """Serialize tracker state."""
        return {
            "records": [
                {
                    "tier": r.tier,
                    "division": r.division,
                    "lp": r.lp,
                    "win": r.win,
                    "timestamp": r.timestamp,
                }
                for r in self._records
            ],
        }

    def from_dict(self, data: dict[str, Any]) -> None:
        """Restore from serialized state."""
        self._records.clear()
        for r in data.get("records", []):
            self._records.append(RankRecord(
                tier=r.get("tier", "Iron"),
                division=r.get("division", 4),
                lp=r.get("lp", 0),
                win=r.get("win"),
                timestamp=r.get("timestamp", time.time()),
            ))

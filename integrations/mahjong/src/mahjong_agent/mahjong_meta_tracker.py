"""
Mahjong Meta Tracker — Track rank/ruleset meta changes.

Location: integrations/mahjong/src/mahjong_agent/mahjong_meta_tracker.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_meta_tracker.v1"

class MahjongMetaTracker:
    """Track meta shifts in mahjong across ranks and rulesets."""

    def __init__(self) -> None:
        self._snapshots: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_snapshot(self, rank: str, ruleset: str, avg_win_rate: float, avg_deal_in_rate: float) -> None:
        self._snapshots.append({"rank": rank, "ruleset": ruleset, "avg_win_rate": avg_win_rate, "avg_deal_in_rate": avg_deal_in_rate, "timestamp": time.time()})
        self._fire_evolution("snapshot_recorded", {"rank": rank, "ruleset": ruleset})

    def get_trend(self, rank: str = None, limit: int = 20) -> list[dict[str, Any]]:
        result = self._snapshots
        if rank:
            result = [s for s in result if s["rank"] == rank]
        return result[-limit:]

    def snapshot_count(self) -> int:
        return len(self._snapshots)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

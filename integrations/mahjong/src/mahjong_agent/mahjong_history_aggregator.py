"""
Mahjong History Aggregator — Cross-game stats (和牌率/放铳率/立直率).

Location: integrations/mahjong/src/mahjong_agent/mahjong_history_aggregator.py

Reference (拿来主義):
  - Akagi/akagi: game log parsing patterns
  - Mortal/mortal: mjai protocol stats
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_history_aggregator.v1"

class MahjongHistoryAggregator:
    """Aggregate mahjong match statistics."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def aggregate(self, rounds: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(rounds)
        if total == 0:
            return {"total_rounds": 0, "win_rate": 0.0, "deal_in_rate": 0.0, "riichi_rate": 0.0, "avg_placement": 0.0}
        wins = sum(1 for r in rounds if r.get("won", False))
        deal_ins = sum(1 for r in rounds if r.get("deal_in", False))
        riichi = sum(1 for r in rounds if r.get("riichi", False))
        placements = [r.get("placement", 2.5) for r in rounds]
        self._fire_evolution("aggregated", {"total": total})
        return {"total_rounds": total, "win_rate": wins / total, "deal_in_rate": deal_ins / total, "riichi_rate": riichi / total, "avg_placement": sum(placements) / total}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

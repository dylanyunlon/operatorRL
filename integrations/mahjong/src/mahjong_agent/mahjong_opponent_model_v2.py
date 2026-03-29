"""
Mahjong Opponent Model V2 — History-based opponent tendency prediction.

Location: integrations/mahjong/src/mahjong_agent/mahjong_opponent_model_v2.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_opponent_model_v2.v1"

class MahjongOpponentModelV2:
    """Predict opponent tendencies from historical mahjong data."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def build_model(self, opponent_rounds: list[dict[str, Any]]) -> dict[str, Any]:
        if not opponent_rounds:
            return {"style": "unknown", "aggression": 0.5, "defense": 0.5, "riichi_tendency": 0.5}
        riichi_count = sum(1 for r in opponent_rounds if r.get("riichi", False))
        deal_in_count = sum(1 for r in opponent_rounds if r.get("deal_in", False))
        total = len(opponent_rounds)
        riichi_rate = riichi_count / total
        deal_in_rate = deal_in_count / total
        aggression = riichi_rate
        defense = 1.0 - deal_in_rate
        style = "aggressive" if aggression > 0.3 else ("defensive" if defense > 0.8 else "balanced")
        self._fire_evolution("model_built", {"style": style})
        return {"style": style, "aggression": aggression, "defense": defense, "riichi_tendency": riichi_rate}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

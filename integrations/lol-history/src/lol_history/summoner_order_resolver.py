"""
Summoner Order Resolver — Sort summoners by standard LoL position order.
Location: integrations/lol-history/src/lol_history/summoner_order_resolver.py
Reference: Seraphine/app/lol/tools.py: parseSummonerOrder, sortedSummonersByGameRole
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.summoner_order_resolver.v1"
_POSITION_PRIORITY = {"TOP":0,"JUNGLE":1,"MIDDLE":2,"MID":2,"BOTTOM":3,"ADC":3,"UTILITY":4,"SUPPORT":4}

class SummonerOrderResolver:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def position_priority(self, position: str) -> int:
        return _POSITION_PRIORITY.get(position.upper(), 99)

    def sort_by_game_role(self, summoners: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def key_fn(s: dict) -> int:
            pos = s.get("selectedPosition", s.get("assignedPosition", ""))
            return self.position_priority(pos)
        return sorted(summoners, key=key_fn)

    def resolve_order(self, team: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.sort_by_game_role(team)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "summoner_order_resolver", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

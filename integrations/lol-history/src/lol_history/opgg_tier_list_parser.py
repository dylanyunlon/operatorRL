"""
OPGG Tier List Parser — Parse and query champion tier list data.
Location: integrations/lol-history/src/lol_history/opgg_tier_list_parser.py
Reference: Seraphine/app/lol/opgg.py: __fetchTierList, getChampionPositions
"""
from __future__ import annotations
import logging, time
from collections import Counter
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.opgg_tier_list_parser.v1"

class OpggTierListParser:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_tier_list(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        entries = []
        for champ in data:
            cid = champ.get("id", 0)
            for pos in champ.get("positions", []):
                stats = pos.get("stats", {})
                td = stats.get("tier_data", {})
                entries.append({"champion_id": cid, "position": pos.get("name", ""),
                                "tier": td.get("tier", 99), "rank": td.get("rank", 99),
                                "win_rate": stats.get("win_rate", 0.0), "pick_rate": stats.get("pick_rate", 0.0)})
        return entries

    def filter_by_position(self, entries: list[dict], position: str) -> list[dict]:
        return [e for e in entries if e.get("position", "").upper() == position.upper()]

    def filter_by_tier(self, entries: list[dict], max_tier: int) -> list[dict]:
        return [e for e in entries if e.get("tier", 99) <= max_tier]

    def get_top_n(self, entries: list[dict], n: int = 10) -> list[dict]:
        return sorted(entries, key=lambda e: (e.get("tier", 99), e.get("rank", 99)))[:n]

    def lookup_champion(self, entries: list[dict], champion_id: int) -> Optional[dict]:
        for e in entries:
            if e.get("champion_id") == champion_id:
                return e
        return None

    def position_distribution(self, entries: list[dict]) -> dict[str, int]:
        return dict(Counter(e.get("position", "") for e in entries))

    def meta_strength_score(self, entry: dict[str, Any]) -> float:
        tier = entry.get("tier", 5)
        rank = entry.get("rank", 50)
        wr = entry.get("win_rate", 0.5)
        pr = entry.get("pick_rate", 0.01)
        tier_score = max(0, (6 - tier)) / 5.0
        rank_score = max(0, (100 - rank)) / 100.0
        return min(1.0, 0.4 * tier_score + 0.3 * rank_score + 0.2 * wr + 0.1 * min(pr * 10, 1.0))

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "opgg_tier_list_parser", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

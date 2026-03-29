"""
Rank Tier Resolver — Parse ranked stats and compare tiers.
Location: integrations/lol-history/src/lol_history/rank_tier_resolver.py
Reference: Seraphine/app/lol/tools.py: parseRankInfo, parseDetailRankInfo, translateTier
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.rank_tier_resolver.v1"
_TIER_ORDER = ["IRON","BRONZE","SILVER","GOLD","PLATINUM","EMERALD","DIAMOND","MASTER","GRANDMASTER","CHALLENGER"]
_DIVISION_ORDER = ["IV","III","II","I"]

class RankTierResolver:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_rank_info(self, info: dict[str, Any]) -> dict[str, dict[str, Any]]:
        queue_map = info.get("queueMap", {})
        solo = self._extract_queue(queue_map.get("RANKED_SOLO_5x5", {}))
        flex = self._extract_queue(queue_map.get("RANKED_FLEX_SR", {}))
        return {"solo": solo, "flex": flex}

    def _extract_queue(self, q: dict[str, Any]) -> dict[str, Any]:
        tier = q.get("tier", "UNRANKED")
        division = q.get("division", "")
        lp = q.get("leaguePoints", 0)
        wins = q.get("wins", 0)
        losses = q.get("losses", 0)
        total = wins + losses
        return {"tier": tier if tier else "UNRANKED", "division": division, "lp": lp,
                "wins": wins, "losses": losses, "winrate": wins / total if total > 0 else 0.0}

    def tier_to_numeric(self, tier: str, division: str) -> int:
        tier_upper = tier.upper()
        if tier_upper == "UNRANKED":
            return 0
        tier_val = (_TIER_ORDER.index(tier_upper) + 1) * 100 if tier_upper in _TIER_ORDER else 0
        div_val = (_DIVISION_ORDER.index(division) + 1) * 10 if division in _DIVISION_ORDER else 0
        return tier_val + div_val

    def compare_ranks(self, tier1: str, div1: str, tier2: str, div2: str) -> int:
        return self.tier_to_numeric(tier1, div1) - self.tier_to_numeric(tier2, div2)

    def format_rank_string(self, tier: str, division: str, lp: int = 0) -> str:
        tier_display = tier.capitalize() if tier else "Unranked"
        if tier.upper() in ("MASTER","GRANDMASTER","CHALLENGER"):
            return f"{tier_display} ({lp} LP)"
        return f"{tier_display} {division} ({lp} LP)"

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "rank_tier_resolver", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

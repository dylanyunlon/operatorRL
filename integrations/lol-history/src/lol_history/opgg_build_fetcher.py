"""
OPGG Build Fetcher — Build URLs and parse OPGG champion data.
Location: integrations/lol-history/src/lol_history/opgg_build_fetcher.py
Reference: Seraphine/app/lol/opgg.py: Opgg class, getChampionBuild
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "lol_history.opgg_build_fetcher.v1"

class OpggBuildFetcher:
    def __init__(self, base_url: str = "https://lol-api-champion.op.gg") -> None:
        self.base_url = base_url.rstrip("/")
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def build_champion_url(self, champion_id: int, position: str, region: str = "global", mode: str = "ranked") -> str:
        return f"{self.base_url}/api/{region}/champions/{mode}/{champion_id}/{position}"

    def build_tier_list_url(self, region: str = "kr", mode: str = "ranked") -> str:
        return f"{self.base_url}/api/{region}/champions/{mode}"

    def parse_build_response(self, resp: dict[str, Any]) -> dict[str, Any]:
        data = resp.get("data", {})
        item_builds = [{"items": ib.get("ids", []), "pick_rate": ib.get("pickRate", 0.0),
                         "win_rate": ib.get("winRate", 0.0)} for ib in data.get("items", [])]
        rune_builds = [{"primaryStyleId": rb.get("primaryStyleId", 0), "subStyleId": rb.get("subStyleId", 0),
                         "perkIds": rb.get("perkIds", []), "pick_rate": rb.get("pickRate", 0.0),
                         "win_rate": rb.get("winRate", 0.0)} for rb in data.get("runes", [])]
        spell_builds = [{"spells": sb.get("ids", []), "pick_rate": sb.get("pickRate", 0.0),
                          "win_rate": sb.get("winRate", 0.0)} for sb in data.get("spells", [])]
        skill_orders = [{"order": sk.get("order", ""), "pick_rate": sk.get("pickRate", 0.0)}
                        for sk in data.get("skills", [])]
        return {"item_builds": item_builds, "rune_builds": rune_builds,
                "spell_builds": spell_builds, "skill_orders": skill_orders}

    def get_best_build(self, builds: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not builds:
            return None
        return max(builds, key=lambda b: b.get("win_rate", 0.0))

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "opgg_build_fetcher", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

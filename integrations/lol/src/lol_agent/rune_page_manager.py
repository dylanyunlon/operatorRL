"""
Rune Page Manager — Build, validate, and manage rune pages.
Location: integrations/lol/src/lol_agent/rune_page_manager.py
Reference: Seraphine/app/lol/tools.py: createAndSetRunePage
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.rune_page_manager.v1"

class RunePageManager:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def build_rune_page(self, name: str, primary_style: int, sub_style: int, perks: list[int]) -> dict[str, Any]:
        return {"name": name, "primaryStyleId": primary_style, "subStyleId": sub_style,
                "selectedPerkIds": list(perks), "current": True}

    def validate_rune_page(self, primary_style: int, sub_style: int, perks: list[int]) -> bool:
        if primary_style == sub_style:
            return False
        if len(perks) != 6:
            return False
        return True

    def build_lcu_payload(self, name: str, primary_style: int, sub_style: int, perks: list[int]) -> dict[str, Any]:
        return {"name": name, "primaryStyleId": primary_style, "subStyleId": sub_style,
                "selectedPerkIds": list(perks), "current": True}

    def recommend_runes(self, opgg_runes: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not opgg_runes:
            return None
        return max(opgg_runes, key=lambda r: r.get("winRate", 0.0))

    def format_rune_name(self, champion: str, source: str = "OPGG") -> str:
        return f"[{source}] {champion}"

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "rune_page_manager", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

"""
Summoner Spell Advisor — Recommend summoner spells by position.
Location: integrations/lol/src/lol_agent/summoner_spell_advisor.py
Reference: Seraphine/app/lol/tools.py: autoSetSummonerSpell
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.summoner_spell_advisor.v1"
_SPELL_NAMES = {1:"Cleanse",3:"Exhaust",4:"Flash",6:"Ghost",7:"Heal",11:"Smite",12:"Teleport",14:"Ignite",21:"Barrier",32:"Mark"}
_POSITION_DEFAULTS = {"ADC":[4,7],"BOTTOM":[4,7],"SUPPORT":[4,14],"UTILITY":[4,14],"JUNGLE":[4,11],"MIDDLE":[4,14],"MID":[4,14],"TOP":[4,12]}

class SummonerSpellAdvisor:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def recommend_spells(self, position: str, champion_id: int = 0) -> dict[str, Any]:
        spells = _POSITION_DEFAULTS.get(position.upper(), [4, 14])
        return {"spells": list(spells), "source": "default"}

    def spell_name(self, spell_id: int) -> str:
        return _SPELL_NAMES.get(spell_id, "Unknown")

    def from_opgg_data(self, opgg_spells: list[dict[str, Any]]) -> dict[str, Any]:
        if not opgg_spells:
            return {"spells": [], "source": "opgg"}
        best = max(opgg_spells, key=lambda s: s.get("winRate", s.get("win_rate", 0.0)))
        return {"spells": best.get("ids", []), "source": "opgg"}

    def validate_spells(self, spells: list[int]) -> bool:
        if len(spells) != 2:
            return False
        if spells[0] == spells[1]:
            return False
        return True

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "summoner_spell_advisor", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

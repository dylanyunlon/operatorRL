"""
AramIntelligenceEnricher — Enriches ARAM game data with champion-specific modifiers.

Architecture (拿来主义):
  Seraphine/app/lol/aram.py — AramBuff: getInfoByChampionId, damage/reduction modifiers
  Seraphine/app/lol/aram.py — isAvailable, getDataVersion data freshness checks

Location: integrations/lol-history/src/lol_history/aram_intelligence_enricher.py

Design Notes (Knuth-level critique):
  User:
    - ARAM-specific intelligence: "Sona has 15% damage reduction in ARAM, still strong."
    - Adjusts winrate predictions for ARAM queue to account for hidden buff/nerf modifiers.
  System:
    - Buff data cached with version check (mirrors Seraphine AramBuff.__needUpdate).
    - Stateless enrichment per-champion; buff data is read-only reference.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.aram_intelligence_enricher.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class AramIntelligenceEnricher:
    """Enriches ARAM game data with champion-specific damage modifiers.

    Public API: load_buff_data, enrich_champion, enrich_team,
                get_aram_advantage, is_data_available, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._enrich_count = 0
        self._buff_data: Dict[int, Dict[str, Any]] = {}
        self._data_version: str = ""
        self._last_update: float = 0.0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def load_buff_data(self, buff_entries: List[Dict[str, Any]],
                        version: str = "") -> Dict[str, Any]:
        """Load ARAM buff/nerf data. Mirrors Seraphine AramBuff data loading."""
        self._op_count += 1
        self._buff_data.clear()
        for entry in buff_entries:
            champ_id = entry.get("championId", entry.get("id", 0))
            if champ_id:
                self._buff_data[champ_id] = {
                    "damage_dealt_modifier": entry.get("dmg_dealt",
                                                        entry.get("damageDealt", 1.0)),
                    "damage_taken_modifier": entry.get("dmg_taken",
                                                        entry.get("damageTaken", 1.0)),
                    "healing_modifier": entry.get("healing", 1.0),
                    "shielding_modifier": entry.get("shielding", 1.0),
                    "ability_haste_modifier": entry.get("abilityHaste", 0),
                    "attack_speed_modifier": entry.get("attackSpeed", 0),
                    "energy_regen_modifier": entry.get("energyRegen", 0),
                }
        self._data_version = version
        self._last_update = time.time()
        self._fire("data_loaded", {"champions": len(self._buff_data), "version": version})
        return {"status": "ok", "champions_loaded": len(self._buff_data),
                "version": version}

    def is_data_available(self) -> Dict[str, Any]:
        """Check if buff data is available and fresh. Mirrors Seraphine AramBuff.isAvailable."""
        self._op_count += 1
        available = len(self._buff_data) > 0
        age = time.time() - self._last_update if self._last_update else float("inf")
        fresh = age < 86400  # 24 hours
        return {"status": "ok", "available": available, "fresh": fresh,
                "version": self._data_version, "champions": len(self._buff_data),
                "age_seconds": round(age, 1)}

    def enrich_champion(self, champion_id: int) -> Dict[str, Any]:
        """Get ARAM modifiers for a champion. Mirrors Seraphine getInfoByChampionId."""
        self._op_count += 1
        self._enrich_count += 1
        buff = self._buff_data.get(champion_id)
        if not buff:
            return {"status": "ok", "champion_id": champion_id,
                    "has_aram_modifiers": False, "modifiers": {}}
        # Classify impact
        dmg_dealt = buff.get("damage_dealt_modifier", 1.0)
        dmg_taken = buff.get("damage_taken_modifier", 1.0)
        if dmg_dealt > 1.0 or dmg_taken < 1.0:
            impact = "buffed"
        elif dmg_dealt < 1.0 or dmg_taken > 1.0:
            impact = "nerfed"
        else:
            impact = "neutral"
        net_advantage = round((dmg_dealt - 1.0) * 100 - (dmg_taken - 1.0) * 100, 1)
        return {"status": "ok", "champion_id": champion_id,
                "has_aram_modifiers": True, "modifiers": buff,
                "impact": impact, "net_advantage_pct": net_advantage}

    def enrich_team(self, champion_ids: List[int]) -> Dict[str, Any]:
        """Enrich entire team's ARAM modifiers."""
        self._op_count += 1
        enriched = []
        total_advantage = 0.0
        for cid in champion_ids:
            r = self.enrich_champion(cid)
            enriched.append(r)
            total_advantage += r.get("net_advantage_pct", 0.0)
        avg_advantage = round(_safe_div(total_advantage, len(champion_ids)), 2)
        buffed = sum(1 for e in enriched if e.get("impact") == "buffed")
        nerfed = sum(1 for e in enriched if e.get("impact") == "nerfed")
        return {"status": "ok", "team_size": len(champion_ids),
                "avg_advantage_pct": avg_advantage,
                "buffed_count": buffed, "nerfed_count": nerfed,
                "members": enriched}

    def get_aram_advantage(self, team_a_ids: List[int],
                            team_b_ids: List[int]) -> Dict[str, Any]:
        """Compare ARAM advantage between two teams."""
        self._op_count += 1
        team_a = self.enrich_team(team_a_ids)
        team_b = self.enrich_team(team_b_ids)
        diff = team_a.get("avg_advantage_pct", 0) - team_b.get("avg_advantage_pct", 0)
        if abs(diff) < 2.0:
            advantage = "even"
        elif diff > 0:
            advantage = "team_a"
        else:
            advantage = "team_b"
        return {"status": "ok", "advantage": advantage, "diff_pct": round(diff, 2),
                "team_a_avg": team_a.get("avg_advantage_pct", 0),
                "team_b_avg": team_b.get("avg_advantage_pct", 0)}

    def get_stats(self) -> Dict[str, Any]:
        return {"enrich_count": self._enrich_count,
                "buff_data_size": len(self._buff_data),
                "data_version": self._data_version,
                "total_ops": self._op_count}

"""
ARAM Strategy Advisor — ARAM-specific champion evaluation and strategy.
Location: integrations/lol/src/lol_agent/aram_strategy_advisor.py
Reference: Seraphine/app/lol/aram.py, Seraphine/app/lol/tools.py: ChampionSelection
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "integrations.lol.aram_strategy_advisor.v1"
_MELEE_CHAMPIONS = {2,3,5,10,11,23,24,36,48,54,56,57,58,59,62,64,75,76,77,79,80,82,84,86,89,92,98,102,105,111,113,114,120,121,122,131,141,154,157,164,234,240,245,246,254,266,412,421,427,429,516,517,518,555,777,887,897}

class AramStrategyAdvisor:
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def evaluate_champion(self, champion_id: int, aram_data: dict[str, Any]) -> float:
        return min(max(aram_data.get("winRate", 0.5), 0.0), 1.0)

    def should_reroll(self, current_champion: int, bench_champions: list[int],
                      aram_tier_data: dict[int, dict[str, Any]]) -> dict[str, Any]:
        my_tier = aram_tier_data.get(current_champion, {}).get("tier", 3)
        bench_tiers = [aram_tier_data.get(c, {}).get("tier", 3) for c in bench_champions]
        best_bench = min(bench_tiers) if bench_tiers else 99
        if my_tier > 3 and best_bench < my_tier:
            return {"should_reroll": True, "reason": "better_options_on_bench"}
        if my_tier <= 2:
            return {"should_reroll": False, "reason": "current_champion_is_strong"}
        return {"should_reroll": my_tier > 3, "reason": "current_tier_weak"}

    def recommend_bench_swap(self, current_champion: int, bench_champions: list[int],
                             aram_tier_data: dict[int, dict[str, Any]]) -> dict[str, Any]:
        my_tier = aram_tier_data.get(current_champion, {}).get("tier", 3)
        best_champ, best_tier = None, my_tier
        for c in bench_champions:
            c_tier = aram_tier_data.get(c, {}).get("tier", 3)
            if c_tier < best_tier:
                best_tier = c_tier
                best_champ = c
        return {"swap_target": best_champ}

    def recommend_items(self, champion_id: int, aram_builds: list[dict[str, Any]]) -> dict[str, Any]:
        if not aram_builds:
            return {"items": []}
        best = max(aram_builds, key=lambda b: b.get("winRate", 0.0))
        return {"items": best.get("items", [])}

    def snowball_priority(self, champion_id: int) -> float:
        return 0.8 if champion_id in _MELEE_CHAMPIONS else 0.2

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is None:
            return
        enriched = {"module": "aram_strategy_advisor", "timestamp": time.time(), **data}
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

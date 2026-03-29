"""
Item Build Advisor — Context-aware item recommendation engine.

Recommends items based on champion, role, current items, gold budget,
and opponent composition. Scores items using a multi-factor heuristic.

Location: integrations/lol/src/lol_agent/item_build_advisor.py

Reference (拿来主义):
  - leagueoflegends-optimizer: item recommendation pipeline
  - Seraphine/app/lol/tools.py: item utilities
  - integrations/lol-history/src/lol_history/champion_meta.py: meta patterns
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.item_build_advisor.v1"

# Simplified item pool (production would load from data dragon)
_ITEM_POOL: list[dict[str, Any]] = [
    {"name": "Infinity Edge", "cost": 3400, "tags": ["crit", "ad"], "roles": ["ADC", "MID"]},
    {"name": "Kraken Slayer", "cost": 3400, "tags": ["attack_speed", "ad"], "roles": ["ADC"]},
    {"name": "Lord Dominik's Regards", "cost": 3000, "tags": ["armor_pen", "ad"], "roles": ["ADC", "MID"]},
    {"name": "Phantom Dancer", "cost": 2800, "tags": ["attack_speed", "crit"], "roles": ["ADC"]},
    {"name": "Bloodthirster", "cost": 3200, "tags": ["lifesteal", "ad"], "roles": ["ADC"]},
    {"name": "Luden's Tempest", "cost": 3200, "tags": ["ap", "mana"], "roles": ["MID", "SUPPORT"]},
    {"name": "Rabadon's Deathcap", "cost": 3600, "tags": ["ap"], "roles": ["MID"]},
    {"name": "Void Staff", "cost": 2800, "tags": ["magic_pen", "ap"], "roles": ["MID"]},
    {"name": "Zhonya's Hourglass", "cost": 3000, "tags": ["ap", "armor"], "roles": ["MID"]},
    {"name": "Sunfire Aegis", "cost": 3200, "tags": ["tank", "armor", "health"], "roles": ["TOP", "JUNGLE"]},
    {"name": "Thornmail", "cost": 2700, "tags": ["armor", "tank"], "roles": ["TOP", "JUNGLE", "SUPPORT"]},
    {"name": "Gargoyle Stoneplate", "cost": 3200, "tags": ["tank", "armor", "magic_resist"], "roles": ["TOP", "JUNGLE"]},
    {"name": "Boots of Swiftness", "cost": 900, "tags": ["boots"], "roles": ["ADC", "MID", "TOP", "JUNGLE", "SUPPORT"]},
    {"name": "Long Sword", "cost": 350, "tags": ["ad"], "roles": ["ADC", "MID", "TOP", "JUNGLE"]},
    {"name": "Amplifying Tome", "cost": 435, "tags": ["ap"], "roles": ["MID", "SUPPORT"]},
    {"name": "Doran's Blade", "cost": 450, "tags": ["ad", "starter"], "roles": ["ADC", "TOP"]},
]


class ItemBuildAdvisor:
    """Context-aware item recommendation engine.

    Scores and ranks items based on champion role, budget, current
    build, and opponent composition.

    Attributes:
        item_pool: Available item pool.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        item_pool: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._item_pool: list[dict[str, Any]] = list(item_pool or _ITEM_POOL)
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def item_pool(self) -> list[dict[str, Any]]:
        """Current item pool."""
        return self._item_pool

    def score_item(
        self, item_name: str, context: dict[str, Any]
    ) -> float:
        """Score a single item given the build context.

        Factors:
          - Role match (item tags vs champion role)
          - Budget fit (cost vs available gold)
          - Not already owned

        Args:
            item_name: Item name.
            context: Build context dict.

        Returns:
            Score >= 0. Higher is better.
        """
        item = None
        for it in self._item_pool:
            if it["name"] == item_name:
                item = it
                break
        if item is None:
            return 0.0

        # Already owned → 0
        if item_name in context.get("current_items", []):
            return 0.0

        score = 1.0

        # Role match bonus
        role = context.get("role", "").upper()
        if role in item.get("roles", []):
            score += 2.0

        # Budget fit
        gold = context.get("gold", 0)
        if item["cost"] <= gold:
            score += 1.5
        else:
            score *= 0.3  # Penalty for unaffordable

        # Opponent-aware bonus
        opponents = context.get("opponents", [])
        for opp in opponents:
            if opp.get("armor", 0) > 150 and "armor_pen" in item.get("tags", []):
                score += 2.0
            if opp.get("magic_resist", 0) > 150 and "magic_pen" in item.get("tags", []):
                score += 2.0

        return score

    def recommend(
        self, context: dict[str, Any], top_k: int = 3
    ) -> list[str]:
        """Recommend top-k items for the current context.

        Args:
            context: Build context with champion, role, current_items,
                     gold, opponents.
            top_k: Number of items to recommend.

        Returns:
            List of item name strings.
        """
        scored = []
        for item in self._item_pool:
            name = item["name"]
            if name in context.get("current_items", []):
                continue
            s = self.score_item(name, context)
            scored.append((s, name))

        scored.sort(key=lambda x: -x[0])
        result = [name for _, name in scored[:top_k]]

        self._fire_evolution("items_recommended", {
            "champion": context.get("champion", ""),
            "recommended": result,
        })
        return result

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

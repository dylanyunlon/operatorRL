"""
Hero Selector — Draft Recommendation + Team Composition Evaluation.

Provides hero pick/ban recommendations and team composition scoring,
adapted from dota2bot-OpenHyperAI's hero selection logic and
BotLib/hero_*.lua role definitions.

Location: integrations/dota2/src/dota2_agent/hero_selector.py

Reference: dota2bot-OpenHyperAI hero_*.lua + FunLib role tagging.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "dota2_agent.hero_selector.v1"

# Role definitions adapted from dota2bot-OpenHyperAI role tagging
_HERO_ROLES: dict[str, list[str]] = {
    "axe": ["offlane", "initiator", "tank"],
    "crystal_maiden": ["support", "disabler"],
    "juggernaut": ["carry", "pusher"],
    "invoker": ["midlane", "nuker", "disabler"],
    "pudge": ["offlane", "ganker", "tank"],
    "lion": ["support", "disabler", "nuker"],
    "sniper": ["carry", "nuker"],
    "earthshaker": ["support", "initiator", "disabler"],
    "slardar": ["offlane", "carry", "initiator"],
    "phantom_assassin": ["carry", "escape"],
    "rubick": ["support", "nuker"],
    "witch_doctor": ["support", "nuker"],
    "bane": ["support", "disabler"],
    "venomancer": ["support", "pusher", "nuker"],
    "dragon_knight": ["midlane", "carry", "tank", "pusher"],
    "ursa": ["carry", "jungler"],
    "dazzle": ["support", "healer"],
    "enigma": ["offlane", "initiator", "jungler"],
    "omniknight": ["support", "tank", "healer"],
    "abaddon": ["support", "tank"],
    "spectre": ["carry", "escape"],
    "ogre_magi": ["support", "nuker", "tank"],
    "centaur": ["offlane", "initiator", "tank"],
    "mars": ["offlane", "initiator", "tank"],
    "dawnbreaker": ["offlane", "carry", "healer"],
    "meepo": ["midlane", "carry", "pusher"],
    "faceless_void": ["carry", "initiator", "escape"],
    "necrolyte": ["midlane", "carry", "nuker"],
}

# Counter-pick heuristics (simplified from community meta)
_COUNTER_MAP: dict[str, list[str]] = {
    "invoker": ["nyx_assassin", "spirit_breaker", "anti_mage", "silencer"],
    "axe": ["venomancer", "dazzle", "winter_wyvern"],
    "phantom_assassin": ["axe", "lion", "lina"],
    "spectre": ["anti_mage", "nyx_assassin", "spirit_breaker"],
    "meepo": ["earthshaker", "winter_wyvern", "sven"],
}


class HeroSelector:
    """Dota 2 hero draft recommender and composition evaluator.

    Uses role-based composition scoring and counter-pick analysis
    adapted from dota2bot-OpenHyperAI's hero selection patterns.
    """

    def __init__(self) -> None:
        self.hero_pool: dict[str, list[str]] = dict(_HERO_ROLES)
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def recommend_pick(
        self,
        allied_picks: list[str],
        enemy_picks: list[str],
        banned: list[str],
        top_n: int = 5,
    ) -> list[dict[str, Any]]:
        """Recommend heroes for next pick.

        Args:
            allied_picks: Heroes already picked by our team.
            enemy_picks: Heroes already picked by enemy.
            banned: Banned heroes.
            top_n: Number of recommendations.

        Returns:
            List of dicts with hero_name and score.
        """
        excluded = set(allied_picks) | set(enemy_picks) | set(banned)
        candidates = []

        allied_roles = set()
        for h in allied_picks:
            allied_roles.update(self.hero_pool.get(h, []))

        for hero, roles in self.hero_pool.items():
            if hero in excluded:
                continue
            # Score: prefer roles not yet covered
            new_roles = sum(1 for r in roles if r not in allied_roles)
            # Bonus for counter-picking enemy
            counter_bonus = 0
            for enemy in enemy_picks:
                if hero in _COUNTER_MAP.get(enemy, []):
                    counter_bonus += 2
            score = new_roles + counter_bonus
            candidates.append({"hero_name": hero, "score": float(score), "roles": roles})

        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates[:top_n]

    def evaluate_team_composition(self, heroes: list[str]) -> float:
        """Evaluate team composition completeness.

        Checks role coverage: carry, support, initiator, tank, nuker.

        Args:
            heroes: List of hero names.

        Returns:
            Score between 0.0 and 1.0.
        """
        if not heroes:
            return 0.0

        key_roles = {"carry", "support", "initiator", "tank", "nuker"}
        covered = set()
        for h in heroes:
            covered.update(self.hero_pool.get(h, []))

        coverage = len(key_roles & covered) / len(key_roles)
        return min(1.0, coverage)

    def get_hero_roles(self, hero_name: str) -> list[str]:
        """Get roles for a hero.

        Args:
            hero_name: Hero internal name.

        Returns:
            List of role strings, or empty list if unknown.
        """
        return list(self.hero_pool.get(hero_name, []))

    def counter_pick_analysis(self, enemy_hero: str) -> list[str]:
        """Get counter-picks for an enemy hero.

        Args:
            enemy_hero: Enemy hero name.

        Returns:
            List of hero names that counter the enemy.
        """
        return list(_COUNTER_MAP.get(enemy_hero, []))

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

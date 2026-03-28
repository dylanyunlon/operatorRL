"""
Item Database - LoL item registry with build path analysis.

Tracks item gold efficiency, component trees, and optimal build
sequences per champion/role. Used by the strategy engine to
recommend item purchases during recall decisions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ItemCategory(str, Enum):
    STARTER = "starter"
    COMPONENT = "component"
    LEGENDARY = "legendary"
    MYTHIC = "mythic"
    BOOTS = "boots"
    CONSUMABLE = "consumable"
    TRINKET = "trinket"


class ItemStat(str, Enum):
    AD = "attack_damage"
    AP = "ability_power"
    HP = "health"
    MANA = "mana"
    ARMOR = "armor"
    MR = "magic_resist"
    ATTACK_SPEED = "attack_speed"
    CRIT = "crit_chance"
    LETHALITY = "lethality"
    ABILITY_HASTE = "ability_haste"
    LIFE_STEAL = "life_steal"
    OMNIVAMP = "omnivamp"
    MOVE_SPEED = "move_speed"


# Gold value per stat point (for gold efficiency calculation)
_STAT_GOLD_VALUES: dict[ItemStat, float] = {
    ItemStat.AD: 35.0,
    ItemStat.AP: 21.75,
    ItemStat.HP: 2.67,
    ItemStat.MANA: 1.4,
    ItemStat.ARMOR: 20.0,
    ItemStat.MR: 18.0,
    ItemStat.ATTACK_SPEED: 25.0,  # per 1% AS
    ItemStat.CRIT: 40.0,
    ItemStat.LETHALITY: 5.0,
    ItemStat.ABILITY_HASTE: 26.67,
    ItemStat.LIFE_STEAL: 37.5,
    ItemStat.OMNIVAMP: 39.47,
    ItemStat.MOVE_SPEED: 12.0,
}


@dataclass
class ItemData:
    """Complete item information."""
    item_id: int
    name: str
    total_gold: int
    category: ItemCategory = ItemCategory.COMPONENT
    stats: dict[ItemStat, float] = field(default_factory=dict)
    components: list[int] = field(default_factory=list)  # item IDs
    builds_into: list[int] = field(default_factory=list)
    passive_name: str = ""
    passive_description: str = ""
    is_consumable: bool = False
    max_stacks: int = 1

    @property
    def combine_cost(self) -> int:
        """Calculate the combine cost (total - sum of component costs)."""
        # This requires the database to resolve component costs
        return self.total_gold  # Simplified; resolved by database

    def gold_efficiency(self) -> float:
        """Calculate gold efficiency based on raw stat values.

        Returns ratio where 1.0 = 100% gold efficient.
        """
        if self.total_gold <= 0:
            return 0.0
        stat_value = sum(
            amount * _STAT_GOLD_VALUES.get(stat, 0)
            for stat, amount in self.stats.items()
        )
        return stat_value / self.total_gold

    @property
    def is_completed(self) -> bool:
        return self.category in (ItemCategory.LEGENDARY, ItemCategory.MYTHIC)


@dataclass
class BuildPath:
    """Recommended build path for a champion/role."""
    name: str  # e.g., "Standard ADC"
    champion_key: str = ""  # Empty = generic
    items: list[int] = field(default_factory=list)  # Ordered item IDs
    boots_id: int = 0
    situational: list[int] = field(default_factory=list)
    win_rate: float = 0.50
    pick_rate: float = 0.0

    @property
    def core_items(self) -> list[int]:
        """First 3 items (the core build)."""
        return self.items[:3]


class ItemDatabase:
    """Queryable item registry.

    Example::

        db = ItemDatabase()
        db.load_defaults()
        ie = db.get(3031)
        print(f"IE gold efficiency: {ie.gold_efficiency():.0%}")
    """

    def __init__(self) -> None:
        self._items: dict[int, ItemData] = {}
        self._builds: list[BuildPath] = []
        self._name_index: dict[str, int] = {}

    def register(self, item: ItemData) -> None:
        self._items[item.item_id] = item
        self._name_index[item.name.lower()] = item.item_id

    def get(self, item_id: int) -> Optional[ItemData]:
        return self._items.get(item_id)

    def get_by_name(self, name: str) -> Optional[ItemData]:
        item_id = self._name_index.get(name.lower())
        if item_id is not None:
            return self._items.get(item_id)
        # Fuzzy match
        lower = name.lower()
        for stored_name, iid in self._name_index.items():
            if lower in stored_name or stored_name in lower:
                return self._items.get(iid)
        return None

    def get_category(self, category: ItemCategory) -> list[ItemData]:
        return [i for i in self._items.values() if i.category == category]

    def get_components_for(self, item_id: int) -> list[ItemData]:
        """Get all component items for a given item."""
        item = self.get(item_id)
        if not item:
            return []
        return [self._items[c] for c in item.components if c in self._items]

    def calculate_remaining_cost(self, target_id: int, owned_ids: list[int]) -> int:
        """Calculate gold needed to complete an item given owned components."""
        target = self.get(target_id)
        if not target:
            return 0

        owned_set = set(owned_ids)
        remaining = target.total_gold

        for comp_id in target.components:
            if comp_id in owned_set:
                comp = self.get(comp_id)
                if comp:
                    remaining -= comp.total_gold
                    owned_set.discard(comp_id)

        return max(0, remaining)

    def recommend_purchase(self, gold: float, build_path: BuildPath, owned_ids: list[int]) -> list[ItemData]:
        """Recommend items to buy given gold and build path."""
        recommendations: list[ItemData] = []
        remaining_gold = gold

        for item_id in build_path.items:
            if item_id in owned_ids:
                continue
            cost = self.calculate_remaining_cost(item_id, owned_ids)
            if cost <= remaining_gold:
                item = self.get(item_id)
                if item:
                    recommendations.append(item)
                    remaining_gold -= cost
                    break
            else:
                # Can we buy components?
                for comp_id in (self.get(item_id) or ItemData(0, "", 0)).components:
                    if comp_id not in owned_ids:
                        comp = self.get(comp_id)
                        if comp and comp.total_gold <= remaining_gold:
                            recommendations.append(comp)
                            remaining_gold -= comp.total_gold
                break

        return recommendations

    def add_build_path(self, build: BuildPath) -> None:
        self._builds.append(build)

    def get_builds_for(self, champion_key: str) -> list[BuildPath]:
        return [b for b in self._builds if b.champion_key.lower() == champion_key.lower() or b.champion_key == ""]

    @property
    def item_count(self) -> int:
        return len(self._items)

    def load_defaults(self) -> None:
        """Load a minimal item set for testing."""
        defaults = [
            ItemData(1055, "Doran's Blade", 450, ItemCategory.STARTER,
                     {ItemStat.AD: 8, ItemStat.HP: 80, ItemStat.LIFE_STEAL: 2.5}),
            ItemData(1056, "Doran's Ring", 400, ItemCategory.STARTER,
                     {ItemStat.AP: 15, ItemStat.HP: 70, ItemStat.MANA: 50}),
            ItemData(3006, "Berserker's Greaves", 1100, ItemCategory.BOOTS,
                     {ItemStat.ATTACK_SPEED: 35, ItemStat.MOVE_SPEED: 45}),
            ItemData(3020, "Sorcerer's Shoes", 1100, ItemCategory.BOOTS,
                     {ItemStat.MOVE_SPEED: 45}),
            ItemData(3031, "Infinity Edge", 3400, ItemCategory.LEGENDARY,
                     {ItemStat.AD: 70, ItemStat.CRIT: 20}),
            ItemData(3089, "Rabadon's Deathcap", 3600, ItemCategory.LEGENDARY,
                     {ItemStat.AP: 120}),
            ItemData(3071, "Black Cleaver", 3100, ItemCategory.LEGENDARY,
                     {ItemStat.AD: 40, ItemStat.HP: 450, ItemStat.ABILITY_HASTE: 25}),
            ItemData(3153, "Blade of the Ruined King", 3200, ItemCategory.LEGENDARY,
                     {ItemStat.AD: 40, ItemStat.ATTACK_SPEED: 25, ItemStat.LIFE_STEAL: 8}),
            ItemData(3036, "Lord Dominik's Regards", 3000, ItemCategory.LEGENDARY,
                     {ItemStat.AD: 35, ItemStat.CRIT: 20}),
            ItemData(2003, "Health Potion", 50, ItemCategory.CONSUMABLE, is_consumable=True, max_stacks=5),
        ]
        for item in defaults:
            self.register(item)

        # Default ADC build path
        self.add_build_path(BuildPath(
            name="Standard ADC",
            items=[3031, 3153, 3036],
            boots_id=3006,
        ))
        logger.info("Loaded %d default items", len(defaults))

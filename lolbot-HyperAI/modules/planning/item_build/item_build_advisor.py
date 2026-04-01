"""
ItemBuildAdvisor — 上下文感知出装推荐引擎
==========================================
查看 integrations/lol/src/lol_agent/item_build_advisor.py 上现有
多因子出装评分器的实现方式, 理解其模式, 特别是 item_pool 静态数据
和 score_item 评分函数 是如何与游戏状态查询分离的。
可以从 leagueoflegends-optimizer/notebooks/models_2025.ipynb 中
基于真实对局数据的出装路径分析 这个好例子开始。
然后, 遵循该模式实现一个新的 ItemBuildAdvisor (Apollo planning 子模块),
让 PlanningComponent 可以 在每次 Proc() 中根据当前金币/对手阵容/游戏阶段
生成出装建议, 并能 通过 /lol/strategy_advice 频道将推荐结果推送给控制层。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

Architecture position:
    modules/planning/item_build/item_build_advisor.py   ← YOU ARE HERE
    ├─ Called by: planning_component.py
    ├─ Input: GameSnapshot (player items, gold, champion, opponents)
    ├─ Output: List of item recommendations with reasoning
    └─ Reference: integrations/lol/src/lol_agent/item_build_advisor.py

Apollo reference:
    modules/planning/tasks/deciders/ — decision modules for path planning
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    PlayerState,
    TeamSide,
)

logger = get_logger("planning.items")

# ─── Item Database ───────────────────────────────────────────────────────────

class ItemTag(Enum):
    AD = auto()
    AP = auto()
    TANK = auto()
    CRIT = auto()
    ATTACK_SPEED = auto()
    LETHALITY = auto()
    ARMOR_PEN = auto()
    MAGIC_PEN = auto()
    LIFESTEAL = auto()
    ARMOR = auto()
    MAGIC_RESIST = auto()
    HEALTH = auto()
    MANA = auto()
    CDR = auto()
    SUPPORT = auto()
    BOOTS = auto()


@dataclass(frozen=True)
class ItemDef:
    """Static item definition from the item database."""
    item_id: int
    name: str
    cost: int
    tags: Tuple[ItemTag, ...]
    roles: Tuple[str, ...]  # "ADC", "MID", "TOP", "JGL", "SUP"
    stat_value: float = 1.0  # relative power rating
    anti_tank: bool = False
    anti_squishy: bool = False

    @property
    def is_boots(self) -> bool:
        return ItemTag.BOOTS in self.tags


# Simplified item pool — production would load from Data Dragon JSON
_ITEM_DB: List[ItemDef] = [
    # ADC items
    ItemDef(3031, "Infinity Edge", 3400, (ItemTag.AD, ItemTag.CRIT), ("ADC", "MID"), 1.3),
    ItemDef(3153, "Blade of the Ruined King", 3200, (ItemTag.AD, ItemTag.ATTACK_SPEED, ItemTag.LIFESTEAL), ("ADC", "TOP"), 1.1, anti_tank=True),
    ItemDef(3036, "Lord Dominik's Regards", 3000, (ItemTag.AD, ItemTag.ARMOR_PEN), ("ADC",), 1.0, anti_tank=True),
    ItemDef(3094, "Rapid Firecannon", 2800, (ItemTag.CRIT, ItemTag.ATTACK_SPEED), ("ADC",), 0.9),
    ItemDef(3046, "Phantom Dancer", 2800, (ItemTag.CRIT, ItemTag.ATTACK_SPEED), ("ADC",), 0.9),
    # AP items
    ItemDef(3089, "Rabadon's Deathcap", 3600, (ItemTag.AP,), ("MID",), 1.4),
    ItemDef(3135, "Void Staff", 2800, (ItemTag.AP, ItemTag.MAGIC_PEN), ("MID",), 1.0, anti_tank=True),
    ItemDef(3157, "Zhonya's Hourglass", 3250, (ItemTag.AP, ItemTag.ARMOR), ("MID",), 1.1),
    ItemDef(3165, "Morellonomicon", 2500, (ItemTag.AP, ItemTag.HEALTH), ("MID", "SUP"), 0.8),
    # Tank items
    ItemDef(3075, "Thornmail", 2700, (ItemTag.ARMOR, ItemTag.HEALTH, ItemTag.TANK), ("TOP", "JGL", "SUP"), 0.9),
    ItemDef(3065, "Spirit Visage", 2900, (ItemTag.MAGIC_RESIST, ItemTag.HEALTH, ItemTag.TANK), ("TOP", "JGL"), 0.9),
    ItemDef(3143, "Randuin's Omen", 2700, (ItemTag.ARMOR, ItemTag.HEALTH, ItemTag.TANK), ("TOP", "JGL"), 0.9, anti_squishy=False),
    ItemDef(3742, "Dead Man's Plate", 2900, (ItemTag.ARMOR, ItemTag.HEALTH, ItemTag.TANK), ("TOP", "JGL"), 0.85),
    # Boots
    ItemDef(3006, "Berserker's Greaves", 1100, (ItemTag.BOOTS, ItemTag.ATTACK_SPEED), ("ADC",), 0.6),
    ItemDef(3020, "Sorcerer's Shoes", 1100, (ItemTag.BOOTS, ItemTag.MAGIC_PEN), ("MID",), 0.6),
    ItemDef(3047, "Plated Steelcaps", 1100, (ItemTag.BOOTS, ItemTag.ARMOR), ("TOP", "JGL", "SUP"), 0.6),
    ItemDef(3111, "Mercury's Treads", 1100, (ItemTag.BOOTS, ItemTag.MAGIC_RESIST), ("TOP", "JGL", "MID"), 0.6),
    # Support items
    ItemDef(3504, "Ardent Censer", 2300, (ItemTag.AP, ItemTag.SUPPORT), ("SUP",), 0.7),
    ItemDef(3190, "Locket of the Iron Solari", 2500, (ItemTag.SUPPORT, ItemTag.HEALTH), ("SUP",), 0.8),
]

_ITEM_BY_ID: Dict[int, ItemDef] = {item.item_id: item for item in _ITEM_DB}

# Position normalization
_POSITION_ROLE_MAP = {
    "TOP": "TOP", "JUNGLE": "JGL", "MIDDLE": "MID",
    "BOTTOM": "ADC", "UTILITY": "SUP", "": "ADC",
}


@dataclass
class ItemRecommendation:
    """A single item recommendation with reasoning."""
    item: ItemDef
    score: float
    reasoning: str
    priority: int = 0  # lower = buy first
    affordable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item.item_id,
            "name": self.item.name,
            "cost": self.item.cost,
            "score": round(self.score, 2),
            "reasoning": self.reasoning,
            "priority": self.priority,
            "affordable": self.affordable,
        }


class ItemBuildAdvisor:
    """Context-aware item recommendation engine.

    Scores candidate items based on:
    - Role appropriateness (ADC items for ADC, etc.)
    - Gold efficiency (can the player afford it?)
    - Enemy composition (anti-tank if enemies are tanky)
    - Current item slots (avoid duplicates, fill gaps)
    - Game phase (early = components, late = full items)

    Usage::

        advisor = ItemBuildAdvisor()
        recs = advisor.recommend(snapshot)
        for rec in recs[:3]:
            print(f"Buy {rec.item.name}: {rec.reasoning}")
    """

    def __init__(self) -> None:
        self._recommendation_count: int = 0
        self._last_recommendation_time: float = 0.0

    def recommend(
        self,
        snapshot: GameSnapshot,
        max_recommendations: int = 3,
    ) -> List[ItemRecommendation]:
        """Generate item recommendations for the active player.

        Args:
            snapshot: Current game snapshot.
            max_recommendations: Max items to suggest.

        Returns:
            Ranked list of ItemRecommendation objects.
        """
        active = snapshot.active_player
        if active is None:
            return []

        self._recommendation_count += 1
        role = _POSITION_ROLE_MAP.get(active.position, "ADC")
        current_gold = active.current_gold
        owned_ids = set(active.items.item_ids)
        enemy_team = snapshot.enemy_team
        phase = snapshot.phase

        # Analyze enemy composition
        enemy_armor_avg = self._avg_enemy_stat(enemy_team, "armor")
        enemy_mr_avg = self._avg_enemy_stat(enemy_team, "magic_resist")
        enemy_health_avg = self._avg_enemy_stat(enemy_team, "max_health")
        enemies_are_tanky = enemy_armor_avg > 100 or enemy_health_avg > 2500

        # Check if player needs boots
        has_boots = any(
            _ITEM_BY_ID.get(iid, ItemDef(0, "", 0, (), ())).is_boots
            for iid in owned_ids
        )

        candidates: List[ItemRecommendation] = []

        for item in _ITEM_DB:
            # Skip already owned
            if item.item_id in owned_ids:
                continue

            # Skip boots if already have boots
            if item.is_boots and has_boots:
                continue

            score = self._score_item(
                item, role, current_gold, phase,
                enemies_are_tanky, enemy_armor_avg, enemy_mr_avg,
                owned_ids, has_boots,
            )

            if score <= 0:
                continue

            reasoning = self._build_reasoning(
                item, role, current_gold, enemies_are_tanky, phase
            )

            candidates.append(ItemRecommendation(
                item=item,
                score=score,
                reasoning=reasoning,
                affordable=current_gold >= item.cost,
            ))

        # Sort by score descending
        candidates.sort(key=lambda r: r.score, reverse=True)

        # Assign priority
        for i, rec in enumerate(candidates):
            rec.priority = i + 1

        return candidates[:max_recommendations]

    def _score_item(
        self,
        item: ItemDef,
        role: str,
        gold: float,
        phase: GamePhase,
        enemies_tanky: bool,
        enemy_armor: float,
        enemy_mr: float,
        owned_ids: set,
        has_boots: bool,
    ) -> float:
        """Multi-factor item scoring.

        Returns:
            Score > 0 if recommended, <= 0 if not.
        """
        score = item.stat_value

        # Role match (critical)
        if role in item.roles:
            score += 2.0
        elif any(r in item.roles for r in ("TOP", "JGL", "MID", "ADC", "SUP")):
            score -= 1.0  # penalty for off-role

        # Affordability bonus
        if gold >= item.cost:
            score += 1.0
        elif gold >= item.cost * 0.7:
            score += 0.3  # close to affording
        else:
            score -= 0.5

        # Anti-tank bonus when enemies are tanky
        if enemies_tanky and item.anti_tank:
            score += 1.5

        # Armor pen value scales with enemy armor
        if ItemTag.ARMOR_PEN in item.tags:
            score += enemy_armor / 200.0  # 0.5 bonus at 100 armor

        # Magic pen value scales with enemy MR
        if ItemTag.MAGIC_PEN in item.tags:
            score += enemy_mr / 200.0

        # Boots priority if none owned
        if item.is_boots and not has_boots and phase != GamePhase.LOADING:
            score += 1.5

        # Phase adjustments
        if phase == GamePhase.EARLY:
            if item.cost > 3000:
                score -= 0.5  # prefer cheaper early
        elif phase == GamePhase.LATE:
            if item.cost < 2000 and not item.is_boots:
                score -= 0.5  # prefer big items late

        return score

    def _build_reasoning(
        self,
        item: ItemDef,
        role: str,
        gold: float,
        enemies_tanky: bool,
        phase: GamePhase,
    ) -> str:
        """Build human-readable reasoning for a recommendation."""
        parts: List[str] = []

        if role in item.roles:
            parts.append(f"core {role} item")

        if enemies_tanky and item.anti_tank:
            parts.append("counters tanky enemies")

        if gold >= item.cost:
            parts.append(f"affordable ({item.cost}g)")
        else:
            parts.append(f"need {item.cost - int(gold)}g more")

        if item.is_boots:
            parts.append("mobility")

        if not parts:
            parts.append("good stat efficiency")

        return " | ".join(parts)

    def _avg_enemy_stat(self, team: Any, stat: str) -> float:
        """Compute average of a stat across enemy team."""
        values = [getattr(p, stat, 0.0) for p in team.players]
        if not values:
            return 0.0
        return sum(values) / len(values)

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "recommendation_count": self._recommendation_count,
            "item_db_size": len(_ITEM_DB),
        }

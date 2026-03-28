"""
Champion Database - Static champion data, roles, and matchup information.

Provides a queryable registry of all LoL champions with their base stats,
roles, damage profiles, and synergy/counter relationships. Data is loaded
from a bundled JSON file or fetched from Data Dragon on first use.

This is intentionally a pure-data module with no network I/O at import
time; the ``load`` function is called explicitly by the application.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DamageType(str, Enum):
    PHYSICAL = "physical"
    MAGIC = "magic"
    TRUE = "true"
    MIXED = "mixed"


class ChampionRole(str, Enum):
    ASSASSIN = "Assassin"
    FIGHTER = "Fighter"
    MAGE = "Mage"
    MARKSMAN = "Marksman"
    SUPPORT = "Support"
    TANK = "Tank"


class LaneAssignment(str, Enum):
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MIDDLE"
    BOT = "BOTTOM"
    SUPPORT = "UTILITY"


@dataclass(frozen=True)
class ChampionBaseStats:
    """Level-1 base statistics for a champion."""
    hp: float = 580.0
    hp_per_level: float = 95.0
    mp: float = 300.0
    mp_per_level: float = 40.0
    armor: float = 30.0
    armor_per_level: float = 4.2
    mr: float = 30.0
    mr_per_level: float = 1.3
    ad: float = 60.0
    ad_per_level: float = 3.0
    attack_speed: float = 0.625
    attack_speed_per_level: float = 2.5
    move_speed: float = 330.0
    attack_range: float = 125.0

    def at_level(self, level: int) -> dict[str, float]:
        """Calculate stats at a given level."""
        lvl = max(1, min(level, 18)) - 1
        return {
            "hp": self.hp + self.hp_per_level * lvl,
            "mp": self.mp + self.mp_per_level * lvl,
            "armor": self.armor + self.armor_per_level * lvl,
            "mr": self.mr + self.mr_per_level * lvl,
            "ad": self.ad + self.ad_per_level * lvl,
            "attack_speed": self.attack_speed * (1 + self.attack_speed_per_level * lvl / 100),
            "move_speed": self.move_speed,
            "attack_range": self.attack_range,
        }


@dataclass
class ChampionInfo:
    """Complete champion information."""
    champion_id: int
    name: str
    key: str  # Internal key e.g. "Aatrox"
    title: str = ""
    roles: list[ChampionRole] = field(default_factory=list)
    primary_damage: DamageType = DamageType.PHYSICAL
    lanes: list[LaneAssignment] = field(default_factory=list)
    base_stats: ChampionBaseStats = field(default_factory=ChampionBaseStats)
    difficulty: int = 5  # 1-10 scale
    is_ranged: bool = False

    # Win rate thresholds from optimizer data
    avg_win_rate: float = 0.50
    play_rate: float = 0.02

    @property
    def is_melee(self) -> bool:
        return not self.is_ranged

    def plays_lane(self, lane: LaneAssignment) -> bool:
        return lane in self.lanes

    def has_role(self, role: ChampionRole) -> bool:
        return role in self.roles


@dataclass
class MatchupData:
    """Win-rate data for a champion vs. champion matchup."""
    champion: str
    opponent: str
    win_rate: float = 0.50
    sample_size: int = 0
    gold_diff_at_15: float = 0.0
    kill_diff_at_15: float = 0.0
    lane: Optional[LaneAssignment] = None

    @property
    def is_favorable(self) -> bool:
        return self.win_rate >= 0.52

    @property
    def is_unfavorable(self) -> bool:
        return self.win_rate <= 0.48

    @property
    def confidence(self) -> str:
        if self.sample_size >= 10000:
            return "high"
        elif self.sample_size >= 1000:
            return "medium"
        return "low"


class ChampionDatabase:
    """Queryable champion registry.

    Example::

        db = ChampionDatabase()
        db.load_defaults()
        aatrox = db.get("Aatrox")
        print(aatrox.base_stats.at_level(6))
        tops = db.get_by_lane(LaneAssignment.TOP)
    """

    def __init__(self) -> None:
        self._champions: dict[str, ChampionInfo] = {}
        self._matchups: dict[str, list[MatchupData]] = {}
        self._loaded = False

    def register(self, champion: ChampionInfo) -> None:
        self._champions[champion.key] = champion

    def get(self, name: str) -> Optional[ChampionInfo]:
        """Get champion by key or name (case-insensitive)."""
        # Try exact key first
        if name in self._champions:
            return self._champions[name]
        # Case-insensitive search
        lower = name.lower()
        for key, champ in self._champions.items():
            if key.lower() == lower or champ.name.lower() == lower:
                return champ
        return None

    def get_by_lane(self, lane: LaneAssignment) -> list[ChampionInfo]:
        return [c for c in self._champions.values() if c.plays_lane(lane)]

    def get_by_role(self, role: ChampionRole) -> list[ChampionInfo]:
        return [c for c in self._champions.values() if c.has_role(role)]

    def get_by_damage_type(self, damage: DamageType) -> list[ChampionInfo]:
        return [c for c in self._champions.values() if c.primary_damage == damage]

    def get_matchup(
        self, champion: str, opponent: str, lane: Optional[LaneAssignment] = None,
    ) -> Optional[MatchupData]:
        """Get matchup data for champion vs opponent."""
        matchups = self._matchups.get(champion.lower(), [])
        for m in matchups:
            if m.opponent.lower() == opponent.lower():
                if lane is None or m.lane == lane:
                    return m
        return None

    def add_matchup(self, matchup: MatchupData) -> None:
        key = matchup.champion.lower()
        if key not in self._matchups:
            self._matchups[key] = []
        self._matchups[key].append(matchup)

    def get_counters(self, champion: str, top_n: int = 5) -> list[MatchupData]:
        """Get worst matchups (counters) for a champion."""
        matchups = self._matchups.get(champion.lower(), [])
        return sorted(matchups, key=lambda m: m.win_rate)[:top_n]

    def get_favorable(self, champion: str, top_n: int = 5) -> list[MatchupData]:
        """Get best matchups for a champion."""
        matchups = self._matchups.get(champion.lower(), [])
        return sorted(matchups, key=lambda m: m.win_rate, reverse=True)[:top_n]

    @property
    def champion_count(self) -> int:
        return len(self._champions)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def all_names(self) -> list[str]:
        return sorted(self._champions.keys())

    def load_defaults(self) -> None:
        """Load a minimal set of popular champions for testing."""
        defaults = [
            ChampionInfo(1, "Annie", "Annie", "the Dark Child",
                         [ChampionRole.MAGE], DamageType.MAGIC,
                         [LaneAssignment.MID, LaneAssignment.SUPPORT],
                         ChampionBaseStats(hp=524, ad=50.41, attack_range=625),
                         difficulty=2, is_ranged=True),
            ChampionInfo(266, "Aatrox", "Aatrox", "the Darkin Blade",
                         [ChampionRole.FIGHTER], DamageType.PHYSICAL,
                         [LaneAssignment.TOP],
                         ChampionBaseStats(hp=650, ad=60, attack_range=175),
                         difficulty=4),
            ChampionInfo(64, "Lee Sin", "LeeSin", "the Blind Monk",
                         [ChampionRole.FIGHTER, ChampionRole.ASSASSIN], DamageType.PHYSICAL,
                         [LaneAssignment.JUNGLE],
                         ChampionBaseStats(hp=645, ad=66, attack_range=125),
                         difficulty=8),
            ChampionInfo(222, "Jinx", "Jinx", "the Loose Cannon",
                         [ChampionRole.MARKSMAN], DamageType.PHYSICAL,
                         [LaneAssignment.BOT],
                         ChampionBaseStats(hp=610, ad=57, attack_range=525),
                         difficulty=3, is_ranged=True),
            ChampionInfo(412, "Thresh", "Thresh", "the Chain Warden",
                         [ChampionRole.SUPPORT, ChampionRole.FIGHTER], DamageType.MAGIC,
                         [LaneAssignment.SUPPORT],
                         ChampionBaseStats(hp=600, ad=56, attack_range=450),
                         difficulty=7, is_ranged=True),
            ChampionInfo(157, "Yasuo", "Yasuo", "the Unforgiven",
                         [ChampionRole.FIGHTER, ChampionRole.ASSASSIN], DamageType.PHYSICAL,
                         [LaneAssignment.MID, LaneAssignment.TOP],
                         ChampionBaseStats(hp=590, ad=60, attack_range=175),
                         difficulty=10),
            ChampionInfo(103, "Ahri", "Ahri", "the Nine-Tailed Fox",
                         [ChampionRole.MAGE, ChampionRole.ASSASSIN], DamageType.MAGIC,
                         [LaneAssignment.MID],
                         ChampionBaseStats(hp=526, ad=53, attack_range=550),
                         difficulty=5, is_ranged=True),
            ChampionInfo(236, "Lucian", "Lucian", "the Purifier",
                         [ChampionRole.MARKSMAN], DamageType.PHYSICAL,
                         [LaneAssignment.BOT, LaneAssignment.MID],
                         ChampionBaseStats(hp=641, ad=60, attack_range=500),
                         difficulty=6, is_ranged=True),
        ]

        for champ in defaults:
            self.register(champ)
        self._loaded = True
        logger.info("Loaded %d default champions", len(defaults))

    def load_from_dict(self, data: dict[str, Any]) -> int:
        """Load champions from a dictionary (e.g., parsed JSON)."""
        count = 0
        for key, info in data.items():
            try:
                roles = [ChampionRole(r) for r in info.get("roles", [])]
                lanes = [LaneAssignment(l) for l in info.get("lanes", [])]
                stats_data = info.get("stats", {})
                base_stats = ChampionBaseStats(**{
                    k: float(v) for k, v in stats_data.items()
                    if hasattr(ChampionBaseStats, k)
                })
                champ = ChampionInfo(
                    champion_id=info.get("id", 0),
                    name=info.get("name", key),
                    key=key,
                    title=info.get("title", ""),
                    roles=roles,
                    primary_damage=DamageType(info.get("damage_type", "physical")),
                    lanes=lanes,
                    base_stats=base_stats,
                    difficulty=info.get("difficulty", 5),
                    is_ranged=info.get("is_ranged", False),
                )
                self.register(champ)
                count += 1
            except Exception as e:
                logger.warning("Failed to load champion %s: %s", key, e)
        self._loaded = True
        return count

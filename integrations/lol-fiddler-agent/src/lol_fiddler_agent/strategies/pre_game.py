"""
Pre-Game Analyzer - Champion select and loading screen analysis.

Provides team composition analysis, matchup advice, and rune/item
recommendations during champion select phase (detected via LCU
traffic through Fiddler).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.models.champion_db import ChampionDatabase, ChampionInfo, LaneAssignment
from lol_fiddler_agent.models.item_db import ItemDatabase, BuildPath
from lol_fiddler_agent.models.rune_analyzer import RuneAnalyzer, RuneRecommendation

logger = logging.getLogger(__name__)


@dataclass
class PreGameAdvice:
    """Comprehensive pre-game recommendation package."""
    champion: str = ""
    lane: str = ""
    rune_recommendations: list[RuneRecommendation] = field(default_factory=list)
    build_path: Optional[BuildPath] = None
    matchup_notes: list[str] = field(default_factory=list)
    team_comp_notes: list[str] = field(default_factory=list)
    early_game_plan: str = ""
    win_condition: str = ""


class PreGameAnalyzer:
    """Analyzes champion select state and generates pre-game advice.

    Example::

        analyzer = PreGameAnalyzer()
        advice = analyzer.analyze(
            my_champion="Jinx",
            my_lane="BOTTOM",
            ally_champions=["Aatrox", "LeeSin", "Ahri", "Thresh"],
            enemy_champions=["Yasuo", "Graves", "Syndra", "Lucian", "Nautilus"],
        )
    """

    def __init__(
        self,
        champion_db: Optional[ChampionDatabase] = None,
        item_db: Optional[ItemDatabase] = None,
        rune_analyzer: Optional[RuneAnalyzer] = None,
    ) -> None:
        self._champ_db = champion_db or ChampionDatabase()
        self._item_db = item_db or ItemDatabase()
        self._rune_analyzer = rune_analyzer or RuneAnalyzer()

        if not self._champ_db.is_loaded:
            self._champ_db.load_defaults()
        if self._item_db.item_count == 0:
            self._item_db.load_defaults()

    def analyze(
        self,
        my_champion: str,
        my_lane: str = "",
        ally_champions: Optional[list[str]] = None,
        enemy_champions: Optional[list[str]] = None,
    ) -> PreGameAdvice:
        """Generate comprehensive pre-game advice."""
        allies = ally_champions or []
        enemies = enemy_champions or []

        champ = self._champ_db.get(my_champion)
        roles = [r.value for r in champ.roles] if champ else []

        advice = PreGameAdvice(
            champion=my_champion,
            lane=my_lane,
        )

        # Rune recommendations
        lane_opponent = self._find_lane_opponent(my_lane, enemies)
        advice.rune_recommendations = self._rune_analyzer.recommend(
            my_champion, roles=roles, opponent=lane_opponent,
        )

        # Build path
        builds = self._item_db.get_builds_for(my_champion)
        if builds:
            advice.build_path = builds[0]

        # Matchup notes
        if lane_opponent:
            advice.matchup_notes = self._generate_matchup_notes(my_champion, lane_opponent, champ)

        # Team comp notes
        advice.team_comp_notes = self._generate_comp_notes(
            my_champion, allies, enemies,
        )

        # Game plan
        advice.early_game_plan = self._generate_early_plan(my_champion, my_lane, champ)
        advice.win_condition = self._generate_win_condition(
            my_champion, allies, enemies,
        )

        return advice

    def _find_lane_opponent(self, my_lane: str, enemies: list[str]) -> Optional[str]:
        """Attempt to identify the lane opponent."""
        if not my_lane or not enemies:
            return None
        lane_enum = None
        try:
            lane_enum = LaneAssignment(my_lane)
        except ValueError:
            return None

        for enemy_name in enemies:
            champ = self._champ_db.get(enemy_name)
            if champ and champ.plays_lane(lane_enum):
                return enemy_name
        return enemies[0] if enemies else None

    def _generate_matchup_notes(
        self, my_champ: str, opponent: str, my_info: Optional[ChampionInfo],
    ) -> list[str]:
        notes: list[str] = []
        opp_info = self._champ_db.get(opponent)

        if my_info and opp_info:
            if my_info.is_ranged and opp_info.is_melee:
                notes.append(f"Range advantage vs {opponent} - poke in lane")
            elif my_info.is_melee and opp_info.is_ranged:
                notes.append(f"{opponent} has range advantage - use bushes and all-in windows")

            matchup = self._champ_db.get_matchup(my_champ, opponent)
            if matchup:
                if matchup.is_favorable:
                    notes.append(f"Favorable matchup ({matchup.win_rate:.0%} WR)")
                elif matchup.is_unfavorable:
                    notes.append(f"Difficult matchup ({matchup.win_rate:.0%} WR) - play safe early")
        else:
            notes.append(f"Facing {opponent} - watch for their power spikes")

        return notes

    def _generate_comp_notes(
        self, my_champ: str, allies: list[str], enemies: list[str],
    ) -> list[str]:
        notes: list[str] = []
        # Simple comp analysis
        ally_tanks = sum(1 for a in allies if self._is_role(a, "Tank"))
        ally_carries = sum(1 for a in allies if self._is_role(a, "Marksman"))

        if ally_tanks == 0:
            notes.append("No frontline on team - positioning is critical")
        if ally_carries >= 2:
            notes.append("Multiple carries - team fights should favor us late game")

        enemy_assassins = sum(1 for e in enemies if self._is_role(e, "Assassin"))
        if enemy_assassins >= 2:
            notes.append("Multiple enemy assassins - group and peel for carries")

        return notes

    def _generate_early_plan(
        self, champion: str, lane: str, info: Optional[ChampionInfo],
    ) -> str:
        if info and info.difficulty >= 7:
            return f"Focus on learning {champion}'s mechanics. Farm safely until comfortable."
        if lane == "JUNGLE":
            return "Full clear → scuttle → look for gank opportunities"
        if lane in ("BOTTOM", "UTILITY"):
            return "Focus on CS and trading in lane. Ward river at 2:30 for jungler."
        return "Farm safely, look for trades when cooldowns are up."

    def _generate_win_condition(
        self, my_champ: str, allies: list[str], enemies: list[str],
    ) -> str:
        all_allies = [my_champ] + allies
        ally_info = [self._champ_db.get(a) for a in all_allies]
        marksman_count = sum(1 for i in ally_info if i and any(r.value == "Marksman" for r in i.roles))

        if marksman_count >= 2:
            return "Scale to late game and win through sustained DPS in team fights"
        fighter_count = sum(1 for i in ally_info if i and any(r.value == "Fighter" for r in i.roles))
        if fighter_count >= 2:
            return "Press early-mid game advantage through skirmishes and split pushing"
        return "Control objectives and look for picks around vision"

    def _is_role(self, champion_name: str, role: str) -> bool:
        champ = self._champ_db.get(champion_name)
        if champ:
            return any(r.value == role for r in champ.roles)
        return False

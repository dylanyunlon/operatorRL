"""
Rune Analyzer - Rune page analysis and strategic recommendations.

Evaluates rune choices in context of champion, lane matchup, and
team composition to provide pre-game and adaptive rune advice.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RuneTreeType(str, Enum):
    PRECISION = "Precision"
    DOMINATION = "Domination"
    SORCERY = "Sorcery"
    RESOLVE = "Resolve"
    INSPIRATION = "Inspiration"


class KeystoneType(str, Enum):
    # Precision
    PRESS_THE_ATTACK = "Press the Attack"
    LETHAL_TEMPO = "Lethal Tempo"
    FLEET_FOOTWORK = "Fleet Footwork"
    CONQUEROR = "Conqueror"
    # Domination
    ELECTROCUTE = "Electrocute"
    PREDATOR = "Predator"
    DARK_HARVEST = "Dark Harvest"
    HAIL_OF_BLADES = "Hail of Blades"
    # Sorcery
    SUMMON_AERY = "Summon Aery"
    ARCANE_COMET = "Arcane Comet"
    PHASE_RUSH = "Phase Rush"
    # Resolve
    GRASP = "Grasp of the Undying"
    AFTERSHOCK = "Aftershock"
    GUARDIAN = "Guardian"
    # Inspiration
    GLACIAL_AUGMENT = "Glacial Augment"
    UNSEALED_SPELLBOOK = "Unsealed Spellbook"
    FIRST_STRIKE = "First Strike"


@dataclass
class RunePageInfo:
    """Parsed rune page configuration."""
    keystone: str = ""
    primary_tree: RuneTreeType = RuneTreeType.PRECISION
    secondary_tree: RuneTreeType = RuneTreeType.DOMINATION
    primary_runes: list[str] = field(default_factory=list)
    secondary_runes: list[str] = field(default_factory=list)
    stat_shards: list[str] = field(default_factory=list)


@dataclass
class RuneRecommendation:
    """A rune page recommendation with context."""
    keystone: str
    primary_tree: RuneTreeType
    secondary_tree: RuneTreeType
    reason: str
    win_rate: float = 0.50
    pick_rate: float = 0.0
    matchup_specific: bool = False
    opponent_champion: str = ""

    def to_display(self) -> str:
        wr_str = f"{self.win_rate:.1%}" if self.win_rate > 0 else "N/A"
        return f"{self.keystone} ({self.primary_tree.value}/{self.secondary_tree.value}) WR: {wr_str} - {self.reason}"


# Keystone → champion role affinity mapping
_KEYSTONE_ROLE_AFFINITY: dict[str, list[str]] = {
    KeystoneType.CONQUEROR.value: ["Fighter", "Assassin"],
    KeystoneType.LETHAL_TEMPO.value: ["Marksman", "Fighter"],
    KeystoneType.PRESS_THE_ATTACK.value: ["Marksman", "Assassin"],
    KeystoneType.FLEET_FOOTWORK.value: ["Marksman", "Fighter"],
    KeystoneType.ELECTROCUTE.value: ["Assassin", "Mage"],
    KeystoneType.DARK_HARVEST.value: ["Assassin", "Mage"],
    KeystoneType.ARCANE_COMET.value: ["Mage", "Support"],
    KeystoneType.SUMMON_AERY.value: ["Mage", "Support"],
    KeystoneType.GRASP.value: ["Tank", "Fighter"],
    KeystoneType.AFTERSHOCK.value: ["Tank", "Support"],
    KeystoneType.GUARDIAN.value: ["Support"],
    KeystoneType.FIRST_STRIKE.value: ["Mage", "Assassin"],
}


class RuneAnalyzer:
    """Analyzes and recommends rune configurations.

    Example::

        analyzer = RuneAnalyzer()
        recs = analyzer.recommend("Yasuo", roles=["Fighter", "Assassin"])
        for rec in recs:
            print(rec.to_display())
    """

    def __init__(self) -> None:
        self._custom_overrides: dict[str, RuneRecommendation] = {}

    def analyze_page(self, page: RunePageInfo, champion: str, roles: list[str]) -> dict[str, Any]:
        """Evaluate how well a rune page fits a champion."""
        score = 0.0
        issues: list[str] = []
        strengths: list[str] = []

        # Check keystone affinity
        affinity = _KEYSTONE_ROLE_AFFINITY.get(page.keystone, [])
        role_match = any(r in affinity for r in roles)
        if role_match:
            score += 0.4
            strengths.append(f"{page.keystone} synergizes well with {'/'.join(roles)}")
        else:
            score += 0.1
            issues.append(f"{page.keystone} is unusual for {'/'.join(roles)}")

        # Check tree synergy
        if page.primary_tree == RuneTreeType.PRECISION and "Marksman" in roles:
            score += 0.2
            strengths.append("Precision tree provides AS and sustained damage")
        elif page.primary_tree == RuneTreeType.DOMINATION and "Assassin" in roles:
            score += 0.2
            strengths.append("Domination tree enhances burst damage")
        elif page.primary_tree == RuneTreeType.SORCERY and "Mage" in roles:
            score += 0.2
            strengths.append("Sorcery tree amplifies ability damage")
        elif page.primary_tree == RuneTreeType.RESOLVE and "Tank" in roles:
            score += 0.2
            strengths.append("Resolve tree enhances durability")
        else:
            score += 0.1

        # Secondary tree bonus
        score += 0.1  # Some value for any secondary

        return {
            "score": min(1.0, score),
            "grade": _score_to_grade(score),
            "strengths": strengths,
            "issues": issues,
            "champion": champion,
        }

    def recommend(
        self,
        champion: str,
        roles: Optional[list[str]] = None,
        opponent: Optional[str] = None,
        lane: Optional[str] = None,
    ) -> list[RuneRecommendation]:
        """Generate rune recommendations for a champion."""
        roles = roles or []
        recommendations: list[RuneRecommendation] = []

        # Check custom overrides
        if champion in self._custom_overrides:
            recommendations.append(self._custom_overrides[champion])

        # Generate role-based recommendations
        for keystone, affinities in _KEYSTONE_ROLE_AFFINITY.items():
            overlap = set(roles) & set(affinities)
            if overlap:
                primary = _keystone_to_tree(keystone)
                secondary = _best_secondary(primary, roles)
                rec = RuneRecommendation(
                    keystone=keystone,
                    primary_tree=primary,
                    secondary_tree=secondary,
                    reason=f"Strong for {', '.join(overlap)} champions",
                    win_rate=0.50 + len(overlap) * 0.01,
                )
                if opponent:
                    rec.matchup_specific = True
                    rec.opponent_champion = opponent
                recommendations.append(rec)

        # Sort by win rate, deduplicate by keystone
        seen: set[str] = set()
        unique: list[RuneRecommendation] = []
        for rec in sorted(recommendations, key=lambda r: -r.win_rate):
            if rec.keystone not in seen:
                seen.add(rec.keystone)
                unique.append(rec)

        return unique[:3]

    def set_override(self, champion: str, rec: RuneRecommendation) -> None:
        self._custom_overrides[champion] = rec


def _score_to_grade(score: float) -> str:
    if score >= 0.8:
        return "S"
    elif score >= 0.6:
        return "A"
    elif score >= 0.4:
        return "B"
    elif score >= 0.2:
        return "C"
    return "D"


def _keystone_to_tree(keystone: str) -> RuneTreeType:
    tree_map: dict[str, RuneTreeType] = {
        **{k.value: RuneTreeType.PRECISION for k in
           [KeystoneType.PRESS_THE_ATTACK, KeystoneType.LETHAL_TEMPO,
            KeystoneType.FLEET_FOOTWORK, KeystoneType.CONQUEROR]},
        **{k.value: RuneTreeType.DOMINATION for k in
           [KeystoneType.ELECTROCUTE, KeystoneType.PREDATOR,
            KeystoneType.DARK_HARVEST, KeystoneType.HAIL_OF_BLADES]},
        **{k.value: RuneTreeType.SORCERY for k in
           [KeystoneType.SUMMON_AERY, KeystoneType.ARCANE_COMET,
            KeystoneType.PHASE_RUSH]},
        **{k.value: RuneTreeType.RESOLVE for k in
           [KeystoneType.GRASP, KeystoneType.AFTERSHOCK, KeystoneType.GUARDIAN]},
        **{k.value: RuneTreeType.INSPIRATION for k in
           [KeystoneType.GLACIAL_AUGMENT, KeystoneType.UNSEALED_SPELLBOOK,
            KeystoneType.FIRST_STRIKE]},
    }
    return tree_map.get(keystone, RuneTreeType.PRECISION)


def _best_secondary(primary: RuneTreeType, roles: list[str]) -> RuneTreeType:
    """Select the best secondary tree based on primary and roles."""
    # Common secondary pairings
    pairings: dict[RuneTreeType, RuneTreeType] = {
        RuneTreeType.PRECISION: RuneTreeType.DOMINATION,
        RuneTreeType.DOMINATION: RuneTreeType.PRECISION,
        RuneTreeType.SORCERY: RuneTreeType.INSPIRATION,
        RuneTreeType.RESOLVE: RuneTreeType.PRECISION,
        RuneTreeType.INSPIRATION: RuneTreeType.SORCERY,
    }
    return pairings.get(primary, RuneTreeType.RESOLVE)

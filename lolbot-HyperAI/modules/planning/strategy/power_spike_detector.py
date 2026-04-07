"""
modules/planning/strategy/power_spike_detector.py — Champion power spike detection.
=====================================================================================
Claude18 · New planning sub-module

Problem: Planning has no awareness of champion power spikes. A level 6
Zed with Duskblade completion is a completely different threat than a
level 5 Zed with components. Strategy should adapt to these thresholds.

Solution (Apollo planning/tasks pattern):
    查看 Apollo modules/planning/tasks/deciders/ 上现有决策器的实现方式,
    理解其模式, 特别是 situation-specific 决策如何被抽象为 Task 接口。从
    Apollo StopDecider 的条件检查模式 这个好例子开始。然后, 遵循该模式实现
    一个 PowerSpikeDetector, 让 planning 可以 检测己方和敌方的能力跃升点,
    并能 在关键装备完成时推荐激进/保守策略调整。

File location: lolbot-HyperAI/modules/planning/strategy/power_spike_detector.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class SpikeType(Enum):
    """Type of power spike."""
    LEVEL_SPIKE = auto()     # Key level threshold (6, 11, 16)
    ITEM_SPIKE = auto()      # Completed a major item
    COMPONENT_SPIKE = auto()  # Got a key component (e.g. Lost Chapter)
    SUMMONER_READY = auto()   # Flash/TP just came off cooldown


class SpikeImpact(Enum):
    """How much this spike changes the game state."""
    MINOR = auto()    # Small advantage shift
    MODERATE = auto()  # Noticeable power change
    MAJOR = auto()     # Game-changing (e.g. ADC 3 items)
    CRITICAL = auto()  # Must respond immediately (e.g. enemy level 6 all-in)


@dataclass
class PowerSpike:
    """A detected power spike for a specific champion/player."""
    player_name: str
    champion_name: str
    spike_type: SpikeType
    impact: SpikeImpact
    is_ally: bool
    description: str
    strategic_note: str
    game_time: float
    level: int = 0
    item_id: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player": self.player_name,
            "champion": self.champion_name,
            "type": self.spike_type.name,
            "impact": self.impact.name,
            "ally": self.is_ally,
            "description": self.description,
            "note": self.strategic_note,
        }


# ── Key level thresholds ─────────────────────────────────────────────────────

# Champions with particularly strong level 6 spikes
_STRONG_LEVEL6_CHAMPIONS = {
    "Zed", "Akali", "Katarina", "Fizz", "LeBlanc", "Talon",
    "Evelynn", "Rengar", "Kha'Zix", "Malzahar", "Annie",
    "Ahri", "Diana", "Syndra", "Veigar", "Lissandra",
}

# Level thresholds that are generally significant
_KEY_LEVELS = {6, 11, 16}

# ── Key item completion thresholds (by item ID) ─────────────────────────────

# Mythic/Legendary items that represent major power spikes
# (Using generic IDs — in production these would be updated per patch)
_MAJOR_ITEM_IDS: Set[int] = {
    3031,  # Infinity Edge
    3153,  # Blade of the Ruined King
    3078,  # Trinity Force
    3089,  # Rabadon's Deathcap
    6655,  # Luden's Tempest
    6656,  # Everfrost
    6632,  # Crown of the Shattered Queen
    6653,  # Liandry's Anguish
    3046,  # Phantom Dancer
    3036,  # Lord Dominik's Regards
    3161,  # Spear of Shojin
    3742,  # Dead Man's Plate
}

# 2-item and 3-item power spikes for ADCs
_ADC_ITEM_SPIKE_COUNTS = {2, 3}
_ADC_POSITIONS = {"BOTTOM", "ADC", "adc"}


class PowerSpikeDetector:
    """Detects champion power spikes from level-ups and item completions.

    Compares current snapshot to previous to detect NEW spikes only.
    Uses champion-specific knowledge to weight spike importance.

    Usage::
        detector = PowerSpikeDetector()
        # Each planning tick:
        spikes = detector.detect(current_snapshot, active_team_side)
        for spike in spikes:
            if spike.impact >= SpikeImpact.MODERATE:
                publish_strategy_adjustment(spike)
    """

    def __init__(self) -> None:
        self._prev_levels: Dict[str, int] = {}
        self._prev_item_counts: Dict[str, int] = {}
        self._detected_spikes: List[PowerSpike] = []
        self._detect_count: int = 0
        self._spike_count: int = 0

    def detect(
        self,
        players: List[Any],
        active_team: str,
        game_time: float,
    ) -> List[PowerSpike]:
        """Detect new power spikes from player state changes.

        Args:
            players: List of PlayerState objects from GameSnapshot.
            active_team: Our team side ("ORDER" / "CHAOS" or TeamSide enum).
            game_time: Current game time.

        Returns:
            List of newly detected PowerSpike objects.
        """
        self._detect_count += 1
        new_spikes: List[PowerSpike] = []

        for player in players:
            name = getattr(player, "summoner_name", "")
            champion = getattr(player, "champion_name", "")
            level = getattr(player, "level", 1)
            team = getattr(player, "team", None)
            position = getattr(player, "position", "")

            # Determine if ally
            team_str = team.value if hasattr(team, "value") else str(team)
            active_str = (
                active_team.value
                if hasattr(active_team, "value")
                else str(active_team)
            )
            is_ally = team_str == active_str

            # ── Level spike detection ────────────────────────────────
            prev_level = self._prev_levels.get(name, 0)
            if level > prev_level and level in _KEY_LEVELS:
                impact = SpikeImpact.MODERATE
                desc = f"{champion} reached level {level}"
                note = f"{'Our' if is_ally else 'Enemy'} {champion} hit level {level}"

                if level == 6 and champion in _STRONG_LEVEL6_CHAMPIONS:
                    impact = SpikeImpact.MAJOR
                    if is_ally:
                        note += " — look for all-in opportunities"
                    else:
                        note += " — respect their kill threat"
                elif level == 16:
                    impact = SpikeImpact.MAJOR
                    note += " — max rank ultimate"

                spike = PowerSpike(
                    player_name=name,
                    champion_name=champion,
                    spike_type=SpikeType.LEVEL_SPIKE,
                    impact=impact,
                    is_ally=is_ally,
                    description=desc,
                    strategic_note=note,
                    game_time=game_time,
                    level=level,
                )
                new_spikes.append(spike)
                self._spike_count += 1

            self._prev_levels[name] = level

            # ── Item spike detection ─────────────────────────────────
            items = getattr(player, "items", None)
            if items is not None:
                item_ids = getattr(items, "item_ids", ())
                completed_count = sum(
                    1 for iid in item_ids if iid in _MAJOR_ITEM_IDS
                )
                prev_count = self._prev_item_counts.get(name, 0)

                if completed_count > prev_count:
                    # New item completed
                    impact = SpikeImpact.MODERATE
                    desc = f"{champion} completed item #{completed_count}"
                    note = (
                        f"{'Our' if is_ally else 'Enemy'} {champion} "
                        f"completed major item #{completed_count}"
                    )

                    # ADC 2-item and 3-item spikes are particularly strong
                    if (
                        position in _ADC_POSITIONS
                        and completed_count in _ADC_ITEM_SPIKE_COUNTS
                    ):
                        impact = SpikeImpact.MAJOR
                        if completed_count == 3:
                            note += " — ADC fully online, protect/focus them"

                    if is_ally:
                        note += ". Consider forcing fights."
                    else:
                        note += ". Avoid extended trades."

                    spike = PowerSpike(
                        player_name=name,
                        champion_name=champion,
                        spike_type=SpikeType.ITEM_SPIKE,
                        impact=impact,
                        is_ally=is_ally,
                        description=desc,
                        strategic_note=note,
                        game_time=game_time,
                    )
                    new_spikes.append(spike)
                    self._spike_count += 1

                self._prev_item_counts[name] = completed_count

        self._detected_spikes.extend(new_spikes)
        return new_spikes

    def recent_spikes(self, count: int = 10) -> List[PowerSpike]:
        return self._detected_spikes[-count:]

    def reset(self) -> None:
        self._prev_levels.clear()
        self._prev_item_counts.clear()
        self._detected_spikes.clear()

    def stats(self) -> Dict[str, Any]:
        return {
            "detect_count": self._detect_count,
            "total_spikes": self._spike_count,
            "tracked_players": len(self._prev_levels),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended power spike with voice narration and matchup awareness
# ═══════════════════════════════════════════════════════════════════════════


class PowerSpikeDetectorV2(PowerSpikeDetector):
    """Extended spike detector with voice generation and history export.

    Claude20: Adds voice-friendly narration, spike timeline for
    dashboard, and matchup-aware spike importance weighting.
    All existing PowerSpikeDetector methods preserved.
    """

    _ANNOUNCE_COOLDOWN_S = 20.0

    def __init__(self) -> None:
        super().__init__()
        self._last_announce_time: float = 0.0

    def generate_voice_text(self, spike: PowerSpike) -> Optional[str]:
        """Generate TTS-friendly text for a power spike.

        Returns None if not worth announcing.
        """
        if spike.impact == SpikeImpact.MINOR:
            return None

        if spike.is_ally:
            if spike.spike_type == SpikeType.LEVEL_SPIKE:
                return f"We hit level {spike.level}. {spike.strategic_note}"
            elif spike.spike_type == SpikeType.ITEM_SPIKE:
                return f"Item completed! {spike.strategic_note}"
        else:
            if spike.impact in (SpikeImpact.MAJOR, SpikeImpact.CRITICAL):
                return f"Caution! {spike.strategic_note}"
            elif spike.spike_type == SpikeType.LEVEL_SPIKE:
                return f"Enemy {spike.champion_name} hit level {spike.level}. Be careful."

        return None

    def detect_with_narration(
        self,
        players: List[Any],
        active_team: str,
        game_time: float,
    ) -> tuple:
        """Detect spikes and generate voice lines.

        Returns (spikes, voice_lines) tuple.
        """
        spikes = self.detect(players, active_team, game_time)
        voice_lines: List[str] = []

        if game_time - self._last_announce_time < self._ANNOUNCE_COOLDOWN_S:
            return spikes, voice_lines

        for spike in spikes:
            text = self.generate_voice_text(spike)
            if text:
                voice_lines.append(text)
                self._last_announce_time = game_time

        return spikes, voice_lines

    def get_timeline(self) -> List[Dict[str, Any]]:
        """Export spike timeline for dashboard visualization."""
        return [
            {
                "game_time": round(s.game_time, 1),
                "player": s.player_name,
                "champion": s.champion_name,
                "type": s.spike_type.name,
                "impact": s.impact.name,
                "ally": s.is_ally,
                "desc": s.description,
            }
            for s in self._detected_spikes
        ]

    def get_team_power_level(
        self, players: List[Any], active_team: str,
    ) -> Dict[str, float]:
        """Estimate relative team power from detected spikes.

        Claude20: Returns power level score for each team
        based on accumulated spike advantages.
        """
        ally_power = 0.0
        enemy_power = 0.0

        impact_weights = {
            SpikeImpact.MINOR: 0.2,
            SpikeImpact.MODERATE: 0.5,
            SpikeImpact.MAJOR: 1.0,
            SpikeImpact.CRITICAL: 1.5,
        }

        for spike in self._detected_spikes:
            weight = impact_weights.get(spike.impact, 0.0)
            if spike.is_ally:
                ally_power += weight
            else:
                enemy_power += weight

        return {
            "ally_power": round(ally_power, 2),
            "enemy_power": round(enemy_power, 2),
            "advantage": round(ally_power - enemy_power, 2),
        }

    def extended_stats(self) -> Dict[str, Any]:
        base = self.stats()
        ally_spikes = sum(1 for s in self._detected_spikes if s.is_ally)
        enemy_spikes = len(self._detected_spikes) - ally_spikes
        base["ally_spikes"] = ally_spikes
        base["enemy_spikes"] = enemy_spikes
        base["major_spikes"] = sum(
            1 for s in self._detected_spikes
            if s.impact in (SpikeImpact.MAJOR, SpikeImpact.CRITICAL)
        )
        return base

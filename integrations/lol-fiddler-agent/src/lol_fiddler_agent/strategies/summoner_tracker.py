"""
Summoner Spell Tracker - Tracks enemy summoner spell cooldowns.

When an enemy uses Flash, TP, Ignite, etc., the tracker starts
a cooldown timer and notifies the strategy engine when spells
are back up, enabling more informed engage decisions.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Summoner spell cooldowns (seconds) - base values
_SPELL_COOLDOWNS: dict[str, float] = {
    "Flash": 300.0,
    "Teleport": 360.0,
    "Ignite": 180.0,
    "Heal": 240.0,
    "Barrier": 180.0,
    "Exhaust": 210.0,
    "Cleanse": 210.0,
    "Ghost": 210.0,
    "Smite": 90.0,
    "Mark": 80.0,  # ARAM snowball
}


@dataclass
class SpellCooldownState:
    """Tracks a single summoner spell's cooldown."""
    spell_name: str
    player_name: str
    champion_name: str
    cooldown_duration: float
    used_at_game_time: float = 0.0
    is_on_cooldown: bool = False

    @property
    def ready_at(self) -> float:
        """Game time when the spell will be available."""
        if not self.is_on_cooldown:
            return 0.0
        return self.used_at_game_time + self.cooldown_duration

    def remaining(self, current_game_time: float) -> float:
        """Seconds remaining on cooldown."""
        if not self.is_on_cooldown:
            return 0.0
        remaining = self.ready_at - current_game_time
        if remaining <= 0:
            self.is_on_cooldown = False
            return 0.0
        return remaining

    def mark_used(self, game_time: float) -> None:
        self.used_at_game_time = game_time
        self.is_on_cooldown = True

    def is_available(self, current_game_time: float) -> bool:
        return self.remaining(current_game_time) <= 0


class SummonerSpellTracker:
    """Tracks all enemy summoner spell cooldowns.

    Example::

        tracker = SummonerSpellTracker()
        tracker.register_spell("EnemyMid", "Ahri", "Flash")
        tracker.mark_used("EnemyMid", "Flash", game_time=180.0)
        remaining = tracker.get_remaining("EnemyMid", "Flash", game_time=200.0)
        # remaining = 280.0 seconds
    """

    def __init__(self) -> None:
        # Key: f"{player_name}:{spell_name}"
        self._spells: dict[str, SpellCooldownState] = {}
        self._usage_log: list[dict[str, Any]] = []

    def register_spell(
        self, player_name: str, champion_name: str, spell_name: str,
    ) -> None:
        """Register a summoner spell for tracking."""
        key = f"{player_name}:{spell_name}"
        cooldown = _SPELL_COOLDOWNS.get(spell_name, 300.0)
        self._spells[key] = SpellCooldownState(
            spell_name=spell_name,
            player_name=player_name,
            champion_name=champion_name,
            cooldown_duration=cooldown,
        )

    def mark_used(
        self, player_name: str, spell_name: str, game_time: float,
    ) -> None:
        """Mark a spell as used, starting cooldown timer."""
        key = f"{player_name}:{spell_name}"
        state = self._spells.get(key)
        if state:
            state.mark_used(game_time)
            self._usage_log.append({
                "player": player_name,
                "champion": state.champion_name,
                "spell": spell_name,
                "game_time": game_time,
            })
            logger.info(
                "%s (%s) used %s at %.0fs (CD: %.0fs)",
                player_name, state.champion_name, spell_name,
                game_time, state.cooldown_duration,
            )
        else:
            logger.debug("Unregistered spell: %s for %s", spell_name, player_name)

    def get_remaining(
        self, player_name: str, spell_name: str, game_time: float,
    ) -> float:
        """Get remaining cooldown for a spell."""
        key = f"{player_name}:{spell_name}"
        state = self._spells.get(key)
        if state:
            return state.remaining(game_time)
        return 0.0

    def is_flash_down(self, player_name: str, game_time: float) -> bool:
        """Quick check: is this player's Flash on cooldown?"""
        return self.get_remaining(player_name, "Flash", game_time) > 0

    def get_all_cooldowns(self, game_time: float) -> list[dict[str, Any]]:
        """Get all active cooldowns."""
        active: list[dict[str, Any]] = []
        for key, state in self._spells.items():
            remaining = state.remaining(game_time)
            if remaining > 0:
                active.append({
                    "player": state.player_name,
                    "champion": state.champion_name,
                    "spell": state.spell_name,
                    "remaining": remaining,
                    "ready_at": state.ready_at,
                })
        return active

    def get_engage_windows(self, game_time: float) -> list[str]:
        """Get summaries of enemies with Flash down."""
        windows: list[str] = []
        for key, state in self._spells.items():
            if state.spell_name == "Flash" and state.is_on_cooldown:
                remaining = state.remaining(game_time)
                if remaining > 0:
                    windows.append(
                        f"{state.champion_name} Flash down ({remaining:.0f}s)"
                    )
        return windows

    @property
    def tracked_count(self) -> int:
        return len(self._spells)

    def reset(self) -> None:
        self._spells.clear()
        self._usage_log.clear()

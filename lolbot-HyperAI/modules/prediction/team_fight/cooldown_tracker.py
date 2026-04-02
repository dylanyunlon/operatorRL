"""
CooldownTracker — Summoner spell and ultimate cooldown estimation.
===================================================================
lolbot-HyperAI · Prediction Layer

Tracks Flash, Teleport, and ultimate cooldowns by observing events.
Provides estimated availability for teamfight prediction scoring.

Architecture position:
    modules/prediction/team_fight/cooldown_tracker.py   ← YOU ARE HERE
    ├─ Input: GameEvent stream (kill events contain summoner spell usage hints)
    ├─ Input: GameSnapshot (player levels for ult cooldown scaling)
    ├─ Output: CooldownState per player
    └─ Consumed by: TeamfightPredictor for fight scoring

Design notes:
    - Flash: 300s cooldown (with Cosmic Insight: 285s)
    - Teleport: 360s cooldown
    - Ultimate: champion-dependent, estimated from level
    - We cannot directly observe spell usage from LCU API during game,
      so we infer from death events (flash often used before death)
      and maintain conservative estimates
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from modules.common.adapters.game_messages import (
    GameEvent, GameSnapshot, PlayerState, TeamSide,
)

_FLASH_CD_S = 300.0
_TP_CD_S = 360.0
_IGNITE_CD_S = 180.0
_EXHAUST_CD_S = 210.0
_HEAL_CD_S = 240.0
_BARRIER_CD_S = 180.0

_SPELL_COOLDOWNS: Dict[str, float] = {
    "Flash": _FLASH_CD_S,
    "Teleport": _TP_CD_S,
    "Ignite": _IGNITE_CD_S,
    "Exhaust": _EXHAUST_CD_S,
    "Heal": _HEAL_CD_S,
    "Barrier": _BARRIER_CD_S,
    "Smite": 90.0,
    "Ghost": 210.0,
    "Cleanse": 210.0,
}

# Base ultimate cooldowns by level bracket (rough estimate)
_ULT_BASE_CD_S = {
    6: 120.0, 7: 115.0, 8: 110.0, 9: 105.0, 10: 100.0,
    11: 90.0, 12: 85.0, 13: 80.0, 14: 75.0, 15: 70.0,
    16: 60.0, 17: 55.0, 18: 50.0,
}


@dataclass
class SpellCooldown:
    """Cooldown state for a single spell."""
    spell_name: str = ""
    base_cooldown_s: float = 0.0
    last_used_game_time: float = -9999.0
    is_known_used: bool = False

    def estimated_ready_time(self) -> float:
        if not self.is_known_used:
            return 0.0  # assume available
        return self.last_used_game_time + self.base_cooldown_s

    def is_likely_up(self, game_time: float) -> bool:
        return game_time >= self.estimated_ready_time()

    def time_remaining(self, game_time: float) -> float:
        return max(0.0, self.estimated_ready_time() - game_time)


@dataclass
class PlayerCooldowns:
    """All tracked cooldowns for a single player."""
    name: str = ""
    team: TeamSide = TeamSide.UNKNOWN
    spell_d: SpellCooldown = field(default_factory=SpellCooldown)
    spell_f: SpellCooldown = field(default_factory=SpellCooldown)
    ultimate: SpellCooldown = field(default_factory=lambda: SpellCooldown(
        spell_name="Ultimate", base_cooldown_s=100.0,
    ))

    @property
    def flash_up(self) -> bool:
        for spell in (self.spell_d, self.spell_f):
            if spell.spell_name == "Flash":
                return not spell.is_known_used or spell.is_likely_up(0)
        return True  # assume up if not Flash holder

    def to_dict(self, game_time: float) -> Dict[str, Any]:
        return {
            "name": self.name,
            "spell_d": {
                "name": self.spell_d.spell_name,
                "up": self.spell_d.is_likely_up(game_time),
                "remaining": round(self.spell_d.time_remaining(game_time), 0),
            },
            "spell_f": {
                "name": self.spell_f.spell_name,
                "up": self.spell_f.is_likely_up(game_time),
                "remaining": round(self.spell_f.time_remaining(game_time), 0),
            },
            "ult_up": self.ultimate.is_likely_up(game_time),
        }


class CooldownTracker:
    """Tracks estimated spell cooldowns for all players.

    Usage::
        tracker = CooldownTracker()
        tracker.update_from_snapshot(snapshot)
        tracker.record_spell_usage("PlayerName", "Flash", game_time)
        state = tracker.get_player("PlayerName")
    """

    def __init__(self) -> None:
        self._players: Dict[str, PlayerCooldowns] = {}

    def update_from_snapshot(self, snapshot: GameSnapshot) -> None:
        """Initialize/update player data from game snapshot."""
        for player in snapshot.all_players:
            if player.summoner_name not in self._players:
                cd = PlayerCooldowns(
                    name=player.summoner_name,
                    team=player.team,
                )
                # Set spell cooldowns
                d_name = player.spell_d
                f_name = player.spell_f
                cd.spell_d = SpellCooldown(
                    spell_name=d_name,
                    base_cooldown_s=_SPELL_COOLDOWNS.get(d_name, 300.0),
                )
                cd.spell_f = SpellCooldown(
                    spell_name=f_name,
                    base_cooldown_s=_SPELL_COOLDOWNS.get(f_name, 300.0),
                )
                self._players[player.summoner_name] = cd

            # Update ult cooldown estimate based on level
            pc = self._players[player.summoner_name]
            if player.level >= 6:
                pc.ultimate.base_cooldown_s = _ULT_BASE_CD_S.get(
                    player.level, 100.0
                )

    def record_death(self, player_name: str, game_time: float) -> None:
        """When a player dies, conservatively assume Flash was used.

        This is a heuristic: most players flash before dying.
        """
        pc = self._players.get(player_name)
        if pc is None:
            return
        for spell in (pc.spell_d, pc.spell_f):
            if spell.spell_name == "Flash" and spell.is_likely_up(game_time):
                spell.last_used_game_time = game_time
                spell.is_known_used = True

    def record_spell_usage(
        self, player_name: str, spell_name: str, game_time: float
    ) -> None:
        """Record explicit spell usage (if we can observe it)."""
        pc = self._players.get(player_name)
        if pc is None:
            return
        for spell in (pc.spell_d, pc.spell_f):
            if spell.spell_name == spell_name:
                spell.last_used_game_time = game_time
                spell.is_known_used = True
                return
        if spell_name == "Ultimate":
            pc.ultimate.last_used_game_time = game_time
            pc.ultimate.is_known_used = True

    def get_player(self, name: str) -> Optional[PlayerCooldowns]:
        return self._players.get(name)

    def team_flash_count(self, team: TeamSide, game_time: float) -> int:
        """Count how many players on a team likely have Flash available."""
        count = 0
        for pc in self._players.values():
            if pc.team != team:
                continue
            for spell in (pc.spell_d, pc.spell_f):
                if spell.spell_name == "Flash" and spell.is_likely_up(game_time):
                    count += 1
                    break
        return count

    def team_ult_count(self, team: TeamSide, game_time: float) -> int:
        """Count how many ultimates are likely available on a team."""
        count = 0
        for pc in self._players.values():
            if pc.team != team:
                continue
            if pc.ultimate.is_likely_up(game_time):
                count += 1
        return count

    def all_cooldowns(self, game_time: float) -> Dict[str, Dict[str, Any]]:
        return {
            name: pc.to_dict(game_time)
            for name, pc in self._players.items()
        }

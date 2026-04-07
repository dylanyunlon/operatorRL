"""
modules/planning/summoner/spell_tracker.py — Summoner spell cooldown tracker.
===============================================================================
Claude19 · Wires into PlanningComponent for engagement window decisions

Tracks enemy summoner spell usage (Flash, Teleport, Exhaust, etc.)
and estimates when they'll be available again. When key spells like
Flash are on cooldown, it opens windows for aggressive plays.

Apollo analogy: planning/tasks/path_bound_decider.cc considers vehicle
capability constraints — we consider champion capability (spell availability).

File location: lolbot-HyperAI/modules/planning/summoner/spell_tracker.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Summoner spell base cooldowns (seconds)
_SPELL_COOLDOWNS: Dict[str, float] = {
    "Flash": 300.0,
    "Teleport": 360.0,
    "Ignite": 180.0,
    "Heal": 240.0,
    "Barrier": 180.0,
    "Exhaust": 210.0,
    "Cleanse": 210.0,
    "Ghost": 210.0,
    "Smite": 90.0,
    "Mark": 80.0,    # ARAM snowball
}

# Cosmic Insight / other CDR reduces by ~5-15%
_CDR_ESTIMATE = 0.95  # Conservative: assume slight CDR

# Flash is the most important spell for engagement windows
_CRITICAL_SPELLS = {"Flash", "Teleport", "Exhaust"}


@dataclass
class SpellState:
    """State of one summoner spell for one player."""
    spell_name: str
    player_name: str
    champion_name: str
    team: str
    last_used_time: float = 0.0
    estimated_ready_time: float = 0.0
    times_tracked: int = 0

    @property
    def base_cooldown(self) -> float:
        return _SPELL_COOLDOWNS.get(self.spell_name, 300.0) * _CDR_ESTIMATE

    def is_available(self, game_time: float) -> bool:
        if self.last_used_time == 0.0:
            return True  # Never seen used → assume available
        return game_time >= self.estimated_ready_time

    def time_until_ready(self, game_time: float) -> float:
        if self.is_available(game_time):
            return 0.0
        return max(0, self.estimated_ready_time - game_time)

    def to_dict(self, game_time: float = 0.0) -> Dict[str, Any]:
        return {
            "spell": self.spell_name,
            "player": self.player_name,
            "champion": self.champion_name,
            "team": self.team,
            "available": self.is_available(game_time),
            "time_until_ready": round(self.time_until_ready(game_time), 1),
            "times_tracked": self.times_tracked,
        }


@dataclass
class SpellWindowReport:
    """Report of engagement windows based on summoner spell CDs."""
    game_time: float = 0.0
    enemy_flash_down: List[str] = field(default_factory=list)
    enemy_tp_down: List[str] = field(default_factory=list)
    enemy_exhaust_down: List[str] = field(default_factory=list)
    best_target: str = ""
    best_target_reason: str = ""
    window_quality: float = 0.0  # 0-1, how good the window is

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time": round(self.game_time, 1),
            "enemy_flash_down": self.enemy_flash_down,
            "enemy_tp_down": self.enemy_tp_down,
            "enemy_exhaust_down": self.enemy_exhaust_down,
            "best_target": self.best_target,
            "best_target_reason": self.best_target_reason,
            "window_quality": round(self.window_quality, 3),
        }


class SummonerSpellTracker:
    """Tracks summoner spell cooldowns for all players.

    Usage::
        tracker = SummonerSpellTracker()
        # When we detect a spell was used (from events):
        tracker.record_usage("EnemyPlayer", "Flash", game_time=600.0)
        # Each planning tick:
        report = tracker.evaluate(game_time=605.0, active_team="BLUE")
        if report.enemy_flash_down:
            planning.prioritize_target(report.best_target)
    """

    def __init__(self) -> None:
        # Key: (player_name, spell_slot) → SpellState
        self._spells: Dict[Tuple[str, str], SpellState] = {}
        self._player_teams: Dict[str, str] = {}  # name → team
        self._record_count: int = 0

    def register_player(
        self,
        player_name: str,
        champion_name: str,
        team: str,
        spell_d: str,
        spell_f: str,
    ) -> None:
        """Register a player's summoner spells at game start."""
        for slot, spell_name in [("D", spell_d), ("F", spell_f)]:
            if spell_name:
                self._spells[(player_name, slot)] = SpellState(
                    spell_name=spell_name,
                    player_name=player_name,
                    champion_name=champion_name,
                    team=team.upper(),
                )
        self._player_teams[player_name] = team.upper()

    def record_usage(
        self,
        player_name: str,
        spell_name: str,
        game_time: float,
    ) -> None:
        """Record that a player used a summoner spell.

        Finds the matching spell slot and sets the cooldown timer.
        """
        for (pname, slot), state in self._spells.items():
            if pname == player_name and state.spell_name == spell_name:
                state.last_used_time = game_time
                state.estimated_ready_time = game_time + state.base_cooldown
                state.times_tracked += 1
                self._record_count += 1
                logger.debug(
                    "Tracked %s %s used at %.0fs (ready at %.0fs)",
                    player_name, spell_name, game_time,
                    state.estimated_ready_time,
                )
                return

    def evaluate(
        self,
        game_time: float,
        active_team: str = "BLUE",
    ) -> SpellWindowReport:
        """Evaluate engagement windows from enemy spell cooldowns.

        Checks which critical enemy spells are on cooldown and
        identifies the best target for aggression.
        """
        enemy_team = "RED" if active_team == "BLUE" else "BLUE"

        flash_down: List[str] = []
        tp_down: List[str] = []
        exhaust_down: List[str] = []

        for (pname, slot), state in self._spells.items():
            if state.team != enemy_team:
                continue
            if state.is_available(game_time):
                continue

            # Spell is on cooldown
            if state.spell_name == "Flash":
                flash_down.append(f"{state.champion_name} ({state.time_until_ready(game_time):.0f}s)")
            elif state.spell_name == "Teleport":
                tp_down.append(f"{state.champion_name} ({state.time_until_ready(game_time):.0f}s)")
            elif state.spell_name == "Exhaust":
                exhaust_down.append(f"{state.champion_name} ({state.time_until_ready(game_time):.0f}s)")

        # Pick best target (no flash = most vulnerable)
        best_target = ""
        best_reason = ""
        window_quality = 0.0

        if flash_down:
            # Target with longest remaining flash CD
            best_target = flash_down[0].split(" (")[0] if flash_down else ""
            best_reason = "Flash on cooldown"
            window_quality = min(1.0, len(flash_down) * 0.3)

        if exhaust_down:
            window_quality += 0.15

        return SpellWindowReport(
            game_time=game_time,
            enemy_flash_down=flash_down,
            enemy_tp_down=tp_down,
            enemy_exhaust_down=exhaust_down,
            best_target=best_target,
            best_target_reason=best_reason,
            window_quality=min(1.0, window_quality),
        )

    def get_all_states(self, game_time: float) -> List[Dict[str, Any]]:
        return [
            state.to_dict(game_time)
            for state in self._spells.values()
        ]

    def stats(self) -> Dict[str, Any]:
        return {
            "players_registered": len(self._player_teams),
            "spells_tracked": len(self._spells),
            "record_count": self._record_count,
        }

    def reset(self) -> None:
        self._spells.clear()
        self._player_teams.clear()
        self._record_count = 0

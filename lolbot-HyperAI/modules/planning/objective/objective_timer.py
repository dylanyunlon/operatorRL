"""
modules/planning/objective/objective_timer.py — Neutral objective spawn tracker.
=================================================================================
Claude19 · Wires into PlanningComponent.Proc() to feed ObjectiveWindowAdvisor

Tracks spawn/kill/respawn state of all neutral objectives (Dragon, Baron,
Herald, Elder Dragon) based on events from the perception pipeline.
Publishes objective state on /lol/objective_state for planning and prediction.

Mirrors Apollo's planning/tasks/deciders/open_space_decider.cc pattern:
maintain state from fused inputs, then provide it to the decision layer.

File location: lolbot-HyperAI/modules/planning/objective/objective_timer.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

# Respawn times in seconds
_DRAGON_RESPAWN_S = 300.0    # 5 min
_BARON_RESPAWN_S = 360.0     # 6 min
_HERALD_RESPAWN_S = 480.0    # 8 min (only 2 heralds total)
_ELDER_RESPAWN_S = 360.0     # 6 min after soul

# Spawn times (game time when objective first appears)
_FIRST_DRAGON_SPAWN_S = 300.0   # 5:00
_FIRST_HERALD_SPAWN_S = 480.0   # 8:00
_BARON_SPAWN_S = 1200.0         # 20:00
_ELDER_SPAWN_CONDITION = 4       # After 4 drakes (soul taken)

# Herald disappears when baron spawns
_HERALD_DESPAWN_S = _BARON_SPAWN_S

_MAX_HERALDS = 2


class ObjectiveState(Enum):
    """State of a neutral objective."""
    NOT_SPAWNED = auto()   # Before first spawn
    ALIVE = auto()         # On the map, available
    DEAD = auto()          # Killed, waiting for respawn
    EXPIRED = auto()       # No longer available (herald after baron)
    SOUL_TAKEN = auto()    # Dragon soul claimed, switches to Elder


@dataclass
class ObjectiveInfo:
    """State of a single objective type."""
    name: str
    state: ObjectiveState = ObjectiveState.NOT_SPAWNED
    first_spawn_time: float = 0.0
    respawn_time: float = 0.0
    kill_count: int = 0
    last_kill_time: float = 0.0
    last_kill_team: str = ""
    respawn_game_time: float = 0.0  # When it will respawn
    blue_count: int = 0  # How many blue team has taken
    red_count: int = 0   # How many red team has taken

    @property
    def total_taken(self) -> int:
        return self.blue_count + self.red_count

    @property
    def is_available(self) -> bool:
        return self.state == ObjectiveState.ALIVE

    def time_until_spawn(self, game_time: float) -> float:
        """Seconds until this objective spawns/respawns. 0 if alive."""
        if self.state == ObjectiveState.ALIVE:
            return 0.0
        if self.state == ObjectiveState.NOT_SPAWNED:
            return max(0, self.first_spawn_time - game_time)
        if self.state == ObjectiveState.DEAD:
            return max(0, self.respawn_game_time - game_time)
        return float("inf")  # EXPIRED or SOUL_TAKEN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.name,
            "kill_count": self.kill_count,
            "blue_count": self.blue_count,
            "red_count": self.red_count,
            "last_kill_team": self.last_kill_team,
            "last_kill_time": round(self.last_kill_time, 1),
            "respawn_game_time": round(self.respawn_game_time, 1),
        }


class ObjectiveTimer:
    """Tracks neutral objective spawn/kill/respawn state.

    Usage::
        timer = ObjectiveTimer()
        # Each tick from perception events:
        for event in new_events:
            timer.process_event(event_type, killer_team, game_time)
        timer.tick(game_time)  # update spawn states based on time
        states = timer.stats()  # get all objective states for planning
    """

    def __init__(self) -> None:
        self._dragon = ObjectiveInfo(
            name="dragon",
            first_spawn_time=_FIRST_DRAGON_SPAWN_S,
            respawn_time=_DRAGON_RESPAWN_S,
        )
        self._baron = ObjectiveInfo(
            name="baron",
            first_spawn_time=_BARON_SPAWN_S,
            respawn_time=_BARON_RESPAWN_S,
        )
        self._herald = ObjectiveInfo(
            name="herald",
            first_spawn_time=_FIRST_HERALD_SPAWN_S,
            respawn_time=_HERALD_RESPAWN_S,
        )
        self._elder = ObjectiveInfo(
            name="elder",
            first_spawn_time=0.0,  # Dynamic, after soul
            respawn_time=_ELDER_RESPAWN_S,
        )

        self._dragon_soul_team: Optional[str] = None
        self._tick_count: int = 0

    def process_event(
        self,
        event_type: str,
        killer_team: str,
        game_time: float,
        event_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Process a game event that might relate to objectives.

        Args:
            event_type: Event type string (DragonKill, BaronKill, HeraldKill, etc.)
            killer_team: BLUE or RED
            game_time: When it happened
            event_data: Optional extra data
        """
        etype = event_type.lower()

        if "dragon" in etype and "elder" not in etype:
            self._on_dragon_kill(killer_team, game_time)
        elif "elder" in etype:
            self._on_elder_kill(killer_team, game_time)
        elif "baron" in etype:
            self._on_baron_kill(killer_team, game_time)
        elif "herald" in etype or "rift" in etype:
            self._on_herald_kill(killer_team, game_time)

    def tick(self, game_time: float) -> None:
        """Update spawn states based on current game time.

        Call once per perception/planning tick.
        """
        self._tick_count += 1

        # Dragon spawning
        if self._dragon.state == ObjectiveState.NOT_SPAWNED:
            if game_time >= self._dragon.first_spawn_time:
                self._dragon.state = ObjectiveState.ALIVE
                logger.debug("Dragon spawned at %.0fs", game_time)
        elif self._dragon.state == ObjectiveState.DEAD:
            if game_time >= self._dragon.respawn_game_time:
                if self._dragon_soul_team is not None:
                    self._dragon.state = ObjectiveState.SOUL_TAKEN
                    # Elder dragon starts spawning
                    self._elder.first_spawn_time = game_time
                    self._elder.state = ObjectiveState.ALIVE
                    logger.info("Dragon soul claimed. Elder spawns.")
                else:
                    self._dragon.state = ObjectiveState.ALIVE
                    logger.debug("Dragon respawned at %.0fs", game_time)

        # Herald spawning / expiry
        if self._herald.state == ObjectiveState.NOT_SPAWNED:
            if game_time >= self._herald.first_spawn_time:
                if game_time < _HERALD_DESPAWN_S:
                    self._herald.state = ObjectiveState.ALIVE
                    logger.debug("Herald spawned at %.0fs", game_time)
                else:
                    self._herald.state = ObjectiveState.EXPIRED
        elif self._herald.state == ObjectiveState.ALIVE:
            if game_time >= _HERALD_DESPAWN_S:
                self._herald.state = ObjectiveState.EXPIRED
                logger.debug("Herald expired (baron spawning)")
        elif self._herald.state == ObjectiveState.DEAD:
            if self._herald.total_taken >= _MAX_HERALDS:
                self._herald.state = ObjectiveState.EXPIRED
            elif game_time >= self._herald.respawn_game_time:
                if game_time < _HERALD_DESPAWN_S:
                    self._herald.state = ObjectiveState.ALIVE
                else:
                    self._herald.state = ObjectiveState.EXPIRED

        # Baron spawning
        if self._baron.state == ObjectiveState.NOT_SPAWNED:
            if game_time >= self._baron.first_spawn_time:
                self._baron.state = ObjectiveState.ALIVE
                logger.debug("Baron spawned at %.0fs", game_time)
        elif self._baron.state == ObjectiveState.DEAD:
            if game_time >= self._baron.respawn_game_time:
                self._baron.state = ObjectiveState.ALIVE
                logger.debug("Baron respawned at %.0fs", game_time)

        # Elder dragon
        if self._elder.state == ObjectiveState.DEAD:
            if game_time >= self._elder.respawn_game_time:
                self._elder.state = ObjectiveState.ALIVE
                logger.debug("Elder respawned at %.0fs", game_time)

    def _on_dragon_kill(self, team: str, game_time: float) -> None:
        self._dragon.kill_count += 1
        self._dragon.last_kill_time = game_time
        self._dragon.last_kill_team = team
        self._dragon.state = ObjectiveState.DEAD
        self._dragon.respawn_game_time = game_time + _DRAGON_RESPAWN_S
        if team == "BLUE":
            self._dragon.blue_count += 1
        else:
            self._dragon.red_count += 1
        # Check for dragon soul (4th dragon)
        team_count = (
            self._dragon.blue_count if team == "BLUE"
            else self._dragon.red_count
        )
        if team_count >= _ELDER_SPAWN_CONDITION and self._dragon_soul_team is None:
            self._dragon_soul_team = team
            logger.info("Dragon soul claimed by %s!", team)

    def _on_baron_kill(self, team: str, game_time: float) -> None:
        self._baron.kill_count += 1
        self._baron.last_kill_time = game_time
        self._baron.last_kill_team = team
        self._baron.state = ObjectiveState.DEAD
        self._baron.respawn_game_time = game_time + _BARON_RESPAWN_S
        if team == "BLUE":
            self._baron.blue_count += 1
        else:
            self._baron.red_count += 1

    def _on_herald_kill(self, team: str, game_time: float) -> None:
        self._herald.kill_count += 1
        self._herald.last_kill_time = game_time
        self._herald.last_kill_team = team
        self._herald.state = ObjectiveState.DEAD
        self._herald.respawn_game_time = game_time + _HERALD_RESPAWN_S
        if team == "BLUE":
            self._herald.blue_count += 1
        else:
            self._herald.red_count += 1

    def _on_elder_kill(self, team: str, game_time: float) -> None:
        self._elder.kill_count += 1
        self._elder.last_kill_time = game_time
        self._elder.last_kill_team = team
        self._elder.state = ObjectiveState.DEAD
        self._elder.respawn_game_time = game_time + _ELDER_RESPAWN_S
        if team == "BLUE":
            self._elder.blue_count += 1
        else:
            self._elder.red_count += 1

    # ─── Queries ────────────────────────────────────────────────────

    def get_objective(self, name: str) -> Optional[ObjectiveInfo]:
        mapping = {
            "dragon": self._dragon,
            "baron": self._baron,
            "herald": self._herald,
            "elder": self._elder,
        }
        return mapping.get(name.lower())

    def get_all_states(self) -> Dict[str, ObjectiveInfo]:
        return {
            "dragon": self._dragon,
            "baron": self._baron,
            "herald": self._herald,
            "elder": self._elder,
        }

    @property
    def dragon_soul_team(self) -> Optional[str]:
        return self._dragon_soul_team

    def stats(self) -> Dict[str, Any]:
        return {
            "tick_count": self._tick_count,
            "dragon_soul_team": self._dragon_soul_team,
            "objectives": {
                name: obj.to_dict()
                for name, obj in self.get_all_states().items()
            },
        }

    def reset(self) -> None:
        """Reset for a new game session."""
        self.__init__()

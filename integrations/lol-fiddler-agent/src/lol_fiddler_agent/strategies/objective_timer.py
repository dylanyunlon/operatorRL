"""
Objective Timer - Tracks spawn and kill times for major objectives.

Maintains a state machine for each objective (Dragon, Baron, Herald,
Tower, Inhibitor) with automatic respawn countdown and alerts.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from lol_fiddler_agent.network.live_client_data import GameEvent, LiveGameState, Team

logger = logging.getLogger(__name__)


class ObjectiveType(str, Enum):
    DRAGON = "dragon"
    BARON = "baron"
    RIFT_HERALD = "rift_herald"
    ELDER_DRAGON = "elder_dragon"
    TOWER = "tower"
    INHIBITOR = "inhibitor"


class ObjectiveStatus(str, Enum):
    ALIVE = "alive"
    DEAD = "dead"
    SPAWNING_SOON = "spawning_soon"
    NOT_YET_SPAWNED = "not_yet_spawned"


@dataclass
class ObjectiveState:
    """Tracks state of a single objective."""
    objective_type: ObjectiveType
    status: ObjectiveStatus = ObjectiveStatus.NOT_YET_SPAWNED
    killed_by: Optional[Team] = None
    killed_at: float = 0.0  # game time
    respawn_time: float = 0.0  # game time when it respawns
    kill_count: int = 0
    details: str = ""

    @property
    def is_alive(self) -> bool:
        return self.status == ObjectiveStatus.ALIVE

    @property
    def time_until_respawn(self) -> float:
        if self.respawn_time <= 0:
            return 0.0
        return max(0.0, self.respawn_time - self.killed_at)


# Spawn/respawn timers (seconds)
_TIMERS = {
    ObjectiveType.DRAGON: {"first_spawn": 5 * 60, "respawn": 5 * 60},
    ObjectiveType.BARON: {"first_spawn": 20 * 60, "respawn": 6 * 60},
    ObjectiveType.RIFT_HERALD: {"first_spawn": 8 * 60, "respawn": 6 * 60},
    ObjectiveType.ELDER_DRAGON: {"first_spawn": 35 * 60, "respawn": 6 * 60},
    ObjectiveType.INHIBITOR: {"respawn": 5 * 60},
}
_ALERT_WINDOW = 60.0  # Alert 60s before spawn


class ObjectiveTracker:
    """Tracks all major game objectives.

    Example::

        tracker = ObjectiveTracker()
        tracker.update(game_state)
        alerts = tracker.get_alerts(game_time=1200)
    """

    def __init__(self) -> None:
        self._dragon = ObjectiveState(ObjectiveType.DRAGON, ObjectiveStatus.NOT_YET_SPAWNED)
        self._baron = ObjectiveState(ObjectiveType.BARON, ObjectiveStatus.NOT_YET_SPAWNED)
        self._herald = ObjectiveState(ObjectiveType.RIFT_HERALD, ObjectiveStatus.NOT_YET_SPAWNED)
        self._inhibitors: dict[str, ObjectiveState] = {}
        self._dragon_count: dict[Team, int] = {Team.ORDER: 0, Team.CHAOS: 0}
        self._processed_event_ids: set[int] = set()

    def update(self, state: LiveGameState) -> list[str]:
        """Update objective states from game state events.

        Returns list of new alerts.
        """
        alerts: list[str] = []
        if not state.game_data:
            return alerts

        game_time = state.game_data.game_time

        # Update spawn statuses
        self._update_spawn_status(game_time)

        # Process events
        for event in state.events:
            if event.event_id in self._processed_event_ids:
                continue
            self._processed_event_ids.add(event.event_id)

            if event.is_dragon_event():
                alert = self._handle_dragon_kill(event, state, game_time)
                if alert:
                    alerts.append(alert)
            elif event.is_baron_event():
                alert = self._handle_baron_kill(event, state, game_time)
                if alert:
                    alerts.append(alert)
            elif event.is_turret_event():
                alerts.append(f"Tower killed: {event.turret_killed}")
            elif event.is_inhibitor_event():
                alert = self._handle_inhibitor_kill(event, game_time)
                if alert:
                    alerts.append(alert)

        return alerts

    def _update_spawn_status(self, game_time: float) -> None:
        """Update spawn status based on game time."""
        # Dragon
        dragon_timer = _TIMERS[ObjectiveType.DRAGON]
        if game_time >= dragon_timer["first_spawn"] and self._dragon.status == ObjectiveStatus.NOT_YET_SPAWNED:
            self._dragon.status = ObjectiveStatus.ALIVE
        elif self._dragon.status == ObjectiveStatus.DEAD:
            if game_time >= self._dragon.respawn_time:
                self._dragon.status = ObjectiveStatus.ALIVE
            elif game_time >= self._dragon.respawn_time - _ALERT_WINDOW:
                self._dragon.status = ObjectiveStatus.SPAWNING_SOON

        # Baron
        baron_timer = _TIMERS[ObjectiveType.BARON]
        if game_time >= baron_timer["first_spawn"] and self._baron.status == ObjectiveStatus.NOT_YET_SPAWNED:
            self._baron.status = ObjectiveStatus.ALIVE

        elif self._baron.status == ObjectiveStatus.DEAD:
            if game_time >= self._baron.respawn_time:
                self._baron.status = ObjectiveStatus.ALIVE
            elif game_time >= self._baron.respawn_time - _ALERT_WINDOW:
                self._baron.status = ObjectiveStatus.SPAWNING_SOON

        # Herald
        herald_timer = _TIMERS[ObjectiveType.RIFT_HERALD]
        if game_time >= herald_timer["first_spawn"] and self._herald.status == ObjectiveStatus.NOT_YET_SPAWNED:
            if game_time < _TIMERS[ObjectiveType.BARON]["first_spawn"]:
                self._herald.status = ObjectiveStatus.ALIVE

    def _handle_dragon_kill(self, event: GameEvent, state: LiveGameState, game_time: float) -> Optional[str]:
        killer_team = self._find_killer_team(event.killer_name, state)
        respawn = game_time + _TIMERS[ObjectiveType.DRAGON]["respawn"]

        self._dragon.status = ObjectiveStatus.DEAD
        self._dragon.killed_by = killer_team
        self._dragon.killed_at = game_time
        self._dragon.respawn_time = respawn
        self._dragon.kill_count += 1
        self._dragon.details = event.dragon_type or ""

        if killer_team:
            self._dragon_count[killer_team] = self._dragon_count.get(killer_team, 0) + 1

        team_name = killer_team.value if killer_team else "Unknown"
        dragon_type = event.dragon_type or "Dragon"
        return f"{dragon_type} killed by {team_name} (next spawn at {self._format_time(respawn)})"

    def _handle_baron_kill(self, event: GameEvent, state: LiveGameState, game_time: float) -> Optional[str]:
        killer_team = self._find_killer_team(event.killer_name, state)
        respawn = game_time + _TIMERS[ObjectiveType.BARON]["respawn"]

        self._baron.status = ObjectiveStatus.DEAD
        self._baron.killed_by = killer_team
        self._baron.killed_at = game_time
        self._baron.respawn_time = respawn
        self._baron.kill_count += 1

        team_name = killer_team.value if killer_team else "Unknown"
        stolen = " (STOLEN!)" if event.stolen else ""
        return f"Baron killed by {team_name}{stolen} (next spawn at {self._format_time(respawn)})"

    def _handle_inhibitor_kill(self, event: GameEvent, game_time: float) -> Optional[str]:
        inhib_name = event.inhib_killed or "unknown"
        respawn = game_time + _TIMERS[ObjectiveType.INHIBITOR]["respawn"]

        state = ObjectiveState(
            objective_type=ObjectiveType.INHIBITOR,
            status=ObjectiveStatus.DEAD,
            killed_at=game_time,
            respawn_time=respawn,
            details=inhib_name,
        )
        self._inhibitors[inhib_name] = state
        return f"Inhibitor {inhib_name} destroyed (respawns at {self._format_time(respawn)})"

    def _find_killer_team(self, killer_name: Optional[str], state: LiveGameState) -> Optional[Team]:
        if not killer_name:
            return None
        for p in state.all_players:
            if p.summoner_name == killer_name:
                return p.team_enum
        return None

    def get_alerts(self, game_time: float) -> list[str]:
        """Get current objective alerts."""
        alerts: list[str] = []

        if self._dragon.status == ObjectiveStatus.SPAWNING_SOON:
            time_left = self._dragon.respawn_time - game_time
            alerts.append(f"Dragon spawning in {time_left:.0f}s")

        if self._baron.status == ObjectiveStatus.SPAWNING_SOON:
            time_left = self._baron.respawn_time - game_time
            alerts.append(f"Baron spawning in {time_left:.0f}s")

        for name, inhib in self._inhibitors.items():
            if inhib.status == ObjectiveStatus.DEAD:
                time_left = inhib.respawn_time - game_time
                if 0 < time_left < 30:
                    alerts.append(f"Inhibitor {name} respawning in {time_left:.0f}s")

        return alerts

    def get_dragon_count(self, team: Team) -> int:
        return self._dragon_count.get(team, 0)

    @property
    def dragon_state(self) -> ObjectiveState:
        return self._dragon

    @property
    def baron_state(self) -> ObjectiveState:
        return self._baron

    @staticmethod
    def _format_time(game_seconds: float) -> str:
        m = int(game_seconds // 60)
        s = int(game_seconds % 60)
        return f"{m:02d}:{s:02d}"

"""
Event Processor - Processes and indexes in-game events.

Tracks kill streaks, objective sequences, and momentum shifts
from the Live Client API event feed.
"""

from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.network.live_client_data import GameEvent, LiveGameState, Team

logger = logging.getLogger(__name__)


@dataclass
class KillStreak:
    """Tracks a kill streak for a player."""
    player_name: str
    kills: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0

    @property
    def is_active(self) -> bool:
        return self.ended_at == 0.0


@dataclass
class MomentumShift:
    """Detected momentum shift in the game."""
    game_time: float
    description: str
    magnitude: float = 0.0  # -1 to 1, positive = good for us
    trigger_event: str = ""


class EventProcessor:
    """Processes game events into higher-level game narratives.

    Example::

        processor = EventProcessor()
        processor.process_events(state.events, state)
        shifts = processor.get_momentum_shifts()
    """

    def __init__(self) -> None:
        self._processed_ids: set[int] = set()
        self._kill_counts: Counter[str] = Counter()
        self._death_counts: Counter[str] = Counter()
        self._streaks: dict[str, KillStreak] = {}
        self._momentum_shifts: list[MomentumShift] = []
        self._team_kills_timeline: list[tuple[float, str]] = []
        self._objective_timeline: list[tuple[float, str, str]] = []

    def process_events(self, events: list[GameEvent], state: LiveGameState) -> list[str]:
        """Process a batch of events. Returns alert messages."""
        alerts: list[str] = []
        my_team = state.get_my_team()

        for event in events:
            if event.event_id in self._processed_ids:
                continue
            self._processed_ids.add(event.event_id)

            if event.is_kill_event():
                alert = self._process_kill(event, state, my_team)
                if alert:
                    alerts.append(alert)

            elif event.is_dragon_event():
                self._objective_timeline.append(
                    (event.event_time, "dragon", event.dragon_type or "unknown")
                )

            elif event.is_baron_event():
                self._objective_timeline.append(
                    (event.event_time, "baron", "stolen" if event.stolen else "secured")
                )
                if event.stolen:
                    alerts.append("BARON STOLEN!")

            elif event.is_turret_event():
                self._objective_timeline.append(
                    (event.event_time, "turret", event.turret_killed or "")
                )

        return alerts

    def _process_kill(
        self, event: GameEvent, state: LiveGameState, my_team: Team,
    ) -> Optional[str]:
        """Process a champion kill event."""
        killer = event.killer_name or ""
        victim = event.victim_name or ""

        self._kill_counts[killer] += 1
        self._death_counts[victim] += 1

        # Track team kill for timeline
        killer_team = self._find_team(killer, state)
        if killer_team:
            self._team_kills_timeline.append((event.event_time, killer_team.value))

        # Kill streak tracking
        if killer in self._streaks and self._streaks[killer].is_active:
            self._streaks[killer].kills += 1
        else:
            self._streaks[killer] = KillStreak(
                player_name=killer,
                kills=1,
                started_at=event.event_time,
            )

        # End victim's streak
        if victim in self._streaks and self._streaks[victim].is_active:
            self._streaks[victim].ended_at = event.event_time
            streak = self._streaks[victim]
            if streak.kills >= 3:
                return f"{killer} ended {victim}'s {streak.kills}-kill streak!"

        # Detect momentum shifts
        self._check_momentum(event.event_time, state, my_team)

        # Alert on significant streaks
        streak = self._streaks[killer]
        if streak.kills == 3:
            team = "ally" if killer_team == my_team else "enemy"
            return f"{team.upper()} {killer} on a KILLING SPREE (3 kills)"
        elif streak.kills == 5:
            return f"{killer} is LEGENDARY (5 kill streak)!"

        return None

    def _check_momentum(
        self, game_time: float, state: LiveGameState, my_team: Team,
    ) -> None:
        """Check for momentum shifts based on recent kill patterns."""
        # Look at last 60 seconds of kills
        cutoff = game_time - 60
        recent = [t for t, team in self._team_kills_timeline if t > cutoff]
        if len(recent) < 3:
            return

        my_team_str = my_team.value
        my_recent = sum(1 for t, team in self._team_kills_timeline if t > cutoff and team == my_team_str)
        enemy_recent = len(recent) - my_recent

        if my_recent >= 3 and enemy_recent == 0:
            self._momentum_shifts.append(MomentumShift(
                game_time=game_time,
                description="Strong momentum - multiple kills, no deaths",
                magnitude=0.8,
                trigger_event="kill_streak",
            ))
        elif enemy_recent >= 3 and my_recent == 0:
            self._momentum_shifts.append(MomentumShift(
                game_time=game_time,
                description="Losing momentum - enemies on a run",
                magnitude=-0.8,
                trigger_event="enemy_kill_streak",
            ))

    def _find_team(self, player_name: str, state: LiveGameState) -> Optional[Team]:
        for p in state.all_players:
            if p.summoner_name == player_name:
                return p.team_enum
        return None

    def get_momentum_shifts(self) -> list[MomentumShift]:
        return list(self._momentum_shifts)

    def get_kill_leaders(self, top_n: int = 3) -> list[tuple[str, int]]:
        return self._kill_counts.most_common(top_n)

    def get_objective_timeline(self) -> list[tuple[float, str, str]]:
        return list(self._objective_timeline)

    def get_summary(self) -> dict[str, Any]:
        return {
            "total_events_processed": len(self._processed_ids),
            "total_kills": sum(self._kill_counts.values()),
            "kill_leaders": self.get_kill_leaders(),
            "momentum_shifts": len(self._momentum_shifts),
            "objectives": len(self._objective_timeline),
        }

    def reset(self) -> None:
        self._processed_ids.clear()
        self._kill_counts.clear()
        self._death_counts.clear()
        self._streaks.clear()
        self._momentum_shifts.clear()
        self._team_kills_timeline.clear()
        self._objective_timeline.clear()

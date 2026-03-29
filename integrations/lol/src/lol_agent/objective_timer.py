"""
Objective Timer — Dragon/Baron/Herald respawn countdown management.

Tracks objective kill times and computes respawn countdowns
based on official LoL respawn timers.

Location: integrations/lol/src/lol_agent/objective_timer.py

Reference (拿来主义):
  - dota2bot-OpenHyperAI: mode_roshan.lua Roshan respawn timer
  - integrations/lol/src/lol_agent/live_game_state.py: event timeline tracking
  - LeagueAI: game state tracking for objective timing
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.objective_timer.v1"

# Official LoL respawn timers (seconds)
_RESPAWN_TIMES: dict[str, float] = {
    "dragon": 300.0,    # 5 minutes
    "baron": 360.0,     # 6 minutes
    "herald": 360.0,    # 6 minutes
    "elder": 360.0,     # 6 minutes
}


class ObjectiveTimer:
    """Tracks objective respawn timers.

    Mirrors dota2bot Roshan timer pattern adapted for LoL objectives.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._timers: dict[str, dict[str, float]] = {}

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def start_timer(self, objective: str, killed_at: float) -> None:
        """Start a respawn timer for an objective.

        Args:
            objective: Objective name (dragon, baron, herald, elder).
            killed_at: Game time when objective was killed.
        """
        respawn_duration = _RESPAWN_TIMES.get(objective, 300.0)
        self._timers[objective] = {
            "killed_at": killed_at,
            "respawn_at": killed_at + respawn_duration,
            "respawn_duration": respawn_duration,
        }

        self._fire_evolution("timer_started", {
            "objective": objective,
            "respawn_at": killed_at + respawn_duration,
        })

    def get_timer(self, objective: str) -> Optional[dict[str, float]]:
        """Get timer info for an objective.

        Returns:
            Timer dict or None if not tracked.
        """
        return self._timers.get(objective)

    def time_remaining(self, objective: str, current_time: float) -> float:
        """Get time remaining until respawn.

        Args:
            objective: Objective name.
            current_time: Current game time.

        Returns:
            Seconds remaining (negative if already respawned).
        """
        timer = self._timers.get(objective)
        if timer is None:
            return 0.0
        return timer["respawn_at"] - current_time

    def clear_timer(self, objective: str) -> None:
        """Remove a timer."""
        self._timers.pop(objective, None)

    def active_count(self) -> int:
        """Number of active timers."""
        return len(self._timers)

    def get_all_timers(self) -> dict[str, dict[str, float]]:
        """Return all active timers."""
        return dict(self._timers)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

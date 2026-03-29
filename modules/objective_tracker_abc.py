"""
Objective Tracker ABC — Cross-game unified objective timing interface.

Provides abstract base class for all game-specific objective timers,
enabling unified start/remaining/clear/active access across games.

Location: modules/objective_tracker_abc.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/objective_timer.py: dragon/baron timers
  - dota2bot-OpenHyperAI/mode_roshan.lua: Roshan timer logic
  - modules/game_bridge_abc.py: ABC pattern
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

_EVOLUTION_KEY: str = "modules.objective_tracker_abc.v1"


class ObjectiveTrackerABC(ABC):
    """Abstract base class for game-specific objective timers.

    All objective trackers must provide:
    - start_timer: begin tracking an objective respawn
    - time_remaining: query remaining time
    - clear: remove a timer
    - active_timers: list active objective timers
    """

    @abstractmethod
    def start_timer(self, objective: str, game_time: float) -> None:
        """Start a respawn timer for an objective.

        Args:
            objective: Objective identifier (e.g., 'dragon', 'roshan').
            game_time: Game time when objective was taken.
        """
        ...

    @abstractmethod
    def time_remaining(self, objective: str, current_time: float) -> float:
        """Get remaining respawn time for an objective.

        Args:
            objective: Objective identifier.
            current_time: Current game time.

        Returns:
            Remaining seconds (0.0 if expired or not tracked).
        """
        ...

    @abstractmethod
    def clear(self, objective: str) -> None:
        """Clear an objective timer.

        Args:
            objective: Objective identifier to clear.
        """
        ...

    @abstractmethod
    def active_timers(self) -> list[str]:
        """List currently active objective timers.

        Returns:
            List of objective identifier strings.
        """
        ...

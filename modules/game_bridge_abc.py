"""
Game Bridge ABC — Cross-game unified bridge interface.

Provides abstract base class for all game bridges (LoL, Dota 2,
Mahjong), enabling unified connect/disconnect/state/action access.

Location: modules/game_bridge_abc.py

Reference: open_spiel/python/rl_environment.py + operatorRL patterns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

_EVOLUTION_KEY: str = "modules.game_bridge_abc.v1"


class GameBridgeABC(ABC):
    """Abstract base class for game bridge adapters.

    All game-specific bridges must implement:
    - game_name: property returning game identifier
    - connect: establish connection to game
    - disconnect: close connection
    - get_game_state: retrieve current game state
    - send_action: dispatch action to game

    Mirrors open_spiel's rl_environment.Environment interface
    adapted for real-time game integration.
    """

    @property
    @abstractmethod
    def game_name(self) -> str:
        """Unique game identifier string."""
        ...

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the game environment."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Close connection to the game environment."""
        ...

    @abstractmethod
    def get_game_state(self) -> dict[str, Any]:
        """Retrieve current game state.

        Returns:
            Dict representing current game state.
        """
        ...

    @abstractmethod
    def send_action(self, action: Any) -> bool:
        """Send an action to the game environment.

        Args:
            action: Action to execute (format depends on game).

        Returns:
            True if action was accepted.
        """
        ...

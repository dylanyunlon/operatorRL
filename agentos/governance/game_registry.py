"""
Game Registry — Unified game registration and configuration.

Central registry for all games managed by AgentOS: LoL, Dota2, Mahjong, etc.
Provides register/unregister/lookup with evolution callback hooks.

Location: agentos/governance/game_registry.py

Reference (拿来主义):
  - DI-star: policy_factory.py game registration pattern
  - agentlightning/trainer/multi_game_trainer.py: game registration dict
  - PARL: agent_factory.py build/register pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.game_registry.v1"


class GameRegistry:
    """Central registry for all games under AgentOS governance.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._registry: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register(self, game_name: str, config: dict[str, Any]) -> None:
        """Register a game with its configuration.

        Args:
            game_name: Unique game identifier.
            config: Game configuration dict.
        """
        self._registry[game_name] = {
            "config": config,
            "registered_at": time.time(),
        }
        self._fire_evolution("game_registered", {"game": game_name})

    def unregister(self, game_name: str) -> None:
        """Remove a game from the registry.

        Args:
            game_name: Game to remove.

        Raises:
            KeyError: If game not registered.
        """
        if game_name not in self._registry:
            raise KeyError(f"Game '{game_name}' not registered")
        del self._registry[game_name]

    def is_registered(self, game_name: str) -> bool:
        """Check if a game is registered."""
        return game_name in self._registry

    def get_config(self, game_name: str) -> dict[str, Any]:
        """Get configuration for a registered game.

        Args:
            game_name: Game identifier.

        Returns:
            Configuration dict.

        Raises:
            KeyError: If game not registered.
        """
        if game_name not in self._registry:
            raise KeyError(f"Game '{game_name}' not registered")
        return self._registry[game_name]["config"]

    def list_games(self) -> list[str]:
        """List all registered game names."""
        return list(self._registry.keys())

    def count(self) -> int:
        """Number of registered games."""
        return len(self._registry)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

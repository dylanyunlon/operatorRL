"""
Game Launcher — Unified game agent start/stop and process management.

Registers game configurations, launches/stops agents, tracks status.
Mirrors PARL's AgentBase lifecycle with Akagi's Controller dispatch.

Location: agentos/cli/game_launcher.py

Reference (拿来主义):
  - PARL/parl/core/agent_base.py: learn/predict/sample lifecycle
  - Akagi/mjai_bot/controller.py: bot selection + instantiation
  - agentos/governance/deployment_manager.py: deploy/stop/status pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.cli.game_launcher.v1"


class GameLauncher:
    """Unified game agent launcher and process manager.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._games: dict[str, dict[str, Any]] = {}
        self._status: dict[str, dict[str, Any]] = {}

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_game(self, game: str, config: dict[str, Any]) -> None:
        """Register a game configuration.

        Args:
            game: Game identifier.
            config: Game configuration dict with 'entry' key.
        """
        self._games[game] = config
        self._status[game] = {"status": "idle", "launched_at": None}

    def list_games(self) -> list[str]:
        """List registered game identifiers."""
        return list(self._games.keys())

    def launch(self, game: str) -> dict[str, Any]:
        """Launch a game agent.

        Args:
            game: Game identifier.

        Returns:
            Status dict.

        Raises:
            KeyError: If game not registered.
        """
        if game not in self._games:
            raise KeyError(f"Game '{game}' not registered")

        self._status[game] = {
            "status": "running",
            "launched_at": time.time(),
            "config": self._games[game],
        }

        self._fire_evolution("game_launched", {"game": game})
        return {"status": "launched", "game": game}

    def stop(self, game: str) -> None:
        """Stop a running game agent.

        Args:
            game: Game identifier.
        """
        if game in self._status:
            self._status[game]["status"] = "stopped"
            self._status[game]["stopped_at"] = time.time()

    def get_game_status(self, game: str) -> dict[str, Any]:
        """Get status of a game agent.

        Args:
            game: Game identifier.

        Returns:
            Status dict.
        """
        if game not in self._status:
            return {"status": "not_registered", "game": game}
        return dict(self._status[game])

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

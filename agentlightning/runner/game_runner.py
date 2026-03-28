"""
Game Runner — Unified game launcher and monitor.

Provides game registration, launch, stop, and status monitoring.
Adapted from agentlightning/runner/base.py lifecycle patterns
and ELF's game context management.

Location: agentlightning/runner/game_runner.py

Reference: agentlightning/runner/base.py, ELF game context.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.runner.game_runner.v1"


class GameRunner:
    """Unified game session launcher and monitor.

    Manages concurrent game sessions across different game types.
    Each game type registers a launcher callable.
    """

    def __init__(self, max_concurrent: int = 1) -> None:
        self.max_concurrent = max_concurrent
        self.registered_launchers: dict[str, Callable] = {}
        self.active_games: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_game(self, game: str, launcher: Callable) -> None:
        """Register a game type with its launcher.

        Args:
            game: Game identifier.
            launcher: Callable that returns session dict.
        """
        self.registered_launchers[game] = launcher

    def start_game(self, game: str) -> dict[str, Any]:
        """Start a game session.

        Args:
            game: Game identifier.

        Returns:
            Session dict with pid, status, etc.

        Raises:
            KeyError: If game not registered.
        """
        if game not in self.registered_launchers:
            raise KeyError(f"Game '{game}' not registered")

        launcher = self.registered_launchers[game]
        session = launcher()
        session["game"] = game
        session["start_time"] = time.time()
        self.active_games[game] = session

        self._fire_evolution({"event": "game_started", "game": game})
        return session

    def stop_game(self, game: str) -> None:
        """Stop a running game session.

        Args:
            game: Game identifier.
        """
        if game in self.active_games:
            self.active_games[game]["status"] = "stopped"
            self.active_games[game]["stop_time"] = time.time()
            del self.active_games[game]

    def get_status(self, game: str) -> Optional[dict[str, Any]]:
        """Get status of a game session.

        Args:
            game: Game identifier.

        Returns:
            Session dict or None if not running.
        """
        return self.active_games.get(game)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

"""
Assistant Registry — Cross-game real-time assistant registration/query/management.

Central registry for game-specific real-time assistants.
Supports register/unregister/query/export operations.

Location: agentos/governance/assistant_registry.py

Reference (拿来主义):
  - DI-star/distar/agent/default/policy/policy_factory.py: policy registration
  - agentos/governance/game_registry.py: register/unregister pattern
  - extensions/protocol-decoder/src/protocol_registry.py: registry pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.assistant_registry.v1"


class AssistantRegistry:
    """Central registry for game real-time assistants.

    Supports register/unregister/get_info/list/count/export.
    Duplicate registration overwrites previous entry.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._registry: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register(self, game: str, info: dict[str, Any]) -> None:
        """Register an assistant for a game.

        Args:
            game: Game identifier.
            info: Assistant info dict (name, version, etc.).
        """
        self._registry[game] = dict(info)
        logger.info("Registered assistant for: %s", game)
        self._fire_evolution({"action": "register", "game": game})

    def unregister(self, game: str) -> None:
        """Remove an assistant registration."""
        self._registry.pop(game, None)

    def get_info(self, game: str) -> Optional[dict[str, Any]]:
        """Get assistant info for a game. Returns None if not found."""
        entry = self._registry.get(game)
        return dict(entry) if entry is not None else None

    def list_games(self) -> list[str]:
        """List all registered game identifiers."""
        return sorted(self._registry.keys())

    def count(self) -> int:
        """Return number of registered assistants."""
        return len(self._registry)

    def export_summary(self) -> dict[str, Any]:
        """Export summary of all registered assistants."""
        return {game: dict(info) for game, info in self._registry.items()}

    # --- Evolution pattern ---
    def _fire_evolution(self, detail: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "key": _EVOLUTION_KEY,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                logger.warning("Evolution callback error (assistant_registry)")

"""
Protocol Registry — Multi-game protocol decoder registration + routing.

Central registry for game-specific protocol decoders.
Routes raw data to the correct decoder based on game identifier.

Location: extensions/protocol-decoder/src/protocol_registry.py

Reference (拿来主义):
  - DI-star/distar/agent/default/policy/policy_factory.py: policy registration
  - agentos/governance/game_registry.py: register/unregister/route pattern
  - extensions/protocol-decoder/src/protocol_decoder/codec.py: codec registry
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.protocol_registry.v1"


class ProtocolRegistry:
    """Central registry for game protocol decoders.

    Supports register/unregister/route/list operations.
    Duplicate registration overwrites the previous decoder.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._decoders: dict[str, Any] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register(self, game: str, decoder: Any) -> None:
        """Register a decoder for a game.

        Args:
            game: Game identifier.
            decoder: Decoder instance with decode() method.
        """
        self._decoders[game] = decoder
        logger.info("Registered decoder for game: %s", game)
        self._fire_evolution({"action": "register", "game": game})

    def unregister(self, game: str) -> None:
        """Remove a decoder for a game."""
        self._decoders.pop(game, None)

    def get_decoder(self, game: str) -> Optional[Any]:
        """Get the decoder for a game. Returns None if not found."""
        return self._decoders.get(game, None)

    def route(self, game: str, raw_data: Any, **kwargs: Any) -> dict[str, Any]:
        """Route data to the correct decoder.

        Args:
            game: Game identifier.
            raw_data: Raw data to decode.
            **kwargs: Extra args passed to decoder.decode().

        Returns:
            Decoded result dict.

        Raises:
            KeyError: If game has no registered decoder.
        """
        decoder = self._decoders.get(game)
        if decoder is None:
            raise KeyError(f"No decoder registered for game: {game}")
        return decoder.decode(raw_data, **kwargs)

    def list_games(self) -> list[str]:
        """List all registered game identifiers."""
        return sorted(self._decoders.keys())

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
                logger.warning("Evolution callback error (protocol_registry)")

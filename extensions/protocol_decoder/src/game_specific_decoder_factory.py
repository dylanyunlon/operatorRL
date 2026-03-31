"""
GameSpecificDecoderFactory — Factory-create protocol decoders by game type with caching.

Creates and caches game-specific protocol decoder instances, avoiding repeated
initialization. Integrates with GameAdapterRegistry for adapter discovery.

Location: extensions/protocol_decoder/src/game_specific_decoder_factory.py

Reference (拿来主義):
  - extensions/fiddler_bridge/src/fiddler_lol_decoder.py: decoder structure
  - extensions/protocol_decoder/src/game_adapter_registry.py（M674）: registry lookup
  - DI-star: factory pattern for observation processors

Design Notes (Knuth-level critique):
  User:
    - get_decoder() creates or returns cached — transparent caching.
    - clear_cache() forces re-creation on next get.
    - register_decoder_class() extends without modifying factory code.
  System:
    - Cache is per game_type — O(1) lookup.
    - Factory callables stored as class references — lazy instantiation.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.game_specific_decoder_factory.v1"

try:
    from .game_protocol_adapter_base import GameProtocolAdapterBase
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from game_protocol_adapter_base import GameProtocolAdapterBase


class GameSpecificDecoderFactory:
    """Factory for game-specific protocol decoders.

    Public API:
        register_decoder_class(game_type, cls)
        get_decoder(game_type) -> GameProtocolAdapterBase
        has_decoder(game_type) -> bool
        clear_cache(game_type=None)
        list_registered() -> list[str]
        get_stats() -> dict
    """

    def __init__(self) -> None:
        self._classes: Dict[str, Type[GameProtocolAdapterBase]] = {}
        self._cache: Dict[str, GameProtocolAdapterBase] = {}
        self._create_count: int = 0
        self._cache_hit_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def register_decoder_class(
        self, game_type: str, cls: Type[GameProtocolAdapterBase],
    ) -> None:
        """Register a decoder class for a game type."""
        self._classes[game_type] = cls
        # Invalidate cache for this game type if it existed
        if game_type in self._cache:
            del self._cache[game_type]
        self._fire("class_registered", {"game_type": game_type, "class": cls.__name__})

    def get_decoder(self, game_type: str) -> Optional[GameProtocolAdapterBase]:
        """Get (or create) a decoder for the given game type.

        Returns None if no class is registered for that game type.
        """
        # Cache hit
        if game_type in self._cache:
            self._cache_hit_count += 1
            return self._cache[game_type]

        # Create
        cls = self._classes.get(game_type)
        if cls is None:
            return None

        instance = cls()
        self._cache[game_type] = instance
        self._create_count += 1
        self._fire("decoder_created", {"game_type": game_type, "class": cls.__name__})
        return instance

    def has_decoder(self, game_type: str) -> bool:
        return game_type in self._classes

    def clear_cache(self, game_type: Optional[str] = None) -> None:
        """Clear cached decoder instances."""
        if game_type is not None:
            self._cache.pop(game_type, None)
        else:
            self._cache.clear()

    def list_registered(self) -> List[str]:
        return list(self._classes.keys())

    def get_stats(self) -> Dict[str, Any]:
        return {
            "registered": list(self._classes.keys()),
            "cached": list(self._cache.keys()),
            "create_count": self._create_count,
            "cache_hit_count": self._cache_hit_count,
            "cache_hit_rate": (
                self._cache_hit_count / max(self._cache_hit_count + self._create_count, 1)
            ),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")

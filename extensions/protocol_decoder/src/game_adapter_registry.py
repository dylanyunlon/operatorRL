"""
GameAdapterRegistry — Register and discover game protocol adapters by game type.

Central registry for game protocol adapters, supporting hot-plug of new game
adapters, lifecycle management, and adapter health aggregation.

Location: extensions/protocol_decoder/src/game_adapter_registry.py

Reference (拿来主义):
  - integrations/lol-history/src/lol_history/seraphine_deep_history_pipeline.py（M604）:
    module registration pattern
  - capture_to_decision_orchestrator.py（M665）: register_stage pattern
  - agentos/governance/model_versioner.py: registry pattern

Design Notes (Knuth-level critique):
  User:
    - register() is idempotent — re-registering same game_type replaces the old adapter.
    - get() returns None for unregistered types — never raises.
    - list_adapters() provides snapshot for UIs/dashboards.
  System:
    - O(1) lookup by game_type.
    - Health aggregation across all adapters in single call.
    - Hot-plug: register new adapter at runtime without restart.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.game_adapter_registry.v1"

try:
    from .game_protocol_adapter_base import GameProtocolAdapterBase
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from game_protocol_adapter_base import GameProtocolAdapterBase


class GameAdapterRegistry:
    """Central registry for game protocol adapters.

    Public API:
        register(adapter)
        unregister(game_type) -> bool
        get(game_type) -> adapter | None
        list_adapters() -> list[dict]
        get_health_all() -> dict
        connect_all(configs) -> dict[str, bool]
        disconnect_all()
    """

    def __init__(self) -> None:
        self._adapters: Dict[str, GameProtocolAdapterBase] = {}
        self._register_count: int = 0
        self._register_history: List[Dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def register(self, adapter: GameProtocolAdapterBase) -> None:
        """Register a game protocol adapter.

        Replaces existing adapter for the same game_type.
        """
        gt = adapter.game_type
        was_replaced = gt in self._adapters
        self._adapters[gt] = adapter
        self._register_count += 1
        self._register_history.append({
            "game_type": gt,
            "adapter_class": adapter.__class__.__name__,
            "replaced": was_replaced,
            "ts": time.time(),
        })
        self._fire("adapter_registered", {
            "game_type": gt,
            "replaced": was_replaced,
        })

    def unregister(self, game_type: str) -> bool:
        """Unregister an adapter. Returns True if it existed."""
        if game_type not in self._adapters:
            return False
        adapter = self._adapters.pop(game_type)
        # Disconnect if connected
        if adapter.is_connected:
            adapter.disconnect()
        self._fire("adapter_unregistered", {"game_type": game_type})
        return True

    def get(self, game_type: str) -> Optional[GameProtocolAdapterBase]:
        """Get adapter by game type. Returns None if not registered."""
        return self._adapters.get(game_type)

    def list_adapters(self) -> List[Dict[str, Any]]:
        """List all registered adapters with basic info."""
        result = []
        for gt, adapter in self._adapters.items():
            result.append({
                "game_type": gt,
                "adapter_class": adapter.__class__.__name__,
                "state": adapter.state,
                "is_connected": adapter.is_connected,
            })
        return result

    def get_health_all(self) -> Dict[str, Any]:
        """Aggregate health from all registered adapters."""
        healths = {}
        for gt, adapter in self._adapters.items():
            healths[gt] = adapter.get_health()
        return {
            "total_adapters": len(self._adapters),
            "connected": sum(1 for a in self._adapters.values() if a.is_connected),
            "adapters": healths,
        }

    def connect_all(self, configs: Dict[str, Dict[str, Any]]) -> Dict[str, bool]:
        """Connect all adapters with per-game configs.

        Args:
            configs: Dict of game_type → config dict.

        Returns:
            Dict of game_type → success bool.
        """
        results = {}
        for gt, adapter in self._adapters.items():
            cfg = configs.get(gt, {})
            results[gt] = adapter.connect(cfg)
        return results

    def disconnect_all(self) -> None:
        """Disconnect all adapters."""
        for adapter in self._adapters.values():
            adapter.disconnect()

    @property
    def registered_count(self) -> int:
        return len(self._adapters)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "registered_count": len(self._adapters),
            "total_registrations": self._register_count,
            "game_types": list(self._adapters.keys()),
            "history_len": len(self._register_history),
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

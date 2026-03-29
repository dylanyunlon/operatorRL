"""
Unified Protocol Adapter — LoL/Dota2/Mahjong protocol → unified event format.

Location: extensions/protocol-decoder/src/unified_protocol_adapter.py

Reference (拿来主義):
  - extensions/protocol-decoder/src/protocol_decoder/codec.py: codec pattern
  - Akagi/mitm: MITM protocol interception
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "extensions.protocol_decoder.unified_protocol_adapter.v1"

_SUPPORTED_GAMES = {"lol", "dota2", "mahjong"}

class UnifiedProtocolAdapter:
    """Convert game-specific protocol events into unified format."""

    def __init__(self) -> None:
        self._adapters: dict[str, Callable] = {
            "lol": self._adapt_lol,
            "dota2": self._adapt_dota2,
            "mahjong": self._adapt_mahjong,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def adapt(self, game: str, raw_event: dict[str, Any]) -> dict[str, Any]:
        adapter = self._adapters.get(game)
        if adapter is None:
            return {"game": game, "type": "unknown", "data": raw_event, "timestamp": time.time()}
        result = adapter(raw_event)
        self._fire_evolution("event_adapted", {"game": game, "type": result.get("type", "unknown")})
        return result

    def supported_games(self) -> list[str]:
        return sorted(_SUPPORTED_GAMES)

    def _adapt_lol(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"game": "lol", "type": raw.get("event_type", "game_state"), "player": raw.get("summonerName", ""), "data": raw, "timestamp": time.time()}

    def _adapt_dota2(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"game": "dota2", "type": raw.get("type", "game_state"), "player": raw.get("player_name", ""), "data": raw, "timestamp": time.time()}

    def _adapt_mahjong(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {"game": "mahjong", "type": raw.get("type", "round_event"), "player": raw.get("actor", ""), "data": raw, "timestamp": time.time()}

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

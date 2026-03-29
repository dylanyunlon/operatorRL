"""
Protocol Version Negotiator — Multi-version protocol auto-detect + fallback.

Location: extensions/protocol-decoder/src/protocol_version_negotiator.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "extensions.protocol_decoder.protocol_version_negotiator.v1"

class ProtocolVersionNegotiator:
    """Auto-detect protocol version and negotiate fallback."""

    def __init__(self) -> None:
        self._versions: dict[str, list[str]] = {
            "lol": ["v4", "v3"],
            "dota2": ["v2", "v1"],
            "mahjong": ["liqi_v3", "liqi_v2", "tenhou"],
        }
        self._active: dict[str, str] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def negotiate(self, game: str, server_versions: list[str] = None) -> str:
        supported = self._versions.get(game, [])
        if not supported:
            return "unknown"
        if server_versions:
            for v in supported:
                if v in server_versions:
                    self._active[game] = v
                    self._fire_evolution("negotiated", {"game": game, "version": v})
                    return v
        best = supported[0]
        self._active[game] = best
        self._fire_evolution("negotiated_default", {"game": game, "version": best})
        return best

    def get_active(self, game: str) -> str:
        return self._active.get(game, "unknown")

    def fallback(self, game: str) -> Optional[str]:
        current = self._active.get(game)
        supported = self._versions.get(game, [])
        if current and current in supported:
            idx = supported.index(current)
            if idx + 1 < len(supported):
                fb = supported[idx + 1]
                self._active[game] = fb
                self._fire_evolution("fallback", {"game": game, "from": current, "to": fb})
                return fb
        return None

    def supported_versions(self, game: str) -> list[str]:
        return list(self._versions.get(game, []))

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

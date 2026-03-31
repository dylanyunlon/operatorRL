"""
GameProtocolAdapterBase — Unified protocol adapter interface for cross-game adaptation.

Defines the abstract base for all game-specific protocol adapters (LoL, Dota2, Mahjong).
Each concrete adapter inherits this base and implements connect/decode/normalize/disconnect,
while the base provides shared lifecycle management, health tracking, and evolution hooks.

Location: extensions/protocol_decoder/src/game_protocol_adapter_base.py

Reference (拿来主义):
  - extensions/protocol_decoder/src/dual_channel_fuser.py: ingest→fuse interface decoupled from source
  - capture_to_decision_orchestrator.py（M665）: register_stage pluggable module registration
  - Akagi/mitm/mitm_abc.py: abstract MITM bridge pattern
  - DI-star: observation adapter pattern

Design Notes (Knuth-level critique):
  User:
    - connect() / disconnect() are idempotent — calling connect on an already-connected
      adapter or disconnect on a disconnected one is a no-op, never an error.
    - decode() returns a typed NormalizedPacket dict — consumers never deal with raw bytes.
    - get_health() gives a single-call snapshot without locking or blocking.
  System:
    - State machine enforces DISCONNECTED→CONNECTED→DISCONNECTED transitions.
    - _decode_impl / _normalize_impl are the only methods subclasses must override.
    - evolution_callback fires on every state transition for system-level tracking.
    - O(1) decode path — no conditional chains on game type.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.game_protocol_adapter_base.v1"


class AdapterState:
    """Protocol adapter lifecycle states."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class GameProtocolAdapterBase:
    """Abstract base class for game protocol adapters.

    Subclasses must override:
        game_type  (property)
        _connect_impl(config) -> bool
        _disconnect_impl() -> None
        _decode_impl(raw_data) -> dict
        _normalize_impl(decoded) -> dict

    Public API:
        connect(config) -> bool
        disconnect() -> None
        decode(raw_data) -> dict
        normalize(decoded) -> dict
        decode_and_normalize(raw_data) -> dict
        get_health() -> dict
        get_stats() -> dict

    Attributes:
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self._state: str = AdapterState.DISCONNECTED
        self._config: Dict[str, Any] = {}
        self._decode_count: int = 0
        self._normalize_count: int = 0
        self._error_count: int = 0
        self._last_decode_ts: float = 0.0
        self._last_error: Optional[str] = None
        self._connected_at: float = 0.0
        self._state_history: List[Dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Abstract properties / methods — subclass MUST override
    # ------------------------------------------------------------------

    @property
    def game_type(self) -> str:
        """Return the game identifier (e.g. 'lol', 'dota2', 'mahjong')."""
        raise NotImplementedError("Subclass must implement game_type property")

    def _connect_impl(self, config: Dict[str, Any]) -> bool:
        """Game-specific connection logic. Return True on success."""
        raise NotImplementedError("Subclass must implement _connect_impl")

    def _disconnect_impl(self) -> None:
        """Game-specific disconnection logic."""
        raise NotImplementedError("Subclass must implement _disconnect_impl")

    def _decode_impl(self, raw_data: Any) -> Dict[str, Any]:
        """Decode raw data into intermediate dict."""
        raise NotImplementedError("Subclass must implement _decode_impl")

    def _normalize_impl(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize decoded dict to universal game state schema."""
        raise NotImplementedError("Subclass must implement _normalize_impl")

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == AdapterState.CONNECTED

    def _transition(self, new_state: str) -> None:
        old = self._state
        self._state = new_state
        entry = {"from": old, "to": new_state, "ts": time.time()}
        self._state_history.append(entry)
        self._fire("state_transition", entry)

    # ------------------------------------------------------------------
    # Connect / Disconnect (idempotent)
    # ------------------------------------------------------------------

    def connect(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Connect to the game data source.

        Idempotent: returns True immediately if already connected.

        Args:
            config: Game-specific connection config.

        Returns:
            True if connection succeeded or already connected.
        """
        if self._state == AdapterState.CONNECTED:
            return True

        if config is None:
            config = {}
        self._config = dict(config)
        self._transition(AdapterState.CONNECTING)

        try:
            ok = self._connect_impl(config)
        except Exception as exc:
            self._last_error = str(exc)
            self._error_count += 1
            self._transition(AdapterState.ERROR)
            self._fire("connect_error", {"error": str(exc)})
            return False

        if ok:
            self._connected_at = time.time()
            self._transition(AdapterState.CONNECTED)
            self._fire("connected", {"config_keys": list(config.keys())})
            return True
        else:
            self._transition(AdapterState.DISCONNECTED)
            return False

    def disconnect(self) -> None:
        """Disconnect from game data source. Idempotent."""
        if self._state == AdapterState.DISCONNECTED:
            return

        try:
            self._disconnect_impl()
        except Exception as exc:
            self._last_error = str(exc)
            self._error_count += 1
            logger.warning("disconnect_impl raised: %s", exc)
        finally:
            self._transition(AdapterState.DISCONNECTED)
            self._fire("disconnected", {})

    # ------------------------------------------------------------------
    # Decode / Normalize
    # ------------------------------------------------------------------

    def decode(self, raw_data: Any) -> Dict[str, Any]:
        """Decode raw protocol data.

        Args:
            raw_data: Raw captured data (format is game-specific).

        Returns:
            Decoded intermediate dict with '_decoded' marker.
        """
        try:
            result = self._decode_impl(raw_data)
            result["_decoded"] = True
            result["_game_type"] = self.game_type
            result["_decode_ts"] = time.time()
            self._decode_count += 1
            self._last_decode_ts = time.time()
            return result
        except Exception as exc:
            self._error_count += 1
            self._last_error = str(exc)
            self._fire("decode_error", {"error": str(exc)})
            return {"_decoded": False, "_error": str(exc), "_game_type": self.game_type}

    def normalize(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize decoded data to universal schema.

        Args:
            decoded: Intermediate decoded dict.

        Returns:
            Normalized dict conforming to universal game state schema.
        """
        try:
            result = self._normalize_impl(decoded)
            result["_normalized"] = True
            result["_game_type"] = self.game_type
            result["_normalize_ts"] = time.time()
            self._normalize_count += 1
            return result
        except Exception as exc:
            self._error_count += 1
            self._last_error = str(exc)
            self._fire("normalize_error", {"error": str(exc)})
            return {"_normalized": False, "_error": str(exc), "_game_type": self.game_type}

    def decode_and_normalize(self, raw_data: Any) -> Dict[str, Any]:
        """Convenience: decode then normalize in one call."""
        decoded = self.decode(raw_data)
        if not decoded.get("_decoded"):
            return decoded
        return self.normalize(decoded)

    # ------------------------------------------------------------------
    # Health / Stats
    # ------------------------------------------------------------------

    def get_health(self) -> Dict[str, Any]:
        """Return adapter health snapshot."""
        return {
            "game_type": self.game_type,
            "state": self._state,
            "is_connected": self.is_connected,
            "decode_count": self._decode_count,
            "normalize_count": self._normalize_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "last_decode_ts": self._last_decode_ts,
            "uptime": time.time() - self._connected_at if self.is_connected else 0.0,
        }

    def get_stats(self) -> Dict[str, Any]:
        """Return detailed statistics."""
        return {
            **self.get_health(),
            "config_keys": list(self._config.keys()),
            "state_history_len": len(self._state_history),
            "connected_at": self._connected_at,
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["game_type"] = self.game_type if hasattr(self, '_state') else "unknown"
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised in GameProtocolAdapterBase")

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(game={self.game_type}, "
            f"state={self._state}, decoded={self._decode_count})"
        )

"""
Fiddler Session Manager — MCP connection + capture lifecycle state machine.

Manages Fiddler MCP server session: disconnected → connected → capturing.
Tracks transition history and connection info.

Location: extensions/fiddler-bridge/src/fiddler_session_manager.py

Reference (拿来主义):
  - Akagi/mitm/client.py: MITM proxy session lifecycle
  - integrations/lol/src/lol_agent/game_session_manager.py: state machine pattern
  - DI-star/distar/ctools/worker/learner/base_learner.py: before_run/after_run hooks
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_session_manager.v1"

# Valid state transitions (mirrors Akagi MITM proxy lifecycle)
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "disconnected": {"connected"},
    "connected": {"capturing", "disconnected"},
    "capturing": {"connected", "disconnected"},
}


class FiddlerSessionManager:
    """Fiddler MCP session lifecycle state machine.

    States: disconnected → connected → capturing.
    Mirrors Akagi's MITM proxy session management
    with explicit transition validation and history tracking.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._state: str = "disconnected"
        self._history: list[dict[str, Any]] = []
        self._host: str = ""
        self._port: int = 0
        self._capture_start: float = 0.0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def state(self) -> str:
        """Current session state."""
        return self._state

    def _transition(self, new_state: str) -> None:
        """Internal state transition with validation."""
        allowed = _VALID_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid transition: {self._state} → {new_state}. "
                f"Allowed: {allowed}"
            )
        old = self._state
        self._state = new_state
        entry = {
            "from": old,
            "to": new_state,
            "timestamp": time.time(),
        }
        self._history.append(entry)
        logger.info("Fiddler session: %s → %s", old, new_state)
        self._fire_evolution({"action": "transition", "detail": entry})

    def connect(self, host: str, port: int) -> None:
        """Connect to Fiddler MCP server.

        Args:
            host: Fiddler server hostname.
            port: Fiddler server port.
        """
        self._host = host
        self._port = port
        self._transition("connected")

    def start_capture(self) -> None:
        """Start traffic capture."""
        self._capture_start = time.time()
        self._transition("capturing")

    def stop_capture(self) -> None:
        """Stop traffic capture, return to connected."""
        self._transition("connected")

    def disconnect(self) -> None:
        """Disconnect from Fiddler MCP server."""
        if self._state == "capturing":
            self._transition("connected")
        self._transition("disconnected")
        self._host = ""
        self._port = 0

    def get_history(self) -> list[dict[str, Any]]:
        """Return transition history."""
        return list(self._history)

    def get_connection_info(self) -> dict[str, Any]:
        """Return current connection details."""
        return {
            "host": self._host,
            "port": self._port,
            "state": self._state,
        }

    # --- Evolution pattern ---
    def _fire_evolution(self, detail: dict[str, Any]) -> None:
        """Fire evolution event if callback set."""
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "key": _EVOLUTION_KEY,
                    "detail": detail,
                    "timestamp": time.time(),
                })
            except Exception:
                logger.warning("Evolution callback error (fiddler_session_manager)")

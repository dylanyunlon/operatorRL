"""
Game Session Manager — Game lifecycle state machine.

Manages game session states: idle → loading → running → ended.
Tracks transition history and session duration.

Location: integrations/lol/src/lol_agent/game_session_manager.py

Reference (拿来主义):
  - Akagi/mjai_bot/controller.py: Controller.react() event-driven state (starting_game flag)
  - DI-star/distar/ctools/worker/learner/base_learner.py: lifecycle hooks (before_run/after_run)
  - agentos/governance/deployment_manager.py: deploy/stop/status pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.game_session_manager.v1"

# Valid state transitions (Akagi: starting_game → playing → ended)
_VALID_TRANSITIONS: dict[str, set[str]] = {
    "idle": {"loading"},
    "loading": {"running", "idle"},
    "running": {"ended", "idle"},
    "ended": {"idle"},
}


class GameSessionManager:
    """Game session lifecycle state machine.

    States: idle → loading → running → ended.
    Mirrors Akagi's Controller starting_game/playing lifecycle
    with explicit transition validation and history tracking.

    Attributes:
        state: Current session state.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._state: str = "idle"
        self._history: list[dict[str, Any]] = []
        self._running_since: float = 0.0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def state(self) -> str:
        """Current session state."""
        return self._state

    def transition(self, new_state: str) -> None:
        """Transition to a new state.

        Args:
            new_state: Target state string.

        Raises:
            ValueError: If transition is not valid from current state.
        """
        valid = _VALID_TRANSITIONS.get(self._state, set())
        if new_state not in valid:
            raise ValueError(
                f"Invalid transition: {self._state} → {new_state}. "
                f"Valid targets: {valid}"
            )

        old_state = self._state
        self._state = new_state

        if new_state == "running":
            self._running_since = time.time()

        self._history.append({
            "from": old_state,
            "to": new_state,
            "timestamp": time.time(),
        })

        self._fire_evolution("state_transition", {
            "from": old_state,
            "to": new_state,
        })

    def reset(self) -> None:
        """Reset to idle state, clearing running timer."""
        self._state = "idle"
        self._running_since = 0.0

    def get_duration(self) -> float:
        """Get session duration in seconds.

        Returns:
            Duration since entering 'running' state, or 0.0 if not running.
        """
        if self._state == "running" and self._running_since > 0:
            return time.time() - self._running_since
        if self._state == "ended" and self._running_since > 0:
            return time.time() - self._running_since
        return 0.0

    def get_history(self) -> list[dict[str, Any]]:
        """Return transition history."""
        return list(self._history)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

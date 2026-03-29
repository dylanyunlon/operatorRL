"""
Rollback Manager — Auto-rollback on evolution failure.

Location: agentos/governance/rollback_manager.py
"""

from __future__ import annotations
import logging, time, copy
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.rollback_manager.v1"

class RollbackManager:
    """Manage evolution checkpoints and auto-rollback."""

    def __init__(self, max_checkpoints: int = 10) -> None:
        self._checkpoints: list[dict[str, Any]] = []
        self._max = max_checkpoints
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def checkpoint(self, generation: int, state: dict[str, Any]) -> int:
        cp = {"generation": generation, "state": copy.deepcopy(state), "timestamp": time.time()}
        self._checkpoints.append(cp)
        if len(self._checkpoints) > self._max:
            self._checkpoints = self._checkpoints[-self._max:]
        self._fire_evolution("checkpoint_created", {"generation": generation})
        return len(self._checkpoints) - 1

    def rollback(self, steps: int = 1) -> Optional[dict[str, Any]]:
        idx = len(self._checkpoints) - 1 - steps
        if idx < 0 or not self._checkpoints:
            return None
        target = self._checkpoints[idx]
        self._checkpoints = self._checkpoints[:idx + 1]
        self._fire_evolution("rollback_executed", {"to_generation": target["generation"]})
        return copy.deepcopy(target["state"])

    def get_latest(self) -> Optional[dict[str, Any]]:
        return copy.deepcopy(self._checkpoints[-1]["state"]) if self._checkpoints else None

    def checkpoint_count(self) -> int:
        return len(self._checkpoints)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

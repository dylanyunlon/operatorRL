"""
Action Space Mapper — Map strategy labels ↔ discrete action indices.

Provides bidirectional mapping, one-hot encoding, and context-aware
action masking for RL training.

Location: integrations/lol/src/lol_agent/action_space_mapper.py

Reference (拿来主义):
  - DI-star/distar/agent/default/lib/actions.py: action space definition
  - ELF: action enumeration patterns
  - operatorRL decision_engine: strategy label taxonomy
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.action_space_mapper.v1"

_DEFAULT_ACTIONS: list[str] = [
    "farm_safely",
    "push_lane",
    "freeze_lane",
    "roam_gank",
    "contest_dragon",
    "contest_baron",
    "engage_teamfight",
    "disengage",
    "split_push",
    "recall_base",
    "ward_defensively",
    "ward_aggressively",
    "poke_harass",
    "all_in",
    "retreat",
]


class ActionSpaceMapper:
    """Bidirectional mapping between strategy labels and action indices.

    Attributes:
        action_space_size: Number of discrete actions.
        default_action_index: Index returned for unknown strategies.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self, actions: Optional[list[str]] = None) -> None:
        self._actions = list(actions) if actions else list(_DEFAULT_ACTIONS)
        self._str_to_idx: dict[str, int] = {a: i for i, a in enumerate(self._actions)}
        self.action_space_size: int = len(self._actions)
        self.default_action_index: int = self._str_to_idx.get("farm_safely", 0)
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def strategy_to_action(self, strategy: str) -> int:
        idx = self._str_to_idx.get(strategy, self.default_action_index)
        self._fire_evolution({"event": "strategy_mapped", "strategy": strategy, "index": idx})
        return idx

    def action_to_strategy(self, index: int) -> str:
        if 0 <= index < len(self._actions):
            return self._actions[index]
        return self._actions[self.default_action_index]

    def list_actions(self) -> list[str]:
        return list(self._actions)

    def to_one_hot(self, index: int) -> list[float]:
        vec = [0.0] * self.action_space_size
        if 0 <= index < self.action_space_size:
            vec[index] = 1.0
        return vec

    def from_one_hot(self, one_hot: list[float]) -> int:
        return int(max(range(len(one_hot)), key=lambda i: one_hot[i]))

    def get_action_mask(self, context: dict[str, Any]) -> list[bool]:
        """Return boolean mask of valid actions given context.

        Args:
            context: Dict with in_base, alive, etc.

        Returns:
            List of booleans, True = action allowed.
        """
        mask = [True] * self.action_space_size
        alive = context.get("alive", True)
        in_base = context.get("in_base", False)

        if not alive:
            # Only recall is valid when dead
            for i in range(self.action_space_size):
                mask[i] = self._actions[i] == "recall_base"
        elif in_base:
            # Can't engage from base
            combat_actions = {"engage_teamfight", "all_in", "poke_harass"}
            for i, a in enumerate(self._actions):
                if a in combat_actions:
                    mask[i] = False

        return mask

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

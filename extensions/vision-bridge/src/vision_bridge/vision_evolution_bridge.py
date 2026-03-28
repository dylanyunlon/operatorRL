"""
Vision Evolution Bridge — Converts visual features to training spans.

Records state/action/reward triplets from the vision pipeline and
assembles them into training spans for the self-evolution loop.
Mirrors FiddlerEvolutionBridge's incomplete-triplet truncation behavior.

Location: extensions/vision-bridge/src/vision_bridge/vision_evolution_bridge.py

Reference (拿来主義):
  - operatorRL fiddler-bridge fiddler_evolution_bridge.py: build_training_spans + min(len) truncation
  - modules/evolution_loop_abc.py: record/export lifecycle
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.vision_evolution_bridge.v1"


class VisionEvolutionBridge:
    """Bridges vision features into the operatorRL training pipeline.

    Records (state, action, reward) triplets and builds training spans.
    Incomplete triplets are truncated to min(states, actions, rewards)
    following the FiddlerEvolutionBridge pattern.

    Attributes:
        state_count: Number of states recorded.
        action_count: Number of actions recorded.
        reward_count: Number of rewards recorded.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self._states: list[dict[str, Any]] = []
        self._actions: list[dict[str, Any]] = []
        self._rewards: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable] = None

    @property
    def state_count(self) -> int:
        return len(self._states)

    @property
    def action_count(self) -> int:
        return len(self._actions)

    @property
    def reward_count(self) -> int:
        return len(self._rewards)

    def record_state(self, features: list[float], timestamp: float) -> None:
        """Record a visual state observation.

        Args:
            features: Feature vector from VisualStateEncoder.
            timestamp: Capture timestamp.
        """
        self._states.append({"features": features, "timestamp": timestamp})

    def record_action(self, action: Any, timestamp: float) -> None:
        """Record an action taken.

        Args:
            action: Action value (int, dict, etc.).
            timestamp: Action timestamp.
        """
        self._actions.append({"action": action, "timestamp": timestamp})

    def record_reward(self, reward: float, timestamp: float) -> None:
        """Record a reward signal.

        Args:
            reward: Reward value.
            timestamp: Reward timestamp.
        """
        self._rewards.append({"reward": reward, "timestamp": timestamp})

    def build_training_spans(self) -> list[dict[str, Any]]:
        """Build training spans from recorded triplets.

        Uses min(len) truncation for incomplete triplets, following
        FiddlerEvolutionBridge pattern.

        Returns:
            List of training span dicts with 'state', 'action', 'reward'.
        """
        n = min(len(self._states), len(self._actions), len(self._rewards))
        if n == 0:
            return []

        spans = []
        for i in range(n):
            spans.append({
                "state": self._states[i]["features"],
                "action": self._actions[i]["action"],
                "reward": self._rewards[i]["reward"],
                "timestamp": self._states[i]["timestamp"],
            })

        return spans

    def reset(self) -> None:
        """Clear all recorded data."""
        self._states.clear()
        self._actions.clear()
        self._rewards.clear()

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

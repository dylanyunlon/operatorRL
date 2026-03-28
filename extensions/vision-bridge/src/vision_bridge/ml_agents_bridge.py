"""
ML-Agents Bridge — Unity ML-Agents environment adapter for operatorRL.

Provides a Gym-like step/reset interface that mirrors ml-agents'
observation/action/reward cycle, enabling integration with operatorRL
training pipelines.

Location: extensions/vision-bridge/src/vision_bridge/ml_agents_bridge.py

Reference (拿来主義):
  - ml-agents/ml-agents-trainer-plugin: a2c_trainer.py / dqn_trainer.py step patterns
  - ml-agents AgentAction / DecisionStep / TerminalStep interfaces
  - modules/game_bridge_abc.py: connect/step/reset pattern
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.ml_agents_bridge.v1"


class MLAgentsBridge:
    """Gym-like bridge to Unity ML-Agents environments.

    Provides step(action) → (obs, reward, done, info) interface
    plus batch_step for parallel environments.

    Attributes:
        observation_size: Dimension of observation vector.
        action_size: Number of discrete actions.
        step_count: Number of steps taken in current episode.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        obs_size: int = 64,
        action_size: int = 4,
    ) -> None:
        self.observation_size = obs_size
        self.action_size = action_size
        self.step_count: int = 0
        self._done: bool = False
        self._total_reward: float = 0.0
        self.evolution_callback: Optional[Callable] = None

    def reset(self) -> list[float]:
        """Reset the environment and return initial observation.

        Returns:
            Initial observation vector (zeros).
        """
        self.step_count = 0
        self._done = False
        self._total_reward = 0.0
        return [0.0] * self.observation_size

    def step(
        self, action: int
    ) -> tuple[list[float], float, bool, dict[str, Any]]:
        """Take one step in the environment.

        Args:
            action: Discrete action index.

        Returns:
            Tuple of (observation, reward, done, info).
        """
        self.step_count += 1

        # Stub observation: step-dependent values
        obs = [float(action) / max(self.action_size, 1)] * self.observation_size

        # Stub reward: small positive
        reward = 0.01
        self._total_reward += reward

        # Done after 1000 steps (episode length)
        done = self.step_count >= 1000
        self._done = done

        info = {
            "step": self.step_count,
            "total_reward": self._total_reward,
        }

        return obs, reward, done, info

    def batch_step(
        self, actions: list[int]
    ) -> list[tuple[list[float], float, bool, dict[str, Any]]]:
        """Take parallel steps (one per environment instance).

        Args:
            actions: List of action indices.

        Returns:
            List of (obs, reward, done, info) tuples.
        """
        results = []
        for action in actions:
            results.append(self.step(action))
        return results

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

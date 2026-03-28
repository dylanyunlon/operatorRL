"""
Dota2 Evolution Loop — Self-evolution training cycle.

Provides episode recording, fitness computation, and generation
advancement for Dota 2 agents. Adapted from DI-star's RL learner
training loop and PARL's agent-algorithm separation.

Location: integrations/dota2/src/dota2_agent/evolution_loop.py

Reference: DI-star rl_learner.py + PARL agent_base.py.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "dota2_agent.evolution_loop.v1"


class Dota2EvolutionLoop:
    """Self-evolution training loop for Dota 2 agents.

    Implements the record → evaluate → evolve → reset cycle
    adapted from DI-star's RL learner + PARL's agent.learn() pattern.
    """

    def __init__(self) -> None:
        self.generation: int = 0
        self.reward_history: list[float] = []
        self._episodes: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_episode(
        self,
        states: list[Any],
        actions: list[Any],
        reward: float,
    ) -> None:
        """Record a completed episode.

        Args:
            states: Sequence of game states.
            actions: Sequence of actions taken.
            reward: Final episode reward.
        """
        self._episodes.append({
            "states": states,
            "actions": actions,
            "reward": reward,
            "generation": self.generation,
        })
        self.reward_history.append(reward)

    def compute_fitness(self) -> float:
        """Compute current fitness from reward history.

        Uses mean reward clamped to [-1, 1].

        Returns:
            Fitness score.
        """
        if not self.reward_history:
            return 0.0
        mean_r = sum(self.reward_history) / len(self.reward_history)
        return max(-1.0, min(1.0, mean_r))

    def should_evolve(self, min_episodes: int = 10) -> bool:
        """Check if enough data for evolution step.

        Args:
            min_episodes: Minimum episodes before evolving.

        Returns:
            True if ready to evolve.
        """
        return len(self.reward_history) >= min_episodes

    def advance_generation(self) -> None:
        """Advance to next evolution generation."""
        self.generation += 1
        self._fire_evolution({
            "event": "generation_advanced",
            "generation": self.generation,
            "fitness": self.compute_fitness(),
        })

    def export_training_data(self) -> list[dict[str, Any]]:
        """Export recorded episodes for training.

        Returns:
            List of episode dicts.
        """
        return list(self._episodes)

    def reset(self) -> None:
        """Reset loop state for new training cycle."""
        self.generation = 0
        self.reward_history = []
        self._episodes = []

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

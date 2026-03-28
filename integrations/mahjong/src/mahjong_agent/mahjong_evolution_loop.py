"""
Mahjong Evolution Loop — Self-evolution lifecycle for mahjong AI.

Implements EvolutionLoopABC: record episodes → compute fitness →
evolve when ready → export training data → advance generation.

Location: integrations/mahjong/src/mahjong_agent/mahjong_evolution_loop.py

Reference (拿来主義):
  - modules/evolution_loop_abc.py: EvolutionLoopABC interface
  - Mortal reward_calculator.py: rank-based fitness computation
  - DI-star rl_learner.py: training loop lifecycle
  - operatorRL Dota2EvolutionLoop pattern (M311)
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_evolution_loop.v1"


class MahjongEvolutionLoop:
    """Self-evolution loop for mahjong AI training.

    Follows EvolutionLoopABC contract:
    1. record_episode: store completed game data
    2. compute_fitness: evaluate current model performance
    3. should_evolve: check if ready for next generation
    4. advance_generation: move to next generation
    5. export_training_data: produce training spans
    6. reset: clear for new cycle

    Attributes:
        generation: Current evolution generation number.
        episode_count: Number of episodes recorded in current generation.
        min_episodes: Minimum episodes required before evolution.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, min_episodes: int = 10) -> None:
        self.generation: int = 0
        self.min_episodes: int = min_episodes
        self._episodes: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable] = None

    @property
    def episode_count(self) -> int:
        return len(self._episodes)

    def record_episode(
        self,
        states: list[Any],
        actions: list[Any],
        reward: float,
    ) -> None:
        """Record a completed mahjong episode.

        Args:
            states: Sequence of observed game states.
            actions: Sequence of actions taken.
            reward: Final episode reward (e.g. rank-based).
        """
        self._episodes.append({
            "states": list(states),
            "actions": list(actions),
            "reward": float(reward),
            "generation": self.generation,
        })

    def compute_fitness(self) -> float:
        """Compute average fitness across recorded episodes.

        Uses average reward clipped to [0, 1].

        Returns:
            Fitness value between 0.0 and 1.0.
        """
        if not self._episodes:
            return 0.0

        avg_reward = sum(e["reward"] for e in self._episodes) / len(self._episodes)
        return max(0.0, min(1.0, avg_reward))

    def should_evolve(self, **kwargs: Any) -> bool:
        """Check if conditions for evolution are met.

        Returns True when enough episodes have been collected.
        """
        return self.episode_count >= self.min_episodes

    def advance_generation(self) -> None:
        """Advance to the next evolution generation.

        Increments generation counter and logs transition.
        """
        fitness = self.compute_fitness()
        logger.info(
            "Mahjong evolution gen %d → %d (fitness=%.4f, episodes=%d)",
            self.generation, self.generation + 1, fitness, self.episode_count,
        )
        self.generation += 1

    def export_training_data(self) -> list[dict[str, Any]]:
        """Export recorded episodes as training data.

        Returns:
            List of episode dicts with states, actions, reward.
        """
        return [
            {
                "states": e["states"],
                "actions": e["actions"],
                "reward": e["reward"],
            }
            for e in self._episodes
        ]

    def reset(self) -> None:
        """Reset loop state for a new training cycle."""
        self._episodes.clear()

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

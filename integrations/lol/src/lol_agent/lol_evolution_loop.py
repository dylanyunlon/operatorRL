"""
LoL Evolution Loop — Self-evolution lifecycle for League of Legends.

Implements EvolutionLoopABC: record episodes, compute fitness,
manage generations, export training data, and reset cycles.

Location: integrations/lol/src/lol_agent/lol_evolution_loop.py

Reference (拿来主义):
  - modules/evolution_loop_abc.py: record/fitness/evolve/export/reset contract
  - integrations/mahjong/src/mahjong_agent/mahjong_evolution_loop.py: same pattern
  - DI-star: rl_learner.py training lifecycle
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.lol_evolution_loop.v1"


class LoLEvolutionLoop:
    """Self-evolution loop for League of Legends agent.

    Implements the EvolutionLoopABC contract:
      - record_episode: store completed game episodes
      - compute_fitness: evaluate current performance
      - should_evolve: check if enough data for next generation
      - advance_generation: step to next generation
      - export_training_data: produce training spans
      - reset: clear state for new cycle

    Attributes:
        evolve_threshold: Minimum episodes before evolution triggers.
        generation: Current generation number.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        evolve_threshold: int = 10,
    ) -> None:
        self.evolve_threshold = evolve_threshold
        self.generation: int = 0
        self._episodes: list[dict[str, Any]] = []

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record_episode(
        self,
        states: list[Any],
        actions: list[Any],
        reward: float,
    ) -> None:
        """Record a completed LoL game episode.

        Args:
            states: Sequence of game states.
            actions: Sequence of actions taken.
            reward: Final episode reward.
        """
        self._episodes.append({
            "states": states,
            "actions": actions,
            "reward": max(-10.0, min(10.0, reward)),  # clip
            "timestamp": time.time(),
            "generation": self.generation,
        })

    def episode_count(self) -> int:
        """Number of recorded episodes."""
        return len(self._episodes)

    def compute_fitness(self) -> float:
        """Compute current fitness from recorded episodes.

        Uses mean clipped reward normalized to [-1, 1].

        Returns:
            Fitness score in [-1.0, 1.0].
        """
        if not self._episodes:
            return 0.0
        rewards = [ep["reward"] for ep in self._episodes]
        mean_reward = sum(rewards) / len(rewards)
        return max(-1.0, min(1.0, mean_reward))

    def should_evolve(self) -> bool:
        """Check if enough episodes to trigger evolution.

        Returns:
            True if episode_count >= evolve_threshold.
        """
        return len(self._episodes) >= self.evolve_threshold

    def advance_generation(self) -> None:
        """Advance to the next generation."""
        self.generation += 1
        self._fire_evolution("generation_advanced", {
            "generation": self.generation,
            "episodes_used": len(self._episodes),
            "fitness": self.compute_fitness(),
        })

    def export_training_data(self) -> list[dict[str, Any]]:
        """Export recorded episodes as training spans.

        Returns:
            List of training span dicts.
        """
        spans = []
        for ep in self._episodes:
            states = ep["states"]
            actions = ep["actions"]
            n = min(len(states), len(actions))
            for i in range(n):
                spans.append({
                    "state": states[i],
                    "action": actions[i],
                    "reward": ep["reward"],
                    "generation": ep["generation"],
                })
        return spans

    def reset(self) -> None:
        """Clear all recorded episodes for new cycle."""
        self._episodes.clear()

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

"""
Evolution Loop ABC — Cross-game unified self-evolution interface.

Provides abstract base class for all game-specific evolution loops,
enabling unified record/evaluate/evolve lifecycle across games.

Location: modules/evolution_loop_abc.py

Reference: DI-star rl_learner lifecycle + PARL agent.learn() pattern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

_EVOLUTION_KEY: str = "modules.evolution_loop_abc.v1"


class EvolutionLoopABC(ABC):
    """Abstract base class for game-specific evolution loops.

    All self-evolution implementations must provide:
    - record_episode: store a completed episode
    - compute_fitness: evaluate current performance
    - should_evolve: check readiness for evolution step
    - advance_generation: move to next generation
    - export_training_data: produce training spans
    - reset: clear state for new cycle
    """

    @abstractmethod
    def record_episode(
        self, states: list[Any], actions: list[Any], reward: float
    ) -> None:
        """Record a completed episode.

        Args:
            states: Sequence of observed states.
            actions: Sequence of actions taken.
            reward: Final episode reward.
        """
        ...

    @abstractmethod
    def compute_fitness(self) -> float:
        """Compute current fitness score.

        Returns:
            Fitness value (higher is better).
        """
        ...

    @abstractmethod
    def should_evolve(self, **kwargs: Any) -> bool:
        """Check if conditions for evolution are met.

        Returns:
            True if ready to evolve.
        """
        ...

    @abstractmethod
    def advance_generation(self) -> None:
        """Advance to the next evolution generation."""
        ...

    @abstractmethod
    def export_training_data(self) -> list[Any]:
        """Export recorded data for training.

        Returns:
            List of training data items.
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset loop state for a new training cycle."""
        ...

"""
Experience Store — Cross-game experience replay pool.

Provides a unified experience buffer supporting multiple games,
capacity management, random sampling, and game-specific filtering.
Adapted from agentlightning/store/base.py patterns.

Location: agentlightning/store/experience_store.py

Reference: agentlightning/store/base.py, PARL replay memory.
"""

from __future__ import annotations

import logging
import random
from collections import deque
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.store.experience_store.v1"


class ExperienceStore:
    """Cross-game experience replay buffer.

    Supports multiple game types in a single pool with
    capacity-based eviction and game-filtered sampling.
    """

    def __init__(self, capacity: int = 10000) -> None:
        self.capacity = capacity
        self._buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def __len__(self) -> int:
        return len(self._buffer)

    def add(
        self,
        game: str,
        state: Any,
        action: Any,
        reward: float,
        next_state: Any,
        done: bool,
    ) -> None:
        """Add an experience tuple.

        Args:
            game: Game identifier.
            state: Observed state.
            action: Action taken.
            reward: Reward received.
            next_state: Next observed state.
            done: Episode termination flag.
        """
        self._buffer.append({
            "game": game,
            "state": state,
            "action": action,
            "reward": reward,
            "next_state": next_state,
            "done": done,
        })

    def sample(self, batch_size: int) -> list[dict[str, Any]]:
        """Sample a random batch of experiences.

        Args:
            batch_size: Number of experiences to sample.

        Returns:
            List of experience dicts (up to min(batch_size, len)).
        """
        n = min(batch_size, len(self._buffer))
        if n == 0:
            return []
        return random.sample(list(self._buffer), n)

    def filter_by_game(self, game: str) -> list[dict[str, Any]]:
        """Filter experiences by game type.

        Args:
            game: Game identifier.

        Returns:
            List of matching experience dicts.
        """
        return [e for e in self._buffer if e["game"] == game]

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

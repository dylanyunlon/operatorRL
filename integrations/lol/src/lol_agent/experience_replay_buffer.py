"""
Experience Replay Buffer — Prioritized experience replay for RL.

Implements proportional priority replay with importance-sampling
weight correction, capacity management, and priority updates.

Location: integrations/lol/src/lol_agent/experience_replay_buffer.py

Reference (拿来主义):
  - DI-star/distar/agent/default/rl_learner.py: replay buffer
  - PARL: proportional PER implementation
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import random
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.experience_replay_buffer.v1"


class ExperienceReplayBuffer:
    """Prioritized experience replay buffer.

    Attributes:
        capacity: Maximum buffer size.
        alpha: Priority exponent (0 = uniform, 1 = full priority).
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self, capacity: int = 10000, alpha: float = 0.6) -> None:
        self.capacity = capacity
        self.alpha = alpha
        self._buffer: list[dict[str, Any]] = []
        self._priorities: list[float] = []
        self._pos: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def __len__(self) -> int:
        return len(self._buffer)

    def add(self, experience: dict[str, Any]) -> None:
        """Add experience with priority derived from td_error."""
        td_error = abs(experience.get("td_error", 1.0))
        priority = (td_error + 1e-6) ** self.alpha

        if len(self._buffer) < self.capacity:
            self._buffer.append(experience)
            self._priorities.append(priority)
        else:
            idx = self._pos % self.capacity
            self._buffer[idx] = experience
            self._priorities[idx] = priority
        self._pos += 1

        self._fire_evolution({"event": "experience_added", "buffer_size": len(self._buffer)})

    def sample(self, batch_size: int, beta: float = 0.4) -> tuple[list[dict], list[int], list[float]]:
        """Sample batch with priority-based probabilities.

        Args:
            batch_size: Number of samples.
            beta: IS weight correction exponent.

        Returns:
            (experiences, indices, is_weights)
        """
        n = len(self._buffer)
        batch_size = min(batch_size, n)

        total = sum(self._priorities)
        probs = [p / total for p in self._priorities]

        indices = random.choices(range(n), weights=probs, k=batch_size)

        # Importance-sampling weights
        min_prob = min(probs)
        weights = []
        for idx in indices:
            w = (n * probs[idx]) ** (-beta)
            weights.append(w)
        max_w = max(weights) if weights else 1.0
        weights = [w / max_w for w in weights]

        batch = [self._buffer[i] for i in indices]
        return batch, indices, weights

    def update_priorities(self, indices: list[int], td_errors: list[float]) -> None:
        """Update priorities for sampled transitions."""
        for idx, td in zip(indices, td_errors):
            if 0 <= idx < len(self._priorities):
                self._priorities[idx] = (abs(td) + 1e-6) ** self.alpha

    def clear(self) -> None:
        self._buffer.clear()
        self._priorities.clear()
        self._pos = 0

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

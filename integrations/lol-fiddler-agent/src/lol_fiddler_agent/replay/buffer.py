"""
Replay Buffer - Experience replay buffer for RL training.

Implements a prioritized experience replay buffer that stores
(state, action, reward, next_state) transitions with configurable
capacity and sampling strategies.
"""

from __future__ import annotations

import logging
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from lol_fiddler_agent.models.game_snapshot import GameSnapshot
from lol_fiddler_agent.ml.feature_extractor import extract_feature_vector, FEATURE_NAMES

logger = logging.getLogger(__name__)


@dataclass
class Transition:
    """A single RL transition."""
    state: list[float]
    action: int  # Action index
    reward: float
    next_state: list[float]
    done: bool = False
    game_time: float = 0.0
    priority: float = 1.0
    timestamp: float = field(default_factory=time.time)

    @property
    def td_error_estimate(self) -> float:
        """Rough estimate of TD error magnitude."""
        return abs(self.reward) + 0.1


@dataclass
class SampledBatch:
    """A batch of transitions sampled from the buffer."""
    states: list[list[float]]
    actions: list[int]
    rewards: list[float]
    next_states: list[list[float]]
    dones: list[bool]
    indices: list[int]
    weights: list[float]  # Importance sampling weights

    @property
    def batch_size(self) -> int:
        return len(self.states)


class ReplayBuffer:
    """Fixed-capacity replay buffer with uniform sampling.

    Example::

        buffer = ReplayBuffer(capacity=10000)
        buffer.add(state, action, reward, next_state, done)
        batch = buffer.sample(batch_size=32)
    """

    def __init__(self, capacity: int = 10000) -> None:
        self._capacity = capacity
        self._buffer: deque[Transition] = deque(maxlen=capacity)
        self._total_added = 0

    def add(
        self,
        state: list[float],
        action: int,
        reward: float,
        next_state: list[float],
        done: bool = False,
        game_time: float = 0.0,
    ) -> None:
        transition = Transition(
            state=state,
            action=action,
            reward=reward,
            next_state=next_state,
            done=done,
            game_time=game_time,
        )
        self._buffer.append(transition)
        self._total_added += 1

    def add_transition(self, transition: Transition) -> None:
        self._buffer.append(transition)
        self._total_added += 1

    def sample(self, batch_size: int) -> Optional[SampledBatch]:
        """Sample a uniform random batch."""
        if len(self._buffer) < batch_size:
            return None

        indices = random.sample(range(len(self._buffer)), batch_size)
        transitions = [self._buffer[i] for i in indices]
        uniform_weight = 1.0

        return SampledBatch(
            states=[t.state for t in transitions],
            actions=[t.action for t in transitions],
            rewards=[t.reward for t in transitions],
            next_states=[t.next_state for t in transitions],
            dones=[t.done for t in transitions],
            indices=indices,
            weights=[uniform_weight] * batch_size,
        )

    def add_from_snapshots(
        self,
        old_snapshot: GameSnapshot,
        new_snapshot: GameSnapshot,
        action: int,
        reward: float,
        done: bool = False,
    ) -> None:
        """Convenience: add transition from game snapshots."""
        state = extract_feature_vector(old_snapshot)
        next_state = extract_feature_vector(new_snapshot)
        self.add(state, action, reward, next_state, done, new_snapshot.game_time)

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def is_full(self) -> bool:
        return len(self._buffer) >= self._capacity

    @property
    def total_added(self) -> int:
        return self._total_added

    def clear(self) -> None:
        self._buffer.clear()

    def get_stats(self) -> dict[str, Any]:
        rewards = [t.reward for t in self._buffer] if self._buffer else [0.0]
        return {
            "size": len(self._buffer),
            "capacity": self._capacity,
            "total_added": self._total_added,
            "avg_reward": sum(rewards) / len(rewards),
            "min_reward": min(rewards),
            "max_reward": max(rewards),
        }


class PrioritizedReplayBuffer:
    """Prioritized experience replay with proportional prioritization.

    Higher-priority transitions (larger TD error) are sampled
    more frequently, with importance-sampling weights to correct bias.

    Example::

        buffer = PrioritizedReplayBuffer(capacity=10000, alpha=0.6)
        buffer.add(state, action, reward, next_state, done)
        batch = buffer.sample(32, beta=0.4)
        # After training, update priorities:
        buffer.update_priorities(batch.indices, new_td_errors)
    """

    def __init__(
        self,
        capacity: int = 10000,
        alpha: float = 0.6,
        epsilon: float = 1e-6,
    ) -> None:
        self._capacity = capacity
        self._alpha = alpha
        self._epsilon = epsilon
        self._buffer: list[Optional[Transition]] = [None] * capacity
        self._priorities: list[float] = [0.0] * capacity
        self._position = 0
        self._size = 0
        self._max_priority = 1.0

    def add(
        self,
        state: list[float],
        action: int,
        reward: float,
        next_state: list[float],
        done: bool = False,
        game_time: float = 0.0,
    ) -> None:
        transition = Transition(
            state=state, action=action, reward=reward,
            next_state=next_state, done=done, game_time=game_time,
            priority=self._max_priority,
        )
        self._buffer[self._position] = transition
        self._priorities[self._position] = self._max_priority ** self._alpha
        self._position = (self._position + 1) % self._capacity
        self._size = min(self._size + 1, self._capacity)

    def sample(self, batch_size: int, beta: float = 0.4) -> Optional[SampledBatch]:
        if self._size < batch_size:
            return None

        # Compute sampling probabilities
        priorities = self._priorities[:self._size]
        total_priority = sum(priorities)
        if total_priority == 0:
            probs = [1.0 / self._size] * self._size
        else:
            probs = [p / total_priority for p in priorities]

        indices = random.choices(range(self._size), weights=probs, k=batch_size)
        transitions = [self._buffer[i] for i in indices]

        # Importance sampling weights
        max_weight = (self._size * min(probs)) ** (-beta) if min(probs) > 0 else 1.0
        weights = []
        for i in indices:
            w = (self._size * probs[i]) ** (-beta)
            weights.append(w / max_weight)

        return SampledBatch(
            states=[t.state for t in transitions],
            actions=[t.action for t in transitions],
            rewards=[t.reward for t in transitions],
            next_states=[t.next_state for t in transitions],
            dones=[t.done for t in transitions],
            indices=indices,
            weights=weights,
        )

    def update_priorities(self, indices: list[int], td_errors: list[float]) -> None:
        """Update priorities after training."""
        for idx, td_error in zip(indices, td_errors):
            priority = (abs(td_error) + self._epsilon) ** self._alpha
            self._priorities[idx] = priority
            self._max_priority = max(self._max_priority, priority)

    @property
    def size(self) -> int:
        return self._size

    @property
    def capacity(self) -> int:
        return self._capacity

    def get_stats(self) -> dict[str, Any]:
        active_priorities = self._priorities[:self._size] if self._size > 0 else [0.0]
        return {
            "size": self._size,
            "capacity": self._capacity,
            "max_priority": self._max_priority,
            "avg_priority": sum(active_priorities) / len(active_priorities),
        }


# ── Evolution Integration (M282 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'replay_buffer'


class EvolvableReplayBuffer(ReplayBuffer):
    """ReplayBuffer with self-evolution metadata + training batch export.

    Extends the base buffer to tag transitions with evolution
    generation metadata and export AgentLightning-compatible batches.
    """

    def __init__(self, capacity: int = 10000) -> None:
        super().__init__(capacity)
        self._evolution_callback = None
        self._generation_tags: dict[int, int] = {}  # index -> generation

    @property
    def evolution_callback(self):
        return self._evolution_callback

    @evolution_callback.setter
    def evolution_callback(self, cb):
        self._evolution_callback = cb

    def _fire_evolution(self, data: dict) -> None:
        import time as _time
        data.setdefault('module', _EVOLUTION_KEY)
        data.setdefault('timestamp', _time.time())
        if self._evolution_callback:
            try:
                self._evolution_callback(data)
            except Exception:
                pass

    def add_with_evolution(
        self, state, action, reward, next_state, done=False,
        game_time=0.0, generation=0,
    ) -> None:
        """Add transition with evolution generation tag."""
        self.add(state, action, reward, next_state, done, game_time)
        idx = (self.total_added - 1) % self.capacity
        self._generation_tags[idx] = generation

    def to_training_batch(self, batch_size: int = 32):
        """Sample a batch formatted for AgentLightning training."""
        return self.sample(batch_size)

    def to_training_annotation(self, **kwargs) -> dict:
        annotation = {'module': _EVOLUTION_KEY}
        annotation.update(kwargs)
        return annotation

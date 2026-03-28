"""
Training Collector — converts game state/action spans to training triplets.

Collects (state, action, reward) tuples during mahjong games and exports
them in AgentLightning-compatible batch format for RL training.

Features:
- Buffered collection with configurable overflow callback
- Episode boundary marking with terminal rewards
- AgentLightning batch format export
- Statistics tracking

Location: integrations/mahjong/src/mahjong_agent/training_collector.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class CollectorConfig:
    """Configuration for training data collector."""
    max_buffer_size: int = 10000
    auto_flush: bool = True


@dataclass
class TrainingTriplet:
    """A single (state, action, reward) training sample."""
    state: dict[str, Any]
    action: dict[str, Any]
    reward: float = 0.0
    terminal: bool = False
    timestamp: float = field(default_factory=time.time)


class TrainingCollector:
    """Collects game data for RL training.

    Usage:
        collector = TrainingCollector()
        collector.record(state, action, reward=0.0)
        ...
        collector.mark_episode_end(final_reward=10.0)
        triplets = collector.flush()
        al_batch = collector.to_agent_lightning_batch()
    """

    def __init__(self, config: CollectorConfig | None = None) -> None:
        self.config = config or CollectorConfig()
        self._buffer: list[TrainingTriplet] = []
        self._total_records: int = 0
        self._total_flushes: int = 0
        self.on_overflow: Optional[Callable[[list[dict[str, Any]]], None]] = None

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_records": self._total_records,
            "buffer_size": self.buffer_size,
            "total_flushes": self._total_flushes,
        }

    def record(
        self,
        state: dict[str, Any],
        action: dict[str, Any],
        reward: float = 0.0,
    ) -> None:
        """Record a (state, action, reward) triplet.

        If buffer exceeds max_buffer_size, triggers auto-flush via on_overflow.
        """
        triplet = TrainingTriplet(state=state, action=action, reward=reward)
        self._buffer.append(triplet)
        self._total_records += 1

        # Auto-flush on overflow
        if (
            self.config.auto_flush
            and self.config.max_buffer_size > 0
            and len(self._buffer) >= self.config.max_buffer_size
        ):
            if self.on_overflow is not None:
                overflow_data = self._serialize_buffer()
                self._buffer.clear()
                self._total_flushes += 1
                self.on_overflow(overflow_data)

    def mark_episode_end(self, final_reward: float = 0.0) -> None:
        """Mark the end of a game episode.

        Sets the terminal flag on the last record and optionally
        updates its reward with the final game outcome.
        """
        if self._buffer:
            self._buffer[-1].terminal = True
            if final_reward != 0.0:
                self._buffer[-1].reward = final_reward

    def flush(self) -> list[dict[str, Any]]:
        """Flush buffer and return serialized triplets.

        Returns:
            List of dicts with keys: state, action, reward, terminal, timestamp.
        """
        data = self._serialize_buffer()
        self._buffer.clear()
        self._total_flushes += 1
        return data

    def to_agent_lightning_batch(self) -> dict[str, list[Any]]:
        """Export buffer in AgentLightning batch format.

        Returns:
            Dict with keys: states, actions, rewards, terminals.
        """
        return {
            "states": [t.state for t in self._buffer],
            "actions": [t.action for t in self._buffer],
            "rewards": [t.reward for t in self._buffer],
            "terminals": [t.terminal for t in self._buffer],
        }

    def reset(self) -> None:
        """Clear buffer and reset all statistics."""
        self._buffer.clear()
        self._total_records = 0
        self._total_flushes = 0

    def _serialize_buffer(self) -> list[dict[str, Any]]:
        """Serialize buffer to list of dicts."""
        return [
            {
                "state": t.state,
                "action": t.action,
                "reward": t.reward,
                "terminal": t.terminal,
                "timestamp": t.timestamp,
            }
            for t in self._buffer
        ]

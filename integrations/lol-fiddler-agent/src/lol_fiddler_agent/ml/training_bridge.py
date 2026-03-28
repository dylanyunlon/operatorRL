"""
Training Bridge — AgentLightning trainer bridge for LoL Fiddler Agent.

Collects (state, action, reward) spans from game sessions and
bridges them to the AgentLightning training loop.

Location: integrations/lol-fiddler-agent/src/lol_fiddler_agent/ml/training_bridge.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_fiddler_agent.ml.training_bridge.v1"


@dataclass
class TrainingBridgeConfig:
    """Configuration for the training bridge."""
    discount_factor: float = 0.99
    max_buffer_size: int = 10000
    flush_interval: float = 60.0
    normalize_rewards: bool = True


class TrainingBridge:
    """Bridge between game sessions and AgentLightning training.

    Collects spans (state, action, reward, game_time), computes
    discounted returns, and exports to training format.
    """

    def __init__(self, config: TrainingBridgeConfig | None = None) -> None:
        self.config = config or TrainingBridgeConfig()
        self._span_buffer: list[dict[str, Any]] = []

    @property
    def span_buffer(self) -> list[dict[str, Any]]:
        return self._span_buffer

    def collect_span(self, span: dict[str, Any]) -> None:
        """Add a span to the buffer.

        Args:
            span: Dict with state, action, reward, game_time keys.
        """
        span.setdefault("timestamp", time.time())
        self._span_buffer.append(span)

        # Evict oldest if over capacity
        if len(self._span_buffer) > self.config.max_buffer_size:
            self._span_buffer = self._span_buffer[-self.config.max_buffer_size:]

    def flush(self) -> list[dict[str, Any]]:
        """Flush and return all buffered spans."""
        flushed = list(self._span_buffer)
        self._span_buffer.clear()
        return flushed

    def aggregate_reward(self) -> float:
        """Sum all rewards in the buffer."""
        return sum(s.get("reward", 0.0) for s in self._span_buffer)

    def compute_discounted_returns(self) -> list[float]:
        """Compute discounted returns for the buffered trajectory.

        Uses reverse accumulation: G_t = r_t + gamma * G_{t+1}
        """
        gamma = self.config.discount_factor
        rewards = [s.get("reward", 0.0) for s in self._span_buffer]
        n = len(rewards)
        if n == 0:
            return []

        returns = [0.0] * n
        returns[-1] = rewards[-1]
        for i in range(n - 2, -1, -1):
            returns[i] = rewards[i] + gamma * returns[i + 1]

        return returns

    def to_checkpoint(self) -> dict[str, Any]:
        """Export buffer state for checkpointing."""
        return {
            "spans": list(self._span_buffer),
            "config": {
                "discount_factor": self.config.discount_factor,
                "max_buffer_size": self.config.max_buffer_size,
            },
        }

    def from_checkpoint(self, data: dict[str, Any]) -> None:
        """Restore buffer from checkpoint."""
        self._span_buffer = list(data.get("spans", []))

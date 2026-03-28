"""
Training Data Manager - Collects and manages RL training data.

Stores (state, action, reward) tuples from game sessions for
offline RL training of strategy models.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class TrainingExample:
    """A single training example for RL."""
    state_features: list[float]
    action_taken: str
    reward: float
    next_state_features: list[float]
    game_time: float
    game_id: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state_features,
            "action": self.action_taken,
            "reward": self.reward,
            "next_state": self.next_state_features,
            "game_time": self.game_time,
            "game_id": self.game_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class RewardSignal:
    """Computed reward signal from game outcome."""
    value: float
    components: dict[str, float] = field(default_factory=dict)
    reason: str = ""


class RewardCalculator:
    """Calculates reward signals from game state transitions.

    Reward components:
    - Gold difference change: +/- proportional to delta
    - Kill/death events: +1 for kills, -1 for deaths
    - Objective captures: +2 for dragon, +3 for baron
    - CS improvement: small bonus for maintaining CS pace
    - Win/loss: +10 / -10 at game end
    """

    def __init__(self, gold_weight: float = 0.001, kill_weight: float = 0.5,
                 objective_weight: float = 1.0, cs_weight: float = 0.01) -> None:
        self._gold_w = gold_weight
        self._kill_w = kill_weight
        self._obj_w = objective_weight
        self._cs_w = cs_weight

    def calculate(
        self,
        old_features: dict[str, float],
        new_features: dict[str, float],
        game_ended: bool = False,
        won: Optional[bool] = None,
    ) -> RewardSignal:
        components: dict[str, float] = {}

        # Gold difference change
        gold_delta = new_features.get("gold_diff", 0) - old_features.get("gold_diff", 0)
        components["gold"] = gold_delta * self._gold_w

        # Kill difference change
        kill_delta = new_features.get("kill_diff", 0) - old_features.get("kill_diff", 0)
        components["kills"] = kill_delta * self._kill_w

        # Dragon difference change
        dragon_delta = new_features.get("dragon_diff", 0) - old_features.get("dragon_diff", 0)
        components["objectives"] = dragon_delta * self._obj_w

        # Baron capture
        baron_delta = new_features.get("has_baron", 0) - old_features.get("has_baron", 0)
        if baron_delta > 0:
            components["objectives"] += 3.0 * self._obj_w

        # CS improvement
        cs_delta = new_features.get("cs_per_min", 0) - old_features.get("cs_per_min", 0)
        components["cs"] = max(0, cs_delta) * self._cs_w

        # Game outcome
        if game_ended and won is not None:
            components["outcome"] = 10.0 if won else -10.0

        total = sum(components.values())
        reason_parts = [f"{k}={v:+.2f}" for k, v in components.items() if abs(v) > 0.01]

        return RewardSignal(
            value=total,
            components=components,
            reason=", ".join(reason_parts),
        )


class TrainingDataStore:
    """Stores and manages training data for offline RL.

    Supports in-memory buffering with periodic flush to disk.

    Example::

        store = TrainingDataStore("/path/to/training_data")
        store.add(example)
        store.flush()  # Write to disk
    """

    def __init__(self, data_dir: str = "./training_data", max_buffer: int = 1000) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._buffer: list[TrainingExample] = []
        self._max_buffer = max_buffer
        self._total_examples = 0
        self._flush_count = 0

    def add(self, example: TrainingExample) -> None:
        self._buffer.append(example)
        self._total_examples += 1
        if len(self._buffer) >= self._max_buffer:
            self.flush()

    def add_batch(self, examples: list[TrainingExample]) -> None:
        for ex in examples:
            self.add(ex)

    def flush(self) -> int:
        """Write buffered examples to disk. Returns count written."""
        if not self._buffer:
            return 0

        self._flush_count += 1
        filename = f"training_{int(time.time())}_{self._flush_count}.jsonl"
        filepath = self._data_dir / filename

        count = 0
        with open(filepath, "w") as f:
            for ex in self._buffer:
                f.write(json.dumps(ex.to_dict()) + "\n")
                count += 1

        logger.info("Flushed %d examples to %s", count, filepath)
        self._buffer.clear()
        return count

    def load_all(self) -> list[TrainingExample]:
        """Load all training examples from disk."""
        examples: list[TrainingExample] = []
        for filepath in sorted(self._data_dir.glob("training_*.jsonl")):
            with open(filepath) as f:
                for line in f:
                    data = json.loads(line)
                    examples.append(TrainingExample(
                        state_features=data["state"],
                        action_taken=data["action"],
                        reward=data["reward"],
                        next_state_features=data["next_state"],
                        game_time=data["game_time"],
                        game_id=data.get("game_id", ""),
                        timestamp=data.get("timestamp", 0),
                        metadata=data.get("metadata", {}),
                    ))
        return examples

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    @property
    def total_count(self) -> int:
        return self._total_examples

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_examples": self._total_examples,
            "buffered": len(self._buffer),
            "flush_count": self._flush_count,
            "data_dir": str(self._data_dir),
        }

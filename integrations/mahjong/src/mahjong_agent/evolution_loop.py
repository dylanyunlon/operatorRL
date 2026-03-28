"""
Evolution Loop — Self-evolution closed loop for mahjong agent.

Implements the core cycle:
    game → log → LLM repair → weight update → next generation

Each evolution cycle:
1. Run episode(s) via MahjongGovernedEnv
2. Collect trajectories and rewards
3. Trigger AgentLightning training update
4. Increment generation counter

Location: integrations/mahjong/src/mahjong_agent/evolution_loop.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "mahjong_agent.evolution_loop.v1"


@dataclass
class EvolutionConfig:
    """Configuration for the evolution loop."""
    max_generations: int = 100
    episodes_per_generation: int = 10
    maturity_level: int = 0
    min_reward_threshold: float = -50.0
    checkpoint_interval: int = 5


@dataclass
class Trajectory:
    """A single episode trajectory."""
    events: list[dict[str, Any]] = field(default_factory=list)
    reward: float = 0.0
    start_time: float = 0.0
    end_time: float = 0.0
    steps: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "events": self.events,
            "reward": self.reward,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "steps": self.steps,
            "metadata": self.metadata,
        }


class EvolutionLoop:
    """Self-evolution closed loop manager.

    Orchestrates the cycle of:
    - Running episodes
    - Collecting training data
    - Triggering model updates
    - Tracking generation progression
    """

    def __init__(self, config: EvolutionConfig | None = None) -> None:
        self.config = config or EvolutionConfig()
        self._generation: int = 0
        self._evolution_history: list[dict[str, Any]] = []
        self._current_trajectory: Optional[Trajectory] = None
        self._training_callback: Optional[Callable] = None
        self._total_episodes: int = 0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def evolution_history(self) -> list[dict[str, Any]]:
        return list(self._evolution_history)

    def run_episode(self) -> dict[str, Any]:
        """Run one episode and return trajectory data.

        In production, this would drive MahjongGovernedEnv.
        Here we produce a minimal trajectory structure.
        """
        traj = Trajectory(
            start_time=time.time(),
            events=[],
            reward=0.0,
        )

        # Simulate episode steps (in production: env.reset() → env.step() loop)
        step_count = 0
        episode_reward = 0.0

        # Minimal simulation: 10 steps
        for i in range(10):
            event = {
                "step": i,
                "action": {"type": "dahai", "pai": "5m"},
                "reward": 0.1,
                "timestamp": time.time(),
            }
            traj.events.append(event)
            episode_reward += event["reward"]
            step_count += 1

        traj.reward = episode_reward
        traj.end_time = time.time()
        traj.steps = step_count
        traj.metadata = {
            "generation": self._generation,
            "maturity_level": self.config.maturity_level,
        }

        self._current_trajectory = traj
        self._total_episodes += 1

        return traj.to_dict()

    def evolve(self) -> dict[str, Any]:
        """Run one evolution cycle: episodes → training → generation bump.

        Returns:
            Summary of the evolution cycle.
        """
        cycle_start = time.time()
        trajectories = []
        total_reward = 0.0

        # Run episodes
        for _ in range(min(self.config.episodes_per_generation, 3)):
            traj = self.run_episode()
            trajectories.append(traj)
            total_reward += traj["reward"]

        avg_reward = total_reward / max(len(trajectories), 1)

        # Trigger training callback if registered
        if self._training_callback:
            try:
                self._training_callback(trajectories)
            except Exception as e:
                logger.error("Training callback error: %s", e)

        # Record evolution history
        record = {
            "generation": self._generation,
            "episodes": len(trajectories),
            "avg_reward": avg_reward,
            "total_reward": total_reward,
            "duration": time.time() - cycle_start,
            "maturity_level": self.config.maturity_level,
            "timestamp": time.time(),
        }
        self._evolution_history.append(record)

        # Increment generation
        self._generation += 1

        logger.info(
            "Evolution gen=%d → gen=%d, avg_reward=%.3f",
            self._generation - 1, self._generation, avg_reward,
        )

        return record

    def set_training_callback(self, callback: Callable) -> None:
        """Register callback for training updates."""
        self._training_callback = callback

    def should_checkpoint(self) -> bool:
        """Check if we should save a checkpoint."""
        return self._generation % self.config.checkpoint_interval == 0

    def to_checkpoint(self) -> dict[str, Any]:
        """Export state for checkpointing."""
        return {
            "generation": self._generation,
            "total_episodes": self._total_episodes,
            "history": self._evolution_history,
            "config": {
                "max_generations": self.config.max_generations,
                "maturity_level": self.config.maturity_level,
            },
        }

    def from_checkpoint(self, data: dict[str, Any]) -> None:
        """Restore from checkpoint."""
        self._generation = data.get("generation", 0)
        self._total_episodes = data.get("total_episodes", 0)
        self._evolution_history = data.get("history", [])

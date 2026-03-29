"""
LoL Trainer — PPO/GRPO training loop for LoL agent.

Accepts training batches, computes advantages, runs policy update
steps, and provides checkpoint save/load.

Location: agentlightning/trainer/lol_trainer.py

Reference (拿来主义):
  - DI-star/distar/agent/default/rl_learner.py: PPO training loop
  - PARL: PPO advantage computation
  - agentlightning/trainer/trainer.py: base Trainer pattern
  - agentlightning/trainer/multi_game_trainer.py: metrics buffer
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.trainer.lol_trainer.v1"


class LoLTrainer:
    """PPO/GRPO trainer specialized for LoL agent training.

    Attributes:
        algorithm: Training algorithm name ('ppo' or 'grpo').
        lr: Learning rate.
        global_step: Total training steps executed.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(
        self,
        algorithm: str = "ppo",
        lr: float = 3e-4,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_eps: float = 0.2,
    ) -> None:
        self.algorithm = algorithm
        self.lr = lr
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_eps = clip_eps
        self.global_step: int = 0
        self._total_loss: float = 0.0
        self._metrics_buffer: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def train_step(self, batch: dict[str, Any]) -> dict[str, Any]:
        """Execute one training step.

        Args:
            batch: Dict with states, actions, rewards, next_states, dones.

        Returns:
            Metrics dict with loss, step, etc.

        Raises:
            ValueError: If batch is empty or missing keys.
        """
        if not batch or "rewards" not in batch:
            raise ValueError("Batch must contain 'rewards' key at minimum")

        rewards = batch["rewards"]
        states = batch.get("states", [])
        actions = batch.get("actions", [])

        # Stub loss computation (production: actual gradient update)
        if self.algorithm == "ppo":
            loss = self._ppo_loss(states, actions, rewards)
        elif self.algorithm == "grpo":
            loss = self._grpo_loss(states, actions, rewards)
        else:
            loss = sum(abs(r) for r in rewards) / max(len(rewards), 1)

        self.global_step += 1
        self._total_loss += loss

        metrics = {
            "loss": loss,
            "step": self.global_step,
            "algorithm": self.algorithm,
            "batch_size": len(rewards),
            "avg_reward": sum(rewards) / max(len(rewards), 1),
        }
        self._metrics_buffer.append(metrics)
        self._fire_evolution({"event": "train_step", **metrics})
        return metrics

    def compute_advantages(
        self,
        rewards: list[float],
        values: list[float],
        gamma: Optional[float] = None,
    ) -> list[float]:
        """Compute GAE advantages.

        Args:
            rewards: List of rewards.
            values: List of value estimates.
            gamma: Discount factor (default: self.gamma).

        Returns:
            List of advantage floats.
        """
        g = gamma if gamma is not None else self.gamma
        lam = self.gae_lambda
        n = len(rewards)
        advantages = [0.0] * n
        last_adv = 0.0

        for t in reversed(range(n)):
            next_val = values[t + 1] if t + 1 < len(values) else 0.0
            delta = rewards[t] + g * next_val - values[t]
            last_adv = delta + g * lam * last_adv
            advantages[t] = last_adv

        return advantages

    def get_metrics(self) -> dict[str, Any]:
        return {
            "global_step": self.global_step,
            "algorithm": self.algorithm,
            "avg_loss": self._total_loss / max(self.global_step, 1),
        }

    def save_checkpoint(self) -> dict[str, Any]:
        return {
            "global_step": self.global_step,
            "algorithm": self.algorithm,
            "lr": self.lr,
            "total_loss": self._total_loss,
        }

    def load_checkpoint(self, ckpt: dict[str, Any]) -> None:
        self.global_step = ckpt.get("global_step", 0)
        self.algorithm = ckpt.get("algorithm", self.algorithm)
        self.lr = ckpt.get("lr", self.lr)
        self._total_loss = ckpt.get("total_loss", 0.0)

    # --- internal loss stubs ---

    def _ppo_loss(self, states: list, actions: list, rewards: list) -> float:
        # Stub: mean reward magnitude as proxy loss
        if not rewards:
            return 0.0
        return sum(abs(r) for r in rewards) / len(rewards) * 0.5

    def _grpo_loss(self, states: list, actions: list, rewards: list) -> float:
        if not rewards:
            return 0.0
        return sum(abs(r) for r in rewards) / len(rewards) * 0.6

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

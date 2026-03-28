"""
Multi-Game PPO — Cross-game Proximal Policy Optimization variant.

Provides PPO with per-game reward scaling, clipped surrogate loss,
and entropy bonus computation. Adapted from PARL's PPO algorithm
and open_spiel's RL agent interface.

Location: agentlightning/algorithm/multi_game_ppo.py

Reference: PARL/parl/algorithms/torch/ppo.py,
           open_spiel/python/rl_agent.py.
"""

from __future__ import annotations

import math
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.algorithm.multi_game_ppo.v1"


class MultiGamePPO:
    """PPO algorithm adapted for multi-game training.

    Extends PARL's PPO with per-game reward scaling and
    unified advantage/loss computation across game types.
    """

    def __init__(
        self,
        clip_param: float = 0.2,
        entropy_coef: float = 0.01,
        value_loss_coef: float = 0.5,
        gamma: float = 0.99,
        lam: float = 0.95,
    ) -> None:
        self.clip_param = clip_param
        self.entropy_coef = entropy_coef
        self.value_loss_coef = value_loss_coef
        self.gamma = gamma
        self.lam = lam
        self._game_scalers: dict[str, float] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def compute_advantages(
        self,
        rewards: list[float],
        values: list[float],
        dones: list[bool],
        gamma: Optional[float] = None,
    ) -> list[float]:
        """Compute GAE advantages.

        Args:
            rewards: Step rewards.
            values: Value estimates.
            dones: Episode termination flags.
            gamma: Discount factor (defaults to self.gamma).

        Returns:
            List of advantage floats.
        """
        g = gamma if gamma is not None else self.gamma
        n = len(rewards)
        advantages = [0.0] * n
        last_gae = 0.0

        for t in reversed(range(n)):
            next_value = 0.0 if (t == n - 1 or dones[t]) else values[t + 1]
            delta = rewards[t] + g * next_value - values[t]
            mask = 0.0 if dones[t] else 1.0
            last_gae = delta + g * self.lam * mask * last_gae
            advantages[t] = last_gae

        return advantages

    def compute_clip_loss(self, ratio: float, advantage: float) -> float:
        """Compute PPO clipped surrogate loss for a single sample.

        Args:
            ratio: pi_new / pi_old probability ratio.
            advantage: GAE advantage estimate.

        Returns:
            Clipped loss value.
        """
        unclipped = ratio * advantage
        clipped = max(1.0 - self.clip_param, min(1.0 + self.clip_param, ratio)) * advantage
        return min(unclipped, clipped)

    def compute_entropy_bonus(self, probs: list[float]) -> float:
        """Compute entropy bonus for exploration.

        Args:
            probs: Action probability distribution.

        Returns:
            Entropy bonus (non-negative).
        """
        entropy = 0.0
        for p in probs:
            if p > 0:
                entropy -= p * math.log(p)
        return entropy * self.entropy_coef

    def register_game_scaler(self, game: str, scale: float) -> None:
        """Register per-game reward scaling factor.

        Args:
            game: Game identifier.
            scale: Reward multiplier.
        """
        self._game_scalers[game] = scale

    def get_reward_scale(self, game: str) -> float:
        """Get reward scaling factor for a game.

        Args:
            game: Game identifier.

        Returns:
            Scale factor (1.0 default).
        """
        return self._game_scalers.get(game, 1.0)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

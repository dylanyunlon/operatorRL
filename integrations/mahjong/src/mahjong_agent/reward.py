"""
Mahjong Reward — domain-specific reward functions for RL training.

Converts mahjong game events (win/lose/draw/riichi/tenpai) into
numerical rewards compatible with AgentLightning's RL algorithms.

Reward design principles:
- Win with high score → large positive
- Lose (deal-in) → negative proportional to loss
- Tenpai achieved → small positive (progress signal)
- Riichi declared → small negative (1000pt deposit risk)
- Draw → near-zero
- Placement bonus for final ranking

Location: integrations/mahjong/src/mahjong_agent/reward.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class RewardConfig:
    """Configuration for mahjong reward computation."""
    # Score delta multipliers
    win_multiplier: float = 1.0
    lose_multiplier: float = 1.0

    # Event-specific base rewards
    tenpai_bonus: float = 0.5
    riichi_penalty: float = -0.3
    draw_reward: float = 0.0
    discard_step_reward: float = 0.0

    # Score normalization
    score_normalize_factor: float = 10000.0

    # Clipping
    max_reward: float = 50.0
    min_reward: float = -50.0

    # Placement rewards (1st→4th for 4-player)
    placement_rewards: tuple[float, ...] = (20.0, 5.0, -5.0, -20.0)


class MahjongReward:
    """Computes rewards for mahjong game events."""

    def __init__(self, config: RewardConfig | None = None) -> None:
        self.config = config or RewardConfig()

    def compute(
        self,
        event_type: str,
        score_delta: int = 0,
    ) -> float:
        """Compute reward for a game event.

        Args:
            event_type: mjai event type (agari, ryuukyoku, tenpai_achieved,
                        riichi_declared, dahai, etc.)
            score_delta: Point change (positive = gained, negative = lost).

        Returns:
            Numerical reward value, clipped to [min_reward, max_reward].
        """
        cfg = self.config
        reward = 0.0

        if event_type == "agari":
            # Win or lose (deal-in)
            normalized = score_delta / cfg.score_normalize_factor
            if score_delta >= 0:
                reward = normalized * cfg.win_multiplier
            else:
                reward = normalized * cfg.lose_multiplier

        elif event_type == "tenpai_achieved":
            reward = cfg.tenpai_bonus

        elif event_type == "riichi_declared":
            # Riichi has upfront cost but strategic value
            normalized_loss = score_delta / cfg.score_normalize_factor
            reward = cfg.riichi_penalty + normalized_loss * 0.1

        elif event_type == "ryuukyoku":
            reward = cfg.draw_reward

        elif event_type == "dahai":
            reward = cfg.discard_step_reward

        else:
            # Unknown event type — zero reward
            reward = 0.0

        # Clip to bounds
        return max(cfg.min_reward, min(cfg.max_reward, reward))

    def placement_reward(self, rank: int, total_players: int = 4) -> float:
        """Compute end-of-game placement reward.

        Args:
            rank: Final placement (1 = first, 4 = last).
            total_players: Number of players (3 or 4).

        Returns:
            Placement bonus/penalty.
        """
        cfg = self.config
        idx = rank - 1
        if 0 <= idx < len(cfg.placement_rewards):
            return cfg.placement_rewards[idx]
        return 0.0

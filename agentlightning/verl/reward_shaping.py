"""
Reward Shaping — Cross-game reward normalization.

Provides per-game reward configuration, normalization to a
common range, and clamping. Adapted from PARL's reward utility
patterns and operatorRL's compute_multidim_reward approach.

Location: agentlightning/verl/reward_shaping.py

Reference: PARL reward utils, operatorRL evolution reward patterns.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.verl.reward_shaping.v1"


class RewardShaper:
    """Cross-game reward normalizer.

    Each game registers its raw reward range. The shaper then
    normalizes any raw reward into a common range (default [-1, 1]).
    """

    def __init__(
        self,
        default_range: tuple[float, float] = (-1.0, 1.0),
    ) -> None:
        self.default_range = default_range
        self.game_configs: dict[str, dict[str, float]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_game(
        self,
        game: str,
        raw_min: float = -1.0,
        raw_max: float = 1.0,
    ) -> None:
        """Register a game's raw reward range.

        Args:
            game: Game identifier.
            raw_min: Minimum raw reward value.
            raw_max: Maximum raw reward value.
        """
        self.game_configs[game] = {"raw_min": raw_min, "raw_max": raw_max}

    def normalize(
        self,
        reward: float,
        raw_min: float,
        raw_max: float,
    ) -> float:
        """Normalize a raw reward into the default range.

        Args:
            reward: Raw reward value.
            raw_min: Minimum of raw range.
            raw_max: Maximum of raw range.

        Returns:
            Normalized reward clamped to default_range.
        """
        lo, hi = self.default_range
        if raw_max == raw_min:
            return (lo + hi) / 2.0

        # Linear map: raw_min -> lo, raw_max -> hi
        t = (reward - raw_min) / (raw_max - raw_min)
        normalized = lo + t * (hi - lo)

        # Clamp
        return max(lo, min(hi, normalized))

    def shape(self, game: str, reward: float) -> float:
        """Shape a reward for a specific game.

        Uses registered game config if available, otherwise
        passes through as-is.

        Args:
            game: Game identifier.
            reward: Raw reward.

        Returns:
            Shaped reward.
        """
        config = self.game_configs.get(game)
        if config is None:
            return reward

        return self.normalize(
            reward,
            raw_min=config["raw_min"],
            raw_max=config["raw_max"],
        )

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

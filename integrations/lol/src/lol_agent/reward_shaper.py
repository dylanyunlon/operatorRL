"""
Reward Shaper — Multi-dimension reward signal (win/KDA/CS/vision).

Computes composite reward from game outcome dimensions with configurable
weights, normalization, and reward history tracking.

Location: integrations/lol/src/lol_agent/reward_shaper.py

Reference (拿来主义):
  - DI-star/distar/agent/default/rl_learner.py: reward computation
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.reward_shaper.v1"

_DEFAULT_WEIGHTS: dict[str, float] = {
    "win": 1.0,
    "kda": 0.3,
    "cs": 0.2,
    "vision": 0.1,
}


class RewardShaper:
    """Multi-dimension reward shaper for LoL training.

    Attributes:
        weights: Dict mapping dimension name to weight.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self, weights: Optional[dict[str, float]] = None) -> None:
        self.weights = dict(weights) if weights else dict(_DEFAULT_WEIGHTS)
        self._history: list[float] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- public API ---

    def compute_reward(self, stats: dict[str, Any]) -> float:
        """Compute composite reward from game stats.

        Args:
            stats: Dict with keys win, kills, deaths, assists, cs,
                   vision_score, game_duration_min (optional).

        Returns:
            Composite reward float.
        """
        win_r = self.weights.get("win", 1.0) * (1.0 if stats.get("win") else -1.0)

        kills = stats.get("kills", 0)
        deaths = stats.get("deaths", 0)
        assists = stats.get("assists", 0)
        kda_r = self.weights.get("kda", 0.3) * self.compute_kda_reward(kills, deaths, assists)

        cs = stats.get("cs", 0)
        dur = stats.get("game_duration_min", 25)
        cs_r = self.weights.get("cs", 0.2) * self.compute_cs_reward(cs, dur)

        vis = stats.get("vision_score", 0)
        vis_r = self.weights.get("vision", 0.1) * self.compute_vision_reward(vis)

        composite = win_r + kda_r + cs_r + vis_r
        # clamp to reasonable range
        composite = max(-10.0, min(10.0, composite))

        self._history.append(composite)
        self._fire_evolution({
            "event": "reward_computed",
            "composite": composite,
            "win_r": win_r,
            "kda_r": kda_r,
            "cs_r": cs_r,
            "vis_r": vis_r,
        })
        return composite

    def compute_kda_reward(self, kills: int, deaths: int, assists: int) -> float:
        """KDA-based reward component.

        Returns:
            Float reward in approx [-2, 5] range.
        """
        effective_deaths = max(deaths, 1)
        kda = (kills + assists) / effective_deaths
        return min(kda, 5.0) - 1.0  # shift so 1.0 KDA → 0.0

    def compute_cs_reward(self, cs: int, game_duration_min: float = 25.0) -> float:
        """CS per minute reward component."""
        dur = max(game_duration_min, 1.0)
        cspm = cs / dur
        # 10 cspm is excellent → reward 1.0 ; 0 cspm → 0
        return min(cspm / 10.0, 1.5)

    def compute_vision_reward(self, vision_score: int) -> float:
        """Vision score reward component."""
        return min(vision_score / 80.0, 1.5)

    def get_reward_history(self) -> list[float]:
        return list(self._history)

    # --- internals ---

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

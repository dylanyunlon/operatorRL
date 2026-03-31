"""
CrossGameRewardNormalizer — Normalize reward signals across games to unified scale.

Maps different games' reward structures (LoL win/KDA, Dota2 net worth, Mahjong score)
to a common [-1, 1] range with configurable per-game normalization strategies.

Location: integrations/lol-history/src/lol_history/cross_game_reward_normalizer.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/reward_shaper.py: compute_reward multi-dimension scoring
  - integrations/lol-history/src/lol_history/historical_reward_reshaper.py（M617）:
    adaptive weight adjustment
  - DI-star/distar/agent/default/rl_learner.py: reward computation

Design Notes (Knuth-level critique):
  User:
    - normalize() always returns a float in [-1, 1] — consumers never worry about scale.
    - register_strategy() allows custom normalization per game without modifying core.
    - batch_normalize() processes lists efficiently without per-item overhead.
  System:
    - Strategy dispatch is O(1) dict lookup.
    - Statistics (min/max/mean) tracked per game for auto-calibration.
    - evolution_callback fires on outlier rewards for monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.cross_game_reward_normalizer.v1"


def _clamp(val: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class _GameRewardStats:
    """Per-game running statistics for auto-calibration."""

    __slots__ = ("count", "sum_val", "sum_sq", "min_val", "max_val")

    def __init__(self) -> None:
        self.count: int = 0
        self.sum_val: float = 0.0
        self.sum_sq: float = 0.0
        self.min_val: float = float("inf")
        self.max_val: float = float("-inf")

    def update(self, val: float) -> None:
        self.count += 1
        self.sum_val += val
        self.sum_sq += val * val
        self.min_val = min(self.min_val, val)
        self.max_val = max(self.max_val, val)

    @property
    def mean(self) -> float:
        return _safe_div(self.sum_val, self.count)

    @property
    def std(self) -> float:
        if self.count < 2:
            return 1.0
        var = _safe_div(self.sum_sq, self.count) - self.mean ** 2
        return math.sqrt(max(0.0, var))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "mean": self.mean,
            "std": self.std,
            "min": self.min_val if self.count > 0 else None,
            "max": self.max_val if self.count > 0 else None,
        }


# Default normalization strategies per game
def _lol_normalize(raw_reward: float, stats: Dict[str, Any]) -> float:
    """LoL: reward typically in [-2, 3] range from RewardShaper."""
    return _clamp(raw_reward / 2.0)


def _dota2_normalize(raw_reward: float, stats: Dict[str, Any]) -> float:
    """Dota2: reward based on net worth delta, typically [-5000, 5000]."""
    return _clamp(raw_reward / 5000.0)


def _mahjong_normalize(raw_reward: float, stats: Dict[str, Any]) -> float:
    """Mahjong: score delta, typically [-50000, 50000]."""
    return _clamp(raw_reward / 50000.0)


_DEFAULT_STRATEGIES: Dict[str, Callable[[float, Dict[str, Any]], float]] = {
    "lol": _lol_normalize,
    "dota2": _dota2_normalize,
    "mahjong": _mahjong_normalize,
}


class CrossGameRewardNormalizer:
    """Cross-game reward normalizer.

    Public API:
        normalize(game_type, raw_reward) -> float
        batch_normalize(game_type, rewards) -> list[float]
        register_strategy(game_type, fn)
        get_stats(game_type) -> dict
        get_all_stats() -> dict
    """

    def __init__(self, use_auto_calibration: bool = False) -> None:
        self._strategies: Dict[str, Callable[[float, Dict[str, Any]], float]] = dict(
            _DEFAULT_STRATEGIES
        )
        self._stats: Dict[str, _GameRewardStats] = {}
        self._use_auto: bool = use_auto_calibration
        self._op_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Core
    # ------------------------------------------------------------------

    def normalize(self, game_type: str, raw_reward: float) -> float:
        """Normalize a reward value to [-1, 1].

        Args:
            game_type: Game identifier.
            raw_reward: Raw reward value from game.

        Returns:
            Normalized reward in [-1, 1].
        """
        self._op_count += 1

        # Update stats
        if game_type not in self._stats:
            self._stats[game_type] = _GameRewardStats()
        self._stats[game_type].update(raw_reward)

        stats_dict = self._stats[game_type].to_dict()

        if self._use_auto and self._stats[game_type].count >= 10:
            result = self._auto_normalize(game_type, raw_reward)
        elif game_type in self._strategies:
            result = self._strategies[game_type](raw_reward, stats_dict)
        else:
            # Fallback: zscore-style
            result = self._auto_normalize(game_type, raw_reward)

        result = _clamp(result)

        # Outlier detection
        gs = self._stats[game_type]
        if gs.count > 5 and abs(raw_reward - gs.mean) > 3 * gs.std:
            self._fire("outlier_reward", {
                "game_type": game_type,
                "raw": raw_reward,
                "normalized": result,
                "mean": gs.mean,
                "std": gs.std,
            })

        return result

    def batch_normalize(self, game_type: str, rewards: List[float]) -> List[float]:
        """Normalize a batch of rewards."""
        return [self.normalize(game_type, r) for r in rewards]

    def _auto_normalize(self, game_type: str, raw_reward: float) -> float:
        """Z-score based auto-normalization."""
        gs = self._stats.get(game_type)
        if gs is None or gs.count < 2:
            return _clamp(raw_reward)
        z = _safe_div(raw_reward - gs.mean, gs.std, 0.0)
        return _clamp(z / 3.0)  # scale z-score to [-1,1] range

    # ------------------------------------------------------------------
    # Strategy registration
    # ------------------------------------------------------------------

    def register_strategy(
        self,
        game_type: str,
        fn: Callable[[float, Dict[str, Any]], float],
    ) -> None:
        """Register a custom normalization strategy for a game type."""
        self._strategies[game_type] = fn
        self._fire("strategy_registered", {"game_type": game_type})

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self, game_type: str) -> Dict[str, Any]:
        gs = self._stats.get(game_type)
        return gs.to_dict() if gs else {"count": 0}

    def get_all_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "games": {gt: gs.to_dict() for gt, gs in self._stats.items()},
            "registered_strategies": list(self._strategies.keys()),
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised in CrossGameRewardNormalizer")

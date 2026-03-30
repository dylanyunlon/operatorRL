"""
StreakMomentumAnalyzer — Track win/loss streaks and compute momentum scores.

Architecture (拿来主义):
  查看 **cross_game_pattern_miner.py** 的跨局模式挖掘方式。
  从 **live_match_history_correlator.py** 的detect_streak_pattern开始。
  实现 **StreakMomentumAnalyzer**，支持连胜/连败检测、动量评分和排队建议。

Location: integrations/lol-history/src/lol_history/streak_momentum_analyzer.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.streak_momentum_analyzer.v1"

LOSS_STREAK_WARNING = 3
LOSS_STREAK_STOP = 5


class StreakMomentumAnalyzer:
    """Track win/loss streaks and compute momentum scores."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._results: List[bool] = []

    def record_result(self, won: bool) -> None:
        self._results.append(won)

    def get_current_streak(self) -> Dict[str, Any]:
        if not self._results:
            return {"type": "none", "length": 0}
        last = self._results[-1]
        length = 0
        for r in reversed(self._results):
            if r == last:
                length += 1
            else:
                break
        return {"type": "win" if last else "loss", "length": length}

    def get_longest_streak(self) -> Dict[str, Any]:
        if not self._results:
            return {"type": "none", "length": 0}
        best_type = self._results[0]
        best_len = 1
        cur_type = self._results[0]
        cur_len = 1
        for i in range(1, len(self._results)):
            if self._results[i] == cur_type:
                cur_len += 1
            else:
                cur_type = self._results[i]
                cur_len = 1
            if cur_len > best_len:
                best_len = cur_len
                best_type = cur_type
        return {"type": "win" if best_type else "loss", "length": best_len}

    def compute_momentum(self) -> float:
        """Compute momentum score: positive = hot streak, negative = cold streak.

        Uses exponential decay weighting — recent games matter more.
        """
        if not self._results:
            return 0.0
        score = 0.0
        decay = 0.85
        weight = 1.0
        for r in reversed(self._results):
            score += weight * (1.0 if r else -1.0)
            weight *= decay
        return score

    def get_recent_form(self, n: int = 10) -> Dict[str, Any]:
        recent = self._results[-n:] if len(self._results) >= n else self._results
        if not recent:
            return {"wins": 0, "losses": 0, "winrate": 0.0, "games": 0}
        wins = sum(1 for r in recent if r)
        return {
            "wins": wins,
            "losses": len(recent) - wins,
            "winrate": wins / len(recent),
            "games": len(recent),
        }

    def should_continue_queueing(self) -> Dict[str, Any]:
        streak = self.get_current_streak()
        momentum = self.compute_momentum()
        if streak["type"] == "loss" and streak["length"] >= LOSS_STREAK_STOP:
            return {
                "recommendation": "Take a break. You are on a significant losing streak.",
                "should_queue": False,
                "streak": streak,
                "momentum": momentum,
            }
        elif streak["type"] == "loss" and streak["length"] >= LOSS_STREAK_WARNING:
            return {
                "recommendation": "Consider taking a short break to reset mentality.",
                "should_queue": True,
                "streak": streak,
                "momentum": momentum,
            }
        return {
            "recommendation": "Momentum looks fine. Keep playing if you want.",
            "should_queue": True,
            "streak": streak,
            "momentum": momentum,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_games": len(self._results),
            "current_streak": self.get_current_streak(),
            "momentum": self.compute_momentum(),
            "recent_form": self.get_recent_form(),
        }

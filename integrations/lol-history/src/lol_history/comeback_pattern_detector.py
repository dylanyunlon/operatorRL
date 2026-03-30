"""
ComebackPatternDetector — Detect comeback and throw patterns from gold differentials.

Architecture (拿来主义):
  查看 **win_condition_analyzer.py** 的胜利条件逻辑。
  实现 **ComebackPatternDetector**。

Location: integrations/lol-history/src/lol_history/comeback_pattern_detector.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.comeback_pattern_detector.v1"

COMEBACK_GOLD_THRESHOLD = -2000  # behind at 15 min to qualify


class ComebackPatternDetector:
    """Detect comeback and throw patterns from game data."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._games: List[Dict[str, Any]] = []

    def analyze_game(self, game: Dict[str, Any]) -> Dict[str, Any]:
        won = game.get("won", False)
        gold_15 = game.get("gold_diff_at_15", 0)
        gold_end = game.get("gold_diff_at_end", 0)

        is_comeback = won and gold_15 < COMEBACK_GOLD_THRESHOLD
        is_thrown = not won and gold_15 > abs(COMEBACK_GOLD_THRESHOLD)

        triggers = []
        if is_comeback:
            if game.get("baron_taken"):
                triggers.append("baron_secured")
            if game.get("elder_taken"):
                triggers.append("elder_dragon")
            if not triggers:
                triggers.append("teamfight_outplay")

        result = {
            "is_comeback": is_comeback,
            "is_thrown": is_thrown,
            "gold_diff_at_15": gold_15,
            "gold_diff_at_end": gold_end,
            "gold_swing": gold_end - gold_15,
            "triggers": triggers,
        }
        self._games.append(result)
        return result

    def get_comeback_rate(self) -> float:
        if not self._games:
            return 0.0
        behind_games = [g for g in self._games if g["gold_diff_at_15"] < COMEBACK_GOLD_THRESHOLD]
        if not behind_games:
            return 0.0
        comebacks = sum(1 for g in behind_games if g["is_comeback"])
        return comebacks / len(behind_games)

    def analyze_batch(self, games: List[Dict[str, Any]]) -> Dict[str, Any]:
        results = [self.analyze_game(g) for g in games]
        n = len(results)
        if n == 0:
            return {"total_games": 0, "comebacks": 0, "throws": 0}
        return {
            "total_games": n,
            "comebacks": sum(1 for r in results if r["is_comeback"]),
            "throws": sum(1 for r in results if r["is_thrown"]),
            "comeback_rate": self.get_comeback_rate(),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_games": len(self._games),
            "comebacks": sum(1 for g in self._games if g["is_comeback"]),
            "throws": sum(1 for g in self._games if g["is_thrown"]),
        }

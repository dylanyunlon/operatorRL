"""
ChampionWinrateMatrix — Per-champion and champion-vs-champion winrate tracking.

Architecture (拿来主义):
  查看 **matchup_database.py** 上现有 **(champion, opponent) → {wins, games}** 的对位存储方式，
  理解其模式。从 **history_match_aggregator.py** 的by_champion聚合开始。
  实现 **ChampionWinrateMatrix**，让 **M604管线** 可以 **构建完整的英雄胜率矩阵**。

Location: integrations/lol-history/src/lol_history/champion_winrate_matrix.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.champion_winrate_matrix.v1"


def _confidence(n: int, max_n: int = 20) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class ChampionWinrateMatrix:
    """Track per-champion winrates and champion-vs-champion matchup winrates."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._champ_stats: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"games": 0, "wins": 0}
        )
        self._matchup_stats: Dict[tuple, Dict[str, int]] = defaultdict(
            lambda: {"games": 0, "wins": 0}
        )

    def record_result(self, champion: str, won: bool) -> None:
        self._champ_stats[champion]["games"] += 1
        if won:
            self._champ_stats[champion]["wins"] += 1

    def record_matchup(self, champion: str, opponent: str, won: bool) -> None:
        self._matchup_stats[(champion, opponent)]["games"] += 1
        if won:
            self._matchup_stats[(champion, opponent)]["wins"] += 1
        self.record_result(champion, won)

    def get_champion_stats(self, champion: str) -> Dict[str, Any]:
        s = self._champ_stats.get(champion)
        if not s or s["games"] == 0:
            return {"champion": champion, "games": 0, "wins": 0, "winrate": 0.0, "confidence": 0.0}
        return {
            "champion": champion,
            "games": s["games"],
            "wins": s["wins"],
            "winrate": s["wins"] / s["games"],
            "confidence": _confidence(s["games"]),
        }

    def get_matchup_winrate(self, champion: str, opponent: str) -> Dict[str, Any]:
        s = self._matchup_stats.get((champion, opponent))
        if not s or s["games"] == 0:
            return {"champion": champion, "opponent": opponent, "games": 0, "winrate": 0.5, "confidence": 0.0}
        return {
            "champion": champion,
            "opponent": opponent,
            "games": s["games"],
            "winrate": s["wins"] / s["games"],
            "confidence": _confidence(s["games"]),
        }

    def get_top_champions(self, n: int = 5) -> List[Dict[str, Any]]:
        stats = [self.get_champion_stats(c) for c in self._champ_stats]
        stats.sort(key=lambda x: x["games"], reverse=True)
        return stats[:n]

    def get_worst_matchups(self, champion: str, n: int = 5) -> List[Dict[str, Any]]:
        matchups = []
        for (c, o), s in self._matchup_stats.items():
            if c == champion and s["games"] > 0:
                matchups.append({
                    "opponent": o,
                    "games": s["games"],
                    "winrate": s["wins"] / s["games"],
                })
        matchups.sort(key=lambda x: x["winrate"])
        return matchups[:n]

    def build_matrix(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        matrix: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for (c, o), s in self._matchup_stats.items():
            if c not in matrix:
                matrix[c] = {}
            matrix[c][o] = {
                "games": s["games"],
                "winrate": s["wins"] / s["games"] if s["games"] else 0.0,
            }
        return matrix

    def to_dict(self) -> Dict[str, Any]:
        return {
            "champions": {c: dict(s) for c, s in self._champ_stats.items()},
            "matchups": {f"{c}_vs_{o}": dict(s) for (c, o), s in self._matchup_stats.items()},
        }

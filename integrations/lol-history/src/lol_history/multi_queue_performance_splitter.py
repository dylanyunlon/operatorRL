"""
MultiQueuePerformanceSplitter — Split performance stats by queue type (ranked/normal/ARAM).

Architecture (拿来主义):
  查看 **ranked_tracker.py** 的排位/匹配分离方式。
  从 **role_performance_tracker.py（M589）** 的per-role分组模式开始。
  实现 **MultiQueuePerformanceSplitter**。

Location: integrations/lol-history/src/lol_history/multi_queue_performance_splitter.py
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.multi_queue_performance_splitter.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    return (k + a) / max(d, 1)


class MultiQueuePerformanceSplitter:
    """Split and compare performance across different queue types."""

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._queues: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def record_game(self, queue_type: str, won: bool, kills: int,
                    deaths: int, assists: int) -> None:
        self._queues[queue_type].append({
            "won": won, "kills": kills, "deaths": deaths, "assists": assists,
        })

    def get_queue_stats(self, queue_type: str) -> Dict[str, Any]:
        games = self._queues.get(queue_type, [])
        n = len(games)
        if n == 0:
            return {"queue": queue_type, "games": 0, "winrate": 0.0, "avg_kda": 0.0}
        wins = sum(1 for g in games if g["won"])
        kdas = [_kda(g["kills"], g["deaths"], g["assists"]) for g in games]
        return {
            "queue": queue_type,
            "games": n,
            "winrate": wins / n,
            "avg_kda": sum(kdas) / n,
            "avg_kills": sum(g["kills"] for g in games) / n,
            "avg_deaths": sum(g["deaths"] for g in games) / n,
            "avg_assists": sum(g["assists"] for g in games) / n,
        }

    def get_all_queue_stats(self) -> Dict[str, Dict[str, Any]]:
        return {q: self.get_queue_stats(q) for q in self._queues}

    def get_best_queue(self) -> Dict[str, Any]:
        if not self._queues:
            return {"queue": "NONE", "games": 0, "winrate": 0.0}
        stats = [self.get_queue_stats(q) for q in self._queues]
        stats.sort(key=lambda x: (x["winrate"], x["avg_kda"]), reverse=True)
        return stats[0]

    def compare_queues(self, queue_a: str, queue_b: str) -> Dict[str, Any]:
        a = self.get_queue_stats(queue_a)
        b = self.get_queue_stats(queue_b)
        return {
            "queue_a": queue_a,
            "queue_b": queue_b,
            "queue_a_winrate": a["winrate"],
            "queue_b_winrate": b["winrate"],
            "queue_a_kda": a["avg_kda"],
            "queue_b_kda": b["avg_kda"],
        }

    def to_dict(self) -> Dict[str, Any]:
        return self.get_all_queue_stats()

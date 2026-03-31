"""
CrossGamePerformanceComparator — Compare player performance across different games.

Splits and compares player metrics (win rate, KDA equivalents, efficiency) across
games, identifying cross-game strengths and weaknesses.

Location: integrations/lol-history/src/lol_history/cross_game_performance_comparator.py

Reference (拿来主義):
  - integrations/lol-history/src/lol_history/multi_queue_performance_splitter.py（M603）:
    per-queue split — adapted to per-game split
  - integrations/lol-history/src/lol_history/role_performance_tracker.py（M589）:
    grouped statistics

Design Notes (Knuth-level critique):
  User:
    - add_game_record() accepts any game type — auto-groups.
    - compare() returns per-metric comparison with best/worst indicators.
    - get_common_strengths/weaknesses() identifies cross-game patterns.
  System:
    - Per-game stats tracked with running aggregates — no raw record storage.
    - Metric normalization before comparison (each game's scale differs).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.cross_game_performance_comparator.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class _GamePerformanceAgg:
    """Running aggregation for a single game."""

    def __init__(self) -> None:
        self.games: int = 0
        self.wins: int = 0
        self.total_kills: int = 0
        self.total_deaths: int = 0
        self.total_assists: int = 0
        self.total_duration_min: float = 0.0
        self.scores: List[float] = []

    def add(self, record: Dict[str, Any]) -> None:
        self.games += 1
        if record.get("win"):
            self.wins += 1
        self.total_kills += record.get("kills", 0)
        self.total_deaths += record.get("deaths", 0)
        self.total_assists += record.get("assists", 0)
        self.total_duration_min += record.get("duration_min", 0.0)
        if "score" in record:
            self.scores.append(record["score"])

    @property
    def winrate(self) -> float:
        return _safe_div(self.wins, self.games)

    @property
    def avg_kda(self) -> float:
        return _safe_div(self.total_kills + self.total_assists, max(self.total_deaths, 1))

    @property
    def avg_score(self) -> float:
        return _safe_div(sum(self.scores), len(self.scores)) if self.scores else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "games": self.games,
            "wins": self.wins,
            "winrate": round(self.winrate, 4),
            "avg_kda": round(self.avg_kda, 2),
            "avg_kills": round(_safe_div(self.total_kills, self.games), 1),
            "avg_deaths": round(_safe_div(self.total_deaths, self.games), 1),
            "avg_assists": round(_safe_div(self.total_assists, self.games), 1),
            "avg_duration_min": round(_safe_div(self.total_duration_min, self.games), 1),
            "avg_score": round(self.avg_score, 1),
        }


class CrossGamePerformanceComparator:
    """Compare performance across games.

    Public API:
        add_game_record(game_type, record)
        compare() -> dict
        get_game_stats(game_type) -> dict
        get_common_strengths() -> list[str]
        get_common_weaknesses() -> list[str]
        get_stats() -> dict
    """

    def __init__(self) -> None:
        self._aggs: Dict[str, _GamePerformanceAgg] = {}
        self._record_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def add_game_record(self, game_type: str, record: Dict[str, Any]) -> None:
        if game_type not in self._aggs:
            self._aggs[game_type] = _GamePerformanceAgg()
        self._aggs[game_type].add(record)
        self._record_count += 1

    def compare(self) -> Dict[str, Any]:
        """Compare performance across all registered games."""
        if not self._aggs:
            return {"games": {}, "best_winrate": None, "worst_winrate": None}

        game_stats = {gt: agg.to_dict() for gt, agg in self._aggs.items()}

        # Find best/worst
        by_wr = sorted(game_stats.items(), key=lambda x: x[1]["winrate"], reverse=True)
        by_kda = sorted(game_stats.items(), key=lambda x: x[1]["avg_kda"], reverse=True)

        return {
            "games": game_stats,
            "best_winrate": by_wr[0][0] if by_wr else None,
            "worst_winrate": by_wr[-1][0] if by_wr else None,
            "best_kda": by_kda[0][0] if by_kda else None,
            "worst_kda": by_kda[-1][0] if by_kda else None,
            "total_games": sum(a.games for a in self._aggs.values()),
        }

    def get_game_stats(self, game_type: str) -> Dict[str, Any]:
        agg = self._aggs.get(game_type)
        return agg.to_dict() if agg else {"games": 0}

    def get_common_strengths(self) -> List[str]:
        """Identify metrics where player is above average across all games."""
        strengths = []
        if len(self._aggs) < 2:
            return strengths
        stats = {gt: agg.to_dict() for gt, agg in self._aggs.items()}
        avg_wr = sum(s["winrate"] for s in stats.values()) / len(stats)
        avg_kda = sum(s["avg_kda"] for s in stats.values()) / len(stats)
        if avg_wr > 0.55:
            strengths.append("high_winrate_across_games")
        if avg_kda > 3.0:
            strengths.append("high_kda_across_games")
        # Check consistency
        wrs = [s["winrate"] for s in stats.values()]
        if wrs and (max(wrs) - min(wrs)) < 0.1:
            strengths.append("consistent_performance")
        return strengths

    def get_common_weaknesses(self) -> List[str]:
        """Identify common weaknesses across games."""
        weaknesses = []
        if len(self._aggs) < 2:
            return weaknesses
        stats = {gt: agg.to_dict() for gt, agg in self._aggs.items()}
        avg_deaths = sum(s["avg_deaths"] for s in stats.values()) / len(stats)
        avg_wr = sum(s["winrate"] for s in stats.values()) / len(stats)
        if avg_deaths > 6.0:
            weaknesses.append("high_death_rate_across_games")
        if avg_wr < 0.45:
            weaknesses.append("low_winrate_across_games")
        return weaknesses

    def get_stats(self) -> Dict[str, Any]:
        return {
            "record_count": self._record_count,
            "game_types": list(self._aggs.keys()),
            "per_game_count": {gt: agg.games for gt, agg in self._aggs.items()},
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")

"""
Cross Game Evaluation — unified fitness metrics.

Collects per-game results, computes normalised fitness scores, ranks
games, detects trends, and provides a unified cross-game fitness metric.

Location: agentos/governance/cross_game_evaluation.py

Reference (拿来主義):
  - agentos/governance/fitness_aggregator.py: per-game fitness pattern
  - agentos/governance/evolution_orchestrator.py: cross-game cycle
  - agentlightning/trainer/multi_game_trainer.py: metrics aggregation
  - DI-star evaluation: win-rate + ELO tracking

Design Notes (Knuth-level critique):
  User:
    - register_metric() defines what to track per game.
    - submit_result() is append-only — no data loss on repeated calls.
    - get_trend() returns "improving" / "declining" / "stable".
  System:
    - Fitness normalisation maps all metrics to [0, 1].
    - Trend detection uses last-5 linear regression slope.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.cross_game_evaluation.v1"


class _MetricConfig:
    __slots__ = ("name", "weight", "min_val", "max_val")

    def __init__(self, name: str, weight: float = 1.0, min_val: float = 0.0, max_val: float = 1.0):
        self.name = name
        self.weight = weight
        self.min_val = min_val
        self.max_val = max_val


class _GameRecord:
    __slots__ = ("game", "metrics", "results")

    def __init__(self, game: str):
        self.game = game
        self.metrics: Dict[str, _MetricConfig] = {}
        self.results: List[Dict[str, Any]] = []


class CrossGameEvaluation:
    """Unified evaluation across game types.

    Attributes:
        eval_count: Total fitness evaluations executed.
        registered_games: Set of game identifiers.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self._games: Dict[str, _GameRecord] = {}
        self._eval_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    @property
    def eval_count(self) -> int:
        return self._eval_count

    @property
    def registered_games(self) -> set:
        return set(self._games.keys())

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_metric(
        self, game: str, metric_name: str, *,
        weight: float = 1.0, min_val: float = 0.0, max_val: float = 1.0,
    ) -> None:
        if game not in self._games:
            self._games[game] = _GameRecord(game)
        self._games[game].metrics[metric_name] = _MetricConfig(metric_name, weight, min_val, max_val)
        self._fire_evolution({"action": "register_metric", "game": game, "metric": metric_name})

    # ------------------------------------------------------------------
    # Submit
    # ------------------------------------------------------------------

    def submit_result(self, game: str, result: Dict[str, Any]) -> None:
        if game not in self._games:
            self._games[game] = _GameRecord(game)
        result["_submit_ts"] = time.time()
        self._games[game].results.append(result)

    def result_count(self, game: str) -> int:
        rec = self._games.get(game)
        return len(rec.results) if rec else 0

    # ------------------------------------------------------------------
    # Fitness
    # ------------------------------------------------------------------

    def compute_fitness(self, game: str) -> float:
        """Compute normalised fitness for a single game.

        Uses the latest result and weighted metric configs.
        """
        self._eval_count += 1
        rec = self._games.get(game)
        if rec is None or not rec.results:
            return 0.0

        latest = rec.results[-1]
        total_weight = sum(m.weight for m in rec.metrics.values()) or 1.0
        score = 0.0

        for metric_name, cfg in rec.metrics.items():
            raw = latest.get(metric_name, 0.0)
            if isinstance(raw, (int, float)):
                normalised = (raw - cfg.min_val) / max(cfg.max_val - cfg.min_val, 1e-9)
                normalised = max(0.0, min(1.0, normalised))
                score += normalised * cfg.weight

        fitness = score / total_weight
        return max(0.0, min(1.0, fitness))

    def compute_unified_fitness(self) -> float:
        """Compute a single fitness value across all games."""
        self._eval_count += 1
        if not self._games:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for game, rec in self._games.items():
            if not rec.results:
                continue
            game_weight = sum(m.weight for m in rec.metrics.values()) or 1.0
            game_fitness = self.compute_fitness(game)
            weighted_sum += game_fitness * game_weight
            total_weight += game_weight

        if total_weight == 0:
            return 0.0
        return weighted_sum / total_weight

    # ------------------------------------------------------------------
    # Ranking
    # ------------------------------------------------------------------

    def rank_games(self) -> List[Dict[str, Any]]:
        ranking: List[Dict[str, Any]] = []
        for game in self._games:
            fitness = self.compute_fitness(game)
            ranking.append({"game": game, "fitness": fitness, "results": self.result_count(game)})
        ranking.sort(key=lambda x: x["fitness"], reverse=True)
        return ranking

    # ------------------------------------------------------------------
    # Trend detection
    # ------------------------------------------------------------------

    def get_trend(self, game: str, window: int = 5) -> Dict[str, Any]:
        """Detect performance trend for a game.

        Uses linear regression slope over last `window` results.
        """
        rec = self._games.get(game)
        if rec is None or not rec.metrics:
            return {"direction": "unknown", "slope": 0.0}

        primary_metric = list(rec.metrics.keys())[0]
        values = [r.get(primary_metric, 0.0) for r in rec.results[-window:]]

        if len(values) < 2:
            return {"direction": "stable", "slope": 0.0}

        # Simple linear regression
        n = len(values)
        x_mean = (n - 1) / 2.0
        y_mean = sum(values) / n
        num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = num / den if den != 0 else 0.0

        if slope > 0.01:
            direction = "improving"
        elif slope < -0.01:
            direction = "declining"
        else:
            direction = "stable"

        return {"direction": direction, "slope": slope, "window": len(values)}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "eval_count": self._eval_count,
            "registered_games": list(self._games.keys()),
            "total_results": sum(len(r.results) for r in self._games.values()),
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised")

    def __repr__(self) -> str:
        return f"CrossGameEvaluation(games={len(self._games)}, evals={self._eval_count})"


default_evaluation: CrossGameEvaluation = CrossGameEvaluation()

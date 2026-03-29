"""
Fitness Aggregator — Cross-module fitness score aggregation.
Location: agentos/governance/fitness_aggregator.py
Reference: DI-star training metrics, PARL fitness evaluation
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional
from collections import defaultdict
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.fitness_aggregator.v1"

class FitnessAggregator:
    def __init__(self) -> None:
        self._latest: dict[str, float] = {}
        self._weights: dict[str, float] = {}
        self._history: dict[str, list[float]] = defaultdict(list)
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def report(self, module: str, fitness: float, weight: float = 1.0) -> None:
        clamped = min(max(fitness, 0.0), 1.0)
        self._latest[module] = clamped
        self._weights[module] = weight
        self._history[module].append(clamped)
        if self.evolution_callback:
            self.evolution_callback({"type": "fitness_reported", "key": _EVOLUTION_KEY,
                                     "module": module, "fitness": clamped, "timestamp": time.time()})

    def aggregate(self) -> dict[str, Any]:
        if not self._latest:
            return {"global_fitness": 0.0, "per_module": {}}
        total_w = sum(self._weights.get(m, 1.0) for m in self._latest)
        weighted_sum = sum(self._latest[m] * self._weights.get(m, 1.0) for m in self._latest)
        return {"global_fitness": weighted_sum / total_w if total_w > 0 else 0.0,
                "per_module": dict(self._latest)}

    def get_history(self, module: str) -> list[float]:
        return list(self._history.get(module, []))

    def get_trend(self, module: str) -> str:
        h = self._history.get(module, [])
        if len(h) < 3:
            return "insufficient_data"
        recent = h[-3:]
        if all(recent[i] <= recent[i + 1] for i in range(len(recent) - 1)):
            return "improving"
        if all(recent[i] >= recent[i + 1] for i in range(len(recent) - 1)):
            return "declining"
        return "stable"

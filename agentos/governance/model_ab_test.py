"""
Model A/B Test — New/old model online comparison.

Creates experiments, records outcomes, computes statistical significance
via Welch's t-test, and manages traffic splitting.

Location: agentos/governance/model_ab_test.py

Reference (拿来主义):
  - agentos/governance/ab_test_controller.py: ABTestController pattern
  - agentlightning/algorithm/self_play_scheduler.py: Elo comparison
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import math
import random
import time
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.model_ab_test.v1"


class ModelABTest:
    """A/B testing controller for model comparison.

    Attributes:
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def create_experiment(
        self,
        name: str,
        model_a: str,
        model_b: str,
        split: float = 0.5,
    ) -> str:
        exp_id = uuid.uuid4().hex[:12]
        self._experiments[exp_id] = {
            "name": name,
            "model_a": model_a,
            "model_b": model_b,
            "split": split,
            "outcomes_a": [],
            "outcomes_b": [],
            "created_at": time.time(),
            "concluded": False,
        }
        self._fire_evolution({"event": "experiment_created", "exp_id": exp_id, "name": name})
        return exp_id

    def record_outcome(self, exp_id: str, model: str, reward: float) -> None:
        exp = self._get_exp(exp_id)
        if model == exp["model_a"] or model == "A":
            exp["outcomes_a"].append(reward)
        else:
            exp["outcomes_b"].append(reward)

    def get_experiment_stats(self, exp_id: str) -> dict[str, Any]:
        exp = self._get_exp(exp_id)
        a = exp["outcomes_a"]
        b = exp["outcomes_b"]
        return {
            "a_count": len(a),
            "b_count": len(b),
            "a_mean": sum(a) / max(len(a), 1),
            "b_mean": sum(b) / max(len(b), 1),
        }

    def evaluate_significance(self, exp_id: str, alpha: float = 0.05) -> dict[str, Any]:
        exp = self._get_exp(exp_id)
        a = exp["outcomes_a"]
        b = exp["outcomes_b"]
        if len(a) < 5 or len(b) < 5:
            return {"significant": False, "reason": "insufficient_data"}

        mean_a = sum(a) / len(a)
        mean_b = sum(b) / len(b)
        var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1) if len(a) > 1 else 0
        var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1) if len(b) > 1 else 0

        se = math.sqrt(var_a / len(a) + var_b / len(b)) if (var_a + var_b) > 0 else 1e-9
        t_stat = (mean_a - mean_b) / se

        # Simplified significance check (|t| > 1.96 for p < 0.05)
        significant = abs(t_stat) > 1.96
        return {"significant": significant, "t_stat": t_stat, "mean_a": mean_a, "mean_b": mean_b}

    def select_model(self, exp_id: str) -> str:
        exp = self._get_exp(exp_id)
        if random.random() < exp["split"]:
            return "A"
        return "B"

    def conclude(self, exp_id: str) -> dict[str, Any]:
        exp = self._get_exp(exp_id)
        stats = self.get_experiment_stats(exp_id)
        winner = "A" if stats["a_mean"] >= stats["b_mean"] else "B"
        exp["concluded"] = True
        return {"winner": winner, **stats}

    def list_experiments(self) -> list[dict[str, Any]]:
        return [
            {"exp_id": eid, "name": exp["name"], "concluded": exp["concluded"]}
            for eid, exp in self._experiments.items()
        ]

    def get_experiment(self, exp_id: str) -> dict[str, Any]:
        exp = self._get_exp(exp_id)
        return {"name": exp["name"], "model_a": exp["model_a"], "model_b": exp["model_b"]}

    def _get_exp(self, exp_id: str) -> dict[str, Any]:
        if exp_id not in self._experiments:
            raise KeyError(f"Experiment '{exp_id}' not found")
        return self._experiments[exp_id]

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

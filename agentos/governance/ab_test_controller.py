"""
A/B Test Controller — Multi-strategy parallel comparison.

Manages experiments with multiple variants, records outcomes,
computes statistical significance using Welch's t-test.

Location: agentos/governance/ab_test_controller.py

Reference (拿来主义):
  - agentlightning/algorithm/self_play_scheduler.py: Elo comparison pattern
  - DI-star: rl_learner.py evaluation comparison
  - operatorRL: A/B testing design from plan.md
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.ab_test_controller.v1"


class ABTestController:
    """A/B test controller for multi-strategy comparison.

    Supports experiment creation, variant assignment, outcome recording,
    and statistical significance testing via Welch's t-test.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._experiments: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def create_experiment(
        self,
        name: str,
        variants: list[str],
        traffic_split: list[float],
    ) -> str:
        """Create a new A/B experiment.

        Args:
            name: Unique experiment name.
            variants: List of variant identifiers.
            traffic_split: Traffic allocation per variant (must sum to ~1.0).

        Returns:
            Experiment ID (same as name).
        """
        self._experiments[name] = {
            "variants": variants,
            "traffic_split": traffic_split,
            "outcomes": {v: [] for v in variants},
            "created_at": time.time(),
        }
        self._fire_evolution("experiment_created", {"name": name, "variants": variants})
        return name

    def assign_variant(self, experiment: str, user_id: str) -> str:
        """Assign a user to a variant deterministically.

        Uses hash-based assignment for consistency.

        Args:
            experiment: Experiment name.
            user_id: User identifier.

        Returns:
            Assigned variant name.
        """
        exp = self._experiments[experiment]
        h = int(hashlib.md5(f"{experiment}:{user_id}".encode()).hexdigest(), 16)
        ratio = (h % 10000) / 10000.0

        cumulative = 0.0
        for variant, split in zip(exp["variants"], exp["traffic_split"]):
            cumulative += split
            if ratio < cumulative:
                return variant
        return exp["variants"][-1]

    def record_outcome(
        self, experiment: str, variant: str, metric_value: float
    ) -> None:
        """Record a metric outcome for a variant.

        Args:
            experiment: Experiment name.
            variant: Variant identifier.
            metric_value: Observed metric value.
        """
        self._experiments[experiment]["outcomes"][variant].append(metric_value)

    def get_results(self, experiment: str) -> dict[str, dict[str, Any]]:
        """Get summary results for an experiment.

        Args:
            experiment: Experiment name.

        Returns:
            Dict mapping variant → {mean, std, count}.
        """
        outcomes = self._experiments[experiment]["outcomes"]
        results = {}
        for variant, values in outcomes.items():
            n = len(values)
            if n == 0:
                results[variant] = {"mean": 0.0, "std": 0.0, "count": 0}
                continue
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / max(n - 1, 1)
            results[variant] = {
                "mean": mean,
                "std": math.sqrt(variance),
                "count": n,
            }
        return results

    def is_significant(
        self, experiment: str, p_threshold: float = 0.05
    ) -> bool:
        """Test statistical significance between first two variants.

        Uses Welch's t-test approximation.

        Args:
            experiment: Experiment name.
            p_threshold: Significance threshold.

        Returns:
            True if difference is statistically significant.
        """
        results = self.get_results(experiment)
        variants = list(results.keys())
        if len(variants) < 2:
            return False

        a = results[variants[0]]
        b = results[variants[1]]

        if a["count"] < 5 or b["count"] < 5:
            return False

        se_a = (a["std"] ** 2) / max(a["count"], 1)
        se_b = (b["std"] ** 2) / max(b["count"], 1)
        se_diff = math.sqrt(se_a + se_b)

        if se_diff == 0:
            return a["mean"] != b["mean"]

        t_stat = abs(a["mean"] - b["mean"]) / se_diff

        # Approximate: |t| > 1.96 → p < 0.05 (two-tailed)
        # |t| > 2.576 → p < 0.01
        critical_value = 1.96 if p_threshold >= 0.05 else 2.576
        return t_stat > critical_value

    def list_experiments(self) -> list[str]:
        """List all experiment names."""
        return list(self._experiments.keys())

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

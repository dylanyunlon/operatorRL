"""
Deployment Gate — Model quality gate + auto-deployment.

Evaluates model metrics against thresholds (win rate, sample count,
regression), manages deployment history and rollback.

Location: agentos/governance/deployment_gate.py

Reference (拿来主义):
  - agentos/governance/deployment_manager.py: deployment patterns
  - agentos/governance/ab_test_controller.py: evaluation patterns
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.deployment_gate.v1"


class DeploymentGate:
    """Quality gate for model deployment decisions.

    Attributes:
        min_win_rate: Minimum acceptable win rate.
        min_samples: Minimum evaluation samples required.
        max_regression_pct: Maximum allowed win-rate regression %.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(
        self,
        min_win_rate: float = 0.52,
        min_samples: int = 20,
        max_regression_pct: float = 5.0,
    ) -> None:
        self.min_win_rate = min_win_rate
        self.min_samples = min_samples
        self.max_regression_pct = max_regression_pct
        self._deployment_history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def evaluate(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Evaluate model metrics against quality gates.

        Args:
            metrics: Dict with win_rate, sample_count, prev_win_rate.

        Returns:
            Dict with approved (bool) and reasons list.
        """
        reasons = []
        win_rate = metrics.get("win_rate", 0.0)
        sample_count = metrics.get("sample_count", 0)
        prev_win_rate = metrics.get("prev_win_rate", 0.0)

        if win_rate < self.min_win_rate:
            reasons.append("win_rate")
        if sample_count < self.min_samples:
            reasons.append("sample_count")
        if prev_win_rate > 0 and win_rate < prev_win_rate:
            regression_pct = (prev_win_rate - win_rate) / prev_win_rate * 100
            if regression_pct > self.max_regression_pct:
                reasons.append("regression")

        approved = len(reasons) == 0
        result = {"approved": approved, "reasons": reasons, "win_rate": win_rate}
        self._fire_evolution({"event": "gate_evaluated", **result})
        return result

    def deploy(self, model_id: str, metrics: Optional[dict[str, Any]] = None) -> None:
        self._deployment_history.append({
            "model_id": model_id,
            "deployed_at": time.time(),
            "metrics": metrics or {},
        })
        logger.info("Deployed model %s", model_id)

    def get_deployment_history(self) -> list[dict[str, Any]]:
        return list(self._deployment_history)

    def current_model(self) -> Optional[str]:
        if not self._deployment_history:
            return None
        return self._deployment_history[-1]["model_id"]

    def rollback(self) -> dict[str, Any]:
        """Rollback to previous model.

        Returns:
            Previous deployment entry.

        Raises:
            ValueError: If no previous deployment to rollback to.
        """
        if len(self._deployment_history) < 2:
            raise ValueError("No previous deployment to rollback to")
        self._deployment_history.pop()
        return self._deployment_history[-1]

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

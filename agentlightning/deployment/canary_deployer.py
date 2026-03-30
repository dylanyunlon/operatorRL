"""
Canary Deployer — Gradual model rollout with canary testing.

Manages phased deployment of new model versions: routes a small
percentage of traffic to the canary, monitors error rate and
performance, and automatically promotes or rolls back.

Location: agentlightning/deployment/canary_deployer.py

Reference (拿来主义):
  查看 agentos/governance/deployment_gate.py 上现有 DeploymentGate 的
  gate条件检查方式, 理解其模式, 特别是 should_deploy→deploy→verify
  的三步部署契约如何与门控条件(fitness阈值、测试通过率)分离。
  从 agentos/governance/ab_test_controller.py 这个好例子开始 — 它的
  流量分割和结果收集展示了A/B路由的基本模式。
  遵循该模式实现 CanaryDeployer, 让 deployment_orchestrator(M565)
  可以安全地将新模型版本逐步推向生产, 并能在性能回退时自动回滚.

Design Notes (Knuth-level critique):
  User:
    - Gradual rollout (1%→5%→25%→100%) reduces blast radius
    - Auto-rollback on error spike protects live games
    - Promotion criteria are configurable per deployment
  System:
    - Traffic routing is probabilistic, not session-sticky (simpler)
    - Health checks run on a configurable interval
    - Deployment state machine prevents invalid transitions
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.canary_deployer.v1"

_DEFAULT_STAGES = [0.01, 0.05, 0.25, 0.50, 1.0]


class CanaryDeployment:
    """State of a single canary deployment."""

    __slots__ = (
        "deployment_id", "canary_version", "baseline_version",
        "stage_index", "stages", "status", "created_at",
        "promoted_at", "rolled_back_at",
        "canary_requests", "canary_errors", "baseline_requests", "baseline_errors",
        "max_error_rate", "min_requests_per_stage",
    )

    def __init__(
        self,
        deployment_id: str,
        canary_version: str,
        baseline_version: str,
        stages: Optional[List[float]] = None,
        max_error_rate: float = 0.05,
        min_requests_per_stage: int = 100,
    ) -> None:
        self.deployment_id = deployment_id
        self.canary_version = canary_version
        self.baseline_version = baseline_version
        self.stages = stages or list(_DEFAULT_STAGES)
        self.stage_index: int = 0
        self.status: str = "active"  # active/promoted/rolled_back
        self.created_at = time.time()
        self.promoted_at: float = 0.0
        self.rolled_back_at: float = 0.0
        self.canary_requests: int = 0
        self.canary_errors: int = 0
        self.baseline_requests: int = 0
        self.baseline_errors: int = 0
        self.max_error_rate = max_error_rate
        self.min_requests_per_stage = min_requests_per_stage

    @property
    def current_traffic_pct(self) -> float:
        if self.stage_index < len(self.stages):
            return self.stages[self.stage_index]
        return 1.0

    @property
    def canary_error_rate(self) -> float:
        if self.canary_requests == 0:
            return 0.0
        return self.canary_errors / self.canary_requests

    @property
    def baseline_error_rate(self) -> float:
        if self.baseline_requests == 0:
            return 0.0
        return self.baseline_errors / self.baseline_requests

    def to_dict(self) -> Dict[str, Any]:
        return {
            "deployment_id": self.deployment_id,
            "canary_version": self.canary_version,
            "baseline_version": self.baseline_version,
            "status": self.status,
            "stage_index": self.stage_index,
            "current_traffic_pct": self.current_traffic_pct,
            "canary_requests": self.canary_requests,
            "canary_errors": self.canary_errors,
            "canary_error_rate": round(self.canary_error_rate, 4),
            "baseline_requests": self.baseline_requests,
            "baseline_error_rate": round(self.baseline_error_rate, 4),
        }


class CanaryDeployer:
    """Manages canary deployments with auto-promotion and rollback.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._deployments: Dict[str, CanaryDeployment] = {}
        self._active_deployment: Optional[str] = None
        self._deployment_counter: int = 0
        self._stats = {
            "total_deployments": 0,
            "total_promotions": 0,
            "total_rollbacks": 0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def create_deployment(
        self,
        canary_version: str,
        baseline_version: str,
        stages: Optional[List[float]] = None,
        max_error_rate: float = 0.05,
        min_requests_per_stage: int = 100,
    ) -> str:
        """Create a new canary deployment.

        Args:
            canary_version: New model version to test.
            baseline_version: Current production version.
            stages: Traffic percentage stages.
            max_error_rate: Max acceptable canary error rate.
            min_requests_per_stage: Min requests before advancing.

        Returns:
            Deployment ID.

        Raises:
            RuntimeError: If another deployment is active.
        """
        if self._active_deployment is not None:
            raise RuntimeError(
                f"Active deployment exists: {self._active_deployment}"
            )

        self._deployment_counter += 1
        dep_id = f"canary_{self._deployment_counter}"
        deployment = CanaryDeployment(
            deployment_id=dep_id,
            canary_version=canary_version,
            baseline_version=baseline_version,
            stages=stages,
            max_error_rate=max_error_rate,
            min_requests_per_stage=min_requests_per_stage,
        )
        self._deployments[dep_id] = deployment
        self._active_deployment = dep_id
        self._stats["total_deployments"] += 1

        self._fire_evolution("deployment_created", {
            "id": dep_id, "canary": canary_version, "baseline": baseline_version,
        })
        return dep_id

    def route_request(self) -> str:
        """Route a request to canary or baseline.

        Returns:
            Model version string to use.
        """
        if self._active_deployment is None:
            raise RuntimeError("No active deployment")

        dep = self._deployments[self._active_deployment]
        if dep.status != "active":
            return dep.baseline_version

        if random.random() < dep.current_traffic_pct:
            dep.canary_requests += 1
            return dep.canary_version
        else:
            dep.baseline_requests += 1
            return dep.baseline_version

    def record_result(
        self,
        version: str,
        is_error: bool = False,
    ) -> None:
        """Record a request result.

        Args:
            version: The version that served the request.
            is_error: Whether the request errored.
        """
        if self._active_deployment is None:
            return

        dep = self._deployments[self._active_deployment]
        if version == dep.canary_version:
            if is_error:
                dep.canary_errors += 1
        elif version == dep.baseline_version:
            dep.baseline_requests += 1
            if is_error:
                dep.baseline_errors += 1

    def evaluate(self) -> Dict[str, Any]:
        """Evaluate current deployment and auto-advance/rollback.

        Returns:
            Dict with action taken and deployment status.
        """
        if self._active_deployment is None:
            return {"action": "none", "reason": "no_active_deployment"}

        dep = self._deployments[self._active_deployment]
        if dep.status != "active":
            return {"action": "none", "status": dep.status}

        # Check for rollback condition
        if (dep.canary_requests >= 10 and
                dep.canary_error_rate > dep.max_error_rate):
            return self._rollback(dep, "error_rate_exceeded")

        # Check for advance condition
        if dep.canary_requests >= dep.min_requests_per_stage:
            if dep.stage_index < len(dep.stages) - 1:
                dep.stage_index += 1
                return {
                    "action": "advanced",
                    "stage": dep.stage_index,
                    "traffic_pct": dep.current_traffic_pct,
                }
            else:
                return self._promote(dep)

        return {"action": "monitoring", "status": dep.to_dict()}

    def force_promote(self) -> Dict[str, Any]:
        """Force promote the canary to production.

        Returns:
            Promotion result dict.
        """
        if self._active_deployment is None:
            return {"action": "none", "reason": "no_active_deployment"}
        dep = self._deployments[self._active_deployment]
        return self._promote(dep)

    def force_rollback(self, reason: str = "manual") -> Dict[str, Any]:
        """Force rollback the canary.

        Returns:
            Rollback result dict.
        """
        if self._active_deployment is None:
            return {"action": "none", "reason": "no_active_deployment"}
        dep = self._deployments[self._active_deployment]
        return self._rollback(dep, reason)

    def get_active_deployment(self) -> Optional[Dict[str, Any]]:
        """Get active deployment info."""
        if self._active_deployment is None:
            return None
        return self._deployments[self._active_deployment].to_dict()

    def get_deployment(self, deployment_id: str) -> Dict[str, Any]:
        """Get deployment info by ID."""
        if deployment_id not in self._deployments:
            raise KeyError(f"Deployment '{deployment_id}' not found")
        return self._deployments[deployment_id].to_dict()

    def list_deployments(self) -> List[Dict[str, Any]]:
        """List all deployments."""
        return [d.to_dict() for d in self._deployments.values()]

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    # --- Internal ---

    def _promote(self, dep: CanaryDeployment) -> Dict[str, Any]:
        dep.status = "promoted"
        dep.promoted_at = time.time()
        self._active_deployment = None
        self._stats["total_promotions"] += 1
        self._fire_evolution("canary_promoted", {
            "id": dep.deployment_id, "version": dep.canary_version,
        })
        return {"action": "promoted", "version": dep.canary_version}

    def _rollback(self, dep: CanaryDeployment, reason: str) -> Dict[str, Any]:
        dep.status = "rolled_back"
        dep.rolled_back_at = time.time()
        self._active_deployment = None
        self._stats["total_rollbacks"] += 1
        self._fire_evolution("canary_rolled_back", {
            "id": dep.deployment_id, "reason": reason,
            "canary_error_rate": dep.canary_error_rate,
        })
        return {"action": "rolled_back", "reason": reason, "version": dep.baseline_version}

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

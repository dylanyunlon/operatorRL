"""
Deployment Orchestrator — End-to-end deployment lifecycle coordinator.

Orchestrates the full deployment pipeline: manifest validation → model
warmup → canary deploy → health monitoring → A/B test → promotion.
Coordinates all M556-M564 components into a unified deployment flow.

Location: agentlightning/deployment/deployment_orchestrator.py

Reference (拿来主义):
  查看 agentos/governance/evolution_orchestrator.py 上现有
  EvolutionOrchestrator 的 register_loop→run_cycle→allocate_resources
  方式, 理解其模式, 特别是多个子系统如何通过统一的orchestrator协调。
  从 integrations/lol-history/src/lol_history/seraphine_history_orchestrator.py
  这个好例子开始 — 它编排M506-M524所有子模块。
  遵循该模式实现 DeploymentOrchestrator, 让整个部署管线通过一个入口
  完成从准备到上线的全流程, 并能在任何环节失败时自动回滚.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.deployment_orchestrator.v1"


class DeploymentStep:
    """A step in the deployment pipeline."""

    __slots__ = ("name", "fn", "status", "started_at", "completed_at", "error", "result")

    def __init__(self, name: str, fn: Callable[[], Dict[str, Any]]) -> None:
        self.name = name
        self.fn = fn
        self.status: str = "pending"
        self.started_at: float = 0.0
        self.completed_at: float = 0.0
        self.error: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None

    def execute(self) -> Dict[str, Any]:
        self.status = "running"
        self.started_at = time.time()
        try:
            self.result = self.fn()
            self.status = "completed"
            self.completed_at = time.time()
            return self.result
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            self.completed_at = time.time()
            raise

    @property
    def duration_ms(self) -> float:
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at) * 1000.0
        return 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status,
            "duration_ms": round(self.duration_ms, 3),
            "error": self.error,
        }


class DeploymentOrchestrator:
    """Orchestrates full deployment lifecycle.

    Usage:
        orch = DeploymentOrchestrator()
        orch.add_step("validate", lambda: manifest.validate())
        orch.add_step("warmup", lambda: warmup_engine.warmup("lol", "v2"))
        orch.add_step("canary", lambda: canary.create_deployment("v2", "v1"))
        result = orch.execute()

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, name: str = "deployment") -> None:
        self.name = name
        self._steps: List[DeploymentStep] = []
        self._rollback_fns: Dict[str, Callable[[], None]] = {}
        self._status: str = "idle"  # idle/running/completed/failed/rolled_back
        self._current_step: int = -1
        self._started_at: float = 0.0
        self._completed_at: float = 0.0
        self._stats = {
            "total_deployments": 0,
            "total_successes": 0,
            "total_failures": 0,
            "total_rollbacks": 0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_step(
        self, name: str,
        fn: Callable[[], Dict[str, Any]],
        rollback_fn: Optional[Callable[[], None]] = None,
    ) -> "DeploymentOrchestrator":
        self._steps.append(DeploymentStep(name, fn))
        if rollback_fn is not None:
            self._rollback_fns[name] = rollback_fn
        return self

    def execute(self) -> Dict[str, Any]:
        """Execute all deployment steps in order.

        On failure, automatically rolls back completed steps.

        Returns:
            Dict with status, step results, and timing.
        """
        self._status = "running"
        self._started_at = time.time()
        self._stats["total_deployments"] += 1
        results: Dict[str, Any] = {}

        for i, step in enumerate(self._steps):
            self._current_step = i
            try:
                step_result = step.execute()
                results[step.name] = step_result
            except Exception as exc:
                logger.error("Deployment step '%s' failed: %s", step.name, exc)
                self._status = "failed"
                self._stats["total_failures"] += 1
                # Rollback completed steps in reverse
                self._rollback(i)
                self._completed_at = time.time()
                self._fire_evolution("deployment_failed", {
                    "failed_step": step.name, "error": str(exc),
                })
                return {
                    "status": "failed",
                    "failed_step": step.name,
                    "error": str(exc),
                    "steps": [s.to_dict() for s in self._steps],
                    "duration_ms": round((self._completed_at - self._started_at) * 1000, 3),
                }

        self._status = "completed"
        self._completed_at = time.time()
        self._stats["total_successes"] += 1
        self._fire_evolution("deployment_completed", {
            "name": self.name, "steps": len(self._steps),
        })
        return {
            "status": "completed",
            "results": results,
            "steps": [s.to_dict() for s in self._steps],
            "duration_ms": round((self._completed_at - self._started_at) * 1000, 3),
        }

    def _rollback(self, failed_step_index: int) -> None:
        """Rollback completed steps in reverse order."""
        self._stats["total_rollbacks"] += 1
        for i in range(failed_step_index - 1, -1, -1):
            step = self._steps[i]
            if step.name in self._rollback_fns:
                try:
                    self._rollback_fns[step.name]()
                    logger.info("Rolled back step: %s", step.name)
                except Exception as exc:
                    logger.error("Rollback failed for %s: %s", step.name, exc)

    @property
    def status(self) -> str:
        return self._status

    def current_step_name(self) -> Optional[str]:
        if 0 <= self._current_step < len(self._steps):
            return self._steps[self._current_step].name
        return None

    def get_progress(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self._status,
            "current_step": self._current_step,
            "total_steps": len(self._steps),
            "steps": [s.to_dict() for s in self._steps],
        }

    def reset(self) -> None:
        """Reset for a new deployment."""
        self._steps.clear()
        self._rollback_fns.clear()
        self._status = "idle"
        self._current_step = -1

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    def step_count(self) -> int:
        return len(self._steps)

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY, "type": event_type,
                    "timestamp": time.time(), "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

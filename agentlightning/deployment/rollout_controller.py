"""
Rollout Controller — Phased rollout state machine.

Controls multi-phase deployment rollouts with configurable gate
conditions between phases. Tracks rollout progress and enforces
phase transitions.

Location: agentlightning/deployment/rollout_controller.py

Reference (拿来主义):
  查看 agentos/governance/deployment_gate.py 上现有 DeploymentGate 的
  gate条件方式, 理解其模式, 特别是 gate_check 如何与 deploy 动作分离。
  从 agentlightning/trainer/curriculum_manager.py 这个好例子开始 — 它的
  register_level→should_advance→advance 展示了阶段晋级的模式。
  遵循该模式实现 RolloutController, 让部署管线可以按阶段推进,
  并在每个阶段门控条件不满足时自动暂停.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.rollout_controller.v1"


class RolloutPhase:
    """Single phase in a rollout."""

    __slots__ = (
        "name", "target_pct", "gate_fn", "status",
        "entered_at", "completed_at", "gate_checks",
    )

    def __init__(
        self,
        name: str,
        target_pct: float,
        gate_fn: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.name = name
        self.target_pct = target_pct
        self.gate_fn = gate_fn
        self.status: str = "pending"  # pending/active/passed/failed
        self.entered_at: float = 0.0
        self.completed_at: float = 0.0
        self.gate_checks: int = 0

    def check_gate(self) -> bool:
        self.gate_checks += 1
        if self.gate_fn is None:
            return True
        return self.gate_fn()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "target_pct": self.target_pct,
            "status": self.status,
            "gate_checks": self.gate_checks,
        }


class RolloutController:
    """Controls phased deployment rollouts.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._phases: List[RolloutPhase] = []
        self._current_index: int = -1
        self._status: str = "idle"  # idle/rolling/completed/failed/paused
        self._rollout_id: str = ""
        self._version: str = ""
        self._stats = {
            "total_rollouts": 0,
            "total_completions": 0,
            "total_failures": 0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def configure_phases(self, phases: List[Dict[str, Any]]) -> None:
        """Configure rollout phases.

        Args:
            phases: List of dicts with "name", "target_pct", optional "gate_fn".
        """
        self._phases = [
            RolloutPhase(
                name=p["name"],
                target_pct=p["target_pct"],
                gate_fn=p.get("gate_fn"),
            )
            for p in phases
        ]

    def start_rollout(self, version: str, rollout_id: Optional[str] = None) -> str:
        """Start a new rollout.

        Args:
            version: Version being rolled out.
            rollout_id: Optional custom ID.

        Returns:
            Rollout ID.

        Raises:
            RuntimeError: If a rollout is already in progress.
        """
        if self._status == "rolling":
            raise RuntimeError("Rollout already in progress")
        if not self._phases:
            raise RuntimeError("No phases configured")

        self._stats["total_rollouts"] += 1
        self._rollout_id = rollout_id or f"rollout_{self._stats['total_rollouts']}"
        self._version = version
        self._status = "rolling"
        self._current_index = 0
        self._phases[0].status = "active"
        self._phases[0].entered_at = time.time()

        self._fire_evolution("rollout_started", {
            "id": self._rollout_id, "version": version,
        })
        return self._rollout_id

    def advance(self) -> Dict[str, Any]:
        """Try to advance to the next phase.

        Returns:
            Dict with action and current state.
        """
        if self._status != "rolling":
            return {"action": "none", "status": self._status}

        current = self._phases[self._current_index]

        if not current.check_gate():
            return {
                "action": "gate_blocked",
                "phase": current.name,
                "gate_checks": current.gate_checks,
            }

        current.status = "passed"
        current.completed_at = time.time()

        if self._current_index >= len(self._phases) - 1:
            self._status = "completed"
            self._stats["total_completions"] += 1
            self._fire_evolution("rollout_completed", {
                "id": self._rollout_id, "version": self._version,
            })
            return {"action": "completed", "version": self._version}

        self._current_index += 1
        next_phase = self._phases[self._current_index]
        next_phase.status = "active"
        next_phase.entered_at = time.time()

        return {
            "action": "advanced",
            "from_phase": current.name,
            "to_phase": next_phase.name,
            "traffic_pct": next_phase.target_pct,
        }

    def pause(self) -> None:
        """Pause the rollout."""
        if self._status == "rolling":
            self._status = "paused"

    def resume(self) -> None:
        """Resume a paused rollout."""
        if self._status == "paused":
            self._status = "rolling"

    def fail(self, reason: str = "unknown") -> None:
        """Mark rollout as failed."""
        self._status = "failed"
        if 0 <= self._current_index < len(self._phases):
            self._phases[self._current_index].status = "failed"
        self._stats["total_failures"] += 1
        self._fire_evolution("rollout_failed", {
            "id": self._rollout_id, "reason": reason,
        })

    def current_phase(self) -> Optional[Dict[str, Any]]:
        if 0 <= self._current_index < len(self._phases):
            return self._phases[self._current_index].to_dict()
        return None

    def current_traffic_pct(self) -> float:
        if 0 <= self._current_index < len(self._phases):
            return self._phases[self._current_index].target_pct
        return 0.0

    @property
    def status(self) -> str:
        return self._status

    def get_progress(self) -> Dict[str, Any]:
        return {
            "rollout_id": self._rollout_id,
            "version": self._version,
            "status": self._status,
            "current_phase": self._current_index,
            "total_phases": len(self._phases),
            "phases": [p.to_dict() for p in self._phases],
        }

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

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

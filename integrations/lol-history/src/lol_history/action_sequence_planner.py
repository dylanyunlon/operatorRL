"""
ActionSequencePlanner — Plans multi-step action sequences.

Architecture (拿来主义):
  PARL/benchmark/torch/AlphaZero/submission_template.py — MCTS lookahead
  DI-star/distar/agent/default/agent.py — step→_post_process action sequence

Location: integrations/lol-history/src/lol_history/action_sequence_planner.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.action_sequence_planner.v1"

class ActionSequencePlanner:
    """Plans multi-step action sequences with expected value estimation.

    Public API: plan, cancel, get_active_plan, evaluate_sequence, get_stats
    """
    def __init__(self, max_depth: int = 5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._max_depth = max_depth
        self._active_plan: Optional[Dict] = None
        self._plan_count = 0
        self._cancel_count = 0
        self._value_estimators: Dict[str, Callable] = {}

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_value_estimator(self, action_type: str, estimator: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._value_estimators[action_type] = estimator
        return {"status": "ok", "action_type": action_type}

    def plan(self, context: Dict[str, Any], candidate_actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._op_count += 1
        self._plan_count += 1
        if not candidate_actions:
            return {"status": "ok", "sequence": [], "expected_value": 0.0}
        scored = []
        for action in candidate_actions[:self._max_depth]:
            atype = action.get("type", "unknown")
            estimator = self._value_estimators.get(atype)
            value = estimator(action, context) if estimator else action.get("value", 0.5)
            scored.append({**action, "estimated_value": round(value, 4)})
        scored.sort(key=lambda a: a["estimated_value"], reverse=True)
        sequence = scored[:self._max_depth]
        total_value = sum(a["estimated_value"] * (0.9 ** i) for i, a in enumerate(sequence))
        self._active_plan = {"sequence": sequence, "expected_value": round(total_value, 4),
                             "created_at": time.time(), "step": 0}
        self._fire("plan_created", {"steps": len(sequence), "value": total_value})
        return {"status": "ok", **self._active_plan}

    def cancel(self) -> Dict[str, Any]:
        self._op_count += 1
        if self._active_plan is None: return {"status": "ok", "was_active": False}
        self._cancel_count += 1
        old = self._active_plan
        self._active_plan = None
        return {"status": "ok", "was_active": True, "completed_steps": old.get("step", 0)}

    def get_active_plan(self) -> Dict[str, Any]:
        if self._active_plan is None: return {"status": "ok", "active": False}
        return {"status": "ok", "active": True, **self._active_plan}

    def evaluate_sequence(self, sequence: List[Dict], context: Dict = None) -> Dict[str, Any]:
        self._op_count += 1
        total = 0.0
        for i, action in enumerate(sequence):
            atype = action.get("type", "unknown")
            estimator = self._value_estimators.get(atype)
            v = estimator(action, context or {}) if estimator else action.get("value", 0.5)
            total += v * (0.9 ** i)
        return {"status": "ok", "expected_value": round(total, 4), "steps": len(sequence)}

    def get_stats(self) -> Dict[str, Any]:
        return {"plans_created": self._plan_count, "cancels": self._cancel_count,
                "max_depth": self._max_depth, "estimators": list(self._value_estimators.keys()),
                "has_active_plan": self._active_plan is not None, "total_ops": self._op_count}


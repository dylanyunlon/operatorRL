"""
IntelQualityFeedbackLoop — Auto-adjusts intel config from evaluation results.

Architecture (拿来主义):
  history_feedback_loop_orchestrator.py（M625）— feedback loop
  action_feedback_collector.py（M692）— effectiveness scoring

Location: integrations/lol-history/src/lol_history/intel_quality_feedback_loop.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_quality_feedback_loop.v1"
def _safe_div(a, b, d=0.0): return a / b if b else d

class IntelQualityFeedbackLoop:
    """Feeds back evaluation results to auto-adjust intel pipeline config.

    Public API: ingest_evaluation, get_adjustments, apply_adjustments,
                get_adjustment_history, get_stats
    """
    def __init__(self, adjustment_rate: float = 0.05) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._evaluations: List[Dict[str, Any]] = []
        self._adjustments: Dict[str, Dict[str, float]] = {}
        self._adjustment_history: List[Dict[str, Any]] = []
        self._rate = adjustment_rate

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def ingest_evaluation(self, module: str, precision: float,
                           confidence_avg: float = 0.5) -> Dict[str, Any]:
        self._op_count += 1
        self._evaluations.append({"module": module, "precision": precision,
                                   "confidence_avg": confidence_avg, "timestamp": time.time()})
        # Generate adjustment suggestion
        cal_error = abs(confidence_avg - precision)
        if cal_error > 0.1:
            conf_adjustment = -self._rate if confidence_avg > precision else self._rate
        else:
            conf_adjustment = 0.0
        weight_adjustment = self._rate if precision > 0.6 else -self._rate if precision < 0.4 else 0.0
        adj = {"confidence_threshold_delta": round(conf_adjustment, 4),
               "weight_delta": round(weight_adjustment, 4)}
        self._adjustments[module] = adj
        return {"status": "ok", "module": module, "adjustments": adj}

    def get_adjustments(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "adjustments": dict(self._adjustments)}

    def apply_adjustments(self, current_config: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
        self._op_count += 1
        updated = {}
        for module, adj in self._adjustments.items():
            cfg = current_config.get(module, {"confidence_threshold": 0.5, "weight": 1.0})
            new_cfg = {
                "confidence_threshold": round(max(0.1, min(0.95,
                    cfg.get("confidence_threshold", 0.5) + adj.get("confidence_threshold_delta", 0))), 4),
                "weight": round(max(0.1, min(2.0,
                    cfg.get("weight", 1.0) + adj.get("weight_delta", 0))), 4)}
            updated[module] = new_cfg
        self._adjustment_history.append({"applied": updated, "timestamp": time.time()})
        self._adjustments.clear()
        self._fire("adjustments_applied", {"modules": len(updated)})
        return {"status": "ok", "updated_config": updated, "modules_adjusted": len(updated)}

    def get_adjustment_history(self, n: int = 10) -> List[Dict[str, Any]]:
        self._op_count += 1
        return self._adjustment_history[-n:]

    def get_stats(self) -> Dict[str, Any]:
        return {"evaluations": len(self._evaluations), "pending_adjustments": len(self._adjustments),
                "history_entries": len(self._adjustment_history), "total_ops": self._op_count}

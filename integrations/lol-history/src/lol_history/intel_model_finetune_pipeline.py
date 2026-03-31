"""
IntelModelFinetunePipeline — Fine-tunes intel model parameters from prediction feedback.

Architecture (拿来主义):
  agentlightning/training/training_loop_controller.py — training loop control
  DI-star/distar/agent/default/rl_training/as_rl_utils.py — gradient-based update

Location: integrations/lol-history/src/lol_history/intel_model_finetune_pipeline.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_model_finetune_pipeline.v1"
def _safe_div(a, b, d=0.0): return a / b if b else d

class IntelModelFinetunePipeline:
    """Fine-tunes intel model weights from prediction feedback.

    Public API: set_params, add_feedback, run_finetune_step, get_params,
                get_performance_delta, get_stats
    """
    def __init__(self, learning_rate: float = 0.01, max_gradient: float = 0.1) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._params: Dict[str, float] = {}
        self._lr = learning_rate
        self._max_grad = max_gradient
        self._feedback: List[Dict[str, Any]] = []
        self._step_count = 0
        self._perf_before: Optional[float] = None
        self._perf_after: Optional[float] = None

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_params(self, params: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._params = dict(params)
        return {"status": "ok", "params_count": len(self._params)}

    def add_feedback(self, param_name: str, gradient: float, accuracy: float) -> Dict[str, Any]:
        self._op_count += 1
        self._feedback.append({"param": param_name, "gradient": gradient,
                               "accuracy": accuracy, "timestamp": time.time()})
        return {"status": "ok", "feedback_count": len(self._feedback)}

    def run_finetune_step(self) -> Dict[str, Any]:
        self._op_count += 1
        self._step_count += 1
        if not self._feedback:
            return {"status": "ok", "updates": 0, "reason": "no_feedback"}
        # Record pre-step performance
        pre_accuracies = [f["accuracy"] for f in self._feedback]
        self._perf_before = sum(pre_accuracies) / len(pre_accuracies)
        updates = {}
        for fb in self._feedback:
            p = fb["param"]
            if p not in self._params:
                continue
            grad = max(-self._max_grad, min(fb["gradient"], self._max_grad))
            old_val = self._params[p]
            self._params[p] = old_val + self._lr * grad
            updates[p] = {"old": round(old_val, 6), "new": round(self._params[p], 6),
                          "delta": round(self._lr * grad, 6)}
        self._feedback.clear()
        self._fire("finetune_step", {"step": self._step_count, "updates": len(updates)})
        return {"status": "ok", "step": self._step_count, "updates": updates,
                "params_updated": len(updates)}

    def get_params(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "params": dict(self._params)}

    def get_performance_delta(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "before": self._perf_before, "after": self._perf_after,
                "delta": round((self._perf_after or 0) - (self._perf_before or 0), 4)
                if self._perf_before is not None else None}

    def get_stats(self) -> Dict[str, Any]:
        return {"params_count": len(self._params), "step_count": self._step_count,
                "pending_feedback": len(self._feedback), "total_ops": self._op_count}

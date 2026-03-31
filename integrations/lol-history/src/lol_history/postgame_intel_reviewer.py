"""
PostgameIntelReviewer — Reviews intel predictions vs actual outcomes post-game.

Architecture (拿来主义):
  replay_decision_auditor.py（M612）— post-game audit
  postgame_auto_evaluator.py — automatic post-game evaluation

Location: integrations/lol-history/src/lol_history/postgame_intel_reviewer.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.postgame_intel_reviewer.v1"
def _safe_div(a, b, d=0.0): return a / b if b else d

class PostgameIntelReviewer:
    """Reviews all intel predictions against actual game outcomes.

    Public API: add_prediction_outcome_pair, generate_review, get_module_accuracy,
                get_review_summary, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._pairs: List[Dict[str, Any]] = []
        self._reviews: List[Dict[str, Any]] = []
        self._review_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def add_prediction_outcome_pair(self, module: str, prediction: Any,
                                      actual: Any, context: str = "") -> Dict[str, Any]:
        self._op_count += 1
        if isinstance(prediction, bool):
            correct = prediction == actual
        elif isinstance(prediction, (int, float)) and isinstance(actual, (int, float)):
            correct = abs(prediction - actual) / max(abs(actual), 1) < 0.3
        else:
            correct = str(prediction) == str(actual)
        pair = {"module": module, "prediction": prediction, "actual": actual,
                "correct": correct, "context": context, "timestamp": time.time()}
        self._pairs.append(pair)
        return {"status": "ok", "correct": correct, "total_pairs": len(self._pairs)}

    def generate_review(self) -> Dict[str, Any]:
        self._op_count += 1
        self._review_count += 1
        if not self._pairs:
            return {"status": "ok", "review": {}, "reason": "no_data"}
        module_stats: Dict[str, Dict[str, int]] = {}
        for p in self._pairs:
            m = p["module"]
            if m not in module_stats:
                module_stats[m] = {"correct": 0, "total": 0}
            module_stats[m]["total"] += 1
            if p["correct"]:
                module_stats[m]["correct"] += 1
        review = {}
        for m, stats in module_stats.items():
            review[m] = {"precision": round(_safe_div(stats["correct"], stats["total"]), 4),
                         "correct": stats["correct"], "total": stats["total"],
                         "errors": [p for p in self._pairs if p["module"] == m and not p["correct"]]}
        overall_correct = sum(1 for p in self._pairs if p["correct"])
        summary = {"overall_precision": round(_safe_div(overall_correct, len(self._pairs)), 4),
                   "total_predictions": len(self._pairs), "modules": review}
        self._reviews.append(summary)
        self._fire("review_generated", {"precision": summary["overall_precision"]})
        return {"status": "ok", "review": summary}

    def get_module_accuracy(self, module: str) -> Dict[str, Any]:
        self._op_count += 1
        pairs = [p for p in self._pairs if p["module"] == module]
        if not pairs:
            return {"status": "ok", "module": module, "accuracy": 0.0, "samples": 0}
        correct = sum(1 for p in pairs if p["correct"])
        return {"status": "ok", "module": module,
                "accuracy": round(_safe_div(correct, len(pairs)), 4), "samples": len(pairs)}

    def get_review_summary(self) -> Dict[str, Any]:
        self._op_count += 1
        if not self._reviews:
            return {"status": "ok", "summaries": [], "count": 0}
        return {"status": "ok", "latest": self._reviews[-1], "count": len(self._reviews)}

    def get_stats(self) -> Dict[str, Any]:
        return {"total_pairs": len(self._pairs), "reviews_generated": self._review_count,
                "total_ops": self._op_count}

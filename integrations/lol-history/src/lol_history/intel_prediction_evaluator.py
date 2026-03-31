"""
IntelPredictionEvaluator — Evaluates accuracy of all intel predictions post-game.

Architecture (拿来主义):
  decision_quality_scorer.py（M702）— sliding window quality scoring
  coaching_effectiveness_tracker.py（M613）— effectiveness evaluation

Location: integrations/lol-history/src/lol_history/intel_prediction_evaluator.py

Design Notes (Knuth-level critique):
  User:
    - Per-module prediction accuracy surfaces which intel sources are reliable.
    - Calibration error reveals overconfident vs underconfident predictions.
  System:
    - Prediction-outcome pairs stored for batch evaluation, not evaluated inline.
    - Module-level aggregation avoids coupling evaluator to specific module internals.
"""
from __future__ import annotations
import logging, time, math
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_prediction_evaluator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class IntelPredictionEvaluator:
    """Evaluates intel prediction accuracy across all modules.

    Public API: record_prediction, record_outcome, evaluate_module,
                evaluate_all, get_calibration_error, get_stats
    """
    def __init__(self, window_size: int = 500) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._predictions: Dict[str, Dict[str, Any]] = {}
        self._module_results: Dict[str, List[Dict[str, Any]]] = {}
        self._record_count = 0
        self._window_size = window_size

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def record_prediction(self, prediction_id: str, module_name: str,
                           predicted_value: Any, confidence: float = 0.5,
                           context: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._record_count += 1
        self._predictions[prediction_id] = {
            "module": module_name, "predicted": predicted_value,
            "confidence": confidence, "context": context or {},
            "timestamp": time.time(), "outcome": None}
        return {"status": "ok", "prediction_id": prediction_id, "module": module_name}

    def record_outcome(self, prediction_id: str,
                        actual_value: Any) -> Dict[str, Any]:
        self._op_count += 1
        pred = self._predictions.get(prediction_id)
        if not pred:
            return {"status": "error", "reason": "prediction_not_found"}
        pred["outcome"] = actual_value
        # Compute accuracy
        predicted = pred["predicted"]
        if isinstance(predicted, bool) and isinstance(actual_value, bool):
            correct = predicted == actual_value
            accuracy = 1.0 if correct else 0.0
        elif isinstance(predicted, (int, float)) and isinstance(actual_value, (int, float)):
            accuracy = 1.0 - min(abs(predicted - actual_value) / max(abs(actual_value), 1.0), 1.0)
            correct = accuracy > 0.7
        else:
            correct = str(predicted) == str(actual_value)
            accuracy = 1.0 if correct else 0.0

        result = {"prediction_id": prediction_id, "module": pred["module"],
                  "predicted": predicted, "actual": actual_value,
                  "correct": correct, "accuracy": round(accuracy, 4),
                  "confidence": pred["confidence"]}
        self._module_results.setdefault(pred["module"], []).append(result)
        # Trim to window size
        if len(self._module_results[pred["module"]]) > self._window_size:
            self._module_results[pred["module"]] = self._module_results[pred["module"]][-self._window_size:]
        self._fire("outcome_recorded", {"module": pred["module"], "correct": correct})
        return {"status": "ok", **result}

    def evaluate_module(self, module_name: str) -> Dict[str, Any]:
        self._op_count += 1
        results = self._module_results.get(module_name, [])
        if not results:
            return {"status": "ok", "module": module_name, "evaluated": 0}
        total = len(results)
        correct = sum(1 for r in results if r["correct"])
        accuracies = [r["accuracy"] for r in results]
        precision = _safe_div(correct, total)
        avg_accuracy = sum(accuracies) / total
        avg_confidence = sum(r["confidence"] for r in results) / total
        calibration_error = abs(avg_confidence - precision)
        return {"status": "ok", "module": module_name, "precision": round(precision, 4),
                "avg_accuracy": round(avg_accuracy, 4),
                "avg_confidence": round(avg_confidence, 4),
                "calibration_error": round(calibration_error, 4),
                "evaluated": total, "correct": correct}

    def evaluate_all(self) -> Dict[str, Any]:
        self._op_count += 1
        report = {}
        for module_name in self._module_results:
            report[module_name] = self.evaluate_module(module_name)
        return {"status": "ok", "report": report, "modules_evaluated": len(report)}

    def get_calibration_error(self, module_name: str, bins: int = 5) -> Dict[str, Any]:
        """Expected Calibration Error across confidence bins."""
        self._op_count += 1
        results = self._module_results.get(module_name, [])
        if not results:
            return {"status": "ok", "ece": 0.0, "bins": []}
        bin_boundaries = [i / bins for i in range(bins + 1)]
        bin_data = []
        total_ece = 0.0
        for b in range(bins):
            lo, hi = bin_boundaries[b], bin_boundaries[b + 1]
            in_bin = [r for r in results if lo <= r["confidence"] < hi]
            if not in_bin:
                continue
            avg_conf = sum(r["confidence"] for r in in_bin) / len(in_bin)
            avg_acc = sum(1 for r in in_bin if r["correct"]) / len(in_bin)
            ece_contrib = abs(avg_acc - avg_conf) * len(in_bin) / len(results)
            total_ece += ece_contrib
            bin_data.append({"range": f"{lo:.1f}-{hi:.1f}", "count": len(in_bin),
                             "avg_confidence": round(avg_conf, 4),
                             "avg_accuracy": round(avg_acc, 4)})
        return {"status": "ok", "ece": round(total_ece, 4), "bins": bin_data}

    def get_stats(self) -> Dict[str, Any]:
        total_evaluated = sum(len(v) for v in self._module_results.values())
        return {"predictions_recorded": len(self._predictions),
                "outcomes_evaluated": total_evaluated,
                "modules_tracked": len(self._module_results),
                "record_count": self._record_count, "total_ops": self._op_count}

"""
IntelFeatureImportanceAnalyzer — Analyzes feature importance via permutation.

Architecture (拿来主义):
  history_feature_importance_ranker.py — feature ranking
  historical_feature_vector_builder.py（M602）— feature construction

Location: integrations/lol-history/src/lol_history/intel_feature_importance_analyzer.py
"""
from __future__ import annotations
import logging, time, random
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_feature_importance_analyzer.v1"
def _safe_div(a, b, d=0.0): return a / b if b else d

class IntelFeatureImportanceAnalyzer:
    """Analyzes feature importance via permutation importance and correlation.

    Public API: ingest_sample, compute_importance, get_ranking,
                suggest_removals, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._samples: List[Dict[str, Any]] = []
        self._importance: Dict[str, float] = {}
        self._compute_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def ingest_sample(self, features: Dict[str, float], prediction_correct: bool) -> Dict[str, Any]:
        self._op_count += 1
        self._samples.append({"features": features, "correct": prediction_correct})
        return {"status": "ok", "total_samples": len(self._samples)}

    def compute_importance(self) -> Dict[str, Any]:
        self._op_count += 1
        self._compute_count += 1
        if len(self._samples) < 10:
            return {"status": "ok", "importance": {}, "reason": "insufficient_samples"}
        all_keys = set()
        for s in self._samples:
            all_keys.update(s["features"].keys())
        baseline_accuracy = _safe_div(sum(1 for s in self._samples if s["correct"]), len(self._samples))
        importance = {}
        for key in all_keys:
            # Compute correlation between feature presence/value and correct prediction
            with_feature = [s for s in self._samples if key in s["features"]]
            if len(with_feature) < 5:
                importance[key] = 0.0
                continue
            correct_with = sum(1 for s in with_feature if s["correct"])
            acc_with = _safe_div(correct_with, len(with_feature))
            # Feature importance = how much accuracy drops without this feature
            remaining = [s for s in self._samples if key not in s["features"]]
            if remaining:
                acc_without = _safe_div(sum(1 for s in remaining if s["correct"]), len(remaining))
            else:
                acc_without = baseline_accuracy
            importance[key] = round(acc_with - acc_without, 4)
        self._importance = importance
        self._fire("importance_computed", {"features": len(importance)})
        return {"status": "ok", "importance": importance, "baseline_accuracy": round(baseline_accuracy, 4)}

    def get_ranking(self, top_n: int = 10) -> Dict[str, Any]:
        self._op_count += 1
        sorted_imp = sorted(self._importance.items(), key=lambda x: abs(x[1]), reverse=True)
        return {"status": "ok", "ranking": sorted_imp[:top_n], "total_features": len(self._importance)}

    def suggest_removals(self, threshold: float = 0.01) -> Dict[str, Any]:
        self._op_count += 1
        low_value = [k for k, v in self._importance.items() if abs(v) < threshold]
        return {"status": "ok", "suggest_remove": low_value, "count": len(low_value),
                "threshold": threshold}

    def get_stats(self) -> Dict[str, Any]:
        return {"total_samples": len(self._samples), "features_analyzed": len(self._importance),
                "compute_count": self._compute_count, "total_ops": self._op_count}

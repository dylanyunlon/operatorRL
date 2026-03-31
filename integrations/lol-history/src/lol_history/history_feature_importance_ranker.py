"""
HistoryFeatureImportanceRanker — Ranks feature importance from historical data.

Architecture (拿来主义):
  confidence_calibrator.py（M552）+ historical_reward_reshaper.py（M617）

Location: integrations/lol-history/src/lol_history/history_feature_importance_ranker.py

Design Notes (Knuth-level critique):
  User:
    - rank() returns ordered features with importance scores and reasoning.
    - Works with minimal data (>=5 samples) — falls back to uniform importance.
    - Results include confidence reflecting sample size.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Uses variance-based importance: features with higher outcome-correlation rank higher.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_feature_importance_ranker.v1"

_MIN_SAMPLES: int = 5


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _pearson_r(xs: List[float], ys: List[float]) -> float:
    """Pearson correlation coefficient between two lists."""
    n = len(xs)
    if n < 2 or len(ys) != n:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    std_x = math.sqrt(sum((x - mean_x) ** 2 for x in xs))
    std_y = math.sqrt(sum((y - mean_y) ** 2 for y in ys))
    denom = std_x * std_y
    if denom == 0:
        return 0.0
    return cov / denom


def _confidence(n: int, max_n: int = 100) -> float:
    if n <= 0:
        return 0.0
    return min(1.0, math.log1p(n) / math.log1p(max_n))


class HistoryFeatureImportanceRanker:
    """Ranks feature importance from historical match data.

    Public API
    ----------
    add_sample          — add a (features, outcome) sample
    rank                — compute feature importance ranking
    get_top_features    — get top N most important features
    reset               — clear all samples
    get_stats           — internal statistics

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._samples: List[Dict[str, Any]] = []
        self._outcomes: List[float] = []
        self._feature_names: set = set()

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def add_sample(self, features: Dict[str, float] = None,
                   outcome: float = 0.0) -> Dict[str, Any]:
        """Add a (features, outcome) sample.

        Parameters
        ----------
        features : dict of str -> float
        outcome : float  (e.g., 1.0 = win, 0.0 = loss)

        Returns
        -------
        dict  with status, sample_count
        """
        self._op_count += 1
        if features is None:
            features = {}

        self._samples.append(dict(features))
        self._outcomes.append(outcome)
        self._feature_names.update(features.keys())

        return {"status": "ok", "op": "add_sample",
                "sample_count": len(self._samples)}

    # ------------------------------------------------------------------ #

    def rank(self) -> Dict[str, Any]:
        """Compute feature importance ranking via correlation with outcome.

        Returns
        -------
        dict  with status, ranking (list of {feature, importance, correlation}),
              confidence, sample_count
        """
        self._op_count += 1
        _start = time.time()

        n = len(self._samples)
        if n < _MIN_SAMPLES:
            # Uniform importance
            uniform = 1.0 / max(len(self._feature_names), 1)
            ranking = [{"feature": f, "importance": round(uniform, 4),
                        "correlation": 0.0, "reason": "insufficient_data"}
                       for f in sorted(self._feature_names)]
            return {"status": "ok", "op": "rank",
                    "ranking": ranking, "confidence": 0.0,
                    "sample_count": n, "sufficient_data": False}

        # Compute correlation of each feature with outcome
        importances: List[Dict[str, Any]] = []
        for feat in self._feature_names:
            feat_values = [s.get(feat, 0.0) for s in self._samples]
            corr = _pearson_r(feat_values, self._outcomes)
            abs_corr = abs(corr)
            importances.append({
                "feature": feat,
                "importance": round(abs_corr, 4),
                "correlation": round(corr, 4),
                "direction": "positive" if corr > 0 else "negative" if corr < 0 else "neutral",
            })

        # Normalize importances to sum to 1
        total_imp = sum(i["importance"] for i in importances)
        if total_imp > 0:
            for i in importances:
                i["importance"] = round(i["importance"] / total_imp, 4)

        importances.sort(key=lambda x: -x["importance"])

        conf = _confidence(n)
        elapsed = time.time() - _start
        self._fire("rank_completed", {"elapsed": elapsed, "feature_count": len(importances)})
        return {"status": "ok", "op": "rank",
                "ranking": importances, "confidence": round(conf, 4),
                "sample_count": n, "sufficient_data": True}

    # ------------------------------------------------------------------ #

    def get_top_features(self, top_n: int = 5) -> Dict[str, Any]:
        """Get top N most important features.

        Returns
        -------
        dict  with status, features (list)
        """
        self._op_count += 1
        result = self.rank()
        ranking = result.get("ranking", [])
        return {"status": "ok", "op": "get_top_features",
                "features": ranking[:top_n],
                "sufficient_data": result.get("sufficient_data", False)}

    # ------------------------------------------------------------------ #

    def reset(self) -> Dict[str, Any]:
        """Clear all samples."""
        self._op_count += 1
        self._samples.clear()
        self._outcomes.clear()
        self._feature_names.clear()
        self._fire("reset_completed", {})
        return {"status": "ok", "op": "reset"}

    # ------------------------------------------------------------------ #

    def get_stats(self) -> Dict[str, Any]:
        return {
            "op_count": self._op_count,
            "sample_count": len(self._samples),
            "feature_count": len(self._feature_names),
            "min_samples_required": _MIN_SAMPLES,
        }

"""
HistoryConfidenceCalibrator — Calibrates prediction confidence from historical data quality.

Architecture (拿来主义):
  realtime_decision_confidence_scorer.py（M659）— confidence scoring patterns
  history_data_quality_checker.py（M624）— data quality assessment

Location: integrations/lol-history/src/lol_history/history_confidence_calibrator.py

Design Notes (Knuth-level critique):
  User:
    - calibrate() adjusts any prediction's confidence based on data quality factors.
    - Transparent: returns each factor's contribution to the final confidence.
  System:
    - Factors: sample size, data recency, patch relevance, source reliability.
    - Multiplicative model ensures any single bad factor pulls confidence down.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.history_confidence_calibrator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class HistoryConfidenceCalibrator:
    """Calibrates prediction confidence based on underlying data quality.

    Public API: calibrate, set_current_patch, register_source_reliability, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._current_patch: Optional[str] = None
        self._source_reliability: Dict[str, float] = {}
        self._calibrate_count = 0
        self._sample_size_curve = {1: 0.1, 3: 0.3, 5: 0.5, 10: 0.7, 20: 0.85, 50: 0.95, 100: 1.0}

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_current_patch(self, patch: str) -> Dict[str, Any]:
        """Set the current game patch version (e.g. '14.10')."""
        self._op_count += 1
        self._current_patch = patch
        return {"status": "ok", "patch": patch}

    def register_source_reliability(self, source: str, reliability: float) -> Dict[str, Any]:
        """Register reliability score for a data source (0-1)."""
        self._op_count += 1
        self._source_reliability[source] = min(max(reliability, 0.0), 1.0)
        return {"status": "ok", "source": source, "reliability": self._source_reliability[source]}

    def _sample_size_factor(self, n: int) -> float:
        """Map sample size to confidence factor using interpolation."""
        if n <= 0:
            return 0.0
        prev_n, prev_f = 0, 0.0
        for threshold_n, factor in sorted(self._sample_size_curve.items()):
            if n <= threshold_n:
                # Linear interpolation
                ratio = _safe_div(n - prev_n, threshold_n - prev_n)
                return prev_f + ratio * (factor - prev_f)
            prev_n, prev_f = threshold_n, factor
        return 1.0

    def _recency_factor(self, data_age_days: float) -> float:
        """Map data age to confidence factor (exponential decay)."""
        if data_age_days <= 0:
            return 1.0
        if data_age_days <= 7:
            return 0.95
        if data_age_days <= 14:
            return 0.85
        if data_age_days <= 30:
            return 0.7
        if data_age_days <= 60:
            return 0.5
        return 0.3

    def _patch_relevance_factor(self, data_patch: str) -> float:
        """Map patch match to confidence factor."""
        if not self._current_patch or not data_patch:
            return 0.7  # Unknown patch, moderate confidence

        if data_patch == self._current_patch:
            return 1.0

        # Parse major.minor
        try:
            curr_parts = self._current_patch.split(".")
            data_parts = data_patch.split(".")
            major_diff = abs(int(curr_parts[0]) - int(data_parts[0]))
            minor_diff = abs(int(curr_parts[1]) - int(data_parts[1])) if len(curr_parts) > 1 and len(data_parts) > 1 else 0

            if major_diff > 0:
                return 0.3
            if minor_diff <= 1:
                return 0.9
            if minor_diff <= 3:
                return 0.7
            return 0.5
        except (ValueError, IndexError):
            return 0.7

    def calibrate(self, raw_confidence: float, sample_size: int = 0,
                  data_age_days: float = 0.0, data_patch: str = "",
                  source: str = "", extra_factors: Dict[str, float] = None) -> Dict[str, Any]:
        """Calibrate a prediction's confidence.

        Args:
            raw_confidence: Original confidence from the prediction module.
            sample_size: Number of data points underlying the prediction.
            data_age_days: Age of the most recent data point.
            data_patch: Game patch version of the data.
            source: Data source name for reliability lookup.
            extra_factors: Additional factors {name: 0-1 factor}.

        Returns:
            Dict with calibrated_confidence and factor breakdown.
        """
        self._op_count += 1
        self._calibrate_count += 1

        factors = {}

        # Base confidence
        factors["raw"] = min(max(raw_confidence, 0.0), 1.0)

        # Sample size
        ss_factor = self._sample_size_factor(sample_size)
        factors["sample_size"] = round(ss_factor, 4)

        # Recency
        rec_factor = self._recency_factor(data_age_days)
        factors["recency"] = round(rec_factor, 4)

        # Patch relevance
        patch_factor = self._patch_relevance_factor(data_patch)
        factors["patch_relevance"] = round(patch_factor, 4)

        # Source reliability
        src_factor = self._source_reliability.get(source, 0.8)
        factors["source_reliability"] = round(src_factor, 4)

        # Extra factors
        if extra_factors:
            for k, v in extra_factors.items():
                factors[k] = min(max(v, 0.0), 1.0)

        # Multiplicative combination
        calibrated = factors["raw"]
        for k, v in factors.items():
            if k != "raw":
                calibrated *= v

        calibrated = round(min(max(calibrated, 0.0), 1.0), 4)

        # Confidence tier
        if calibrated >= 0.8:
            tier = "high"
        elif calibrated >= 0.5:
            tier = "medium"
        elif calibrated >= 0.2:
            tier = "low"
        else:
            tier = "very_low"

        result = {
            "status": "ok",
            "calibrated_confidence": calibrated,
            "confidence_tier": tier,
            "factors": factors,
            "raw_confidence": round(raw_confidence, 4),
        }
        self._fire("calibrated", {"calibrated": calibrated, "tier": tier})
        return result

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"op_count": self._op_count, "calibrate_count": self._calibrate_count,
                "current_patch": self._current_patch,
                "sources_registered": len(self._source_reliability)}

"""
Confidence Calibrator — Calibrate model output confidence scores.

Applies temperature scaling, Platt scaling, or isotonic regression to
raw model logits/probabilities so that stated confidence reflects actual
accuracy. Tracks calibration quality via ECE (Expected Calibration Error).

Location: agentlightning/inference/confidence_calibrator.py

Reference (拿来主义):
  查看 agentlightning/algorithm/multi_game_ppo.py 上现有 MultiGamePPO 的
  logits处理方式, 理解其模式, 特别是 logits→probability→action_selection
  的转换链如何独立于训练循环。
  从 integrations/lol/src/lol_agent/decision_engine.py 这个好例子开始 —
  它的 advantage→confidence 映射展示了分数到置信度的转换模式。
  遵循该模式实现 ConfidenceCalibrator, 让 ActionSampler(M536) 和
  DecisionTreeFallback(M539) 可以基于校准后的置信度决定是否使用NN输出
  或切换到规则兜底.

Design Notes (Knuth-level critique):
  User:
    - Calibrated confidence enables meaningful "certainty" display to player
    - ECE metric quantifies whether "70% confident" really means 70% accuracy
    - Multiple calibration methods available for different scenarios
  System:
    - Temperature scaling is O(n) and requires no additional storage
    - Calibration data collection is append-only for replay safety
    - Bin-based ECE is configurable in granularity
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.confidence_calibrator.v1"

_DEFAULT_TEMPERATURE: float = 1.0
_DEFAULT_ECE_BINS: int = 10


class CalibrationRecord:
    """Single prediction record for calibration evaluation."""

    __slots__ = ("predicted_prob", "actual_correct", "timestamp")

    def __init__(self, predicted_prob: float, actual_correct: bool) -> None:
        self.predicted_prob = predicted_prob
        self.actual_correct = actual_correct
        self.timestamp = time.time()


class ConfidenceCalibrator:
    """Calibrates model confidence scores to match actual accuracy.

    Supports temperature scaling, Platt scaling, and histogram binning.
    Tracks ECE (Expected Calibration Error) to evaluate calibration quality.

    Attributes:
        method: Calibration method ("temperature", "platt", "histogram").
        temperature: Temperature scaling parameter.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        method: str = "temperature",
        temperature: float = _DEFAULT_TEMPERATURE,
        ece_bins: int = _DEFAULT_ECE_BINS,
    ) -> None:
        if method not in ("temperature", "platt", "histogram"):
            raise ValueError(f"Unknown calibration method: {method}")
        self.method = method
        self.temperature = temperature
        self.ece_bins = ece_bins
        self._platt_a: float = 1.0  # Platt sigmoid parameters
        self._platt_b: float = 0.0
        self._histogram_map: Dict[int, float] = {}  # bin_index → calibrated_prob
        self._records: List[CalibrationRecord] = []
        self._calibrate_count: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Calibration ---

    def calibrate(self, raw_confidence: float) -> float:
        """Apply calibration to a raw confidence score.

        Args:
            raw_confidence: Raw model output probability [0, 1].

        Returns:
            Calibrated probability [0, 1].
        """
        clamped = max(0.0, min(1.0, raw_confidence))
        self._calibrate_count += 1

        if self.method == "temperature":
            return self._temperature_scale(clamped)
        elif self.method == "platt":
            return self._platt_scale(clamped)
        elif self.method == "histogram":
            return self._histogram_calibrate(clamped)
        return clamped

    def calibrate_batch(self, raw_confidences: List[float]) -> List[float]:
        """Calibrate a batch of confidence scores.

        Args:
            raw_confidences: List of raw probabilities.

        Returns:
            List of calibrated probabilities.
        """
        return [self.calibrate(c) for c in raw_confidences]

    def calibrate_logits(self, logits: List[float]) -> List[float]:
        """Apply temperature scaling to raw logits and return probabilities.

        Args:
            logits: Raw model logit outputs.

        Returns:
            Softmax probabilities after temperature scaling.
        """
        if not logits:
            return []
        scaled = [l / max(self.temperature, 1e-8) for l in logits]
        max_l = max(scaled)
        exps = [math.exp(l - max_l) for l in scaled]
        total = sum(exps)
        return [e / total for e in exps]

    # --- Training / Fitting ---

    def record_outcome(
        self, predicted_prob: float, actual_correct: bool
    ) -> None:
        """Record a prediction outcome for calibration evaluation.

        Args:
            predicted_prob: The confidence score that was output.
            actual_correct: Whether the prediction was correct.
        """
        self._records.append(CalibrationRecord(predicted_prob, actual_correct))

    def fit_temperature(self, target_ece: float = 0.05, max_iter: int = 100) -> float:
        """Optimize temperature parameter to minimize ECE.

        Simple grid search over temperature values.

        Args:
            target_ece: Target ECE to achieve.
            max_iter: Maximum search iterations.

        Returns:
            Optimized temperature value.
        """
        if len(self._records) < 10:
            return self.temperature

        best_temp = self.temperature
        best_ece = self._compute_ece_with_temp(self.temperature)

        for i in range(max_iter):
            # Search around current best
            for delta in [0.01, 0.05, 0.1, 0.5, -0.01, -0.05, -0.1, -0.5]:
                candidate = best_temp + delta
                if candidate <= 0.01:
                    continue
                ece = self._compute_ece_with_temp(candidate)
                if ece < best_ece:
                    best_ece = ece
                    best_temp = candidate

            if best_ece <= target_ece:
                break

        self.temperature = best_temp
        self._fire_evolution("temperature_fitted", {
            "temperature": best_temp, "ece": best_ece, "records": len(self._records),
        })
        return best_temp

    def fit_platt(self) -> Tuple[float, float]:
        """Fit Platt scaling parameters from recorded outcomes.

        Simple logistic regression fit: calibrated = sigmoid(a * raw + b).

        Returns:
            Tuple of (a, b) parameters.
        """
        if len(self._records) < 10:
            return (self._platt_a, self._platt_b)

        # Simple iterative fit
        a, b = 1.0, 0.0
        lr = 0.01
        for _ in range(200):
            grad_a, grad_b = 0.0, 0.0
            for rec in self._records:
                z = a * rec.predicted_prob + b
                p = 1.0 / (1.0 + math.exp(-max(-20, min(20, z))))
                y = 1.0 if rec.actual_correct else 0.0
                err = p - y
                grad_a += err * rec.predicted_prob
                grad_b += err
            n = len(self._records)
            a -= lr * grad_a / n
            b -= lr * grad_b / n

        self._platt_a = a
        self._platt_b = b
        return (a, b)

    def fit_histogram(self) -> Dict[int, float]:
        """Build histogram calibration map from recorded outcomes.

        Returns:
            Dict of bin_index → calibrated probability.
        """
        if len(self._records) < self.ece_bins:
            return dict(self._histogram_map)

        bins: Dict[int, List[bool]] = {}
        for rec in self._records:
            bin_idx = min(int(rec.predicted_prob * self.ece_bins), self.ece_bins - 1)
            if bin_idx not in bins:
                bins[bin_idx] = []
            bins[bin_idx].append(rec.actual_correct)

        self._histogram_map = {}
        for idx, outcomes in bins.items():
            self._histogram_map[idx] = sum(outcomes) / len(outcomes)

        return dict(self._histogram_map)

    # --- Evaluation ---

    def compute_ece(self) -> float:
        """Compute Expected Calibration Error.

        Returns:
            ECE value [0, 1]. Lower is better.
        """
        if not self._records:
            return 0.0
        return self._compute_ece_with_temp(self.temperature)

    def compute_reliability_diagram(self) -> List[Dict[str, Any]]:
        """Compute reliability diagram data (bin-level accuracy vs confidence).

        Returns:
            List of dicts with bin_center, avg_confidence, avg_accuracy, count.
        """
        if not self._records:
            return []

        bins: Dict[int, Dict[str, Any]] = {}
        for rec in self._records:
            bin_idx = min(int(rec.predicted_prob * self.ece_bins), self.ece_bins - 1)
            if bin_idx not in bins:
                bins[bin_idx] = {"conf_sum": 0.0, "correct_sum": 0, "count": 0}
            bins[bin_idx]["conf_sum"] += rec.predicted_prob
            bins[bin_idx]["correct_sum"] += (1 if rec.actual_correct else 0)
            bins[bin_idx]["count"] += 1

        diagram: List[Dict[str, Any]] = []
        for idx in range(self.ece_bins):
            if idx in bins:
                b = bins[idx]
                diagram.append({
                    "bin_center": (idx + 0.5) / self.ece_bins,
                    "avg_confidence": b["conf_sum"] / b["count"],
                    "avg_accuracy": b["correct_sum"] / b["count"],
                    "count": b["count"],
                })
            else:
                diagram.append({
                    "bin_center": (idx + 0.5) / self.ece_bins,
                    "avg_confidence": 0.0,
                    "avg_accuracy": 0.0,
                    "count": 0,
                })
        return diagram

    def get_stats(self) -> Dict[str, Any]:
        """Get calibrator statistics."""
        return {
            "method": self.method,
            "temperature": self.temperature,
            "platt_a": self._platt_a,
            "platt_b": self._platt_b,
            "records_count": len(self._records),
            "calibrate_count": self._calibrate_count,
            "ece": self.compute_ece(),
        }

    def clear_records(self) -> None:
        """Clear all recorded outcomes."""
        self._records.clear()

    # --- Internal ---

    def _temperature_scale(self, prob: float) -> float:
        if self.temperature <= 0:
            return prob
        logit = math.log(max(prob, 1e-10) / max(1.0 - prob, 1e-10))
        scaled_logit = logit / self.temperature
        return 1.0 / (1.0 + math.exp(-max(-20, min(20, scaled_logit))))

    def _platt_scale(self, prob: float) -> float:
        z = self._platt_a * prob + self._platt_b
        return 1.0 / (1.0 + math.exp(-max(-20, min(20, z))))

    def _histogram_calibrate(self, prob: float) -> float:
        bin_idx = min(int(prob * self.ece_bins), self.ece_bins - 1)
        if bin_idx in self._histogram_map:
            return self._histogram_map[bin_idx]
        return prob

    def _compute_ece_with_temp(self, temp: float) -> float:
        original_temp = self.temperature
        self.temperature = temp
        bins: Dict[int, Dict[str, float]] = {}
        for rec in self._records:
            calibrated = self._temperature_scale(rec.predicted_prob)
            bin_idx = min(int(calibrated * self.ece_bins), self.ece_bins - 1)
            if bin_idx not in bins:
                bins[bin_idx] = {"conf_sum": 0.0, "correct_sum": 0.0, "count": 0.0}
            bins[bin_idx]["conf_sum"] += calibrated
            bins[bin_idx]["correct_sum"] += (1.0 if rec.actual_correct else 0.0)
            bins[bin_idx]["count"] += 1.0

        n = len(self._records)
        ece = 0.0
        for b in bins.values():
            avg_conf = b["conf_sum"] / b["count"]
            avg_acc = b["correct_sum"] / b["count"]
            ece += (b["count"] / n) * abs(avg_acc - avg_conf)

        self.temperature = original_temp
        return ece

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

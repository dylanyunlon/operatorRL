"""
ModelCalibrator — Prediction model calibration and Platt scaling.
==================================================================

Ensures that predicted win probabilities are well-calibrated:
a prediction of 60% should win ~60% of the time. Uses Platt scaling
and isotonic regression for post-hoc calibration.

Architecture position:
    modules/calibration/model_calibrator.py   ← YOU ARE HERE
    ├─ Reads: prediction results + actual outcomes
    ├─ Writes: calibration parameters to model config
    └─ Used by: modules/prediction/win_probability/

Apollo reference:
    modules/calibration/ — sensor calibration
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MAX_SAMPLES: int = 10000
_MIN_SAMPLES_FOR_CALIBRATION: int = 50
_NUM_BINS: int = 10
_PLATT_LR: float = 0.01
_PLATT_ITERATIONS: int = 100


@dataclass
class CalibrationSample:
    """Single prediction-outcome pair."""
    predicted: float  # model's predicted probability
    actual: int       # 1 = win, 0 = loss
    timestamp: float = 0.0
    game_time_s: float = 0.0
    generation_id: str = ""


@dataclass
class CalibrationBin:
    """Reliability diagram bin."""
    bin_start: float
    bin_end: float
    mean_predicted: float = 0.0
    mean_actual: float = 0.0
    count: int = 0
    calibration_error: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "range": f"{self.bin_start:.1f}-{self.bin_end:.1f}",
            "mean_predicted": round(self.mean_predicted, 4),
            "mean_actual": round(self.mean_actual, 4),
            "count": self.count,
            "error": round(self.calibration_error, 4),
        }


@dataclass
class CalibrationResult:
    """Output of calibration analysis."""
    ece: float = 0.0  # Expected Calibration Error
    mce: float = 0.0  # Maximum Calibration Error
    brier_score: float = 0.0
    log_loss: float = 0.0
    platt_a: float = 1.0
    platt_b: float = 0.0
    bins: List[CalibrationBin] = field(default_factory=list)
    sample_count: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ece": round(self.ece, 4),
            "mce": round(self.mce, 4),
            "brier_score": round(self.brier_score, 4),
            "log_loss": round(self.log_loss, 4),
            "platt_a": round(self.platt_a, 6),
            "platt_b": round(self.platt_b, 6),
            "sample_count": self.sample_count,
            "bins": [b.to_dict() for b in self.bins],
        }


class PlattScaler:
    """Platt scaling: learns sigmoid mapping p -> 1/(1+exp(a*p+b)).

    Fits parameters a, b via gradient descent to minimize log-loss
    on calibration data.
    """

    def __init__(self) -> None:
        self.a: float = 1.0
        self.b: float = 0.0
        self._fitted: bool = False

    def fit(
        self,
        predictions: List[float],
        outcomes: List[int],
        lr: float = _PLATT_LR,
        iterations: int = _PLATT_ITERATIONS,
    ) -> Tuple[float, float]:
        """Fit Platt scaling parameters via gradient descent.

        Args:
            predictions: Model predicted probabilities.
            outcomes: Binary outcomes (0 or 1).
            lr: Learning rate.
            iterations: Number of gradient descent steps.

        Returns:
            Tuple of (a, b) parameters.
        """
        n = len(predictions)
        if n == 0:
            return (self.a, self.b)

        a = 0.0
        b = 0.0

        for _ in range(iterations):
            grad_a = 0.0
            grad_b = 0.0

            for pred, outcome in zip(predictions, outcomes):
                logit = a * pred + b
                logit = max(-20.0, min(20.0, logit))
                sigmoid = 1.0 / (1.0 + math.exp(-logit))
                error = sigmoid - outcome
                grad_a += error * pred
                grad_b += error

            a -= lr * grad_a / n
            b -= lr * grad_b / n

        self.a = a
        self.b = b
        self._fitted = True
        return (a, b)

    def transform(self, prediction: float) -> float:
        """Apply Platt scaling to a single prediction."""
        if not self._fitted:
            return prediction
        logit = self.a * prediction + self.b
        logit = max(-20.0, min(20.0, logit))
        return 1.0 / (1.0 + math.exp(-logit))

    def transform_batch(self, predictions: List[float]) -> List[float]:
        return [self.transform(p) for p in predictions]


class ModelCalibrator:
    """Full calibration pipeline: collect samples, analyze, fit scaler.

    Usage::

        calibrator = ModelCalibrator()
        # After each game:
        calibrator.add_sample(predicted_win_prob, actual_outcome)
        # Periodically:
        result = calibrator.calibrate()
        print(f"ECE: {result.ece:.4f}")
        # Apply:
        adjusted_prob = calibrator.apply(raw_prob)
    """

    def __init__(self, num_bins: int = _NUM_BINS) -> None:
        self._samples: Deque[CalibrationSample] = deque(maxlen=_MAX_SAMPLES)
        self._num_bins = num_bins
        self._scaler = PlattScaler()
        self._latest_result: Optional[CalibrationResult] = None
        self._calibration_count: int = 0

    def add_sample(
        self,
        predicted: float,
        actual: int,
        game_time_s: float = 0.0,
        generation_id: str = "",
    ) -> None:
        """Record a prediction-outcome pair."""
        self._samples.append(CalibrationSample(
            predicted=max(0.0, min(1.0, predicted)),
            actual=1 if actual else 0,
            timestamp=time.time(),
            game_time_s=game_time_s,
            generation_id=generation_id,
        ))

    def calibrate(self) -> Optional[CalibrationResult]:
        """Run full calibration analysis and fit Platt scaler.

        Returns:
            CalibrationResult or None if insufficient samples.
        """
        if len(self._samples) < _MIN_SAMPLES_FOR_CALIBRATION:
            logger.info(
                "Insufficient samples for calibration: %d < %d",
                len(self._samples), _MIN_SAMPLES_FOR_CALIBRATION,
            )
            return None

        predictions = [s.predicted for s in self._samples]
        outcomes = [s.actual for s in self._samples]

        # Binned calibration
        bins = self._compute_bins(predictions, outcomes)
        ece = self._expected_calibration_error(bins, len(predictions))
        mce = max((b.calibration_error for b in bins), default=0.0)

        # Brier score
        brier = sum(
            (p - o) ** 2 for p, o in zip(predictions, outcomes)
        ) / len(predictions)

        # Log loss
        eps = 1e-15
        log_loss = -sum(
            o * math.log(max(eps, p)) + (1 - o) * math.log(max(eps, 1 - p))
            for p, o in zip(predictions, outcomes)
        ) / len(predictions)

        # Fit Platt scaler
        a, b = self._scaler.fit(predictions, outcomes)

        result = CalibrationResult(
            ece=ece, mce=mce, brier_score=brier,
            log_loss=log_loss, platt_a=a, platt_b=b,
            bins=bins, sample_count=len(predictions),
            timestamp=time.time(),
        )

        self._latest_result = result
        self._calibration_count += 1
        logger.info(
            "Calibration #%d: ECE=%.4f, Brier=%.4f, Platt(a=%.4f, b=%.4f)",
            self._calibration_count, ece, brier, a, b,
        )
        return result

    def apply(self, raw_prediction: float) -> float:
        """Apply calibration to a raw prediction."""
        return self._scaler.transform(raw_prediction)

    def _compute_bins(
        self,
        predictions: List[float],
        outcomes: List[int],
    ) -> List[CalibrationBin]:
        bins = []
        for i in range(self._num_bins):
            lo = i / self._num_bins
            hi = (i + 1) / self._num_bins
            preds_in_bin = []
            outs_in_bin = []
            for p, o in zip(predictions, outcomes):
                if lo <= p < hi or (i == self._num_bins - 1 and p == hi):
                    preds_in_bin.append(p)
                    outs_in_bin.append(o)

            b = CalibrationBin(bin_start=lo, bin_end=hi)
            if preds_in_bin:
                b.mean_predicted = sum(preds_in_bin) / len(preds_in_bin)
                b.mean_actual = sum(outs_in_bin) / len(outs_in_bin)
                b.count = len(preds_in_bin)
                b.calibration_error = abs(b.mean_predicted - b.mean_actual)
            bins.append(b)
        return bins

    def _expected_calibration_error(
        self, bins: List[CalibrationBin], total: int
    ) -> float:
        if total == 0:
            return 0.0
        return sum(
            (b.count / total) * b.calibration_error for b in bins
        )

    def save(self, path: str | Path) -> None:
        if self._latest_result:
            with open(path, "w") as f:
                json.dump(self._latest_result.to_dict(), f, indent=2)

    def load(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        try:
            with open(p) as f:
                data = json.load(f)
            self._scaler.a = data.get("platt_a", 1.0)
            self._scaler.b = data.get("platt_b", 0.0)
            self._scaler._fitted = True
            return True
        except Exception as exc:
            logger.error("Failed to load calibration: %s", exc)
            return False

    def stats(self) -> Dict[str, Any]:
        return {
            "sample_count": len(self._samples),
            "calibration_count": self._calibration_count,
            "platt_fitted": self._scaler._fitted,
            "latest_ece": (self._latest_result.ece
                           if self._latest_result else None),
        }

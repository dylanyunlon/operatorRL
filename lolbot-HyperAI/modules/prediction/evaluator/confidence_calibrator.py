"""
modules/prediction/evaluator/confidence_calibrator.py
======================================================
Claude18 · Prediction confidence calibration

Problem from diagnostic run:
    prediction confidence ramps linearly with game_time only, ignoring
    data quality signals. When canbus feeds stale/mock data, confidence
    should be lower. When perception detects many events, confidence
    should rise faster.

Solution (Apollo evaluator pattern):
    查看 Apollo modules/prediction/evaluator/evaluator_manager.cc 上现有
    评估器管理器的实现方式, 理解其模式, 特别是 多信号融合置信度 是如何计算
    的。从 Apollo EvaluatorManager 的 multi-source confidence 这个好例子开始。
    然后, 遵循该模式实现一个 ConfidenceCalibrator, 让 prediction 可以
    基于数据质量信号调节置信度, 并能 在数据降级时自动降低推荐强度。

File location: lolbot-HyperAI/modules/prediction/evaluator/confidence_calibrator.py
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DataQualitySignal:
    """Aggregated signal about upstream data quality."""
    canbus_source_type: str = "unknown"  # lcu/testdata/mock/simulated
    canbus_stale_count: int = 0
    canbus_error_rate: float = 0.0
    perception_snapshot_count: int = 0
    perception_event_count: int = 0
    game_time: float = 0.0
    time_since_last_event_s: float = 0.0


@dataclass
class CalibratedConfidence:
    """Confidence value with breakdown of contributing factors."""
    final_confidence: float = 0.5
    time_factor: float = 0.0
    data_quality_factor: float = 0.0
    event_richness_factor: float = 0.0
    stability_factor: float = 0.0
    source_penalty: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final": round(self.final_confidence, 4),
            "time": round(self.time_factor, 4),
            "data_quality": round(self.data_quality_factor, 4),
            "event_richness": round(self.event_richness_factor, 4),
            "stability": round(self.stability_factor, 4),
            "source_penalty": round(self.source_penalty, 4),
        }


class ConfidenceCalibrator:
    """Multi-signal confidence calibration for win predictions.

    Instead of a simple linear ramp (confidence = game_time / 600),
    this integrates:

    1. Time factor: Base confidence from game duration (existing behavior)
    2. Data quality: Penalize when canbus reports stale/error states
    3. Event richness: More game events → more information → higher conf
    4. Stability: Recent prediction variance (low variance → high conf)
    5. Source penalty: Mock/simulated sources get lower confidence than LCU

    The final confidence is the geometric mean of these factors,
    ensuring any single bad signal pulls confidence down significantly.
    """

    # Source type → confidence multiplier
    _SOURCE_MULTIPLIERS = {
        "lcu": 1.0,
        "testdata": 0.7,   # testdata is replay, reasonably realistic
        "simulated": 0.65,  # simulated with synthetic advancement
        "replay": 0.6,
        "mock": 0.3,        # mock data is synthetic garbage
        "unknown": 0.5,
    }

    # After this many seconds, time factor is maxed
    _TIME_RAMP_S = 600.0  # 10 min

    # After this many events, event factor is maxed
    _EVENT_RAMP_COUNT = 30

    def __init__(self) -> None:
        self._prediction_history: List[float] = []
        self._max_history = 50
        self._calibration_count: int = 0

    def calibrate(
        self,
        raw_confidence: float,
        signal: DataQualitySignal,
    ) -> CalibratedConfidence:
        """Calibrate raw confidence using data quality signals.

        Args:
            raw_confidence: Original confidence from prediction model.
            signal: Upstream data quality signals.

        Returns:
            CalibratedConfidence with factor breakdown.
        """
        self._calibration_count += 1

        # 1. Time factor (existing behavior, kept as baseline)
        time_factor = min(1.0, signal.game_time / self._TIME_RAMP_S)

        # 2. Data quality factor
        data_quality = 1.0
        if signal.canbus_stale_count > 10:
            data_quality *= max(0.3, 1.0 - signal.canbus_stale_count / 100.0)
        if signal.canbus_error_rate > 0.05:
            data_quality *= max(0.2, 1.0 - signal.canbus_error_rate)

        # 3. Event richness factor
        event_richness = min(
            1.0,
            signal.perception_event_count / self._EVENT_RAMP_COUNT,
        )
        # Penalize if no new events for a long time
        if signal.time_since_last_event_s > 120.0:
            event_richness *= 0.7

        # 4. Stability factor (low prediction variance → high confidence)
        stability = 1.0
        if len(self._prediction_history) >= 5:
            recent = self._prediction_history[-10:]
            variance = sum(
                (x - sum(recent) / len(recent)) ** 2 for x in recent
            ) / len(recent)
            # High variance (>0.01) reduces stability
            stability = max(0.3, 1.0 - variance * 10.0)

        # 5. Source penalty
        source_mult = self._SOURCE_MULTIPLIERS.get(
            signal.canbus_source_type, 0.5
        )

        # Geometric mean of all factors (any bad signal pulls down hard)
        factors = [
            max(0.01, time_factor),
            max(0.01, data_quality),
            max(0.01, event_richness),
            max(0.01, stability),
            max(0.01, source_mult),
        ]
        geo_mean = math.exp(sum(math.log(f) for f in factors) / len(factors))

        # Final confidence: blend with raw (50% model, 50% calibrated)
        final = 0.5 * raw_confidence + 0.5 * geo_mean
        final = max(0.01, min(0.99, final))

        return CalibratedConfidence(
            final_confidence=final,
            time_factor=time_factor,
            data_quality_factor=data_quality,
            event_richness_factor=event_richness,
            stability_factor=stability,
            source_penalty=1.0 - source_mult,
        )

    def record_prediction(self, win_prob: float) -> None:
        """Record a win probability for stability tracking."""
        self._prediction_history.append(win_prob)
        if len(self._prediction_history) > self._max_history:
            self._prediction_history = self._prediction_history[
                -self._max_history:
            ]

    def stats(self) -> Dict[str, Any]:
        return {
            "calibration_count": self._calibration_count,
            "history_size": len(self._prediction_history),
        }


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: ConfidenceCalibratorV2 — isotonic regression calibration,
# reliability diagrams, calibration drift detection, per-phase calibration
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class CalibrationBin:
    """A single bin in a reliability diagram.

    Claude21: For calibration assessment, we bucket predictions by
    confidence, then compare mean predicted probability vs actual
    frequency of positive outcomes in each bin.
    """
    bin_lower: float
    bin_upper: float
    mean_predicted: float = 0.0
    actual_positive_rate: float = 0.0
    count: int = 0
    gap: float = 0.0  # |predicted - actual|

    def to_dict(self) -> Dict[str, Any]:
        return {
            "range": f"[{self.bin_lower:.2f}, {self.bin_upper:.2f})",
            "predicted": round(self.mean_predicted, 4),
            "actual": round(self.actual_positive_rate, 4),
            "count": self.count,
            "gap": round(self.gap, 4),
        }


@dataclass
class CalibrationReport:
    """Full calibration assessment report.

    Claude21: Published after each game for the evolution system to
    use as a fitness signal. Well-calibrated predictions mean the
    model's confidence matches reality.
    """
    ece: float = 0.0              # Expected Calibration Error
    mce: float = 0.0              # Maximum Calibration Error
    brier_score: float = 0.0      # Brier score (MSE of probabilities)
    bins: List[CalibrationBin] = field(default_factory=list)
    sample_count: int = 0
    overconfidence_rate: float = 0.0
    underconfidence_rate: float = 0.0
    game_phase: str = "all"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ece": round(self.ece, 4),
            "mce": round(self.mce, 4),
            "brier": round(self.brier_score, 4),
            "samples": self.sample_count,
            "overconfident": round(self.overconfidence_rate, 4),
            "underconfident": round(self.underconfidence_rate, 4),
            "phase": self.game_phase,
            "bins": [b.to_dict() for b in self.bins],
        }


class IsotonicCalibrator:
    """Isotonic regression calibration function.

    Claude21: Maps raw model outputs to calibrated probabilities using
    a non-parametric monotonic function learned from historical data.
    This is the gold standard for post-hoc probability calibration.

    Based on: Zadrozny & Elkan (2002) "Transforming classifier scores
    into accurate multiclass probability estimates."
    """

    def __init__(self) -> None:
        self._x_points: List[float] = []  # raw predictions (sorted)
        self._y_points: List[float] = []  # calibrated values
        self._fitted: bool = False

    def fit(
        self, raw_predictions: List[float], actual_outcomes: List[float],
    ) -> None:
        """Fit isotonic regression from (prediction, outcome) pairs.

        Claude21: Uses pool adjacent violators algorithm (PAVA).
        """
        if len(raw_predictions) < 3:
            return

        # Sort by raw prediction
        paired = sorted(zip(raw_predictions, actual_outcomes))
        xs = [p[0] for p in paired]
        ys = [p[1] for p in paired]

        # Pool Adjacent Violators
        calibrated = list(ys)
        n = len(calibrated)
        i = 0
        while i < n:
            j = i
            # Find extent of violation
            while j < n - 1 and calibrated[j] > calibrated[j + 1]:
                j += 1
            if j > i:
                # Pool: replace with average
                pool_mean = sum(calibrated[i:j + 1]) / (j - i + 1)
                for k in range(i, j + 1):
                    calibrated[k] = pool_mean
            i = j + 1

        self._x_points = xs
        self._y_points = calibrated
        self._fitted = True

    def calibrate(self, raw_prediction: float) -> float:
        """Map a raw prediction to a calibrated probability.

        Uses linear interpolation between fitted points.
        """
        if not self._fitted or not self._x_points:
            return raw_prediction

        # Clamp to range
        if raw_prediction <= self._x_points[0]:
            return self._y_points[0]
        if raw_prediction >= self._x_points[-1]:
            return self._y_points[-1]

        # Binary search for interpolation bracket
        lo, hi = 0, len(self._x_points) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self._x_points[mid] <= raw_prediction:
                lo = mid
            else:
                hi = mid

        # Linear interpolation
        x0, x1 = self._x_points[lo], self._x_points[hi]
        y0, y1 = self._y_points[lo], self._y_points[hi]
        if abs(x1 - x0) < 1e-10:
            return y0
        t = (raw_prediction - x0) / (x1 - x0)
        return y0 + t * (y1 - y0)


class ConfidenceCalibratorV2(ConfidenceCalibrator):
    """Production-grade confidence calibration with isotonic regression,
    reliability diagrams, drift detection, and per-phase calibration.

    Claude21: Extends ConfidenceCalibrator with:
    - Isotonic regression for post-hoc probability calibration
    - Reliability diagram generation (ECE, MCE, Brier score)
    - Calibration drift detection (alert when calibration degrades)
    - Per-game-phase calibration (early/mid/late separately)
    - Historical calibration tracking for evolution

    Usage::
        calibrator = ConfidenceCalibratorV2(n_bins=10)
        # During game: collect raw predictions
        calibrator.record(raw_prediction=0.72, game_phase="MID")
        # Post-game: label outcomes and refit
        calibrator.label_outcome(game_time=600.0, actual=1.0)
        calibrator.refit()
        # Use calibrated predictions
        p = calibrator.calibrate(0.72)
    """

    def __init__(self, n_bins: int = 10) -> None:
        super().__init__()
        self._n_bins = n_bins
        self._isotonic = IsotonicCalibrator()
        self._phase_isotonics: Dict[str, IsotonicCalibrator] = {}
        self._raw_history: List[Tuple[float, float, str]] = []  # (pred, outcome, phase)
        self._drift_ece_history: List[float] = []
        self._drift_threshold: float = 0.15  # Alert if ECE exceeds this

    def record(
        self, raw_prediction: float, game_phase: str = "all",
    ) -> None:
        """Record a raw prediction for later outcome labeling."""
        self._raw_history.append((raw_prediction, -1.0, game_phase))

    def label_outcome(
        self, game_time: float, actual: float,
    ) -> None:
        """Label the most recent unlabeled prediction with actual outcome.

        Claude21: Called when the ground truth becomes available
        (e.g., game ended, or prediction time window expired).
        """
        for i in range(len(self._raw_history) - 1, -1, -1):
            pred, outcome, phase = self._raw_history[i]
            if outcome < 0:  # unlabeled
                self._raw_history[i] = (pred, actual, phase)
                return

    def refit(self) -> None:
        """Refit isotonic calibrators from labeled data."""
        labeled = [(p, o, ph) for p, o, ph in self._raw_history if o >= 0]
        if len(labeled) < 10:
            return

        # Global calibrator
        preds = [x[0] for x in labeled]
        outcomes = [x[1] for x in labeled]
        self._isotonic.fit(preds, outcomes)

        # Per-phase calibrators
        phases: Dict[str, List[Tuple[float, float]]] = {}
        for p, o, ph in labeled:
            phases.setdefault(ph, []).append((p, o))

        for phase, data in phases.items():
            if len(data) >= 10:
                iso = IsotonicCalibrator()
                iso.fit([d[0] for d in data], [d[1] for d in data])
                self._phase_isotonics[phase] = iso

    def calibrate(
        self, raw_prediction: float, game_phase: str = "all",
    ) -> float:
        """Get calibrated probability."""
        iso = self._phase_isotonics.get(game_phase, self._isotonic)
        return iso.calibrate(raw_prediction)

    def compute_reliability_diagram(
        self, phase: str = "all",
    ) -> CalibrationReport:
        """Compute reliability diagram with ECE, MCE, Brier score.

        Claude21: The reliability diagram is the primary diagnostic tool
        for calibration quality. Each bin shows predicted vs actual.
        """
        labeled = [
            (p, o) for p, o, ph in self._raw_history
            if o >= 0 and (phase == "all" or ph == phase)
        ]
        if not labeled:
            return CalibrationReport(game_phase=phase)

        bins: List[CalibrationBin] = []
        bin_width = 1.0 / self._n_bins

        total_ece = 0.0
        max_gap = 0.0
        brier_sum = 0.0
        over_count = 0
        under_count = 0

        for i in range(self._n_bins):
            lower = i * bin_width
            upper = (i + 1) * bin_width
            bin_items = [
                (p, o) for p, o in labeled if lower <= p < upper
            ]

            b = CalibrationBin(bin_lower=lower, bin_upper=upper)
            if bin_items:
                b.count = len(bin_items)
                b.mean_predicted = sum(p for p, _ in bin_items) / b.count
                b.actual_positive_rate = sum(o for _, o in bin_items) / b.count
                b.gap = abs(b.mean_predicted - b.actual_positive_rate)
                total_ece += b.gap * (b.count / len(labeled))
                max_gap = max(max_gap, b.gap)

                if b.mean_predicted > b.actual_positive_rate:
                    over_count += b.count
                else:
                    under_count += b.count

            bins.append(b)

        for p, o in labeled:
            brier_sum += (p - o) ** 2
        brier = brier_sum / len(labeled)

        total_labeled = over_count + under_count
        report = CalibrationReport(
            ece=total_ece,
            mce=max_gap,
            brier_score=brier,
            bins=bins,
            sample_count=len(labeled),
            overconfidence_rate=over_count / max(total_labeled, 1),
            underconfidence_rate=under_count / max(total_labeled, 1),
            game_phase=phase,
        )

        self._drift_ece_history.append(total_ece)
        return report

    def check_drift(self) -> Optional[str]:
        """Check if calibration has drifted beyond threshold.

        Returns warning message or None if OK.
        """
        if len(self._drift_ece_history) < 3:
            return None
        recent_ece = self._drift_ece_history[-1]
        if recent_ece > self._drift_threshold:
            return (
                f"Calibration drift detected: ECE={recent_ece:.4f} "
                f"exceeds threshold {self._drift_threshold:.4f}"
            )
        return None

    def extended_stats(self) -> Dict[str, Any]:
        base = self.calibrator_stats() if hasattr(self, "calibrator_stats") else {}
        report = self.compute_reliability_diagram()
        base.update({
            "v2_report": report.to_dict(),
            "drift_history": [round(e, 4) for e in self._drift_ece_history[-10:]],
            "phase_calibrators": list(self._phase_isotonics.keys()),
            "labeled_examples": sum(1 for _, o, _ in self._raw_history if o >= 0),
        })
        return base

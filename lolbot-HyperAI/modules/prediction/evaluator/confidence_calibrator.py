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

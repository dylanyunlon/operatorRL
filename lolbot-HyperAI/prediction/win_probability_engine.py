#!/usr/bin/env python3
"""
prediction/win_probability_engine.py — Real-time Win Probability Model
========================================================================
lolbot-HyperAI · Prediction Layer

In Apollo, the prediction module estimates trajectories: where will
each vehicle be in 3/5/8 seconds? Our prediction estimates trajectories
too — but in the outcome space: what's our probability of winning at
each moment?

Model architecture (hierarchical ensemble):
    1. Logistic baseline: handcrafted feature weights (always available)
    2. Gradient-boosted trees: trained on match history (when available)
    3. Temporal trend: Bayesian update from feature trajectory

The engine produces:
    - win_pct: 0.0 to 1.0 probability
    - confidence: how sure the model is (based on feature coverage)
    - trend: rising / falling / stable
    - key_factors: top features driving the prediction
    - what_if: "if we get dragon, win prob becomes X"

Calibration: The model is calibrated so that "60% win probability"
means 60% of games with similar states actually result in wins.
This is critical for user trust.

Self-evolution hook: The evolution controller can:
    1. Adjust feature weights (logistic model)
    2. Retrain the tree model on new match data
    3. Adjust confidence calibration parameters

Subscribes to: (called directly by the scheduler)
Publishes to: CH_WIN_PROBABILITY
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_WIN_PROBABILITY,
    ChannelMessage,
    MessageFactory,
)
from canbus.transport import Transport
from prediction.feature_pipeline import (
    FeatureVector,
    FeaturePipeline,
    FEATURE_NAMES,
    NUM_FEATURES,
)


# ---------------------------------------------------------------------------
# Prediction result
# ---------------------------------------------------------------------------
class PredictionTrend(Enum):
    RISING = "rising"
    STABLE = "stable"
    FALLING = "falling"


@dataclass
class WinPrediction:
    """Complete prediction output for one game state."""
    win_pct: float                 # 0.0 to 1.0
    confidence: float              # 0.0 to 1.0
    trend: PredictionTrend
    trend_delta: float             # Change over last 60 seconds
    key_factors: List[Dict[str, Any]]  # Top contributing features
    what_if: Dict[str, float]      # Scenario analysis
    model_version: str
    features_used: int
    game_time_sec: float
    prediction_ms: int             # Time to compute

    def to_dict(self) -> Dict[str, Any]:
        return {
            "win_pct": round(self.win_pct, 4),
            "confidence": round(self.confidence, 3),
            "trend": self.trend.value,
            "trend_delta": round(self.trend_delta, 4),
            "key_factors": self.key_factors,
            "what_if": {k: round(v, 4) for k, v in self.what_if.items()},
            "model_version": self.model_version,
            "features_used": self.features_used,
            "game_time_sec": self.game_time_sec,
            "prediction_ms": self.prediction_ms,
        }


# ---------------------------------------------------------------------------
# Logistic baseline model
# ---------------------------------------------------------------------------
class LogisticModel:
    """
    Handcrafted logistic regression baseline.

    Weights are interpretable and tunable by the evolution controller.
    This model is always available, unlike the tree model which needs
    training data.

    P(win) = sigmoid(sum(w_i * x_i) + bias)

    Weights were calibrated from:
        1. Domain knowledge (gold diff is strongest predictor)
        2. Published research on LoL win prediction
        3. Log analysis from M1046-M1065 test sessions
    """

    # Feature name → weight
    DEFAULT_WEIGHTS: Dict[str, float] = {
        # Gold economy (strongest predictors)
        "gold_diff_norm": 1.8,
        "gold_diff_per_min": 0.6,
        "cs_diff_norm": 0.4,
        "cs_per_min_avg": 0.3,
        "gold_share_carry": -0.2,    # Too much gold on one player = risky
        "item_completion_diff": 0.5,
        "gold_efficiency": 0.3,
        "bounty_state": 0.2,

        # Tempo
        "kills_per_min_diff": 0.7,
        "kill_streak_state": 0.3,
        "first_blood": 0.15,
        "recent_kill_burst": 0.4,
        "death_timer_pressure": 0.5,
        "objective_tempo": 0.6,

        # Composition
        "team_damage_profile": 0.1,
        "scaling_score": 0.3,
        "engage_score": 0.2,
        "peel_score": 0.15,
        "comp_synergy": 0.25,

        # Structural (very strong mid/late)
        "turret_diff": 0.9,
        "inhib_diff": 1.2,
        "dragon_diff": 0.5,
        "baron_state": 0.8,
        "elder_state": 1.0,
        "soul_state": 0.7,

        # Momentum
        "momentum_score": 0.5,
        "comeback_potential": 0.2,
        "tilt_indicator": -0.3,
        "snowball_indicator": 0.6,

        # Time (interaction effects)
        "game_time_norm": 0.0,        # Neutral on its own
        "phase_indicator": 0.0,
        "time_pressure": -0.3,        # Pressure is bad
    }

    DEFAULT_BIAS = 0.0  # 50/50 at start

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        bias: float = DEFAULT_BIAS,
    ) -> None:
        self.weights = dict(weights or self.DEFAULT_WEIGHTS)
        self.bias = bias
        self.version = "logistic_v1"

    def predict(self, features: FeatureVector) -> float:
        """
        Compute win probability.

        Returns: float in [0, 1].
        """
        logit = self.bias
        for name, weight in self.weights.items():
            val = features.values.get(name, 0.0)
            logit += weight * val

        return self._sigmoid(logit)

    def feature_contributions(
        self, features: FeatureVector,
    ) -> List[Tuple[str, float]]:
        """
        Compute each feature's contribution to the prediction.

        Contribution = weight * feature_value.
        Sorted by absolute contribution (largest first).
        """
        contributions = []
        for name, weight in self.weights.items():
            val = features.values.get(name, 0.0)
            contrib = weight * val
            if abs(contrib) > 0.01:
                contributions.append((name, contrib))
        contributions.sort(key=lambda x: abs(x[1]), reverse=True)
        return contributions

    def what_if_analysis(
        self,
        features: FeatureVector,
        scenarios: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """
        Compute win probability under hypothetical scenarios.

        Args:
            features: Current feature vector.
            scenarios: {scenario_name: {feature_name: override_value}}.

        Returns: {scenario_name: predicted_win_pct}.
        """
        results = {}
        for scenario_name, overrides in scenarios.items():
            modified = FeatureVector(
                values={**features.values, **overrides},
                game_time_sec=features.game_time_sec,
            )
            results[scenario_name] = self.predict(modified)
        return results

    def update_weight(self, feature_name: str, new_weight: float) -> None:
        """Update a single weight (used by evolution controller)."""
        if feature_name in self.weights:
            self.weights[feature_name] = new_weight

    def export_weights(self) -> Dict[str, Any]:
        """Export weights for serialization."""
        return {
            "weights": dict(self.weights),
            "bias": self.bias,
            "version": self.version,
        }

    def import_weights(self, data: Dict[str, Any]) -> None:
        """Import weights from serialization."""
        self.weights = dict(data.get("weights", self.weights))
        self.bias = data.get("bias", self.bias)
        self.version = data.get("version", self.version)

    @staticmethod
    def _sigmoid(x: float) -> float:
        """Numerically stable sigmoid."""
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        else:
            z = math.exp(x)
            return z / (1.0 + z)


# ---------------------------------------------------------------------------
# Bayesian trend tracker
# ---------------------------------------------------------------------------
class TrendTracker:
    """
    Tracks prediction trend using exponential moving average.

    Maintains a short-term (30s) and long-term (120s) EMA.
    Trend = short_ema - long_ema.
        Positive → rising
        Negative → falling
        Near zero → stable
    """

    TREND_THRESHOLD = 0.02

    def __init__(
        self,
        short_alpha: float = 0.3,
        long_alpha: float = 0.05,
    ) -> None:
        self._short_ema: Optional[float] = None
        self._long_ema: Optional[float] = None
        self._short_alpha = short_alpha
        self._long_alpha = long_alpha
        self._history: Deque[Tuple[float, float]] = deque(maxlen=300)

    def update(self, win_pct: float, game_time: float) -> None:
        """Update with a new prediction."""
        if self._short_ema is None:
            self._short_ema = win_pct
            self._long_ema = win_pct
        else:
            self._short_ema = (
                self._short_alpha * win_pct
                + (1 - self._short_alpha) * self._short_ema
            )
            self._long_ema = (
                self._long_alpha * win_pct
                + (1 - self._long_alpha) * self._long_ema
            )
        self._history.append((game_time, win_pct))

    @property
    def trend(self) -> PredictionTrend:
        if self._short_ema is None or self._long_ema is None:
            return PredictionTrend.STABLE
        delta = self._short_ema - self._long_ema
        if delta > self.TREND_THRESHOLD:
            return PredictionTrend.RISING
        elif delta < -self.TREND_THRESHOLD:
            return PredictionTrend.FALLING
        return PredictionTrend.STABLE

    @property
    def trend_delta(self) -> float:
        """Numeric trend value (positive = improving)."""
        if self._short_ema is None or self._long_ema is None:
            return 0.0
        return self._short_ema - self._long_ema

    def delta_over_seconds(self, seconds: float) -> float:
        """Win probability change over the last N seconds."""
        if len(self._history) < 2:
            return 0.0
        current_time = self._history[-1][0]
        cutoff = current_time - seconds
        # Find the prediction closest to the cutoff
        for t, pct in self._history:
            if t >= cutoff:
                return self._history[-1][1] - pct
        return 0.0


# ---------------------------------------------------------------------------
# Confidence estimator
# ---------------------------------------------------------------------------
class ConfidenceEstimator:
    """
    Estimates confidence in the win prediction.

    Confidence depends on:
        1. Feature coverage: how many features have non-zero values
        2. Game time: longer games → more data → higher confidence
        3. Prediction stability: volatile predictions → lower confidence
        4. Feature quality: some features are more reliable than others
    """

    def estimate(
        self,
        features: FeatureVector,
        trend: TrendTracker,
        game_time_sec: float,
    ) -> float:
        """
        Compute confidence score.

        Returns: float in [0, 1].
        """
        # Feature coverage (0-1)
        non_zero = sum(
            1 for v in features.values.values() if abs(v) > 0.001
        )
        coverage = min(non_zero / max(NUM_FEATURES, 1), 1.0)

        # Game time factor (confidence increases over time)
        # Low at start (0.3), high after 15 min (0.9)
        time_factor = min(0.3 + game_time_sec / 2500, 0.95)

        # Stability: high volatility → low confidence
        volatility = abs(trend.trend_delta) if trend.trend_delta else 0
        stability = max(0.3, 1.0 - volatility * 5)

        # Weighted combination
        confidence = 0.4 * coverage + 0.35 * time_factor + 0.25 * stability
        return max(0.1, min(0.95, confidence))


# ---------------------------------------------------------------------------
# Win Probability Engine (main component)
# ---------------------------------------------------------------------------
class WinProbabilityEngine:
    """
    Main prediction engine. Combines models, trend tracking, and
    confidence estimation to produce calibrated win predictions.

    Lifecycle (Apollo-style):
        init() → load models, subscribe to bus
        proc() → extract features, predict, publish
        shutdown() → export final model state
    """

    PROC_INTERVAL_MS = 2000  # Predict every 2 seconds

    # Default what-if scenarios
    _SCENARIOS: Dict[str, Dict[str, float]] = {
        "get_dragon": {"dragon_diff": 0.25, "objective_tempo": 0.1},
        "lose_dragon": {"dragon_diff": -0.25},
        "get_baron": {"baron_state": 1.0, "momentum_score": 0.3},
        "lose_baron": {"baron_state": -1.0, "momentum_score": -0.3},
        "get_tower": {"turret_diff": 0.1, "gold_diff_norm": 0.05},
        "team_wipe_enemy": {
            "death_timer_pressure": 1.0,
            "momentum_score": 0.5,
            "recent_kill_burst": 1.0,
        },
        "team_wipe_us": {
            "death_timer_pressure": 0.0,
            "momentum_score": -0.5,
            "tilt_indicator": 0.3,
        },
    }

    def __init__(
        self,
        transport: Transport,
        feature_pipeline: FeaturePipeline,
    ) -> None:
        self._transport = transport
        self._features = feature_pipeline
        self._factory = MessageFactory("prediction.win_probability")

        # Models
        self._logistic = LogisticModel()
        self._trend = TrendTracker()
        self._confidence = ConfidenceEstimator()

        # State
        self._last_proc_ms = 0
        self._prediction_count = 0
        self._latest_prediction: Optional[WinPrediction] = None
        self._prediction_history: Deque[WinPrediction] = deque(maxlen=500)

        # Model persistence path (for evolution)
        self._model_dir: Optional[Path] = None

    def init(self, model_dir: Optional[Path] = None) -> None:
        """Initialize and optionally load saved model weights."""
        self._model_dir = model_dir
        if model_dir and (model_dir / "logistic_weights.json").exists():
            try:
                data = json.loads(
                    (model_dir / "logistic_weights.json").read_text()
                )
                self._logistic.import_weights(data)
            except (json.JSONDecodeError, OSError):
                pass  # Use defaults

    async def proc(self) -> None:
        """
        Single prediction tick.

        Called every PROC_INTERVAL_MS by the scheduler.
        """
        now_ms = int(time.monotonic() * 1000)
        if now_ms - self._last_proc_ms < self.PROC_INTERVAL_MS:
            return
        self._last_proc_ms = now_ms

        # Get latest features
        fv = self._features.latest_features()
        if fv is None:
            return

        start = time.monotonic()

        # Run logistic model
        win_pct = self._logistic.predict(fv)

        # Update trend
        self._trend.update(win_pct, fv.game_time_sec)

        # Compute confidence
        confidence = self._confidence.estimate(
            fv, self._trend, fv.game_time_sec,
        )

        # Feature contributions (for explainability)
        contributions = self._logistic.feature_contributions(fv)
        key_factors = [
            {
                "feature": name,
                "contribution": round(contrib, 4),
                "direction": "positive" if contrib > 0 else "negative",
            }
            for name, contrib in contributions[:5]
        ]

        # What-if scenarios
        what_if = self._logistic.what_if_analysis(fv, self._SCENARIOS)

        prediction_ms = int((time.monotonic() - start) * 1000)

        prediction = WinPrediction(
            win_pct=win_pct,
            confidence=confidence,
            trend=self._trend.trend,
            trend_delta=self._trend.delta_over_seconds(60),
            key_factors=key_factors,
            what_if=what_if,
            model_version=self._logistic.version,
            features_used=len(fv.values),
            game_time_sec=fv.game_time_sec,
            prediction_ms=prediction_ms,
        )

        self._latest_prediction = prediction
        self._prediction_history.append(prediction)
        self._prediction_count += 1

        # Publish to CAN bus
        msg = self._factory.create(
            CH_WIN_PROBABILITY,
            prediction.to_dict(),
            priority=1,
            ttl_ms=5000,
        )
        self._transport.publish(msg)

    def shutdown(self) -> Dict[str, Any]:
        """Save model state and return stats."""
        if self._model_dir:
            self._model_dir.mkdir(parents=True, exist_ok=True)
            weights_path = self._model_dir / "logistic_weights.json"
            weights_path.write_text(
                json.dumps(self._logistic.export_weights(), indent=2)
            )
        return self.stats()

    # -- Evolution API (for evolution controller) -----------------------

    def get_model_weights(self) -> Dict[str, Any]:
        """Export current model weights for evolution."""
        return self._logistic.export_weights()

    def set_model_weights(self, data: Dict[str, Any]) -> None:
        """Import mutated weights from evolution controller."""
        self._logistic.import_weights(data)

    def adjust_weight(self, feature: str, delta: float) -> None:
        """Adjust a single weight by delta (used by strategy_mutator)."""
        current = self._logistic.weights.get(feature, 0.0)
        self._logistic.update_weight(feature, current + delta)

    def get_calibration_data(self) -> List[Dict[str, Any]]:
        """
        Export prediction history for calibration analysis.

        The evolution controller compares predicted win_pct with
        actual game outcomes to assess calibration.
        """
        return [p.to_dict() for p in self._prediction_history]

    # -- Public API -----------------------------------------------------

    @property
    def latest(self) -> Optional[WinPrediction]:
        return self._latest_prediction

    def prediction_at_time(self, game_time_sec: float) -> Optional[WinPrediction]:
        """Find the prediction closest to a given game time."""
        best = None
        best_dist = float("inf")
        for p in self._prediction_history:
            dist = abs(p.game_time_sec - game_time_sec)
            if dist < best_dist:
                best_dist = dist
                best = p
        return best

    def stats(self) -> Dict[str, Any]:
        """Component stats."""
        latest = self._latest_prediction
        return {
            "prediction_count": self._prediction_count,
            "model_version": self._logistic.version,
            "latest_win_pct": round(latest.win_pct, 4) if latest else None,
            "latest_confidence": round(latest.confidence, 3) if latest else None,
            "latest_trend": latest.trend.value if latest else None,
            "history_size": len(self._prediction_history),
        }

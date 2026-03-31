#!/usr/bin/env python3
"""
M823 - Prediction Model Integration
====================================
OperatorRL Historical Battle System - Win probability estimation

查看游戏胜率预测模型的集成方式，理解其模式，
特别是历史特征和实时特征是如何组合送入模型的。
从逻辑回归基线模型开始，遵循该模式实现预测集成层，
使系统可以在对局任意时刻估算双方胜率。

Core: Win probability estimation from historical patterns
"""

import os
import sys
import json
import time
import math
import logging
import hashlib
import statistics
from pathlib import Path
from enum import Enum, auto
from typing import Dict, List, Any, Optional, Tuple, Set, Union, Callable
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("operatorRL.historical_battle.integration.prediction")
logger.setLevel(logging.DEBUG)

# ─── Constants ──────────────────────────────────────────────────────────────

MODEL_VERSION = "1.0.0"
FEATURE_COUNT = 24
DEFAULT_WIN_PROB = 0.5
LOGISTIC_SCALE = 4.0
MIN_TRAINING_SAMPLES = 50
PREDICTION_HISTORY_MAX = 1000
CONFIDENCE_THRESHOLD = 0.3

class ModelType(Enum):
    LOGISTIC_REGRESSION = "logistic_regression"
    GRADIENT_BOOST = "gradient_boost"
    NEURAL_NETWORK = "neural_network"
    ENSEMBLE = "ensemble"
    HEURISTIC = "heuristic"

class PredictionPhase(Enum):
    PRE_GAME = "pre_game"
    EARLY_GAME = "early_game"
    MID_GAME = "mid_game"
    LATE_GAME = "late_game"

class CalibrationMethod(Enum):
    PLATT = "platt_scaling"
    ISOTONIC = "isotonic"
    NONE = "none"

# ─── Data Models ────────────────────────────────────────────────────────────

@dataclass
class FeatureVector:
    """Input features for prediction model."""
    team_avg_winrate: float = 0.5
    enemy_avg_winrate: float = 0.5
    team_avg_rank_score: float = 0.5
    enemy_avg_rank_score: float = 0.5
    composition_score: float = 0.5
    enemy_composition_score: float = 0.5
    historical_matchup_wr: float = 0.5
    synergy_score: float = 0.0
    counter_score: float = 0.0
    gold_diff_normalized: float = 0.0
    kill_diff: int = 0
    tower_diff: int = 0
    dragon_diff: int = 0
    baron_diff: int = 0
    cs_diff_normalized: float = 0.0
    vision_diff: float = 0.0
    game_time_normalized: float = 0.0
    momentum_score: float = 0.0
    tilt_factor: float = 0.0
    team_scaling: float = 0.5
    enemy_scaling: float = 0.5
    first_blood: float = 0.0
    first_tower: float = 0.0
    soul_point: float = 0.0

    def to_array(self) -> List[float]:
        return [
            self.team_avg_winrate, self.enemy_avg_winrate,
            self.team_avg_rank_score, self.enemy_avg_rank_score,
            self.composition_score, self.enemy_composition_score,
            self.historical_matchup_wr, self.synergy_score, self.counter_score,
            self.gold_diff_normalized, float(self.kill_diff), float(self.tower_diff),
            float(self.dragon_diff), float(self.baron_diff), self.cs_diff_normalized,
            self.vision_diff, self.game_time_normalized, self.momentum_score,
            self.tilt_factor, self.team_scaling, self.enemy_scaling,
            self.first_blood, self.first_tower, self.soul_point,
        ]

    @property
    def feature_count(self) -> int:
        return len(self.to_array())

    @property
    def non_zero_count(self) -> int:
        return sum(1 for f in self.to_array() if f != 0.0)

@dataclass
class Prediction:
    win_probability: float
    confidence: float
    phase: PredictionPhase
    model_type: ModelType
    feature_importance: Dict[str, float] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    explanation: str = ""
    calibrated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        top_factors = dict(sorted(
            self.feature_importance.items(), key=lambda x: abs(x[1]), reverse=True
        )[:5])
        return {
            "win_probability": round(self.win_probability, 4),
            "confidence": round(self.confidence, 3),
            "phase": self.phase.value,
            "model": self.model_type.value,
            "top_factors": top_factors,
            "explanation": self.explanation,
            "calibrated": self.calibrated,
        }

@dataclass
class PredictionHistory:
    predictions: List[Prediction] = field(default_factory=list)

    def trend(self) -> List[float]:
        return [p.win_probability for p in self.predictions]

    def momentum(self) -> float:
        if len(self.predictions) < 2:
            return 0.0
        recent = self.predictions[-3:]
        return recent[-1].win_probability - recent[0].win_probability

    def avg_confidence(self) -> float:
        if not self.predictions:
            return 0.0
        return statistics.mean(p.confidence for p in self.predictions)

    def add(self, pred: Prediction) -> None:
        self.predictions.append(pred)
        if len(self.predictions) > PREDICTION_HISTORY_MAX:
            self.predictions = self.predictions[-PREDICTION_HISTORY_MAX:]

@dataclass
class ModelMetrics:
    """Tracks model performance metrics."""
    total_predictions: int = 0
    correct_predictions: int = 0
    brier_score_sum: float = 0.0
    log_loss_sum: float = 0.0

    @property
    def accuracy(self) -> float:
        return self.correct_predictions / max(self.total_predictions, 1)

    @property
    def avg_brier_score(self) -> float:
        return self.brier_score_sum / max(self.total_predictions, 1)

    def record_outcome(self, predicted_prob: float, actual_win: bool) -> None:
        self.total_predictions += 1
        actual = 1.0 if actual_win else 0.0
        if (predicted_prob > 0.5) == actual_win:
            self.correct_predictions += 1
        self.brier_score_sum += (predicted_prob - actual) ** 2
        eps = 1e-10
        clamped = max(eps, min(1 - eps, predicted_prob))
        self.log_loss_sum -= (actual * math.log(clamped) + (1 - actual) * math.log(1 - clamped))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "predictions": self.total_predictions,
            "accuracy": round(self.accuracy, 4),
            "brier_score": round(self.avg_brier_score, 4),
        }


# ─── Logistic Model ────────────────────────────────────────────────────────

class SimpleLogisticModel:
    """Lightweight logistic regression for baseline predictions."""

    def __init__(self):
        self._weights = [0.0] * FEATURE_COUNT
        self._bias = 0.0
        self._trained = False
        self._training_samples = 0

    def _sigmoid(self, x: float) -> float:
        x = max(-500, min(500, x))
        return 1.0 / (1.0 + math.exp(-x))

    def predict(self, features: List[float]) -> float:
        if not self._trained:
            return self._heuristic_predict(features)
        z = self._bias + sum(w * f for w, f in zip(self._weights, features))
        return self._sigmoid(z)

    def _heuristic_predict(self, features: List[float]) -> float:
        """Heuristic prediction when model isn't trained."""
        base = 0.5
        if len(features) >= 10:
            wr_diff = features[0] - features[1]
            base += wr_diff * 0.3
            gold_diff = features[9]
            base += gold_diff * 0.15
            comp_diff = features[4] - features[5]
            base += comp_diff * 0.1
        return max(0.05, min(0.95, base))

    def train(self, X: List[List[float]], y: List[float], epochs: int = 100, lr: float = 0.01) -> Dict[str, Any]:
        """Train with gradient descent."""
        n = len(X)
        if n < MIN_TRAINING_SAMPLES:
            return {"trained": False, "reason": f"Need {MIN_TRAINING_SAMPLES} samples, got {n}"}
        losses = []
        for epoch in range(epochs):
            epoch_loss = 0.0
            for features, target in zip(X, y):
                pred = self.predict(features) if self._trained else self._heuristic_predict(features)
                error = pred - target
                for j in range(min(len(self._weights), len(features))):
                    self._weights[j] -= lr * error * features[j]
                self._bias -= lr * error
                epoch_loss += error ** 2
            losses.append(epoch_loss / n)
            self._trained = True
        self._training_samples = n
        return {"trained": True, "samples": n, "final_loss": round(losses[-1], 6) if losses else 0}

    @property
    def is_trained(self) -> bool:
        return self._trained


# ─── Prediction Model Integration ──────────────────────────────────────────

class PredictionModelIntegration:
    """
    Integrates prediction models for win probability estimation.
    Combines pre-game analysis with in-game state for real-time predictions.
    """

    FEATURE_NAMES = [
        "team_wr", "enemy_wr", "team_rank", "enemy_rank",
        "comp_score", "enemy_comp", "matchup_wr", "synergy", "counter",
        "gold_diff", "kill_diff", "tower_diff", "dragon_diff", "baron_diff",
        "cs_diff", "vision_diff", "game_time", "momentum",
        "tilt", "team_scale", "enemy_scale", "first_blood", "first_tower", "soul",
    ]

    def __init__(self):
        self._model = SimpleLogisticModel()
        self._history = PredictionHistory()
        self._metrics = ModelMetrics()

    def predict(self, features: FeatureVector, phase: PredictionPhase = PredictionPhase.PRE_GAME) -> Prediction:
        """Generate a win probability prediction."""
        feature_array = features.to_array()
        prob = self._model.predict(feature_array)

        importance = {}
        base_pred = self._model.predict([0.0] * len(feature_array))
        for i, name in enumerate(self.FEATURE_NAMES[:len(feature_array)]):
            modified = feature_array.copy()
            modified[i] = 0.0
            diff = abs(prob - self._model.predict(modified))
            importance[name] = round(diff, 4)

        confidence = features.non_zero_count / features.feature_count

        if prob > 0.6:
            explanation = "Favorable conditions - historical data and current state favor your team"
        elif prob < 0.4:
            explanation = "Challenging conditions - consider playing safe and scaling"
        else:
            explanation = "Even match - execution and objectives will decide the outcome"

        prediction = Prediction(
            win_probability=prob, confidence=confidence, phase=phase,
            model_type=ModelType.LOGISTIC_REGRESSION if self._model.is_trained else ModelType.HEURISTIC,
            feature_importance=importance, explanation=explanation,
        )
        self._history.add(prediction)
        return prediction

    def record_outcome(self, actual_win: bool) -> None:
        """Record actual match outcome for model evaluation."""
        if self._history.predictions:
            last = self._history.predictions[-1]
            self._metrics.record_outcome(last.win_probability, actual_win)

    def train_from_history(self, match_records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Train the model from historical match data."""
        X, y = [], []
        for record in match_records:
            fv = FeatureVector(
                team_avg_winrate=record.get("team_wr", 0.5),
                enemy_avg_winrate=record.get("enemy_wr", 0.5),
                gold_diff_normalized=record.get("gold_diff", 0) / 15000,
                composition_score=record.get("comp_score", 0.5),
            )
            X.append(fv.to_array())
            y.append(1.0 if record.get("win", False) else 0.0)
        if X:
            return self._model.train(X, y)
        return {"trained": False, "reason": "No data"}

    def get_prediction_trend(self) -> List[Dict[str, Any]]:
        return [{"time": p.timestamp, "prob": p.win_probability} for p in self._history.predictions[-20:]]

    def get_model_metrics(self) -> Dict[str, Any]:
        return self._metrics.to_dict()




class FeatureNormalizer:
    """Normalizes feature values based on observed distributions."""

    def __init__(self):
        self._means: Dict[str, float] = {}
        self._stds: Dict[str, float] = {}
        self._fitted = False

    def fit(self, feature_names: List[str], values_by_feature: Dict[str, List[float]]) -> None:
        for name in feature_names:
            vals = values_by_feature.get(name, [])
            if vals:
                self._means[name] = statistics.mean(vals)
                self._stds[name] = statistics.stdev(vals) if len(vals) > 1 else 1.0
            else:
                self._means[name] = 0.0
                self._stds[name] = 1.0
        self._fitted = True

    def normalize(self, feature_name: str, value: float) -> float:
        if not self._fitted or feature_name not in self._means:
            return value
        std = self._stds.get(feature_name, 1.0)
        if std == 0:
            return 0.0
        return (value - self._means[feature_name]) / std

    def denormalize(self, feature_name: str, normalized_value: float) -> float:
        if not self._fitted or feature_name not in self._means:
            return normalized_value
        return normalized_value * self._stds.get(feature_name, 1.0) + self._means.get(feature_name, 0.0)


class PredictionExplainer:
    """Generates human-readable explanations for predictions."""

    FEATURE_LABELS = {
        "team_wr": "Team average winrate",
        "enemy_wr": "Enemy average winrate",
        "gold_diff": "Gold difference",
        "comp_score": "Team composition quality",
        "synergy": "Champion synergy",
        "counter": "Counter-pick advantage",
        "momentum": "Recent momentum",
        "tilt": "Tilt factor",
        "dragon_diff": "Dragon advantage",
        "baron_diff": "Baron advantage",
    }

    def explain(self, prediction: Prediction) -> List[str]:
        explanations = []
        sorted_factors = sorted(
            prediction.feature_importance.items(),
            key=lambda x: abs(x[1]), reverse=True
        )
        for name, impact in sorted_factors[:5]:
            if abs(impact) < 0.01:
                continue
            label = self.FEATURE_LABELS.get(name, name)
            direction = "positively" if impact > 0 else "negatively"
            explanations.append(f"{label} {direction} affects win probability by {abs(impact):.1%}")
        return explanations

    def generate_narrative(self, prediction: Prediction, phase: PredictionPhase) -> str:
        prob = prediction.win_probability
        if phase == PredictionPhase.PRE_GAME:
            if prob > 0.6:
                return "Pre-game analysis favors your team. Capitalize on draft advantages."
            elif prob < 0.4:
                return "Pre-game analysis suggests an uphill battle. Focus on scaling and objective control."
            return "Draft appears even. Execution will be the deciding factor."
        elif phase == PredictionPhase.EARLY_GAME:
            if prob > 0.6:
                return "Strong early lead. Transition advantages into objectives."
            elif prob < 0.4:
                return "Behind in the early game. Play safe and look for opportunities."
            return "Even early game. Continue farming and vision control."
        elif phase == PredictionPhase.MID_GAME:
            if prob > 0.65:
                return "Commanding mid-game lead. Force objectives and extend the advantage."
            elif prob < 0.35:
                return "Significant mid-game deficit. Seek favorable fights or split pressure."
            return "Competitive mid-game. Dragon and Baron control will be crucial."
        else:
            if prob > 0.7:
                return "Late game dominance. Close out the game decisively."
            elif prob < 0.3:
                return "Dire late game position. Look for miracle teamfights or base defense."
            return "Late game is anyone's to take. One teamfight can decide it all."


class EnsemblePredictor:
    """Combines multiple models for improved prediction accuracy."""

    def __init__(self):
        self._models: Dict[str, Callable] = {}
        self._weights: Dict[str, float] = {}

    def add_model(self, name: str, predict_fn: Callable, weight: float = 1.0) -> None:
        self._models[name] = predict_fn
        self._weights[name] = weight

    def predict(self, features: List[float]) -> Tuple[float, Dict[str, float]]:
        predictions = {}
        total_weight = 0.0
        weighted_sum = 0.0
        for name, predict_fn in self._models.items():
            try:
                pred = predict_fn(features)
                predictions[name] = pred
                weight = self._weights.get(name, 1.0)
                weighted_sum += pred * weight
                total_weight += weight
            except Exception:
                continue
        ensemble_pred = weighted_sum / total_weight if total_weight > 0 else DEFAULT_WIN_PROB
        return ensemble_pred, predictions

    def get_model_agreement(self, features: List[float]) -> float:
        _, predictions = self.predict(features)
        if len(predictions) < 2:
            return 1.0
        values = list(predictions.values())
        return 1.0 - statistics.stdev(values) if len(values) > 1 else 1.0


# ─── Module Self-Test ─────────────────────────────────────────────────────

def _self_test() -> Dict[str, Any]:
    results = {"module": "M823_prediction_model_integration", "tests": []}

    try:
        pmi = PredictionModelIntegration()
        fv = FeatureVector(team_avg_winrate=0.55, enemy_avg_winrate=0.48, gold_diff_normalized=0.2)
        pred = pmi.predict(fv, PredictionPhase.MID_GAME)
        assert 0.0 < pred.win_probability < 1.0
        assert pred.confidence > 0
        results["tests"].append({"name": "basic_prediction", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "basic_prediction", "status": "fail", "error": str(e)})

    try:
        model = SimpleLogisticModel()
        X = [[0.6, 0.4] + [0]*22 for _ in range(30)] + [[0.4, 0.6] + [0]*22 for _ in range(30)]
        y = [1.0]*30 + [0.0]*30
        result = model.train(X, y, epochs=50)
        assert result["trained"]
        results["tests"].append({"name": "model_training", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "model_training", "status": "fail", "error": str(e)})

    try:
        fv = FeatureVector()
        arr = fv.to_array()
        assert len(arr) == FEATURE_COUNT
        assert fv.feature_count == FEATURE_COUNT
        results["tests"].append({"name": "feature_vector", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "feature_vector", "status": "fail", "error": str(e)})

    try:
        metrics = ModelMetrics()
        metrics.record_outcome(0.7, True)
        metrics.record_outcome(0.3, False)
        metrics.record_outcome(0.8, False)
        assert metrics.accuracy > 0
        assert metrics.avg_brier_score > 0
        results["tests"].append({"name": "model_metrics", "status": "pass"})
    except Exception as e:
        results["tests"].append({"name": "model_metrics", "status": "fail", "error": str(e)})

    results["passed"] = sum(1 for t in results["tests"] if t["status"] == "pass")
    results["total"] = len(results["tests"])
    return results


if __name__ == "__main__":
    print(json.dumps(_self_test(), indent=2))

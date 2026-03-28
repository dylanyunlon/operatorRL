"""
Prediction Engine - Win probability and strategic action prediction.

Supports pluggable model backends:
  - Built-in logistic model (no dependencies)
  - Scikit-learn model loading
  - AutoGluon model loading (from lol-optimizer)
  - ONNX runtime inference
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lol_fiddler_agent.ml.feature_extractor import (
    FEATURE_NAMES,
    compute_win_probability,
    extract_features,
    normalize_features,
)
from lol_fiddler_agent.models.game_snapshot import GameSnapshot

logger = logging.getLogger(__name__)


@dataclass
class PredictionResult:
    """Result from a prediction model."""
    win_probability: float
    confidence: float
    model_name: str
    features_used: int
    inference_time_ms: float
    explanation: dict[str, float] = field(default_factory=dict)

    @property
    def outcome_label(self) -> str:
        if self.win_probability >= 0.65:
            return "likely_win"
        elif self.win_probability <= 0.35:
            return "likely_loss"
        return "uncertain"


class PredictionModel(ABC):
    """Abstract base class for prediction models."""

    @abstractmethod
    def predict(self, features: dict[str, float]) -> PredictionResult:
        pass

    @abstractmethod
    def is_ready(self) -> bool:
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        pass


class BuiltinLogisticModel(PredictionModel):
    """Built-in logistic regression model. No external dependencies."""

    def __init__(self) -> None:
        self._ready = True

    def predict(self, features: dict[str, float]) -> PredictionResult:
        start = time.monotonic()
        win_prob = compute_win_probability(features)
        elapsed = (time.monotonic() - start) * 1000

        # Feature importance (absolute contribution)
        importance: dict[str, float] = {}
        weights = {
            "gold_diff_per_min": 0.002, "kill_diff": 0.05,
            "dragon_diff": 0.15, "has_baron": 0.3,
        }
        for feat, weight in weights.items():
            val = features.get(feat, 0.0)
            importance[feat] = abs(val * weight)

        return PredictionResult(
            win_probability=win_prob,
            confidence=0.6,  # Fixed confidence for builtin model
            model_name=self.model_name,
            features_used=len(features),
            inference_time_ms=elapsed,
            explanation=importance,
        )

    def is_ready(self) -> bool:
        return self._ready

    @property
    def model_name(self) -> str:
        return "builtin_logistic_v1"


class ExternalModelLoader(PredictionModel):
    """Loads and runs a serialized model (sklearn, ONNX, etc.)."""

    def __init__(self, model_path: str, model_type: str = "sklearn") -> None:
        self._model_path = model_path
        self._model_type = model_type
        self._model: Any = None
        self._ready = False
        self._feature_names: list[str] = list(FEATURE_NAMES)

    def load(self) -> bool:
        """Load model from disk."""
        if not os.path.exists(self._model_path):
            logger.error("Model file not found: %s", self._model_path)
            return False

        try:
            if self._model_type == "sklearn":
                import pickle
                with open(self._model_path, "rb") as f:
                    self._model = pickle.load(f)
                self._ready = True
            elif self._model_type == "onnx":
                import onnxruntime as ort
                self._model = ort.InferenceSession(self._model_path)
                self._ready = True
            elif self._model_type == "autogluon":
                from autogluon.tabular import TabularPredictor
                self._model = TabularPredictor.load(self._model_path)
                self._ready = True
            else:
                logger.error("Unknown model type: %s", self._model_type)
                return False

            logger.info("Loaded %s model from %s", self._model_type, self._model_path)
            return True
        except ImportError as e:
            logger.warning("Missing dependency for %s: %s", self._model_type, e)
            return False
        except Exception as e:
            logger.error("Failed to load model: %s", e)
            return False

    def predict(self, features: dict[str, float]) -> PredictionResult:
        if not self._ready or self._model is None:
            return PredictionResult(
                win_probability=0.5, confidence=0.0,
                model_name=self.model_name, features_used=0,
                inference_time_ms=0.0,
            )

        start = time.monotonic()
        vector = [features.get(n, 0.0) for n in self._feature_names]

        try:
            if self._model_type == "sklearn":
                import numpy as np
                X = np.array([vector])
                proba = self._model.predict_proba(X)
                win_prob = float(proba[0][1]) if proba.shape[1] > 1 else float(proba[0][0])
            elif self._model_type == "autogluon":
                import pandas as pd
                df = pd.DataFrame([dict(zip(self._feature_names, vector))])
                pred = self._model.predict_proba(df)
                win_prob = float(pred.iloc[0, 1]) if pred.shape[1] > 1 else float(pred.iloc[0, 0])
            else:
                win_prob = 0.5
        except Exception as e:
            logger.warning("Prediction failed: %s", e)
            win_prob = 0.5

        elapsed = (time.monotonic() - start) * 1000

        return PredictionResult(
            win_probability=max(0.01, min(0.99, win_prob)),
            confidence=0.8,
            model_name=self.model_name,
            features_used=len(vector),
            inference_time_ms=elapsed,
        )

    def is_ready(self) -> bool:
        return self._ready

    @property
    def model_name(self) -> str:
        return f"external_{self._model_type}"


class EnsemblePredictionEngine:
    """Combines multiple prediction models with weighted averaging.

    Falls back to builtin model when external models are unavailable.

    Example::

        engine = EnsemblePredictionEngine()
        engine.add_model(BuiltinLogisticModel(), weight=0.3)
        engine.add_model(loaded_sklearn_model, weight=0.7)
        result = engine.predict(snapshot)
    """

    def __init__(self) -> None:
        self._models: list[tuple[PredictionModel, float]] = []
        self._fallback = BuiltinLogisticModel()
        self._prediction_count = 0

    def add_model(self, model: PredictionModel, weight: float = 1.0) -> None:
        self._models.append((model, weight))

    def predict(self, snapshot: GameSnapshot) -> PredictionResult:
        """Run prediction using all available models."""
        features = extract_features(snapshot)
        self._prediction_count += 1

        if not self._models:
            return self._fallback.predict(features)

        results: list[tuple[PredictionResult, float]] = []
        total_weight = 0.0

        for model, weight in self._models:
            if model.is_ready():
                result = model.predict(features)
                results.append((result, weight))
                total_weight += weight

        if not results:
            return self._fallback.predict(features)

        # Weighted average
        weighted_prob = sum(r.win_probability * w for r, w in results) / total_weight
        avg_confidence = sum(r.confidence * w for r, w in results) / total_weight
        total_inference = sum(r.inference_time_ms for r, _ in results)

        # Merge explanations
        merged_explanation: dict[str, float] = {}
        for r, w in results:
            for k, v in r.explanation.items():
                merged_explanation[k] = merged_explanation.get(k, 0) + v * (w / total_weight)

        return PredictionResult(
            win_probability=weighted_prob,
            confidence=avg_confidence,
            model_name=f"ensemble({len(results)} models)",
            features_used=len(features),
            inference_time_ms=total_inference,
            explanation=merged_explanation,
        )

    @property
    def model_count(self) -> int:
        return len(self._models)

    @property
    def prediction_count(self) -> int:
        return self._prediction_count

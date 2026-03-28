"""
Prediction Engine V2 — ML prediction with AgentLightning self-evolution.

Wraps the existing prediction_engine.py models with evolution hooks:
- Training data collection during inference
- Evolution callback for model updates
- Model hot-swap support

Location: integrations/lol-fiddler-agent/src/lol_fiddler_agent/ml/prediction.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from lol_fiddler_agent.ml.prediction_engine import (
    BuiltinLogisticModel,
    PredictionModel,
    PredictionResult,
)

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_fiddler_agent.ml.prediction.v1"


class PredictionEngineV2:
    """Prediction engine with evolution integration.

    Wraps existing prediction models with:
    - Evolution callback hooks
    - Training data buffer
    - Model switching
    - Performance tracking
    """

    def __init__(self) -> None:
        self._models: dict[str, PredictionModel] = {
            "builtin": BuiltinLogisticModel(),
        }
        self._active_model_name: str = "builtin"
        self.evolution_callback: Optional[Callable] = None
        self._training_buffer: list[dict[str, Any]] = []
        self._collect_training: bool = False
        self._predictions_count: int = 0

    @property
    def active_model_name(self) -> str:
        return self._active_model_name

    @property
    def training_buffer(self) -> list[dict[str, Any]]:
        return self._training_buffer

    def set_model(self, name: str) -> None:
        """Switch active model."""
        if name in self._models:
            self._active_model_name = name

    def enable_training_collection(self, enabled: bool) -> None:
        """Enable/disable training data collection."""
        self._collect_training = enabled

    def predict(self, features: dict[str, float]) -> PredictionResult:
        """Run prediction with evolution hooks.

        Args:
            features: Feature dict (f1_deaths_per_min, etc.).

        Returns:
            PredictionResult from the active model.
        """
        self._predictions_count += 1
        model = self._models.get(self._active_model_name)

        if model is None or not model.is_ready():
            return PredictionResult(
                win_probability=0.5,
                confidence=0.0,
                model_name="fallback",
                features_used=0,
                inference_time_ms=0.0,
            )

        start = time.time()
        result = model.predict(features)
        elapsed_ms = (time.time() - start) * 1000

        # Collect training data
        if self._collect_training:
            self._training_buffer.append({
                "features": dict(features),
                "prediction": result.win_probability,
                "model": self._active_model_name,
                "timestamp": time.time(),
            })

        # Evolution callback
        if self.evolution_callback is not None:
            self.evolution_callback({
                "type": "prediction",
                "features": dict(features),
                "win_probability": result.win_probability,
                "model": self._active_model_name,
                "inference_ms": elapsed_ms,
            })

        return result

    def get_stats(self) -> dict[str, Any]:
        """Get engine statistics."""
        return {
            "predictions_count": self._predictions_count,
            "active_model": self._active_model_name,
            "training_buffer_size": len(self._training_buffer),
            "models_available": list(self._models.keys()),
        }

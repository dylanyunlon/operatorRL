"""
Model Manager — Hot-swap model management for self-evolution.

Manages versioned prediction models with:
- Model registry and lifecycle
- Hot-swap between models during live games
- Rollback on performance regression
- Performance tracking for A/B testing

Location: integrations/lol-fiddler-agent/src/lol_fiddler_agent/ml/model_manager.py
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "lol_fiddler_agent.ml.model_manager.v1"


@dataclass
class ModelInfo:
    """Metadata for a registered model."""
    model_id: str
    version: str
    model_type: str
    registered_at: float = field(default_factory=time.time)
    is_active: bool = False
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class SwapRecord:
    """Record of a model swap event."""
    from_model: str
    to_model: str
    timestamp: float = field(default_factory=time.time)
    reason: str = ""


class ModelManager:
    """Model registry with hot-swap and rollback support.

    Usage:
        mgr = ModelManager()
        mgr.register("v1", version="1.0", model_type="builtin")
        mgr.register("v2", version="2.0", model_type="onnx")
        mgr.set_active("v1")
        mgr.hot_swap("v2")  # Switch to v2 during live game
        mgr.rollback()       # Revert to v1 if v2 underperforms
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}
        self._active_id: Optional[str] = None
        self._previous_id: Optional[str] = None
        self._swap_history: list[SwapRecord] = []
        self._performance: dict[str, list[dict[str, float]]] = {}

    @property
    def active_model_id(self) -> Optional[str]:
        return self._active_id

    @property
    def swap_history(self) -> list[SwapRecord]:
        return list(self._swap_history)

    def register(self, model_id: str, version: str = "1.0", model_type: str = "builtin") -> None:
        """Register a new model."""
        self._models[model_id] = ModelInfo(
            model_id=model_id,
            version=version,
            model_type=model_type,
        )

    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """Get model metadata."""
        return self._models.get(model_id)

    def list_models(self) -> list[ModelInfo]:
        """List all registered models."""
        return list(self._models.values())

    def set_active(self, model_id: str) -> bool:
        """Set the active model."""
        if model_id not in self._models:
            return False
        if self._active_id:
            self._models[self._active_id].is_active = False
        self._active_id = model_id
        self._models[model_id].is_active = True
        return True

    def hot_swap(self, new_model_id: str, reason: str = "evolution") -> bool:
        """Hot-swap to a new model, preserving rollback capability."""
        if new_model_id not in self._models:
            return False

        self._previous_id = self._active_id
        record = SwapRecord(
            from_model=self._active_id or "",
            to_model=new_model_id,
            reason=reason,
        )
        self._swap_history.append(record)

        if self._active_id and self._active_id in self._models:
            self._models[self._active_id].is_active = False
        self._active_id = new_model_id
        self._models[new_model_id].is_active = True

        logger.info("Hot-swap: %s → %s (reason: %s)",
                     record.from_model, new_model_id, reason)
        return True

    def rollback(self) -> bool:
        """Rollback to the previous model."""
        if self._previous_id is None:
            return False
        return self.hot_swap(self._previous_id, reason="rollback")

    def record_performance(
        self,
        model_id: str,
        predicted: float,
        actual: float,
        latency_ms: float,
    ) -> None:
        """Record a prediction outcome for performance tracking."""
        if model_id not in self._performance:
            self._performance[model_id] = []
        self._performance[model_id].append({
            "predicted": predicted,
            "actual": actual,
            "latency_ms": latency_ms,
            "timestamp": time.time(),
        })

    def get_performance(self, model_id: str) -> Optional[dict[str, Any]]:
        """Get aggregated performance metrics for a model."""
        records = self._performance.get(model_id)
        if not records:
            return None

        n = len(records)
        avg_latency = sum(r["latency_ms"] for r in records) / n
        errors = [abs(r["predicted"] - r["actual"]) for r in records]
        mae = sum(errors) / n

        return {
            "model_id": model_id,
            "sample_count": n,
            "avg_latency_ms": avg_latency,
            "mean_absolute_error": mae,
        }

    def load_model(self, model_id: str) -> bool:
        """Load a model (placeholder for actual model loading)."""
        return model_id in self._models

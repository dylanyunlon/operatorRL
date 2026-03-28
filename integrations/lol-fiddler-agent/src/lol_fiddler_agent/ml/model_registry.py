"""
Model Registry - Manages versioned prediction models.

Provides model versioning, A/B testing support, and automatic
fallback to baseline models when newer models underperform.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lol_fiddler_agent.ml.prediction_engine import PredictionModel, PredictionResult

logger = logging.getLogger(__name__)


@dataclass
class ModelVersion:
    """Versioned model metadata."""
    model_id: str
    version: str
    model_type: str
    path: str
    created_at: float = field(default_factory=time.time)
    accuracy: float = 0.0
    sample_count: int = 0
    is_active: bool = False
    is_baseline: bool = False
    tags: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def qualified_name(self) -> str:
        return f"{self.model_id}:{self.version}"


@dataclass
class ABTestConfig:
    """Configuration for A/B testing between models."""
    model_a_id: str
    model_b_id: str
    traffic_split: float = 0.5  # Fraction going to model B
    min_samples: int = 100
    significance_threshold: float = 0.05


class ModelPerformanceTracker:
    """Tracks online performance of deployed models."""

    def __init__(self) -> None:
        self._predictions: dict[str, list[tuple[float, float]]] = {}  # model_id -> [(predicted, actual)]
        self._latencies: dict[str, list[float]] = {}

    def record(self, model_id: str, predicted: float, actual: Optional[float], latency_ms: float) -> None:
        if model_id not in self._predictions:
            self._predictions[model_id] = []
            self._latencies[model_id] = []

        if actual is not None:
            self._predictions[model_id].append((predicted, actual))
        self._latencies[model_id].append(latency_ms)

    def get_accuracy(self, model_id: str) -> float:
        """Compute binary accuracy (correct outcome prediction)."""
        preds = self._predictions.get(model_id, [])
        if not preds:
            return 0.0
        correct = sum(1 for p, a in preds if (p >= 0.5) == (a >= 0.5))
        return correct / len(preds)

    def get_avg_latency(self, model_id: str) -> float:
        lats = self._latencies.get(model_id, [])
        if not lats:
            return 0.0
        return sum(lats) / len(lats)

    def get_sample_count(self, model_id: str) -> int:
        return len(self._predictions.get(model_id, []))

    def get_summary(self, model_id: str) -> dict[str, Any]:
        return {
            "accuracy": self.get_accuracy(model_id),
            "avg_latency_ms": self.get_avg_latency(model_id),
            "samples": self.get_sample_count(model_id),
        }

    def compare(self, model_a: str, model_b: str) -> dict[str, Any]:
        return {
            "model_a": {"id": model_a, **self.get_summary(model_a)},
            "model_b": {"id": model_b, **self.get_summary(model_b)},
            "winner": model_a if self.get_accuracy(model_a) > self.get_accuracy(model_b) else model_b,
        }


class ModelRegistry:
    """Registry for versioned prediction models.

    Example::

        registry = ModelRegistry("/models")
        registry.register(ModelVersion(
            model_id="win_predictor",
            version="1.0.0",
            model_type="sklearn",
            path="/models/win_v1.pkl",
        ))
        active = registry.get_active("win_predictor")
    """

    def __init__(self, registry_dir: str = "./models") -> None:
        self._registry_dir = Path(registry_dir)
        self._versions: dict[str, list[ModelVersion]] = {}
        self._active: dict[str, str] = {}  # model_id -> version
        self._tracker = ModelPerformanceTracker()

    def register(self, version: ModelVersion) -> None:
        if version.model_id not in self._versions:
            self._versions[version.model_id] = []
        self._versions[version.model_id].append(version)
        logger.info("Registered model %s", version.qualified_name)

    def activate(self, model_id: str, version: str) -> bool:
        versions = self._versions.get(model_id, [])
        for v in versions:
            if v.version == version:
                v.is_active = True
                self._active[model_id] = version
                # Deactivate others
                for other in versions:
                    if other.version != version:
                        other.is_active = False
                logger.info("Activated %s:%s", model_id, version)
                return True
        return False

    def get_active(self, model_id: str) -> Optional[ModelVersion]:
        active_ver = self._active.get(model_id)
        if not active_ver:
            return None
        versions = self._versions.get(model_id, [])
        for v in versions:
            if v.version == active_ver:
                return v
        return None

    def get_all_versions(self, model_id: str) -> list[ModelVersion]:
        return list(self._versions.get(model_id, []))

    def get_latest(self, model_id: str) -> Optional[ModelVersion]:
        versions = self._versions.get(model_id, [])
        if not versions:
            return None
        return max(versions, key=lambda v: v.created_at)

    @property
    def tracker(self) -> ModelPerformanceTracker:
        return self._tracker

    @property
    def model_ids(self) -> list[str]:
        return list(self._versions.keys())

    def save_registry(self) -> None:
        """Persist registry to disk."""
        self._registry_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "versions": {
                mid: [
                    {
                        "model_id": v.model_id, "version": v.version,
                        "model_type": v.model_type, "path": v.path,
                        "accuracy": v.accuracy, "is_active": v.is_active,
                        "is_baseline": v.is_baseline, "tags": v.tags,
                    }
                    for v in versions
                ]
                for mid, versions in self._versions.items()
            },
            "active": self._active,
        }
        path = self._registry_dir / "registry.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def load_registry(self) -> bool:
        """Load registry from disk."""
        path = self._registry_dir / "registry.json"
        if not path.exists():
            return False
        try:
            with open(path) as f:
                data = json.load(f)
            for mid, versions in data.get("versions", {}).items():
                for vd in versions:
                    self.register(ModelVersion(**vd))
            self._active = data.get("active", {})
            return True
        except Exception as e:
            logger.error("Failed to load registry: %s", e)
            return False

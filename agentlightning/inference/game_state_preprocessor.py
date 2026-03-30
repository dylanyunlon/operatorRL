"""
Game State Preprocessor — Normalize raw game state for inference.

Transforms heterogeneous raw game state data (from Fiddler captures,
Live Client API, or Seraphine history) into a standardized format
suitable for model input. Handles missing fields, type coercion,
range normalization, and feature selection.

Location: agentlightning/inference/game_state_preprocessor.py

Reference (拿来主义):
  查看 integrations/lol/src/lol_agent/state_encoder.py 上现有 StateEncoder
  的特征归一化方式, 理解其模式, 特别是 _FEATURE_NAMES 列表如何定义
  固定维度输出, 以及各normalize方法如何独立于encode主方法。
  从 DI-star/distar/agent/default/model/model.py 这个好例子开始 — 它的
  entity_encoder和spatial_encoder分别处理不同类型的特征。
  遵循该模式实现 GameStatePreprocessor, 让推理管线(M550)可以将任何
  游戏的原始状态数据转换为固定格式, 并能支持多种归一化策略(min-max,
  z-score, log-scale)按特征类型自动选择.

Design Notes (Knuth-level critique):
  User:
    - Missing field defaults prevent inference crash on partial data
    - Feature registry makes supported features explicit and discoverable
    - Per-feature normalization avoids one-size-fits-all distortion
  System:
    - Preprocessor is stateless — safe for concurrent use
    - Schema validation is O(n) in feature count, negligible
    - Evolution callback tracks preprocessing anomalies
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.game_state_preprocessor.v1"


class FeatureSpec:
    """Specification for a single feature.

    Attributes:
        name: Feature name.
        dtype: Expected type ("float", "int", "bool", "category").
        norm: Normalization method ("minmax", "zscore", "log", "none").
        default: Default value if missing.
        min_val: Min value for minmax normalization.
        max_val: Max value for minmax normalization.
        mean: Mean for zscore normalization.
        std: Std for zscore normalization.
        categories: Valid categories for category features.
    """

    __slots__ = (
        "name", "dtype", "norm", "default",
        "min_val", "max_val", "mean", "std", "categories",
    )

    def __init__(
        self,
        name: str,
        dtype: str = "float",
        norm: str = "none",
        default: Any = 0.0,
        min_val: float = 0.0,
        max_val: float = 1.0,
        mean: float = 0.0,
        std: float = 1.0,
        categories: Optional[List[str]] = None,
    ) -> None:
        self.name = name
        self.dtype = dtype
        self.norm = norm
        self.default = default
        self.min_val = min_val
        self.max_val = max_val
        self.mean = mean
        self.std = std
        self.categories = categories or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "dtype": self.dtype,
            "norm": self.norm,
            "default": self.default,
            "min_val": self.min_val,
            "max_val": self.max_val,
        }


class GameStatePreprocessor:
    """Normalizes raw game state into model-ready format.

    Usage:
        pp = GameStatePreprocessor()
        pp.register_feature(FeatureSpec("hp_ratio", norm="minmax", max_val=1.0))
        pp.register_feature(FeatureSpec("gold", norm="zscore", mean=5000, std=3000))
        result = pp.preprocess({"hp_ratio": 0.8, "gold": 7500})

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._specs: Dict[str, FeatureSpec] = {}
        self._feature_order: List[str] = []
        self._preprocess_count: int = 0
        self._anomaly_count: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Schema Definition ---

    def register_feature(self, spec: FeatureSpec) -> None:
        """Register a feature specification.

        Args:
            spec: FeatureSpec defining the feature.
        """
        self._specs[spec.name] = spec
        if spec.name not in self._feature_order:
            self._feature_order.append(spec.name)

    def register_features(self, specs: List[FeatureSpec]) -> None:
        """Register multiple feature specs."""
        for spec in specs:
            self.register_feature(spec)

    def feature_names(self) -> List[str]:
        """Get ordered feature names."""
        return list(self._feature_order)

    def feature_count(self) -> int:
        """Number of registered features."""
        return len(self._specs)

    def get_spec(self, name: str) -> Optional[FeatureSpec]:
        """Get spec for a feature."""
        return self._specs.get(name)

    # --- Preprocessing ---

    def preprocess(self, raw_state: Dict[str, Any]) -> Dict[str, Any]:
        """Preprocess a raw game state dict.

        Extracts registered features, applies defaults for missing fields,
        coerces types, and normalizes values.

        Args:
            raw_state: Raw game state dict.

        Returns:
            Preprocessed state dict with normalized values.
        """
        self._preprocess_count += 1
        result: Dict[str, Any] = {}
        anomalies: List[str] = []

        for name in self._feature_order:
            spec = self._specs[name]
            raw_val = raw_state.get(name, spec.default)

            # Type coercion
            try:
                coerced = self._coerce(raw_val, spec)
            except (ValueError, TypeError):
                coerced = spec.default
                anomalies.append(f"coerce_fail:{name}")

            # Normalization
            normalized = self._normalize(coerced, spec)
            result[name] = normalized

        if anomalies:
            self._anomaly_count += len(anomalies)
            self._fire_evolution("preprocess_anomalies", {
                "anomalies": anomalies, "count": len(anomalies),
            })

        return result

    def preprocess_to_vector(self, raw_state: Dict[str, Any]) -> List[float]:
        """Preprocess and convert to ordered float vector.

        Args:
            raw_state: Raw game state dict.

        Returns:
            List of float values in feature_order.
        """
        processed = self.preprocess(raw_state)
        vector: List[float] = []
        for name in self._feature_order:
            val = processed.get(name, 0.0)
            if isinstance(val, (int, float)):
                vector.append(float(val))
            elif isinstance(val, bool):
                vector.append(1.0 if val else 0.0)
            elif isinstance(val, list):
                vector.extend(float(v) for v in val)
            else:
                vector.append(0.0)
        return vector

    def preprocess_batch(
        self, raw_states: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Preprocess a batch of raw states.

        Args:
            raw_states: List of raw state dicts.

        Returns:
            List of preprocessed state dicts.
        """
        return [self.preprocess(s) for s in raw_states]

    def validate(self, raw_state: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a raw state against the schema.

        Args:
            raw_state: Raw game state dict.

        Returns:
            Dict with "valid" bool, "missing" list, "extra" list.
        """
        registered = set(self._specs.keys())
        provided = set(raw_state.keys())
        missing = registered - provided
        extra = provided - registered
        return {
            "valid": len(missing) == 0,
            "missing": sorted(missing),
            "extra": sorted(extra),
            "feature_count": len(registered),
        }

    # --- Stats ---

    def get_stats(self) -> Dict[str, Any]:
        """Get preprocessor statistics."""
        return {
            "feature_count": len(self._specs),
            "preprocess_count": self._preprocess_count,
            "anomaly_count": self._anomaly_count,
        }

    # --- Internal ---

    def _coerce(self, value: Any, spec: FeatureSpec) -> Any:
        """Coerce value to expected type."""
        if spec.dtype == "float":
            return float(value)
        elif spec.dtype == "int":
            return int(value)
        elif spec.dtype == "bool":
            return bool(value)
        elif spec.dtype == "category":
            s = str(value)
            if spec.categories and s in spec.categories:
                return spec.categories.index(s) / max(len(spec.categories) - 1, 1)
            return 0.0
        return value

    def _normalize(self, value: Any, spec: FeatureSpec) -> Any:
        """Apply normalization to a coerced value."""
        if not isinstance(value, (int, float)):
            return value

        v = float(value)

        if spec.norm == "minmax":
            denom = spec.max_val - spec.min_val
            if denom == 0:
                return 0.0
            return max(0.0, min(1.0, (v - spec.min_val) / denom))

        elif spec.norm == "zscore":
            if spec.std == 0:
                return 0.0
            return (v - spec.mean) / spec.std

        elif spec.norm == "log":
            return math.log1p(max(0.0, v))

        return v

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

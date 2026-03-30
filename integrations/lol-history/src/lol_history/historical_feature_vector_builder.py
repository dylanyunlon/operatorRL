"""
HistoricalFeatureVectorBuilder — Build fixed-dimension feature vectors from historical stats.

Architecture (拿来主义):
  查看 **state_encoder.py** 的 _FEATURE_NAMES列表和特征归一化方式。
  从 **game_state_preprocessor.py（M553）** 的FeatureSpec注册模式开始。
  实现 **HistoricalFeatureVectorBuilder**，支持特征注册、多种归一化、批量构建。

Location: integrations/lol-history/src/lol_history/historical_feature_vector_builder.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.historical_feature_vector_builder.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class HistoricalFeatureVectorBuilder:
    """Build normalized feature vectors from raw historical stats.

    Public API
    ----------
    register_feature(name, normalize, default, min_val, max_val, mean, std)
    build_vector(raw) -> list[float]
    build_batch(raws) -> list[list[float]]
    get_feature_names() -> list[str]
    get_dimension() -> int
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._features: List[Dict[str, Any]] = []
        self._name_index: Dict[str, int] = {}

    def register_feature(
        self,
        name: str,
        normalize: str = "none",
        default: float = 0.0,
        min_val: float = 0.0,
        max_val: float = 1.0,
        mean: float = 0.0,
        std: float = 1.0,
    ) -> None:
        """Register a feature spec.

        Parameters
        ----------
        name : str
            Feature name (must be unique).
        normalize : str
            One of 'none', 'minmax', 'zscore'.
        default : float
            Default value when feature is missing.
        min_val, max_val : float
            Range for minmax normalization.
        mean, std : float
            Parameters for zscore normalization.
        """
        idx = len(self._features)
        self._features.append({
            "name": name,
            "normalize": normalize,
            "default": default,
            "min_val": min_val,
            "max_val": max_val,
            "mean": mean,
            "std": std,
        })
        self._name_index[name] = idx

    def get_feature_names(self) -> List[str]:
        return [f["name"] for f in self._features]

    def get_dimension(self) -> int:
        return len(self._features)

    def _normalize(self, value: float, spec: Dict[str, Any]) -> float:
        method = spec["normalize"]
        if method == "minmax":
            rng = spec["max_val"] - spec["min_val"]
            if rng == 0:
                return 0.0
            return (value - spec["min_val"]) / rng
        elif method == "zscore":
            if spec["std"] == 0:
                return 0.0
            return (value - spec["mean"]) / spec["std"]
        return value  # "none"

    def build_vector(self, raw: Dict[str, Any]) -> List[float]:
        """Build a feature vector from raw data dict."""
        vec: List[float] = []
        for spec in self._features:
            value = raw.get(spec["name"], spec["default"])
            vec.append(self._normalize(float(value), spec))
        return vec

    def build_batch(self, raws: List[Dict[str, Any]]) -> List[List[float]]:
        return [self.build_vector(r) for r in raws]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.get_dimension(),
            "features": self._features,
        }

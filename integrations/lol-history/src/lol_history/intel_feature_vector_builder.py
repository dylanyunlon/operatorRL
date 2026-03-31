"""
IntelFeatureVectorBuilder — Encodes intel data into fixed-dimension feature vectors.

Architecture (拿来主义):
  historical_feature_vector_builder.py（M602）— feature construction
  transfer_learning_feature_aligner.py（M675）— feature alignment

Location: integrations/lol-history/src/lol_history/intel_feature_vector_builder.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_feature_vector_builder.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

_FEATURE_GROUPS = ["opponent_profile", "matchup", "draft", "team_comp", "performance"]

class IntelFeatureVectorBuilder:
    """Encodes historical intel data into fixed-dimension feature vectors.

    Public API: register_encoder, build_vector, get_feature_schema,
                build_batch, get_stats
    """
    def __init__(self, vector_dim: int = 128) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._encoders: Dict[str, Callable] = {}
        self._vector_dim = vector_dim
        self._schema: Dict[str, List[str]] = {}
        self._build_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_encoder(self, group: str, encoder_fn: Callable,
                          feature_names: List[str] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._encoders[group] = encoder_fn
        self._schema[group] = feature_names or []
        return {"status": "ok", "group": group, "encoders": len(self._encoders)}

    def build_vector(self, intel_data: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        self._build_count += 1
        vector: List[float] = []
        group_offsets: Dict[str, int] = {}
        errors = []
        for group, encoder in self._encoders.items():
            group_offsets[group] = len(vector)
            try:
                features = encoder(intel_data)
                if isinstance(features, list):
                    vector.extend(features)
                elif isinstance(features, dict):
                    vector.extend(features.values())
                else:
                    vector.append(float(features))
            except Exception as e:
                errors.append(f"{group}: {e}")
        # Pad or truncate to target dimension
        if len(vector) < self._vector_dim:
            vector.extend([0.0] * (self._vector_dim - len(vector)))
        elif len(vector) > self._vector_dim:
            vector = vector[:self._vector_dim]
        self._fire("vector_built", {"dim": len(vector), "groups": len(group_offsets)})
        return {"status": "ok", "vector": vector, "dim": len(vector),
                "group_offsets": group_offsets, "errors": errors}

    def build_batch(self, intel_data_list: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._op_count += 1
        vectors = []
        for data in intel_data_list:
            result = self.build_vector(data)
            vectors.append(result["vector"])
        return {"status": "ok", "vectors": vectors, "count": len(vectors), "dim": self._vector_dim}

    def get_feature_schema(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "schema": dict(self._schema), "groups": len(self._schema),
                "target_dim": self._vector_dim}

    def get_stats(self) -> Dict[str, Any]:
        return {"encoders": len(self._encoders), "build_count": self._build_count,
                "vector_dim": self._vector_dim, "total_ops": self._op_count}

"""
TransferLearningFeatureAligner — Align feature spaces across games for transfer learning.

Maps feature dimensions from a source game to target game feature space,
computes feature similarity, and recommends transferable features.

Location: integrations/lol-history/src/lol_history/transfer_learning_feature_aligner.py

Reference (拿来主義):
  - extensions/protocol_decoder/src/protocol_feature_bridge.py（M656）: feature extraction
  - extensions/protocol_decoder/src/universal_game_state_schema.py（M670）: cross-game schema
  - DI-star: observation space normalization

Design Notes (Knuth-level critique):
  User:
    - register_features() per game — features are named and typed.
    - align() returns a mapping + similarity scores — consumer decides threshold.
    - recommend_transferable() filters by similarity threshold.
  System:
    - Similarity is cosine-based on feature metadata (type overlap, range overlap).
    - O(S*T) alignment where S,T are source/target feature counts — bounded by schema.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.transfer_learning_feature_aligner.v1"


class FeatureDesc:
    """Feature descriptor for alignment."""
    __slots__ = ("name", "dtype", "range_min", "range_max", "category", "description")

    def __init__(
        self, name: str, dtype: str = "float", range_min: float = 0.0,
        range_max: float = 1.0, category: str = "general", description: str = "",
    ) -> None:
        self.name = name
        self.dtype = dtype
        self.range_min = range_min
        self.range_max = range_max
        self.category = category
        self.description = description

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class TransferLearningFeatureAligner:
    """Cross-game feature aligner for transfer learning.

    Public API:
        register_features(game_type, features)
        align(source_game, target_game) -> list[dict]
        recommend_transferable(source, target, threshold) -> list[dict]
        compute_similarity(f1, f2) -> float
        get_stats() -> dict
    """

    def __init__(self) -> None:
        self._features: Dict[str, List[FeatureDesc]] = {}
        self._align_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def register_features(self, game_type: str, features: List[FeatureDesc]) -> None:
        self._features[game_type] = list(features)
        self._fire("features_registered", {"game_type": game_type, "count": len(features)})

    def compute_similarity(self, f1: FeatureDesc, f2: FeatureDesc) -> float:
        """Compute similarity between two features based on metadata."""
        score = 0.0
        # Same dtype
        if f1.dtype == f2.dtype:
            score += 0.3
        # Same category
        if f1.category == f2.category:
            score += 0.4
        # Range overlap
        overlap_min = max(f1.range_min, f2.range_min)
        overlap_max = min(f1.range_max, f2.range_max)
        total_range = max(f1.range_max, f2.range_max) - min(f1.range_min, f2.range_min)
        if total_range > 0 and overlap_max > overlap_min:
            score += 0.3 * ((overlap_max - overlap_min) / total_range)
        return min(score, 1.0)

    def align(self, source_game: str, target_game: str) -> List[Dict[str, Any]]:
        """Align features from source to target game.

        Returns list of {source, target, similarity} dicts sorted by similarity.
        """
        self._align_count += 1
        src = self._features.get(source_game, [])
        tgt = self._features.get(target_game, [])
        if not src or not tgt:
            return []

        alignments: List[Dict[str, Any]] = []
        used_targets: set = set()

        # Greedy best-match alignment
        pairs: List[Tuple[float, int, int]] = []
        for si, sf in enumerate(src):
            for ti, tf in enumerate(tgt):
                sim = self.compute_similarity(sf, tf)
                pairs.append((sim, si, ti))
        pairs.sort(key=lambda x: -x[0])

        used_src: set = set()
        for sim, si, ti in pairs:
            if si in used_src or ti in used_targets:
                continue
            used_src.add(si)
            used_targets.add(ti)
            alignments.append({
                "source_feature": src[si].name,
                "target_feature": tgt[ti].name,
                "similarity": round(sim, 4),
                "source_category": src[si].category,
                "target_category": tgt[ti].category,
            })

        alignments.sort(key=lambda x: -x["similarity"])
        self._fire("aligned", {
            "source": source_game, "target": target_game,
            "pairs": len(alignments),
        })
        return alignments

    def recommend_transferable(
        self, source_game: str, target_game: str, threshold: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """Recommend features above similarity threshold."""
        all_aligned = self.align(source_game, target_game)
        return [a for a in all_aligned if a["similarity"] >= threshold]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "registered_games": {g: len(f) for g, f in self._features.items()},
            "align_count": self._align_count,
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")

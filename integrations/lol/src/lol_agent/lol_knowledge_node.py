"""
LoL Knowledge Node — League of Legends strategy graph node.

Represents a single LoL strategy concept (teamfight, splitpush,
objective control, etc.) as a node in the cross-game Strategy Knowledge
Graph.  Stores champion contexts, matchup contexts, game-phase relevance,
and an embedding vector for graph neural network training.

Location: integrations/lol/src/lol_agent/lol_knowledge_node.py

Reference (拿来主義):
  - Seraphine/app/lol/opgg.py: champion data → strategy mapping
  - integrations/lol/src/lol_agent/matchup_knowledge_base.py: matchup storage
  - integrations/lol/src/lol_agent/decision_engine.py: strategy scoring
  - agentos/governance/strategy_knowledge_graph.py: _Node schema
  - open_spiel: game-specific information state representation

Design Notes (Knuth-level critique):
  User:
    - add_champion_context / add_matchup_context are additive — safe to repeat.
    - compute_relevance uses game_phase matching — context-dependent scoring.
    - serialize/deserialize enable graph persistence without custom codecs.
  System:
    - Embedding is lazily initialised — no memory cost until first access.
    - merge() uses last-writer-wins for attributes — deterministic for replays.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.lol_knowledge_node.v1"

# Phase relevance weights for LoL
_LOL_PHASE_MAP: Dict[str, float] = {
    "early": 0.0,
    "mid": 0.5,
    "late": 1.0,
}


def _deterministic_embedding(seed: str, dim: int) -> List[float]:
    """Generate a deterministic pseudo-random embedding from a string seed."""
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class LoLKnowledgeNode:
    """LoL strategy concept node for the Strategy Knowledge Graph.

    Attributes:
        node_id: Unique identifier for this strategy node.
        game: Always ``"lol"``.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        *,
        node_id: str,
        embedding_dim: int = 64,
    ) -> None:
        self._node_id = node_id
        self._game = "lol"
        self._embedding_dim = embedding_dim

        self._attributes: Dict[str, Any] = {}
        self._champion_contexts: Dict[str, Dict[str, Any]] = {}
        self._matchup_contexts: Dict[str, Dict[str, Any]] = {}
        self._embedding: Optional[List[float]] = None

        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def game(self) -> str:
        return self._game

    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------

    def set_attribute(self, key: str, value: Any) -> None:
        self._attributes[key] = value
        self._fire_evolution({"action": "set_attribute", "key": key})

    def get_attribute(self, key: str, default: Any = None) -> Any:
        return self._attributes.get(key, default)

    # ------------------------------------------------------------------
    # Champion context — per-champion strategy metadata
    # Reference: Seraphine opgg.py champion build data
    # ------------------------------------------------------------------

    def add_champion_context(self, champion: str, data: Dict[str, Any]) -> None:
        self._champion_contexts[champion] = data

    def get_champion_context(self, champion: str) -> Optional[Dict[str, Any]]:
        return self._champion_contexts.get(champion)

    # ------------------------------------------------------------------
    # Matchup context
    # Reference: matchup_knowledge_base.py record/query pattern
    # ------------------------------------------------------------------

    def add_matchup_context(self, matchup_key: str, data: Dict[str, Any]) -> None:
        self._matchup_contexts[matchup_key] = data

    def get_matchup_context(self, matchup_key: str) -> Optional[Dict[str, Any]]:
        return self._matchup_contexts.get(matchup_key)

    # ------------------------------------------------------------------
    # Relevance scoring
    # ------------------------------------------------------------------

    def compute_relevance(self, state: Dict[str, Any]) -> float:
        """Compute how relevant this strategy node is to the current game state.

        Uses game_phase matching and attribute overlap.
        """
        score = 0.5

        # Phase matching
        node_phase = self._attributes.get("game_phase", "")
        state_phase = state.get("game_phase", "")
        if node_phase and state_phase:
            if node_phase == state_phase:
                score += 0.3
            else:
                score -= 0.1

        # Game time proximity
        gt = state.get("game_time", 0.0)
        if isinstance(gt, (int, float)):
            if gt < 600:
                phase_val = 0.0
            elif gt < 1500:
                phase_val = 0.5
            else:
                phase_val = 1.0
            node_agg = self._attributes.get("aggression", 0.5)
            if isinstance(node_agg, (int, float)):
                score += (1.0 - abs(phase_val - node_agg)) * 0.2

        return max(0.0, min(1.0, score))

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def get_embedding(self) -> List[float]:
        if self._embedding is None:
            self._embedding = _deterministic_embedding(
                f"lol:{self._node_id}", self._embedding_dim
            )
        return list(self._embedding)

    def set_embedding(self, vec: List[float]) -> None:
        self._embedding = list(vec)

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge(self, other: "LoLKnowledgeNode") -> None:
        """Merge another node's data into this one (last-writer-wins)."""
        for k, v in other._attributes.items():
            self._attributes[k] = v
        for k, v in other._champion_contexts.items():
            self._champion_contexts[k] = v
        for k, v in other._matchup_contexts.items():
            self._matchup_contexts[k] = v
        if other._embedding is not None:
            self._embedding = list(other._embedding)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> Dict[str, Any]:
        return {
            "node_id": self._node_id,
            "game": self._game,
            "embedding_dim": self._embedding_dim,
            "attributes": self._attributes,
            "champion_contexts": self._champion_contexts,
            "matchup_contexts": self._matchup_contexts,
            "embedding": self._embedding,
        }

    def deserialize(self, data: Dict[str, Any]) -> None:
        self._node_id = data.get("node_id", self._node_id)
        self._embedding_dim = data.get("embedding_dim", self._embedding_dim)
        self._attributes = data.get("attributes", {})
        self._champion_contexts = data.get("champion_contexts", {})
        self._matchup_contexts = data.get("matchup_contexts", {})
        self._embedding = data.get("embedding")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "node_id": self._node_id,
            "game": self._game,
            "attribute_count": len(self._attributes),
            "champion_count": len(self._champion_contexts),
            "matchup_count": len(self._matchup_contexts),
            "has_embedding": self._embedding is not None,
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised")

    def __repr__(self) -> str:
        return f"LoLKnowledgeNode(id={self._node_id})"

"""
Mahjong Knowledge Node — Mahjong strategy graph node.

Represents a single Mahjong strategy concept (riichi decision, betaori
defense, push-or-fold, dama strategy, etc.) as a node in the cross-game
Strategy Knowledge Graph.

Location: integrations/mahjong/src/mahjong_agent/mahjong_knowledge_node.py

Reference (拿来主義):
  - Akagi/akagi/misc.py: tile encoding, hand evaluation utilities
  - Mortal: Mahjong AI strategy representations
  - integrations/mahjong/src/mahjong_agent/discard_advisor.py: discard strategy
  - integrations/mahjong/src/mahjong_agent/shanten_calculator.py: hand state
  - integrations/lol/src/lol_agent/lol_knowledge_node.py: sister implementation
  - agentos/governance/strategy_knowledge_graph.py: _Node schema

Design Notes (Knuth-level critique):
  User:
    - add_hand_context stores expected value per hand pattern.
    - add_opponent_tendency tracks riichi rate / deal-in rate per player type.
    - compute_relevance uses round number + shanten for context scoring.
  System:
    - Same interface contract as LoL/Dota2 nodes for polymorphic graph ops.
    - Embedding seed "mahjong:" prefix prevents cross-game collisions.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_knowledge_node.v1"


def _deterministic_embedding(seed: str, dim: int) -> List[float]:
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class MahjongKnowledgeNode:
    """Mahjong strategy concept node for the Strategy Knowledge Graph.

    Attributes:
        node_id: Unique identifier for this strategy node.
        game: Always ``"mahjong"``.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, *, node_id: str, embedding_dim: int = 64) -> None:
        self._node_id = node_id
        self._game = "mahjong"
        self._embedding_dim = embedding_dim
        self._attributes: Dict[str, Any] = {}
        self._hand_contexts: Dict[str, Dict[str, Any]] = {}
        self._opponent_tendencies: Dict[str, Dict[str, Any]] = {}
        self._embedding: Optional[List[float]] = None
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def game(self) -> str:
        return self._game

    def set_attribute(self, key: str, value: Any) -> None:
        self._attributes[key] = value
        self._fire_evolution({"action": "set_attribute", "key": key})

    def get_attribute(self, key: str, default: Any = None) -> Any:
        return self._attributes.get(key, default)

    def add_hand_context(self, hand_pattern: str, data: Dict[str, Any]) -> None:
        """Add strategy context for a hand pattern (e.g., pinfu_iipeiko)."""
        self._hand_contexts[hand_pattern] = data

    def get_hand_context(self, hand_pattern: str) -> Optional[Dict[str, Any]]:
        return self._hand_contexts.get(hand_pattern)

    def add_opponent_tendency(self, player_type: str, data: Dict[str, Any]) -> None:
        """Add opponent tendency profile (e.g., aggressive riichi rate)."""
        self._opponent_tendencies[player_type] = data

    def get_opponent_tendency(self, player_type: str) -> Optional[Dict[str, Any]]:
        return self._opponent_tendencies.get(player_type)

    def compute_relevance(self, state: Dict[str, Any]) -> float:
        score = 0.5
        node_phase = self._attributes.get("round_phase", "")
        state_phase = state.get("round_phase", "")
        if node_phase and state_phase and node_phase == state_phase:
            score += 0.3
        shanten = state.get("shanten", None)
        if isinstance(shanten, (int, float)):
            if shanten <= 1:
                score += 0.15
            elif shanten >= 4:
                score -= 0.1
        return max(0.0, min(1.0, score))

    def get_embedding(self) -> List[float]:
        if self._embedding is None:
            self._embedding = _deterministic_embedding(f"mahjong:{self._node_id}", self._embedding_dim)
        return list(self._embedding)

    def set_embedding(self, vec: List[float]) -> None:
        self._embedding = list(vec)

    def merge(self, other: "MahjongKnowledgeNode") -> None:
        for k, v in other._attributes.items():
            self._attributes[k] = v
        for k, v in other._hand_contexts.items():
            self._hand_contexts[k] = v
        for k, v in other._opponent_tendencies.items():
            self._opponent_tendencies[k] = v
        if other._embedding is not None:
            self._embedding = list(other._embedding)

    def serialize(self) -> Dict[str, Any]:
        return {
            "node_id": self._node_id, "game": self._game,
            "embedding_dim": self._embedding_dim, "attributes": self._attributes,
            "hand_contexts": self._hand_contexts,
            "opponent_tendencies": self._opponent_tendencies,
            "embedding": self._embedding,
        }

    def deserialize(self, data: Dict[str, Any]) -> None:
        self._node_id = data.get("node_id", self._node_id)
        self._embedding_dim = data.get("embedding_dim", self._embedding_dim)
        self._attributes = data.get("attributes", {})
        self._hand_contexts = data.get("hand_contexts", {})
        self._opponent_tendencies = data.get("opponent_tendencies", {})
        self._embedding = data.get("embedding")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "node_id": self._node_id, "game": self._game,
            "attribute_count": len(self._attributes),
            "hand_context_count": len(self._hand_contexts),
            "opponent_tendency_count": len(self._opponent_tendencies),
            "has_embedding": self._embedding is not None,
        }

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
        return f"MahjongKnowledgeNode(id={self._node_id})"

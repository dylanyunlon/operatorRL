"""
Dota2 Knowledge Node — Dota 2 strategy graph node.

Represents a single Dota 2 strategy concept (Roshan fight, smoke gank,
highground push, buyback, etc.) as a node in the cross-game Strategy
Knowledge Graph.

Location: integrations/dota2/src/dota2_agent/dota2_knowledge_node.py

Reference (拿来主義):
  - dota2bot-OpenHyperAI: hero selection and game strategy
  - DI-star/distar/agent/default/model: unit/hero type heads
  - integrations/dota2/src/dota2_agent/dota2_hero_tendency.py: hero data
  - integrations/lol/src/lol_agent/lol_knowledge_node.py: sister implementation
  - agentos/governance/strategy_knowledge_graph.py: _Node schema

Design Notes (Knuth-level critique):
  User:
    - add_hero_context mirrors LoL's add_champion_context for API parity.
    - Dota2-specific: farm_priority, timing windows, buyback cost.
    - compute_relevance uses Dota2 game phases (laning/mid/late).
  System:
    - Same base pattern as LoLKnowledgeNode — enables polymorphic graph ops.
    - Embedding seed includes "dota2:" prefix — no collision with LoL nodes.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.dota2.dota2_knowledge_node.v1"


def _deterministic_embedding(seed: str, dim: int) -> List[float]:
    h = hashlib.sha256(seed.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(h[:8], "big"))
    vec = [rng.gauss(0.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class Dota2KnowledgeNode:
    """Dota 2 strategy concept node for the Strategy Knowledge Graph.

    Attributes:
        node_id: Unique identifier for this strategy node.
        game: Always ``"dota2"``.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, *, node_id: str, embedding_dim: int = 64) -> None:
        self._node_id = node_id
        self._game = "dota2"
        self._embedding_dim = embedding_dim
        self._attributes: Dict[str, Any] = {}
        self._hero_contexts: Dict[str, Dict[str, Any]] = {}
        self._matchup_contexts: Dict[str, Dict[str, Any]] = {}
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

    def add_hero_context(self, hero: str, data: Dict[str, Any]) -> None:
        self._hero_contexts[hero] = data

    def get_hero_context(self, hero: str) -> Optional[Dict[str, Any]]:
        return self._hero_contexts.get(hero)

    def add_matchup_context(self, matchup_key: str, data: Dict[str, Any]) -> None:
        self._matchup_contexts[matchup_key] = data

    def get_matchup_context(self, matchup_key: str) -> Optional[Dict[str, Any]]:
        return self._matchup_contexts.get(matchup_key)

    def compute_relevance(self, state: Dict[str, Any]) -> float:
        score = 0.5
        node_phase = self._attributes.get("game_phase", "")
        state_phase = state.get("game_phase", "")
        if node_phase and state_phase and node_phase == state_phase:
            score += 0.3
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

    def get_embedding(self) -> List[float]:
        if self._embedding is None:
            self._embedding = _deterministic_embedding(f"dota2:{self._node_id}", self._embedding_dim)
        return list(self._embedding)

    def set_embedding(self, vec: List[float]) -> None:
        self._embedding = list(vec)

    def merge(self, other: "Dota2KnowledgeNode") -> None:
        for k, v in other._attributes.items():
            self._attributes[k] = v
        for k, v in other._hero_contexts.items():
            self._hero_contexts[k] = v
        for k, v in other._matchup_contexts.items():
            self._matchup_contexts[k] = v
        if other._embedding is not None:
            self._embedding = list(other._embedding)

    def serialize(self) -> Dict[str, Any]:
        return {
            "node_id": self._node_id, "game": self._game,
            "embedding_dim": self._embedding_dim, "attributes": self._attributes,
            "hero_contexts": self._hero_contexts, "matchup_contexts": self._matchup_contexts,
            "embedding": self._embedding,
        }

    def deserialize(self, data: Dict[str, Any]) -> None:
        self._node_id = data.get("node_id", self._node_id)
        self._embedding_dim = data.get("embedding_dim", self._embedding_dim)
        self._attributes = data.get("attributes", {})
        self._hero_contexts = data.get("hero_contexts", {})
        self._matchup_contexts = data.get("matchup_contexts", {})
        self._embedding = data.get("embedding")

    def get_stats(self) -> Dict[str, Any]:
        return {
            "node_id": self._node_id, "game": self._game,
            "attribute_count": len(self._attributes),
            "hero_count": len(self._hero_contexts),
            "matchup_count": len(self._matchup_contexts),
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
        return f"Dota2KnowledgeNode(id={self._node_id})"

"""
Knowledge Graph Trainer — knowledge graph embedding training.

Trains node embeddings for the Strategy Knowledge Graph using a
TransE-style approach: for each edge (h, r, t), optimise
||h + r - t|| → 0.  Includes negative sampling, batch generation,
and similarity scoring.

Location: agentlightning/trainer/knowledge_graph_trainer.py

Reference (拿来主義):
  - agentlightning/trainer/trainer.py: Trainer base class lifecycle
  - agentlightning/trainer/multi_game_trainer.py: train_step/train_epoch
  - open_spiel algorithms: embedding-based strategy representation
  - agentos/governance/strategy_knowledge_graph.py: graph data schema
  - Bordes et al. (2013): Translating Embeddings for Modeling Multi-relational Data

Design Notes (Knuth-level critique):
  User:
    - load_graph() from StrategyKnowledgeGraph.serialize() output.
    - train_epoch() returns avg_loss — single metric for monitoring.
    - similarity() computes cosine similarity between two node embeddings.
  System:
    - Pure Python + math — no PyTorch/numpy dependency.
    - SGD with fixed learning rate — production would use Adam.
    - Negative sampling is uniform random — production would use type-aware.
"""

from __future__ import annotations

import logging
import math
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.trainer.knowledge_graph_trainer.v1"


def _rand_vec(dim: int, rng: random.Random) -> List[float]:
    vec = [rng.gauss(0.0, 0.1) for _ in range(dim)]
    return vec


def _vec_add(a: List[float], b: List[float]) -> List[float]:
    return [ai + bi for ai, bi in zip(a, b)]


def _vec_sub(a: List[float], b: List[float]) -> List[float]:
    return [ai - bi for ai, bi in zip(a, b)]


def _vec_scale(a: List[float], s: float) -> List[float]:
    return [ai * s for ai in a]


def _vec_norm(a: List[float]) -> float:
    return math.sqrt(sum(x * x for x in a)) or 1e-12


def _vec_normalise(a: List[float]) -> List[float]:
    n = _vec_norm(a)
    return [x / n for x in a]


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(ai * bi for ai, bi in zip(a, b))
    na = _vec_norm(a)
    nb = _vec_norm(b)
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


class KnowledgeGraphTrainer:
    """Train node and relation embeddings for a Strategy Knowledge Graph.

    Attributes:
        epoch_count: Number of training epochs completed.
        node_count: Number of nodes in loaded graph.
        edge_count: Number of edges in loaded graph.
        learning_rate: Current learning rate.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        *,
        embedding_dim: int = 64,
        learning_rate: float = 0.01,
        neg_samples: int = 5,
        seed: int = 42,
    ) -> None:
        self._dim = embedding_dim
        self._lr = learning_rate
        self._neg_samples = neg_samples
        self._rng = random.Random(seed)

        self._node_ids: List[str] = []
        self._edges: List[Tuple[str, str, str]] = []  # (head, tail, relation)
        self._node_emb: Dict[str, List[float]] = {}
        self._rel_emb: Dict[str, List[float]] = {}

        self._epoch_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    @property
    def epoch_count(self) -> int:
        return self._epoch_count

    @property
    def node_count(self) -> int:
        return len(self._node_ids)

    @property
    def edge_count(self) -> int:
        return len(self._edges)

    @property
    def learning_rate(self) -> float:
        return self._lr

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_graph(self, data: Dict[str, Any]) -> None:
        """Load a graph from StrategyKnowledgeGraph.serialize() output."""
        self._node_ids = []
        self._edges = []
        self._node_emb = {}
        self._rel_emb = {}

        for nd in data.get("nodes", []):
            nid = nd.get("id", nd.get("node_id", ""))
            if nid:
                self._node_ids.append(nid)
                self._node_emb[nid] = _rand_vec(self._dim, self._rng)

        for ed in data.get("edges", []):
            src = ed.get("source", "")
            tgt = ed.get("target", "")
            rel = ed.get("relation", "default")
            if src and tgt:
                self._edges.append((src, tgt, rel))
                if rel not in self._rel_emb:
                    self._rel_emb[rel] = _rand_vec(self._dim, self._rng)

        self._fire_evolution({"action": "load_graph", "nodes": self.node_count, "edges": self.edge_count})

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def _transe_loss(self, h: List[float], r: List[float], t: List[float]) -> float:
        """TransE loss: ||h + r - t||_2."""
        diff = _vec_sub(_vec_add(h, r), t)
        return _vec_norm(diff)

    def train_step(self) -> float:
        """Execute one training step (one edge + negative samples)."""
        if not self._edges:
            return 0.0

        # Sample a positive edge
        h_id, t_id, r_id = self._rng.choice(self._edges)
        h = self._node_emb[h_id]
        t = self._node_emb[t_id]
        r = self._rel_emb.get(r_id, _rand_vec(self._dim, self._rng))

        pos_loss = self._transe_loss(h, r, t)

        # Negative sampling
        neg_loss_sum = 0.0
        for _ in range(self._neg_samples):
            neg_t_id = self._rng.choice(self._node_ids)
            neg_t = self._node_emb[neg_t_id]
            neg_loss_sum += max(0.0, 1.0 - self._transe_loss(h, r, neg_t))

        total_loss = pos_loss + neg_loss_sum / max(self._neg_samples, 1)

        # SGD update
        for i in range(self._dim):
            grad_h = (h[i] + r[i] - t[i]) / max(pos_loss, 1e-9)
            h[i] -= self._lr * grad_h
            t[i] += self._lr * grad_h
            r[i] -= self._lr * grad_h * 0.5

        # Re-normalise
        self._node_emb[h_id] = _vec_normalise(h)
        self._node_emb[t_id] = _vec_normalise(t)

        return total_loss

    def train_epoch(self) -> Dict[str, Any]:
        """Train one full epoch (one step per edge)."""
        if not self._edges:
            self._epoch_count += 1
            return {"avg_loss": 0.0, "epoch": self._epoch_count}

        total_loss = 0.0
        steps = max(len(self._edges), 1)
        for _ in range(steps):
            total_loss += self.train_step()

        self._epoch_count += 1
        avg_loss = total_loss / steps

        self._fire_evolution({"action": "train_epoch", "epoch": self._epoch_count, "avg_loss": avg_loss})
        return {"avg_loss": avg_loss, "epoch": self._epoch_count, "steps": steps}

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    def get_node_embedding(self, node_id: str) -> List[float]:
        emb = self._node_emb.get(node_id)
        if emb is None:
            raise KeyError(f"Node '{node_id}' not found")
        return list(emb)

    def similarity(self, node_a: str, node_b: str) -> float:
        a = self._node_emb.get(node_a)
        b = self._node_emb.get(node_b)
        if a is None or b is None:
            return 0.0
        return _cosine_similarity(a, b)

    # ------------------------------------------------------------------
    # Batch generation
    # ------------------------------------------------------------------

    def generate_training_batch(self) -> Dict[str, Any]:
        """Generate a batch of positive and negative samples."""
        if not self._edges:
            return {"positive": [], "negative": []}

        pos = self._rng.choice(self._edges)
        negatives = []
        for _ in range(self._neg_samples):
            neg_t = self._rng.choice(self._node_ids)
            negatives.append((pos[0], neg_t, pos[2]))

        return {"positive": [pos], "negative": negatives}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def export_embeddings(self) -> Dict[str, Any]:
        return {
            "node_embeddings": {k: list(v) for k, v in self._node_emb.items()},
            "rel_embeddings": {k: list(v) for k, v in self._rel_emb.items()},
            "dim": self._dim,
            "epoch": self._epoch_count,
        }

    def import_embeddings(self, data: Dict[str, Any]) -> None:
        ne = data.get("node_embeddings", {})
        for k, v in ne.items():
            self._node_emb[k] = list(v)
            if k not in self._node_ids:
                self._node_ids.append(k)
        re = data.get("rel_embeddings", {})
        for k, v in re.items():
            self._rel_emb[k] = list(v)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "epoch_count": self._epoch_count,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "embedding_dim": self._dim,
            "learning_rate": self._lr,
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
        return f"KnowledgeGraphTrainer(nodes={self.node_count}, epochs={self._epoch_count})"

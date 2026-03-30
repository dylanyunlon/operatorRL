"""
Strategy Knowledge Graph — cross-game strategy node / edge graph.

A directed labelled graph that stores strategy concepts as nodes and
their relationships (analogies, counters, prerequisites) as edges.
Supports cross-game queries, subgraph extraction, serialisation, and
embedding integration.

Location: agentos/governance/strategy_knowledge_graph.py

Reference (拿来主义):
  - open_spiel game tree representations
  - DI-star strategy representations: hero/unit type → strategy mapping
  - agentos/governance/game_registry.py: game registration pattern
  - agentos/governance/evolution_orchestrator.py: cross-game orchestration
  - Seraphine/app/lol/opgg.py: champion → strategy data mapping

Design Notes (Knuth-level critique):
  User:
    - add_node/add_edge are idempotent — safe to call repeatedly.
    - serialize/deserialize enables persistence across sessions.
    - get_cross_game_nodes enables strategy transfer discovery.
  System:
    - Adjacency list representation — O(degree) neighbor queries.
    - Serialization is JSON-compatible — no pickle dependencies.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.strategy_knowledge_graph.v1"


class _Node:
    """Graph node representing a strategy concept."""

    __slots__ = ("id", "game", "category", "weight", "attributes", "embedding")

    def __init__(
        self,
        node_id: str,
        game: str = "",
        category: str = "",
        weight: float = 1.0,
    ) -> None:
        self.id = node_id
        self.game = game
        self.category = category
        self.weight = weight
        self.attributes: Dict[str, Any] = {}
        self.embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "game": self.game,
            "category": self.category,
            "weight": self.weight,
            "attributes": self.attributes,
        }
        if self.embedding is not None:
            d["embedding"] = self.embedding
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "_Node":
        n = cls(
            node_id=data["id"],
            game=data.get("game", ""),
            category=data.get("category", ""),
            weight=data.get("weight", 1.0),
        )
        n.attributes = data.get("attributes", {})
        n.embedding = data.get("embedding")
        return n


class _Edge:
    """Graph edge representing a relationship between strategy nodes."""

    __slots__ = ("source", "target", "relation", "weight", "attributes")

    def __init__(
        self,
        source: str,
        target: str,
        relation: str = "",
        weight: float = 1.0,
    ) -> None:
        self.source = source
        self.target = target
        self.relation = relation
        self.weight = weight
        self.attributes: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "weight": self.weight,
            "attributes": self.attributes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "_Edge":
        e = cls(
            source=data["source"],
            target=data["target"],
            relation=data.get("relation", ""),
            weight=data.get("weight", 1.0),
        )
        e.attributes = data.get("attributes", {})
        return e


class StrategyKnowledgeGraph:
    """Cross-game strategy knowledge graph.

    Attributes:
        node_count: Number of nodes.
        edge_count: Number of edges.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, _Node] = {}
        self._adj: Dict[str, List[_Edge]] = {}  # forward adjacency
        self._radj: Dict[str, List[_Edge]] = {}  # reverse adjacency
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self._adj.values())

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(self, node_id: str, *, game: str = "", category: str = "", weight: float = 1.0) -> None:
        """Add or update a node in the graph."""
        if node_id in self._nodes:
            n = self._nodes[node_id]
            if game:
                n.game = game
            if category:
                n.category = category
            n.weight = weight
        else:
            self._nodes[node_id] = _Node(node_id, game, category, weight)
            self._adj.setdefault(node_id, [])
            self._radj.setdefault(node_id, [])
        self._fire_evolution({"action": "add_node", "node_id": node_id})

    def remove_node(self, node_id: str) -> None:
        """Remove a node and all its incident edges."""
        if node_id not in self._nodes:
            return
        del self._nodes[node_id]
        self._adj.pop(node_id, None)
        self._radj.pop(node_id, None)
        # Remove edges pointing to/from this node
        for nid in list(self._adj.keys()):
            self._adj[nid] = [e for e in self._adj[nid] if e.target != node_id]
        for nid in list(self._radj.keys()):
            self._radj[nid] = [e for e in self._radj[nid] if e.source != node_id]
        self._fire_evolution({"action": "remove_node", "node_id": node_id})

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        n = self._nodes.get(node_id)
        return n.to_dict() if n else None

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str = "",
        weight: float = 1.0,
    ) -> None:
        """Add a directed edge between two existing nodes."""
        if source not in self._nodes or target not in self._nodes:
            raise KeyError(f"Both {source} and {target} must exist")
        edge = _Edge(source, target, relation, weight)
        self._adj.setdefault(source, []).append(edge)
        self._radj.setdefault(target, []).append(edge)
        self._fire_evolution({"action": "add_edge", "source": source, "target": target})

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_neighbors(self, node_id: str) -> List[Dict[str, Any]]:
        """Return outgoing neighbors of a node."""
        edges = self._adj.get(node_id, [])
        result: List[Dict[str, Any]] = []
        for e in edges:
            target = self._nodes.get(e.target)
            if target:
                result.append({
                    "id": target.id,
                    "game": target.game,
                    "relation": e.relation,
                    "weight": e.weight,
                })
        return result

    def get_cross_game_nodes(self, node_id: str) -> List[Dict[str, Any]]:
        """Return neighbors that belong to a different game."""
        node = self._nodes.get(node_id)
        if node is None:
            return []
        source_game = node.game
        neighbors = self.get_neighbors(node_id)
        return [n for n in neighbors if n.get("game") != source_game]

    def get_subgraph(self, node_ids: List[str]) -> Dict[str, Any]:
        """Extract a subgraph containing only the specified nodes."""
        nodes_set = set(node_ids)
        sub_nodes = [self._nodes[nid].to_dict() for nid in node_ids if nid in self._nodes]
        sub_edges: List[Dict[str, Any]] = []
        for nid in node_ids:
            for e in self._adj.get(nid, []):
                if e.target in nodes_set:
                    sub_edges.append(e.to_dict())
        return {
            "node_count": len(sub_nodes),
            "edge_count": len(sub_edges),
            "nodes": sub_nodes,
            "edges": sub_edges,
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> Dict[str, Any]:
        """Serialize the full graph to a JSON-compatible dict."""
        nodes = [n.to_dict() for n in self._nodes.values()]
        edges: List[Dict[str, Any]] = []
        for edge_list in self._adj.values():
            for e in edge_list:
                edges.append(e.to_dict())
        return {"nodes": nodes, "edges": edges, "version": _EVOLUTION_KEY}

    def deserialize(self, data: Dict[str, Any]) -> None:
        """Deserialize a graph from a dict."""
        self._nodes.clear()
        self._adj.clear()
        self._radj.clear()
        for nd in data.get("nodes", []):
            node = _Node.from_dict(nd)
            self._nodes[node.id] = node
            self._adj.setdefault(node.id, [])
            self._radj.setdefault(node.id, [])
        for ed in data.get("edges", []):
            edge = _Edge.from_dict(ed)
            self._adj.setdefault(edge.source, []).append(edge)
            self._radj.setdefault(edge.target, []).append(edge)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        games: Dict[str, int] = {}
        for n in self._nodes.values():
            games[n.game] = games.get(n.game, 0) + 1
        return {
            "node_count": self.node_count,
            "edge_count": self.edge_count,
            "games": games,
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
                logger.exception("evolution_callback raised in StrategyKnowledgeGraph")

    def __repr__(self) -> str:
        return f"StrategyKnowledgeGraph(nodes={self.node_count}, edges={self.edge_count})"


default_graph: StrategyKnowledgeGraph = StrategyKnowledgeGraph()

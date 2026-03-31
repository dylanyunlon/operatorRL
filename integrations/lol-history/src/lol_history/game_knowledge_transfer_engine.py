"""
GameKnowledgeTransferEngine — Transfer strategy knowledge between games.

Maps abstract strategy concepts from one game to analogous concepts in another,
enabling knowledge transfer (e.g., MOBA map control → Mahjong board control).

Location: integrations/lol-history/src/lol_history/game_knowledge_transfer_engine.py

Reference (拿来主義):
  - integrations/lol-history/src/lol_history/game_event_pattern_library.py（M615）:
    knowledge base pattern
  - integrations/lol-history/src/lol_history/transfer_learning_feature_aligner.py（M675）:
    feature alignment

Design Notes (Knuth-level critique):
  User:
    - register_knowledge() adds strategy patterns per game.
    - transfer() maps knowledge from source to target game.
    - evaluate_transfer() rates transfer effectiveness.
  System:
    - Knowledge stored as (concept, context, strategy) triples.
    - Cross-game concept mapping via abstract categories.
    - Transfer effectiveness tracked for feedback loop.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.game_knowledge_transfer_engine.v1"

# Abstract strategy concepts shared across games
_ABSTRACT_CONCEPTS = {
    "map_control": "Controlling spatial territory / information",
    "resource_optimization": "Efficient use of limited resources",
    "timing_windows": "Exploiting temporal advantages",
    "risk_assessment": "Evaluating risk vs reward",
    "opponent_modeling": "Predicting opponent behavior",
    "adaptation": "Adjusting strategy based on state",
    "information_warfare": "Managing what opponent knows",
    "tempo_control": "Controlling pace of play",
}


class KnowledgeEntry:
    __slots__ = ("game_type", "concept", "context", "strategy", "confidence", "created_at")

    def __init__(
        self, game_type: str, concept: str, context: str,
        strategy: str, confidence: float = 0.5,
    ) -> None:
        self.game_type = game_type
        self.concept = concept
        self.context = context
        self.strategy = strategy
        self.confidence = confidence
        self.created_at = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class TransferResult:
    __slots__ = ("source_entry", "target_game", "transferred_strategy", "similarity", "evaluated")

    def __init__(
        self, source_entry: KnowledgeEntry, target_game: str,
        transferred_strategy: str, similarity: float,
    ) -> None:
        self.source_entry = source_entry
        self.target_game = target_game
        self.transferred_strategy = transferred_strategy
        self.similarity = similarity
        self.evaluated = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_game": self.source_entry.game_type,
            "target_game": self.target_game,
            "concept": self.source_entry.concept,
            "source_strategy": self.source_entry.strategy,
            "transferred_strategy": self.transferred_strategy,
            "similarity": self.similarity,
            "evaluated": self.evaluated,
        }


class GameKnowledgeTransferEngine:
    """Transfer strategy knowledge between games.

    Public API:
        register_knowledge(entry)
        transfer(source_game, target_game, concept=None) -> list[TransferResult]
        evaluate_transfer(result, effectiveness) -> None
        get_knowledge(game_type, concept=None) -> list[KnowledgeEntry]
        get_transfer_history() -> list[dict]
        get_stats() -> dict
    """

    def __init__(self) -> None:
        # game_type → concept → list of KnowledgeEntry
        self._knowledge: Dict[str, Dict[str, List[KnowledgeEntry]]] = {}
        # concept → {game_type: game_specific_description}
        self._concept_mappings: Dict[str, Dict[str, str]] = {}
        self._transfer_history: List[TransferResult] = []
        self._effectiveness_scores: List[float] = []
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def register_knowledge(self, entry: KnowledgeEntry) -> None:
        if entry.game_type not in self._knowledge:
            self._knowledge[entry.game_type] = {}
        if entry.concept not in self._knowledge[entry.game_type]:
            self._knowledge[entry.game_type][entry.concept] = []
        self._knowledge[entry.game_type][entry.concept].append(entry)

    def register_concept_mapping(
        self, concept: str, game_type: str, description: str,
    ) -> None:
        """Register how an abstract concept manifests in a specific game."""
        if concept not in self._concept_mappings:
            self._concept_mappings[concept] = {}
        self._concept_mappings[concept][game_type] = description

    def transfer(
        self,
        source_game: str,
        target_game: str,
        concept: Optional[str] = None,
    ) -> List[TransferResult]:
        """Transfer knowledge from source to target game."""
        results: List[TransferResult] = []
        src = self._knowledge.get(source_game, {})
        if not src:
            return results

        concepts = [concept] if concept else list(src.keys())

        for c in concepts:
            entries = src.get(c, [])
            for entry in entries:
                # Check if concept has a mapping to target game
                target_desc = self._concept_mappings.get(c, {}).get(target_game, "")
                if target_desc:
                    transferred = f"[{c}] {target_desc}: {entry.strategy}"
                    sim = 0.7  # mapped concept has decent similarity
                elif c in _ABSTRACT_CONCEPTS:
                    transferred = f"[{c}] Apply general principle: {entry.strategy}"
                    sim = 0.4  # abstract match only
                else:
                    transferred = f"[{c}] {entry.strategy} (direct transfer)"
                    sim = 0.2  # no mapping, low confidence

                tr = TransferResult(entry, target_game, transferred, sim)
                results.append(tr)
                self._transfer_history.append(tr)

        results.sort(key=lambda r: -r.similarity)
        self._fire("transfer_completed", {
            "source": source_game, "target": target_game, "count": len(results),
        })
        return results

    def evaluate_transfer(self, result: TransferResult, effectiveness: float) -> None:
        """Record effectiveness of a transfer (0.0 to 1.0)."""
        result.evaluated = True
        self._effectiveness_scores.append(effectiveness)
        self._fire("transfer_evaluated", {
            "concept": result.source_entry.concept,
            "effectiveness": effectiveness,
        })

    def get_knowledge(
        self, game_type: str, concept: Optional[str] = None,
    ) -> List[KnowledgeEntry]:
        game_k = self._knowledge.get(game_type, {})
        if concept:
            return list(game_k.get(concept, []))
        result = []
        for entries in game_k.values():
            result.extend(entries)
        return result

    def get_transfer_history(self) -> List[Dict[str, Any]]:
        return [tr.to_dict() for tr in self._transfer_history]

    def get_stats(self) -> Dict[str, Any]:
        total_entries = sum(
            sum(len(v) for v in g.values()) for g in self._knowledge.values()
        )
        return {
            "knowledge_entries": total_entries,
            "game_types": list(self._knowledge.keys()),
            "transfers": len(self._transfer_history),
            "evaluated": sum(1 for s in self._effectiveness_scores),
            "avg_effectiveness": (
                sum(self._effectiveness_scores) / len(self._effectiveness_scores)
                if self._effectiveness_scores else 0.0
            ),
            "concept_mappings": len(self._concept_mappings),
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

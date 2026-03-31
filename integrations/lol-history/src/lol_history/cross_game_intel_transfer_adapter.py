"""
CrossGameIntelTransferAdapter — Transfers intel pipeline patterns across games (LoL→Dota2→Mahjong).

Architecture (拿来主义):
  game_knowledge_transfer_engine.py, transfer_learning_feature_aligner.py

Location: integrations/lol-history/src/lol_history/cross_game_intel_transfer_adapter.py

Design Notes (Knuth-level critique):
  User:
    - Production-grade module with unified {"status": "ok"} response format.
    - Stateless or bounded-state design for long-running sessions.
    - Graceful degradation: partial results on component failure.
  System:
    - All data structures bounded (deque/OrderedDict with maxlen).
    - Evolution callback integration for self-improvement feedback.
    - Comprehensive get_stats() for observability.
    - Zero external dependencies beyond stdlib.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.cross_game_intel_transfer_adapter.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _GameCapability:
    """Describes a game's intel pipeline capabilities."""

    def __init__(self, game_id: str, capabilities: Dict[str, bool]) -> None:
        self.game_id = game_id
        self.capabilities = dict(capabilities)
        self.registered_at = time.monotonic()

    def has_capability(self, cap: str) -> bool:
        return self.capabilities.get(cap, False)

    def to_dict(self) -> Dict[str, Any]:
        return {"game_id": self.game_id, "capabilities": self.capabilities}


class _PatternMapper:
    """Maps intel patterns between games via abstract concepts."""

    ABSTRACT_CONCEPTS = {
        "opponent_profile": "Analyze opponent strengths/weaknesses",
        "prediction": "Predict game outcomes",
        "suggestion": "Generate tactical suggestions",
        "feedback": "Collect and route feedback signals",
        "replay": "Export decision history for review",
        "vision": "Track positional/map information",
        "economy": "Track resource/economy metrics",
    }

    GAME_MAPPINGS = {
        "lol": {"opponent_profile": "summoner_lookup", "prediction": "win_probability",
                "suggestion": "macro_decision", "feedback": "evolution_callback",
                "vision": "minimap_tracker", "economy": "gold_xp_tracker"},
        "dota2": {"opponent_profile": "hero_history", "prediction": "win_probability",
                  "suggestion": "strategy_advisor", "feedback": "mmr_tracking",
                  "vision": "fog_tracker", "economy": "net_worth_tracker"},
        "mahjong": {"opponent_profile": "player_tendency", "prediction": "win_probability",
                    "suggestion": "discard_advisor", "feedback": "elo_tracking",
                    "economy": "point_tracker"},
    }

    def get_mapping(self, source_game: str, target_game: str,
                     concept: str) -> Dict[str, Any]:
        source_impl = self.GAME_MAPPINGS.get(source_game, {}).get(concept)
        target_impl = self.GAME_MAPPINGS.get(target_game, {}).get(concept)
        return {
            "concept": concept,
            "description": self.ABSTRACT_CONCEPTS.get(concept, "Unknown"),
            "source_impl": source_impl,
            "target_impl": target_impl,
            "transferable": source_impl is not None and target_impl is not None,
        }


class _TransferHistory:
    """Records pattern transfer events."""

    def __init__(self, max_records: int = 200) -> None:
        self._records: deque = deque(maxlen=max_records)

    def record(self, source: str, target: str, concept: str,
               success: bool) -> None:
        self._records.append({
            "source": source, "target": target, "concept": concept,
            "success": success, "ts": time.monotonic(),
        })

    def get_recent(self, limit: int = 20) -> List[Dict]:
        return list(self._records)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        total = len(self._records)
        success = sum(1 for r in self._records if r["success"])
        return {"total": total, "success": success,
                "rate": _safe_div(success, total)}


class CrossGameIntelTransferAdapter:
    """Transfers intel pipeline patterns across games (LoL, Dota2, Mahjong).

    Public API: register_game, transfer_pattern, get_compatible_games,
                get_transfer_history, get_concept_map, get_stats
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._games: Dict[str, _GameCapability] = {}
        self._mapper = _PatternMapper()
        self._history = _TransferHistory()

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_game(self, game_id: str,
                       capabilities: Dict[str, bool]) -> Dict[str, Any]:
        self._op_count += 1
        self._games[game_id] = _GameCapability(game_id, capabilities)
        return {
            "status": "ok",
            "game_id": game_id,
            "capabilities": capabilities,
            "total_games": len(self._games),
        }

    def transfer_pattern(self, source_game: str, target_game: str,
                          concept: str) -> Dict[str, Any]:
        self._op_count += 1
        mapping = self._mapper.get_mapping(source_game, target_game, concept)
        success = mapping["transferable"]
        self._history.record(source_game, target_game, concept, success)
        self._fire("pattern_transferred", {
            "source": source_game, "target": target_game,
            "concept": concept, "success": success,
        })
        return {"status": "ok", "mapping": mapping}

    def get_compatible_games(self) -> Dict[str, Any]:
        self._op_count += 1
        compatibility = {}
        games = list(self._games.keys())
        for i, g1 in enumerate(games):
            for g2 in games[i + 1:]:
                shared = []
                for concept in _PatternMapper.ABSTRACT_CONCEPTS:
                    m = self._mapper.get_mapping(g1, g2, concept)
                    if m["transferable"]:
                        shared.append(concept)
                compatibility[f"{g1}<->{g2}"] = {
                    "shared_concepts": shared,
                    "compatibility_score": _safe_div(len(shared),
                                                     len(_PatternMapper.ABSTRACT_CONCEPTS)),
                }
        return {"status": "ok", "compatibility": compatibility}

    def get_transfer_history(self, limit: int = 20) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "history": self._history.get_recent(limit),
            "stats": self._history.get_stats(),
        }

    def get_concept_map(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "concepts": _PatternMapper.ABSTRACT_CONCEPTS,
            "game_mappings": _PatternMapper.GAME_MAPPINGS,
        }

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "registered_games": len(self._games),
            "games": {g: c.to_dict() for g, c in self._games.items()},
            "transfer_history": self._history.get_stats(),
        }

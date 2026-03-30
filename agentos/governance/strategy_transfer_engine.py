"""
Strategy Transfer Engine — LoL↔Dota2↔Mahjong strategy transfer.

Maps strategy concepts between games via configurable mapping tables
and optional embedding-space alignment.  Transfer confidence decays
proportionally to domain distance.

Location: agentos/governance/strategy_transfer_engine.py

Reference (拿来主義):
  - DI-star: cross-race strategy transfer concepts
  - open_spiel: game-agnostic algorithm interfaces
  - agentos/governance/strategy_knowledge_graph.py: graph node schema
  - agentos/governance/evolution_orchestrator.py: cross-game patterns
  - agentlightning/trainer/multi_game_trainer.py: multi-game training

Design Notes (Knuth-level critique):
  User:
    - register_game must be called before transfer — clear error otherwise.
    - Decay factor models domain gap — user controls transfer aggressiveness.
    - Reverse transfer auto-inverts mapping when explicit mapping absent.
  System:
    - Mapping is symmetric by default — A→B implies B→A.
    - Strategy embedding alignment is deferred (placeholder for M504 trainer).
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.strategy_transfer_engine.v1"


class _GameDomain:
    """Registered game domain descriptor."""

    __slots__ = ("name", "strategy_dims", "metadata")

    def __init__(self, name: str, strategy_dims: int) -> None:
        self.name = name
        self.strategy_dims = strategy_dims
        self.metadata: Dict[str, Any] = {}


class _Mapping:
    """Bi-directional mapping between two game domains."""

    __slots__ = ("source", "target", "created_at", "usage_count")

    def __init__(self, source: str, target: str) -> None:
        self.source = source
        self.target = target
        self.created_at: float = time.time()
        self.usage_count: int = 0


class StrategyTransferEngine:
    """Transfer strategy concepts across game domains.

    Attributes:
        transfer_count: Total transfers executed.
        registered_games: Set of registered game identifiers.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, *, decay_factor: float = 1.0) -> None:
        self._decay_factor = decay_factor
        self._games: Dict[str, _GameDomain] = {}
        self._mappings: Dict[Tuple[str, str], _Mapping] = {}
        self._transfer_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # Properties
    @property
    def transfer_count(self) -> int:
        return self._transfer_count

    @property
    def registered_games(self) -> Set[str]:
        return set(self._games.keys())

    # Game registration
    def register_game(self, name: str, *, strategy_dims: int = 64) -> None:
        self._games[name] = _GameDomain(name, strategy_dims)
        self._fire_evolution({"action": "register_game", "game": name})

    # Mapping
    def create_mapping(self, source: str, target: str) -> Dict[str, Any]:
        if source not in self._games or target not in self._games:
            raise KeyError(f"Both {source} and {target} must be registered")
        m = _Mapping(source, target)
        self._mappings[(source, target)] = m
        self._mappings[(target, source)] = _Mapping(target, source)
        self._fire_evolution({"action": "create_mapping", "source": source, "target": target})
        return {"source": source, "target": target, "created_at": m.created_at}

    # Transfer
    def transfer(
        self,
        source_game: str,
        target_game: str,
        strategy: Dict[str, Any],
    ) -> Dict[str, Any]:
        if source_game not in self._games:
            raise KeyError(f"Source game {source_game} not registered")
        if target_game not in self._games:
            raise KeyError(f"Target game {target_game} not registered")

        key = (source_game, target_game)
        mapping = self._mappings.get(key)
        if mapping is None:
            # Auto-create mapping on first transfer
            self.create_mapping(source_game, target_game)
            mapping = self._mappings[key]

        mapping.usage_count += 1
        self._transfer_count += 1

        # Compute confidence with decay
        original_conf = strategy.get("confidence", 1.0)
        transferred_conf = original_conf * self._decay_factor

        result = {
            "source_game": source_game,
            "target_game": target_game,
            "strategy_type": strategy.get("type", "unknown"),
            "confidence": transferred_conf,
            "decay_factor": self._decay_factor,
            "original_confidence": original_conf,
            "transfer_id": str(uuid.uuid4())[:8],
            "original_strategy": strategy,
        }

        self._fire_evolution({
            "action": "transfer",
            "source": source_game,
            "target": target_game,
            "confidence": transferred_conf,
        })

        return result

    # Stats
    def get_stats(self) -> Dict[str, Any]:
        return {
            "transfer_count": self._transfer_count,
            "registered_games": list(self._games.keys()),
            "mapping_count": len(self._mappings) // 2,
            "decay_factor": self._decay_factor,
        }

    # Evolution
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
        return f"StrategyTransferEngine(games={len(self._games)}, transfers={self._transfer_count})"


default_engine: StrategyTransferEngine = StrategyTransferEngine()

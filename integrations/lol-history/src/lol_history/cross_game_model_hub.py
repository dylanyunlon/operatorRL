"""
CrossGameModelHub — Manage and share trained models across games with lineage tracking.

Central model repository that tracks which game's data trained each model,
supports cross-game model transfer, and maintains model lineage.

Location: integrations/lol-history/src/lol_history/cross_game_model_hub.py

Reference (拿来主義):
  - agentos/governance/model_versioner.py: save→load→list_versions pattern
  - integrations/lol-history/src/lol_history/opponent_model_persistence.py（M609）: persistence
  - DI-star: checkpoint management

Design Notes (Knuth-level critique):
  User:
    - save_model() tracks lineage automatically (source_game, training_data_source).
    - load_model() returns None for missing models — never crashes.
    - list_models() supports filtering by game_type.
  System:
    - Storage is in-memory dict keyed by (game_type, model_name, version).
    - Lineage is immutable once saved — append-only history.
    - Transfer tracking records source→target game pairs.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.cross_game_model_hub.v1"


class ModelEntry:
    __slots__ = ("game_type", "model_name", "version", "weights", "lineage", "saved_at", "metadata")

    def __init__(
        self, game_type: str, model_name: str, version: str,
        weights: Dict[str, Any], lineage: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.game_type = game_type
        self.model_name = model_name
        self.version = version
        self.weights = weights
        self.lineage = lineage
        self.saved_at = time.time()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_type": self.game_type,
            "model_name": self.model_name,
            "version": self.version,
            "lineage": self.lineage,
            "saved_at": self.saved_at,
            "metadata": self.metadata,
            "weight_keys": list(self.weights.keys()),
        }


class CrossGameModelHub:
    """Cross-game model hub with lineage tracking.

    Public API:
        save_model(game_type, model_name, version, weights, lineage, metadata)
        load_model(game_type, model_name, version) -> ModelEntry | None
        load_latest(game_type, model_name) -> ModelEntry | None
        list_models(game_type=None) -> list[dict]
        list_versions(game_type, model_name) -> list[str]
        record_transfer(source_game, target_game, model_name)
        get_transfer_history() -> list[dict]
        get_lineage(game_type, model_name) -> list[dict]
        get_stats() -> dict
    """

    def __init__(self) -> None:
        # (game_type, model_name) → list of ModelEntry (ordered by save time)
        self._store: Dict[Tuple[str, str], List[ModelEntry]] = {}
        self._transfers: List[Dict[str, Any]] = []
        self._save_count: int = 0
        self._load_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def save_model(
        self,
        game_type: str,
        model_name: str,
        version: str,
        weights: Dict[str, Any],
        lineage: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        key = (game_type, model_name)
        if key not in self._store:
            self._store[key] = []

        entry = ModelEntry(
            game_type=game_type,
            model_name=model_name,
            version=version,
            weights=weights,
            lineage=lineage or {"source_game": game_type},
            metadata=metadata,
        )
        self._store[key].append(entry)
        self._save_count += 1
        self._fire("model_saved", {
            "game_type": game_type, "model_name": model_name, "version": version,
        })

    def load_model(
        self, game_type: str, model_name: str, version: str,
    ) -> Optional[ModelEntry]:
        self._load_count += 1
        entries = self._store.get((game_type, model_name), [])
        for e in reversed(entries):
            if e.version == version:
                return e
        return None

    def load_latest(self, game_type: str, model_name: str) -> Optional[ModelEntry]:
        self._load_count += 1
        entries = self._store.get((game_type, model_name), [])
        return entries[-1] if entries else None

    def list_models(self, game_type: Optional[str] = None) -> List[Dict[str, Any]]:
        result = []
        for (gt, mn), entries in self._store.items():
            if game_type is not None and gt != game_type:
                continue
            if entries:
                result.append(entries[-1].to_dict())
        return result

    def list_versions(self, game_type: str, model_name: str) -> List[str]:
        entries = self._store.get((game_type, model_name), [])
        return [e.version for e in entries]

    def record_transfer(
        self, source_game: str, target_game: str, model_name: str,
    ) -> None:
        self._transfers.append({
            "source_game": source_game,
            "target_game": target_game,
            "model_name": model_name,
            "ts": time.time(),
        })
        self._fire("transfer_recorded", {
            "source": source_game, "target": target_game, "model": model_name,
        })

    def get_transfer_history(self) -> List[Dict[str, Any]]:
        return list(self._transfers)

    def get_lineage(self, game_type: str, model_name: str) -> List[Dict[str, Any]]:
        entries = self._store.get((game_type, model_name), [])
        return [{"version": e.version, "lineage": e.lineage, "saved_at": e.saved_at} for e in entries]

    def get_stats(self) -> Dict[str, Any]:
        return {
            "save_count": self._save_count,
            "load_count": self._load_count,
            "total_models": sum(len(v) for v in self._store.values()),
            "unique_models": len(self._store),
            "transfers": len(self._transfers),
            "game_types": list(set(k[0] for k in self._store.keys())),
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

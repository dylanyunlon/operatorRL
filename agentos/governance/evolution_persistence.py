"""
Evolution Persistence — Persistence layer for evolution state snapshots.
Location: agentos/governance/evolution_persistence.py
Reference: DI-star checkpoint saving, ELF model persistence
"""
from __future__ import annotations
import copy, logging, time
from typing import Any, Callable, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.evolution_persistence.v1"

class EvolutionPersistence:
    def __init__(self) -> None:
        self._store: dict[str, dict[str, Any]] = {}
        self._metadata: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def save_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        self._store[key] = copy.deepcopy(snapshot)
        self._metadata[key] = {"saved_at": time.time(), "key": key}
        if self.evolution_callback:
            self.evolution_callback({"type": "snapshot_saved", "key": _EVOLUTION_KEY,
                                     "snapshot_key": key, "timestamp": time.time()})

    def load_snapshot(self, key: str) -> Optional[dict[str, Any]]:
        if key not in self._store:
            return None
        return copy.deepcopy(self._store[key])

    def delete_snapshot(self, key: str) -> None:
        self._store.pop(key, None)
        self._metadata.pop(key, None)

    def list_snapshots(self) -> list[str]:
        return list(self._store.keys())

    def get_metadata(self, key: str) -> dict[str, Any]:
        return dict(self._metadata.get(key, {}))

    def snapshot_count(self) -> int:
        return len(self._store)

    def clear_all(self) -> None:
        self._store.clear()
        self._metadata.clear()

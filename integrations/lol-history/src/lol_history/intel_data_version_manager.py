"""
IntelDataVersionManager — Version control for intel datasets.

Architecture (拿来主义):
  agentos/governance/model_versioner.py — versioning patterns
  cross_game_model_hub.py（M676）— version registry

Location: integrations/lol-history/src/lol_history/intel_data_version_manager.py
"""
from __future__ import annotations
import logging, time, hashlib, json
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_data_version_manager.v1"

class IntelDataVersionManager:
    """Manages versioned intel datasets with snapshot, rollback, and provenance.

    Public API: create_snapshot, get_version, rollback, list_versions,
                diff_versions, get_stats
    """
    def __init__(self, max_versions: int = 50) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._versions: Dict[str, Dict[str, Any]] = {}
        self._version_order: List[str] = []
        self._max_versions = max_versions
        self._current_version: Optional[str] = None

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _hash_data(self, data: Any) -> str:
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def create_snapshot(self, data: Dict[str, Any], label: str = "",
                         metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        version_id = f"v{len(self._version_order)+1}_{self._hash_data(data)}"
        entry = {"version_id": version_id, "data": data, "label": label or version_id,
                 "metadata": metadata or {}, "timestamp": time.time(),
                 "data_hash": self._hash_data(data)}
        self._versions[version_id] = entry
        self._version_order.append(version_id)
        self._current_version = version_id
        if len(self._version_order) > self._max_versions:
            oldest = self._version_order.pop(0)
            del self._versions[oldest]
        self._fire("snapshot_created", {"version": version_id})
        return {"status": "ok", "version_id": version_id, "total_versions": len(self._versions)}

    def get_version(self, version_id: str = "") -> Dict[str, Any]:
        self._op_count += 1
        vid = version_id or self._current_version
        if not vid or vid not in self._versions:
            return {"status": "error", "reason": "version_not_found"}
        return {"status": "ok", **self._versions[vid]}

    def rollback(self, version_id: str) -> Dict[str, Any]:
        self._op_count += 1
        if version_id not in self._versions:
            return {"status": "error", "reason": "version_not_found"}
        self._current_version = version_id
        self._fire("rollback", {"version": version_id})
        return {"status": "ok", "rolled_back_to": version_id}

    def list_versions(self) -> Dict[str, Any]:
        self._op_count += 1
        summaries = []
        for vid in self._version_order:
            v = self._versions[vid]
            summaries.append({"version_id": vid, "label": v["label"],
                              "hash": v["data_hash"], "timestamp": v["timestamp"]})
        return {"status": "ok", "versions": summaries, "current": self._current_version}

    def diff_versions(self, v1: str, v2: str) -> Dict[str, Any]:
        self._op_count += 1
        d1 = self._versions.get(v1, {}).get("data", {})
        d2 = self._versions.get(v2, {}).get("data", {})
        if not d1 or not d2:
            return {"status": "error", "reason": "one_or_both_versions_missing"}
        keys_added = set(d2.keys()) - set(d1.keys())
        keys_removed = set(d1.keys()) - set(d2.keys())
        keys_changed = {k for k in set(d1.keys()) & set(d2.keys()) if d1[k] != d2[k]}
        return {"status": "ok", "v1": v1, "v2": v2,
                "added": list(keys_added), "removed": list(keys_removed),
                "changed": list(keys_changed)}

    def get_stats(self) -> Dict[str, Any]:
        return {"total_versions": len(self._versions), "current": self._current_version,
                "total_ops": self._op_count}

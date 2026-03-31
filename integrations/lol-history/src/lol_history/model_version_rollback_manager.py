"""
ModelVersionRollbackManager — Manages intel model versions with auto-rollback.

Architecture (拿来主义):
  intel_data_version_manager.py（M732）— version management patterns
  intel_ab_test_framework.py（M738）— A/B testing, performance tracking

Location: integrations/lol-history/src/lol_history/model_version_rollback_manager.py

Design Notes (Knuth-level critique):
  User:
    - Models are versioned (v1.0, v1.1, etc.) with metadata and performance history.
    - Auto-rollback when online performance drops below configurable thresholds.
    - Version comparison dashboard: winrate, accuracy, latency per version.
  System:
    - Snapshot-based: each version is a frozen config + weights reference.
    - Performance tracking via sliding window of recent games.
    - Rollback is atomic: switch active version pointer, no partial states.
    - Canary support: new version serves N% of requests before full promotion.
"""
from __future__ import annotations

import copy
import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.model_version_rollback_manager.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _VersionRecord:
    """Immutable record of a model version."""

    def __init__(self, version_id: str, config: Dict[str, Any],
                 created_at: float = None) -> None:
        self.version_id = version_id
        self.config = copy.deepcopy(config)
        self.created_at = created_at or time.monotonic()
        self.promoted_at: Optional[float] = None
        self.rolled_back_at: Optional[float] = None
        self.is_active = False
        self.metadata: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version_id": self.version_id,
            "created_at": self.created_at,
            "promoted_at": self.promoted_at,
            "rolled_back_at": self.rolled_back_at,
            "is_active": self.is_active,
            "config_keys": list(self.config.keys()),
            "metadata": self.metadata,
        }


class _PerformanceTracker:
    """Tracks per-version online performance metrics."""

    def __init__(self, window_size: int = 50) -> None:
        self._window_size = window_size
        self._version_metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._version_aggregates: Dict[str, Dict[str, float]] = {}

    def record(self, version_id: str, metrics: Dict[str, float]) -> None:
        self._version_metrics[version_id].append({
            "ts": time.monotonic(), **metrics,
        })
        self._recompute_aggregates(version_id)

    def _recompute_aggregates(self, version_id: str) -> None:
        records = list(self._version_metrics[version_id])
        if not records:
            return
        agg = {}
        for key in records[0]:
            if key == "ts":
                continue
            values = [r.get(key, 0) for r in records if key in r]
            if values:
                agg[f"{key}_mean"] = sum(values) / len(values)
                agg[f"{key}_min"] = min(values)
                agg[f"{key}_max"] = max(values)
                sorted_vals = sorted(values)
                p95_idx = min(int(len(sorted_vals) * 0.95), len(sorted_vals) - 1)
                agg[f"{key}_p95"] = sorted_vals[p95_idx]
        agg["sample_count"] = len(records)
        self._version_aggregates[version_id] = agg

    def get_aggregates(self, version_id: str) -> Dict[str, Any]:
        return self._version_aggregates.get(version_id, {})

    def get_recent(self, version_id: str, limit: int = 10) -> List[Dict]:
        return list(self._version_metrics[version_id])[-limit:]

    def compare_versions(self, v1: str, v2: str) -> Dict[str, Any]:
        a1 = self._version_aggregates.get(v1, {})
        a2 = self._version_aggregates.get(v2, {})
        comparison = {}
        all_keys = set(list(a1.keys()) + list(a2.keys()))
        for key in all_keys:
            if key.endswith("_mean"):
                val1 = a1.get(key, 0)
                val2 = a2.get(key, 0)
                comparison[key] = {
                    v1: val1, v2: val2,
                    "diff": val1 - val2,
                    "better": v1 if val1 > val2 else v2,
                }
        return comparison

    def get_stats(self) -> Dict[str, Any]:
        return {
            "tracked_versions": len(self._version_metrics),
            "per_version_samples": {k: len(v) for k, v in self._version_metrics.items()},
        }


class _RollbackPolicy:
    """Decides when to trigger automatic rollback."""

    def __init__(self, winrate_threshold: float = 0.40,
                 accuracy_threshold: float = 0.50,
                 latency_threshold_ms: float = 200.0,
                 min_samples: int = 10) -> None:
        self._winrate_threshold = winrate_threshold
        self._accuracy_threshold = accuracy_threshold
        self._latency_threshold = latency_threshold_ms
        self._min_samples = min_samples
        self._trigger_count = 0

    def should_rollback(self, aggregates: Dict[str, Any]) -> Tuple[bool, List[str]]:
        reasons = []
        samples = aggregates.get("sample_count", 0)
        if samples < self._min_samples:
            return False, ["insufficient_samples"]

        wr = aggregates.get("winrate_mean")
        if wr is not None and wr < self._winrate_threshold:
            reasons.append(f"winrate={wr:.3f} < {self._winrate_threshold}")

        acc = aggregates.get("accuracy_mean")
        if acc is not None and acc < self._accuracy_threshold:
            reasons.append(f"accuracy={acc:.3f} < {self._accuracy_threshold}")

        lat = aggregates.get("latency_ms_p95")
        if lat is not None and lat > self._latency_threshold:
            reasons.append(f"latency_p95={lat:.1f}ms > {self._latency_threshold}ms")

        if reasons:
            self._trigger_count += 1
        return len(reasons) > 0, reasons

    def get_stats(self) -> Dict[str, Any]:
        return {
            "winrate_threshold": self._winrate_threshold,
            "accuracy_threshold": self._accuracy_threshold,
            "latency_threshold_ms": self._latency_threshold,
            "min_samples": self._min_samples,
            "trigger_count": self._trigger_count,
        }


class _CanaryController:
    """Controls canary deployment of new versions."""

    def __init__(self, initial_pct: float = 10.0,
                 step_pct: float = 10.0,
                 max_pct: float = 100.0) -> None:
        self._initial_pct = initial_pct
        self._step_pct = step_pct
        self._max_pct = max_pct
        self._active_canary: Optional[str] = None
        self._current_pct = 0.0
        self._promotions = 0

    def start_canary(self, version_id: str) -> Dict[str, Any]:
        self._active_canary = version_id
        self._current_pct = self._initial_pct
        return {
            "canary_version": version_id,
            "traffic_pct": self._current_pct,
        }

    def advance_canary(self) -> Dict[str, Any]:
        if not self._active_canary:
            return {"status": "no_active_canary"}
        self._current_pct = min(self._current_pct + self._step_pct, self._max_pct)
        promoted = self._current_pct >= self._max_pct
        if promoted:
            self._promotions += 1
        return {
            "canary_version": self._active_canary,
            "traffic_pct": self._current_pct,
            "fully_promoted": promoted,
        }

    def cancel_canary(self) -> Dict[str, Any]:
        version = self._active_canary
        self._active_canary = None
        self._current_pct = 0.0
        return {"cancelled_version": version}

    def get_stats(self) -> Dict[str, Any]:
        return {
            "active_canary": self._active_canary,
            "current_pct": self._current_pct,
            "promotions": self._promotions,
        }


class _RollbackHistory:
    """Records rollback events for audit trail."""

    def __init__(self, max_records: int = 100) -> None:
        self._records: deque = deque(maxlen=max_records)

    def record(self, from_version: str, to_version: str,
               reasons: List[str]) -> None:
        self._records.append({
            "ts": time.monotonic(),
            "from": from_version,
            "to": to_version,
            "reasons": reasons,
        })

    def get_recent(self, limit: int = 20) -> List[Dict]:
        return list(self._records)[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        return {"total_rollbacks": len(self._records)}


class ModelVersionRollbackManager:
    """Manages model versions with auto-rollback on performance degradation.

    Public API: register_version, activate_version, record_performance,
                check_rollback_needed, rollback, get_version_history,
                compare_versions, start_canary, advance_canary, get_stats
    """

    def __init__(self, winrate_threshold: float = 0.40,
                 accuracy_threshold: float = 0.50) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._versions: Dict[str, _VersionRecord] = {}
        self._version_order: List[str] = []
        self._active_version: Optional[str] = None
        self._previous_version: Optional[str] = None
        self._tracker = _PerformanceTracker()
        self._policy = _RollbackPolicy(winrate_threshold=winrate_threshold,
                                       accuracy_threshold=accuracy_threshold)
        self._canary = _CanaryController()
        self._rollback_history = _RollbackHistory()

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_version(self, version_id: str,
                         config: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        record = _VersionRecord(version_id, config)
        self._versions[version_id] = record
        self._version_order.append(version_id)

        if self._active_version is None:
            self._active_version = version_id
            record.is_active = True
            record.promoted_at = time.monotonic()

        return {
            "status": "ok",
            "version_id": version_id,
            "total_versions": len(self._versions),
            "is_active": record.is_active,
        }

    def activate_version(self, version_id: str) -> Dict[str, Any]:
        self._op_count += 1
        if version_id not in self._versions:
            return {"status": "error", "reason": "version_not_found"}

        if self._active_version:
            self._versions[self._active_version].is_active = False
            self._previous_version = self._active_version

        record = self._versions[version_id]
        record.is_active = True
        record.promoted_at = time.monotonic()
        self._active_version = version_id

        return {
            "status": "ok",
            "activated": version_id,
            "previous": self._previous_version,
        }

    def record_performance(self, version_id: str,
                           metrics: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._tracker.record(version_id, metrics)
        aggregates = self._tracker.get_aggregates(version_id)
        return {
            "status": "ok",
            "version_id": version_id,
            "aggregates": aggregates,
        }

    def check_rollback_needed(self, version_id: str = None) -> Dict[str, Any]:
        self._op_count += 1
        vid = version_id or self._active_version
        if not vid:
            return {"status": "ok", "rollback_needed": False, "reason": "no_active_version"}

        aggregates = self._tracker.get_aggregates(vid)
        should_rollback, reasons = self._policy.should_rollback(aggregates)

        if should_rollback:
            self._fire("rollback_recommended", {
                "version": vid, "reasons": reasons,
            })

        return {
            "status": "ok",
            "version_id": vid,
            "rollback_needed": should_rollback,
            "reasons": reasons,
            "aggregates": aggregates,
        }

    def rollback(self, target_version: str = None) -> Dict[str, Any]:
        self._op_count += 1
        current = self._active_version
        target = target_version or self._previous_version

        if not target or target not in self._versions:
            return {"status": "error", "reason": "no_rollback_target"}

        self._rollback_history.record(current or "", target, ["manual_or_auto"])
        result = self.activate_version(target)

        if current and current in self._versions:
            self._versions[current].rolled_back_at = time.monotonic()

        self._fire("rollback_executed", {
            "from": current, "to": target,
        })

        return {
            "status": "ok",
            "rolled_back_from": current,
            "rolled_back_to": target,
            **result,
        }

    def get_version_history(self) -> Dict[str, Any]:
        self._op_count += 1
        history = [self._versions[vid].to_dict() for vid in self._version_order
                   if vid in self._versions]
        return {
            "status": "ok",
            "versions": history,
            "active_version": self._active_version,
            "total_versions": len(history),
        }

    def compare_versions(self, v1: str, v2: str) -> Dict[str, Any]:
        self._op_count += 1
        comparison = self._tracker.compare_versions(v1, v2)
        return {"status": "ok", "comparison": comparison}

    def start_canary(self, version_id: str) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", **self._canary.start_canary(version_id)}

    def advance_canary(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", **self._canary.advance_canary()}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "total_versions": len(self._versions),
            "active_version": self._active_version,
            "previous_version": self._previous_version,
            "tracker": self._tracker.get_stats(),
            "policy": self._policy.get_stats(),
            "canary": self._canary.get_stats(),
            "rollback_history": self._rollback_history.get_stats(),
        }

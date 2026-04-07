"""
modules/common/health_aggregator.py — Unified Component Health Aggregation
============================================================================
lolbot-HyperAI · modules/common

查看 modules/common/component_base.py 上现有 ComponentRegistry 的 health_summary()
实现方式, 理解其模式, 特别是各组件的 LifecycleState 是如何收集的。从
ComponentRegistry 这个好例子开始。然后, 遵循该模式实现一个新的
HealthAggregator, 让 MonitorComponent 可以通过单一入口获得全系统健康摘要,
并能检测跨组件的级联故障 (如 canbus ERROR 导致 perception 数据饥饿)。

Architecture position:
    modules/common/health_aggregator.py   ← YOU ARE HERE
    ├─ Reads: component_base.py (ComponentRegistry)
    ├─ Reads: cyber/transport/backpressure.py (BackpressureRegistry)
    ├─ Reads: runtime/health_monitor.py (system resource health)
    ├─ Used by: modules/monitor/monitor_component.py (Proc())
    ├─ Used by: modules/dreamview/dashboard/ (websocket push)
    └─ Used by: launch/main_loop.py (supervisor tick)

Apollo reference:
    modules/monitor/hardware/resource_monitor.cc
    modules/monitor/software/channel_monitor.cc

Design notes:
    - Aggregates component lifecycle, Proc() latency, channel health
    - Detects cascade failures: if upstream ERROR → downstream DEGRADED
    - Health scoring: 0.0 (dead) → 1.0 (perfect)
    - History: keeps last N snapshots for trend analysis
    - Thread-safe: called from supervisor thread + dashboard thread
"""

from __future__ import annotations

import enum
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("health.aggregator")

# ─── Constants ───────────────────────────────────────────────────────────────

_HISTORY_SIZE = 120               # 2 minutes at 1Hz
_STALE_THRESHOLD_S = 10.0         # component considered stale if no proc in 10s
_CASCADE_DEPENDENCY_MAP = {
    # Downstream → set of upstream dependencies
    "perception": {"canbus"},
    "prediction": {"perception"},
    "planning":   {"perception", "prediction"},
    "control":    {"planning", "prediction"},
}
_SCORE_WEIGHTS = {
    "lifecycle": 0.30,
    "proc_success": 0.25,
    "proc_latency": 0.20,
    "channel_health": 0.15,
    "resource": 0.10,
}


# ─── Health Level ────────────────────────────────────────────────────────────

class HealthLevel(enum.Enum):
    """System-wide health assessment."""
    HEALTHY = "healthy"           # All components running, score >= 0.8
    DEGRADED = "degraded"         # Some components struggling, 0.5 <= score < 0.8
    UNHEALTHY = "unhealthy"       # Critical component down, score < 0.5
    UNKNOWN = "unknown"           # Not enough data yet


# ─── Component Health Snapshot ───────────────────────────────────────────────

@dataclass
class ComponentHealthSnapshot:
    """Health state of a single component at a point in time."""
    name: str
    lifecycle_state: str = "unknown"
    is_alive: bool = False
    proc_success_rate: float = 0.0
    proc_latency_p95_ms: float = 0.0
    proc_count: int = 0
    last_proc_ts: float = 0.0
    is_stale: bool = False
    error_message: str = ""
    cascade_affected: bool = False
    health_score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.lifecycle_state,
            "alive": self.is_alive,
            "proc_success_rate": round(self.proc_success_rate, 4),
            "proc_latency_p95_ms": round(self.proc_latency_p95_ms, 2),
            "proc_count": self.proc_count,
            "stale": self.is_stale,
            "cascade_affected": self.cascade_affected,
            "health_score": round(self.health_score, 4),
            "error": self.error_message,
        }


# ─── System Health Snapshot ──────────────────────────────────────────────────

@dataclass
class SystemHealthSnapshot:
    """Full system health at a point in time."""
    timestamp: float = 0.0
    level: HealthLevel = HealthLevel.UNKNOWN
    overall_score: float = 0.0
    components: Dict[str, ComponentHealthSnapshot] = field(default_factory=dict)
    cascade_failures: List[Dict[str, Any]] = field(default_factory=list)
    channel_warnings: List[str] = field(default_factory=list)
    resource_warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": round(self.timestamp, 3),
            "level": self.level.value,
            "overall_score": round(self.overall_score, 4),
            "components": {
                n: c.to_dict() for n, c in self.components.items()
            },
            "cascade_failures": self.cascade_failures,
            "channel_warnings": self.channel_warnings,
            "resource_warnings": self.resource_warnings,
        }


# ─── Health Aggregator ───────────────────────────────────────────────────────

class HealthAggregator:
    """Aggregates health from all system components into a unified view.

    Called periodically by MonitorComponent.Proc() or by the main loop
    supervisor tick to produce a SystemHealthSnapshot.

    Features:
    1. Lifecycle state tracking per component
    2. Proc() success rate and latency analysis
    3. Cascade failure detection (upstream → downstream impact)
    4. Channel backpressure health integration
    5. Historical trend tracking for dashboard graphs

    Usage::

        aggregator = HealthAggregator()
        aggregator.set_component_registry(ComponentRegistry.instance())
        aggregator.set_backpressure_registry(BackpressureRegistry.instance())

        snapshot = aggregator.collect()
        if snapshot.level == HealthLevel.UNHEALTHY:
            logger.critical("System unhealthy: %s", snapshot.to_dict())
    """

    def __init__(
        self,
        stale_threshold_s: float = _STALE_THRESHOLD_S,
        history_size: int = _HISTORY_SIZE,
    ) -> None:
        self._stale_threshold_s = stale_threshold_s
        self._history: Deque[SystemHealthSnapshot] = deque(maxlen=history_size)
        self._lock = threading.Lock()

        # External registries (set after construction)
        self._component_registry: Any = None
        self._backpressure_registry: Any = None
        self._resource_monitor: Any = None

        # Cascade dependency map (can be customized)
        self._dependency_map: Dict[str, Set[str]] = dict(
            _CASCADE_DEPENDENCY_MAP
        )

    def set_component_registry(self, registry: Any) -> None:
        """Set the ComponentRegistry to read component health from."""
        self._component_registry = registry

    def set_backpressure_registry(self, registry: Any) -> None:
        """Set the BackpressureRegistry to read channel health from."""
        self._backpressure_registry = registry

    def set_resource_monitor(self, monitor: Any) -> None:
        """Set the runtime resource monitor (CPU, memory, etc.)."""
        self._resource_monitor = monitor

    def collect(self) -> SystemHealthSnapshot:
        """Collect a full system health snapshot.

        This is the main entry point. It reads from all registered
        sources, computes per-component scores, detects cascades,
        and produces a SystemHealthSnapshot.

        Thread-safe: can be called from any thread.
        """
        now = time.monotonic()
        snapshot = SystemHealthSnapshot(timestamp=now)

        # 1. Collect per-component health
        comp_snapshots = self._collect_component_health(now)
        snapshot.components = comp_snapshots

        # 2. Detect cascade failures
        cascades = self._detect_cascades(comp_snapshots)
        snapshot.cascade_failures = cascades

        # Mark cascade-affected components
        for cascade in cascades:
            affected = cascade.get("affected", "")
            if affected in comp_snapshots:
                comp_snapshots[affected].cascade_affected = True

        # 3. Collect channel health
        channel_warnings = self._collect_channel_health()
        snapshot.channel_warnings = channel_warnings

        # 4. Collect resource health
        resource_warnings = self._collect_resource_health()
        snapshot.resource_warnings = resource_warnings

        # 5. Compute overall score
        overall = self._compute_overall_score(
            comp_snapshots, channel_warnings, resource_warnings,
        )
        snapshot.overall_score = overall

        # 6. Determine health level
        if overall >= 0.8:
            snapshot.level = HealthLevel.HEALTHY
        elif overall >= 0.5:
            snapshot.level = HealthLevel.DEGRADED
        elif len(comp_snapshots) > 0:
            snapshot.level = HealthLevel.UNHEALTHY
        else:
            snapshot.level = HealthLevel.UNKNOWN

        # 7. Store in history
        with self._lock:
            self._history.append(snapshot)

        return snapshot

    def latest(self) -> Optional[SystemHealthSnapshot]:
        """Get the most recent snapshot without collecting new data."""
        with self._lock:
            if self._history:
                return self._history[-1]
        return None

    def history(self, count: int = 60) -> List[SystemHealthSnapshot]:
        """Get the last N health snapshots for trend analysis."""
        with self._lock:
            n = min(count, len(self._history))
            return list(self._history)[-n:]

    def score_trend(self, count: int = 30) -> List[float]:
        """Get overall score trend for sparkline display."""
        hist = self.history(count)
        return [s.overall_score for s in hist]

    def is_healthy(self) -> bool:
        """Quick check: is the system currently healthy?"""
        snap = self.latest()
        if snap is None:
            return False
        return snap.level == HealthLevel.HEALTHY

    # ── Private collection methods ───────────────────────────────────────

    def _collect_component_health(
        self, now: float,
    ) -> Dict[str, ComponentHealthSnapshot]:
        """Read health from ComponentRegistry."""
        results: Dict[str, ComponentHealthSnapshot] = {}

        if self._component_registry is None:
            return results

        try:
            all_components = self._component_registry.all()
        except Exception:
            logger.warning(
                "Could not read ComponentRegistry", exc_info=True,
            )
            return results

        for name, comp in all_components.items():
            snap = ComponentHealthSnapshot(name=name)

            # Lifecycle state
            try:
                state = getattr(comp, "_lifecycle_state", None)
                if state is not None:
                    snap.lifecycle_state = (
                        state.value if hasattr(state, "value") else str(state)
                    )
                    snap.is_alive = snap.lifecycle_state in (
                        "running", "degraded",
                    )
            except Exception:
                snap.lifecycle_state = "unknown"

            # Proc metrics (from ManagedComponent._proc_metrics)
            try:
                metrics = getattr(comp, "_proc_metrics", None)
                if metrics is not None:
                    snap.proc_count = getattr(
                        metrics, "total_calls", 0
                    )
                    total = getattr(metrics, "total_calls", 0)
                    failures = getattr(metrics, "total_failures", 0)
                    if total > 0:
                        snap.proc_success_rate = (
                            (total - failures) / total
                        )
                    p95 = getattr(metrics, "p95", None)
                    if callable(p95):
                        snap.proc_latency_p95_ms = p95()
                    elif isinstance(p95, (int, float)):
                        snap.proc_latency_p95_ms = p95
            except Exception:
                pass

            # Staleness check
            try:
                last_ts = getattr(comp, "_last_proc_ts", 0.0)
                if last_ts > 0:
                    snap.last_proc_ts = last_ts
                    snap.is_stale = (now - last_ts) > self._stale_threshold_s
            except Exception:
                pass

            # Error message
            try:
                last_error = getattr(comp, "_last_error", None)
                if last_error:
                    snap.error_message = str(last_error)[:200]
            except Exception:
                pass

            # Compute per-component score
            snap.health_score = self._score_component(snap)
            results[name] = snap

        return results

    def _detect_cascades(
        self,
        components: Dict[str, ComponentHealthSnapshot],
    ) -> List[Dict[str, Any]]:
        """Detect cascade failures from upstream to downstream.

        If canbus is in ERROR state, perception is cascade-affected.
        If perception is stale, prediction/planning are cascade-affected.
        """
        cascades: List[Dict[str, Any]] = []

        for downstream, upstreams in self._dependency_map.items():
            if downstream not in components:
                continue

            for upstream in upstreams:
                if upstream not in components:
                    continue

                up_snap = components[upstream]
                if (not up_snap.is_alive
                        or up_snap.is_stale
                        or up_snap.health_score < 0.3):
                    cascades.append({
                        "upstream": upstream,
                        "upstream_state": up_snap.lifecycle_state,
                        "upstream_score": up_snap.health_score,
                        "affected": downstream,
                        "reason": (
                            "stale" if up_snap.is_stale
                            else "unhealthy" if up_snap.health_score < 0.3
                            else "not_alive"
                        ),
                    })

        return cascades

    def _collect_channel_health(self) -> List[str]:
        """Check backpressure status on all channels."""
        warnings: List[str] = []

        if self._backpressure_registry is None:
            return warnings

        try:
            summary = self._backpressure_registry.summary()
            for channel, metrics in summary.items():
                level = metrics.get("level", "normal")
                if level in ("throttle", "critical"):
                    fill = metrics.get("fill_pct", 0)
                    dropped = metrics.get("total_dropped", 0)
                    warnings.append(
                        f"{channel}: {level} ({fill:.0%} full, "
                        f"{dropped} dropped)"
                    )
        except Exception:
            logger.debug("Could not read backpressure", exc_info=True)

        return warnings

    def _collect_resource_health(self) -> List[str]:
        """Check system resource health (CPU, memory, disk)."""
        warnings: List[str] = []

        if self._resource_monitor is None:
            return warnings

        try:
            if hasattr(self._resource_monitor, "check_resources"):
                alerts = self._resource_monitor.check_resources()
                if isinstance(alerts, list):
                    warnings.extend(str(a) for a in alerts[:5])
        except Exception:
            pass

        return warnings

    def _score_component(self, snap: ComponentHealthSnapshot) -> float:
        """Compute 0.0-1.0 health score for a single component."""
        scores: Dict[str, float] = {}

        # Lifecycle: running=1.0, degraded=0.6, other=0.0
        state_scores = {
            "running": 1.0, "degraded": 0.6, "ready": 0.8,
            "initializing": 0.3, "stopping": 0.2,
        }
        scores["lifecycle"] = state_scores.get(snap.lifecycle_state, 0.0)

        # Proc success rate: direct mapping
        scores["proc_success"] = snap.proc_success_rate

        # Latency: <50ms=1.0, 50-200ms=0.7, 200-500ms=0.3, >500ms=0.0
        lat = snap.proc_latency_p95_ms
        if lat <= 0 and snap.proc_count == 0:
            scores["proc_latency"] = 0.5  # no data yet
        elif lat <= 50:
            scores["proc_latency"] = 1.0
        elif lat <= 200:
            scores["proc_latency"] = 0.7
        elif lat <= 500:
            scores["proc_latency"] = 0.3
        else:
            scores["proc_latency"] = 0.0

        # Staleness penalty
        if snap.is_stale:
            scores["lifecycle"] *= 0.3

        # Weighted average
        total = 0.0
        weight_sum = 0.0
        for key in ("lifecycle", "proc_success", "proc_latency"):
            w = _SCORE_WEIGHTS.get(key, 0.0)
            total += scores.get(key, 0.0) * w
            weight_sum += w

        if weight_sum > 0:
            return round(total / weight_sum, 4)
        return 0.0

    def _compute_overall_score(
        self,
        components: Dict[str, ComponentHealthSnapshot],
        channel_warnings: List[str],
        resource_warnings: List[str],
    ) -> float:
        """Compute system-wide health score."""
        if not components:
            return 0.0

        # Average component scores
        comp_scores = [c.health_score for c in components.values()]
        avg_comp = sum(comp_scores) / len(comp_scores) if comp_scores else 0.0

        # Channel penalty (each warning reduces score)
        channel_penalty = min(0.2, len(channel_warnings) * 0.05)

        # Resource penalty
        resource_penalty = min(0.15, len(resource_warnings) * 0.05)

        # Minimum component score drags the system down
        min_comp = min(comp_scores) if comp_scores else 0.0
        floor_drag = max(0, 0.3 - min_comp) * 0.5

        score = avg_comp - channel_penalty - resource_penalty - floor_drag
        return round(max(0.0, min(1.0, score)), 4)

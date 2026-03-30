"""
Latency Monitor — Inference pipeline latency tracking and alerting.

Tracks per-stage latency (feature fetch, model forward, action sample,
decision output), computes percentile statistics, and fires alerts
when latency exceeds configurable thresholds.

Location: agentlightning/inference/latency_monitor.py

Reference (拿来主义):
  查看 agentos/governance/evolution_metrics_exporter.py 上现有指标导出方式,
  理解其模式, 特别是指标采集(evolution_callback事件)如何与指标展示分离。
  从 agentos/governance/fitness_aggregator.py 这个好例子开始 — 它的
  report→aggregate→get_history→get_trend 四步展示了指标从采集到汇总到
  趋势分析的完整链路。
  遵循该模式实现 LatencyMonitor, 让推理管线的每个阶段可以上报延迟数据,
  并能在延迟超过阈值时自动触发降级策略(如切换到规则引擎兜底).

Design Notes (Knuth-level critique):
  User:
    - Percentile stats (p50/p95/p99) more meaningful than mean for latency
    - Alert callbacks enable automatic degradation without manual watch
    - Per-stage breakdown helps pinpoint bottleneck
  System:
    - Circular buffer bounds memory; O(n log n) percentile on demand
    - Alert dedup interval prevents callback storm
    - Thread-safe for concurrent stage reporting
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.latency_monitor.v1"

_DEFAULT_WINDOW_SIZE: int = 1000
_DEFAULT_ALERT_COOLDOWN: float = 30.0  # seconds between repeated alerts


class StageStats:
    """Per-stage latency statistics accumulator.

    Maintains a circular buffer of latency samples and computes
    descriptive statistics on demand.
    """

    __slots__ = ("name", "_samples", "_total", "_count", "_max", "_min")

    def __init__(self, name: str, window_size: int = _DEFAULT_WINDOW_SIZE) -> None:
        self.name = name
        self._samples: Deque[float] = deque(maxlen=window_size)
        self._total: float = 0.0
        self._count: int = 0
        self._max: float = 0.0
        self._min: float = float("inf")

    def record(self, latency_ms: float) -> None:
        """Record a latency sample in milliseconds."""
        self._samples.append(latency_ms)
        self._total += latency_ms
        self._count += 1
        if latency_ms > self._max:
            self._max = latency_ms
        if latency_ms < self._min:
            self._min = latency_ms

    def mean(self) -> float:
        """Arithmetic mean of samples in window."""
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    def percentile(self, p: float) -> float:
        """Compute p-th percentile (0-100).

        Args:
            p: Percentile value (e.g., 95.0 for p95).

        Returns:
            Latency at the p-th percentile.
        """
        if not self._samples:
            return 0.0
        sorted_s = sorted(self._samples)
        idx = int(math.ceil(p / 100.0 * len(sorted_s))) - 1
        idx = max(0, min(idx, len(sorted_s) - 1))
        return sorted_s[idx]

    def summary(self) -> Dict[str, Any]:
        """Compute full statistics summary."""
        return {
            "stage": self.name,
            "count": self._count,
            "window_size": len(self._samples),
            "mean_ms": round(self.mean(), 3),
            "p50_ms": round(self.percentile(50), 3),
            "p95_ms": round(self.percentile(95), 3),
            "p99_ms": round(self.percentile(99), 3),
            "max_ms": round(self._max, 3) if self._count > 0 else 0.0,
            "min_ms": round(self._min, 3) if self._count > 0 else 0.0,
        }

    def reset(self) -> None:
        """Clear all samples."""
        self._samples.clear()
        self._total = 0.0
        self._count = 0
        self._max = 0.0
        self._min = float("inf")


class LatencyMonitor:
    """Inference pipeline latency monitor.

    Tracks per-stage latencies, computes statistics, and fires
    alerts when thresholds are breached.

    Attributes:
        window_size: Sample window per stage.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        alert_cooldown: float = _DEFAULT_ALERT_COOLDOWN,
    ) -> None:
        self.window_size = window_size
        self.alert_cooldown = alert_cooldown
        self._stages: Dict[str, StageStats] = {}
        self._thresholds: Dict[str, float] = {}  # stage → max_ms
        self._alert_callbacks: List[Callable[[str, float, float], None]] = []
        self._last_alert_time: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._breach_count: Dict[str, int] = defaultdict(int)
        self._total_records: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Stage Management ---

    def register_stage(
        self,
        stage: str,
        threshold_ms: Optional[float] = None,
    ) -> None:
        """Register an inference pipeline stage.

        Args:
            stage: Stage name (e.g., "feature_fetch", "model_forward").
            threshold_ms: Optional alert threshold in milliseconds.
        """
        with self._lock:
            if stage not in self._stages:
                self._stages[stage] = StageStats(stage, self.window_size)
            if threshold_ms is not None:
                self._thresholds[stage] = threshold_ms

    def set_threshold(self, stage: str, threshold_ms: float) -> None:
        """Set or update alert threshold for a stage.

        Args:
            stage: Stage name.
            threshold_ms: Alert threshold in milliseconds.
        """
        self._thresholds[stage] = threshold_ms

    # --- Recording ---

    def record(self, stage: str, latency_ms: float) -> None:
        """Record a latency observation.

        Args:
            stage: Stage name.
            latency_ms: Measured latency in milliseconds.
        """
        with self._lock:
            if stage not in self._stages:
                self._stages[stage] = StageStats(stage, self.window_size)
            self._stages[stage].record(latency_ms)
            self._total_records += 1

        # Check threshold
        threshold = self._thresholds.get(stage)
        if threshold is not None and latency_ms > threshold:
            self._handle_breach(stage, latency_ms, threshold)

    def record_pipeline(self, stage_latencies: Dict[str, float]) -> float:
        """Record latencies for multiple stages in one call.

        Args:
            stage_latencies: Dict of stage → latency_ms.

        Returns:
            Total pipeline latency.
        """
        total = 0.0
        for stage, latency in stage_latencies.items():
            self.record(stage, latency)
            total += latency
        return total

    # --- Context Manager for Timing ---

    def time_stage(self, stage: str) -> "_StageTimer":
        """Context manager to automatically time a stage.

        Usage:
            with monitor.time_stage("model_forward"):
                result = model.forward(input)
        """
        return _StageTimer(self, stage)

    # --- Alerts ---

    def add_alert_callback(
        self, callback: Callable[[str, float, float], None]
    ) -> None:
        """Register an alert callback.

        Args:
            callback: Callable(stage, latency_ms, threshold_ms).
        """
        self._alert_callbacks.append(callback)

    def get_breach_counts(self) -> Dict[str, int]:
        """Get number of threshold breaches per stage."""
        return dict(self._breach_count)

    # --- Statistics ---

    def get_stage_stats(self, stage: str) -> Dict[str, Any]:
        """Get statistics for a specific stage.

        Args:
            stage: Stage name.

        Returns:
            Statistics dict.

        Raises:
            KeyError: If stage not found.
        """
        with self._lock:
            if stage not in self._stages:
                raise KeyError(f"Stage '{stage}' not found")
            return self._stages[stage].summary()

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all stages.

        Returns:
            Dict of stage → stats dict.
        """
        with self._lock:
            return {name: stats.summary() for name, stats in self._stages.items()}

    def get_pipeline_summary(self) -> Dict[str, Any]:
        """Get overall pipeline summary.

        Returns:
            Dict with per-stage stats, total latency estimates,
            breach counts, and total records.
        """
        all_stats = self.get_all_stats()
        total_p50 = sum(s.get("p50_ms", 0) for s in all_stats.values())
        total_p95 = sum(s.get("p95_ms", 0) for s in all_stats.values())
        total_p99 = sum(s.get("p99_ms", 0) for s in all_stats.values())
        return {
            "stages": all_stats,
            "pipeline_p50_ms": round(total_p50, 3),
            "pipeline_p95_ms": round(total_p95, 3),
            "pipeline_p99_ms": round(total_p99, 3),
            "total_records": self._total_records,
            "breach_counts": dict(self._breach_count),
        }

    def stages(self) -> List[str]:
        """List registered stage names."""
        with self._lock:
            return list(self._stages.keys())

    # --- Maintenance ---

    def reset(self, stage: Optional[str] = None) -> None:
        """Reset statistics.

        Args:
            stage: If specified, reset only that stage. Otherwise reset all.
        """
        with self._lock:
            if stage is not None:
                if stage in self._stages:
                    self._stages[stage].reset()
                self._breach_count.pop(stage, None)
            else:
                for s in self._stages.values():
                    s.reset()
                self._breach_count.clear()
                self._total_records = 0

    def is_healthy(self, max_p95_ms: float = 100.0) -> bool:
        """Check if all stages are within acceptable latency.

        Args:
            max_p95_ms: Maximum acceptable p95 latency per stage.

        Returns:
            True if all stages have p95 below threshold.
        """
        with self._lock:
            for stats in self._stages.values():
                if stats.percentile(95) > max_p95_ms:
                    return False
        return True

    # --- Internal ---

    def _handle_breach(
        self, stage: str, latency_ms: float, threshold: float
    ) -> None:
        """Handle a threshold breach. Fire alerts with cooldown."""
        now = time.time()
        last = self._last_alert_time.get(stage, 0.0)
        self._breach_count[stage] = self._breach_count.get(stage, 0) + 1

        if (now - last) < self.alert_cooldown:
            return  # cooldown active

        self._last_alert_time[stage] = now
        for cb in self._alert_callbacks:
            try:
                cb(stage, latency_ms, threshold)
            except Exception as exc:
                logger.warning("Alert callback error: %s", exc)

        self._fire_evolution("latency_breach", {
            "stage": stage,
            "latency_ms": latency_ms,
            "threshold_ms": threshold,
            "breach_count": self._breach_count[stage],
        })

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)


class _StageTimer:
    """Context manager for timing a pipeline stage."""

    def __init__(self, monitor: LatencyMonitor, stage: str) -> None:
        self._monitor = monitor
        self._stage = stage
        self._start: float = 0.0

    def __enter__(self) -> "_StageTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000.0
        self._monitor.record(self._stage, elapsed_ms)

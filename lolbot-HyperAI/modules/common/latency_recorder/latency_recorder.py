"""
LatencyRecorder — End-to-end pipeline latency tracking.
=========================================================
lolbot-HyperAI · modules/common/latency_recorder

查看 Apollo ``modules/common/latency_recorder/latency_recorder.cc`` 上现有
``LatencyRecorder`` 的实现方式, 理解其模式, 特别是每条消息带 header
timestamp、每个组件在 Proc() 记录自身处理耗时、最终由 LatencyRecorder
汇总全链路延迟的设计。从 Apollo LatencyRecorder 这个好例子开始。然后,
遵循该模式实现一个新的 ``LatencyRecorder``, 让每个 TimerComponent 可以
记录自身 Proc() 延迟, 并能通过 ``PipelineLatencyTracker`` 汇总全链路
延迟。接着在 ``LatencyRecord`` 引入 per-component 时间戳链, 使诊断工具
能够定位瓶颈环节, 同时优化内存占用 (bounded deque)。随后整合
``to_dict()`` 导出, 令 Dreamview 仪表盘支持延迟可视化, 进而增强
pipeline 可观测性。最终完善 ``percentile()`` 统计, 确保延迟指标兼容
Apollo 的 ``LatencyRecorderConfig`` 配置方式, 全面系统性升级监控质量。

位置: lolbot-HyperAI/modules/common/latency_recorder/latency_recorder.py

Apollo reference:
    modules/common/latency_recorder/latency_recorder.h — class def
    modules/common/latency_recorder/latency_recorder.cc — impl
"""

from __future__ import annotations

import bisect
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_DEFAULT_HISTORY_SIZE: int = 2000
_PIPELINE_STAGES: Tuple[str, ...] = (
    "canbus",
    "perception",
    "prediction",
    "planning",
    "control",
)


# ── LatencyRecord ────────────────────────────────────────────────────────────

@dataclass
class LatencyRecord:
    """A single message's journey through the pipeline.

    Apollo equivalent: ``LatencyRecord`` struct in latency_recorder.h.
    Each component stamps its arrival/departure time; the recorder
    computes per-stage and total latency.
    """

    message_id: int = 0
    # Per-stage: (stage_name, enter_us, exit_us)
    stamps: List[Tuple[str, float, float]] = field(default_factory=list)
    # Wall-clock time when the record was created
    creation_time: float = 0.0

    def stamp_enter(self, stage: str) -> None:
        """Record entry timestamp for a pipeline stage."""
        self.stamps.append((stage, time.monotonic(), 0.0))

    def stamp_exit(self, stage: str) -> None:
        """Record exit timestamp for a pipeline stage."""
        for i in range(len(self.stamps) - 1, -1, -1):
            if self.stamps[i][0] == stage and self.stamps[i][2] == 0.0:
                self.stamps[i] = (
                    stage,
                    self.stamps[i][1],
                    time.monotonic(),
                )
                return
        # Stage not found — record as standalone exit
        self.stamps.append((stage, 0.0, time.monotonic()))

    def stage_latency_ms(self, stage: str) -> Optional[float]:
        """Get latency for a specific stage in milliseconds."""
        for name, enter_t, exit_t in self.stamps:
            if name == stage and enter_t > 0 and exit_t > 0:
                return (exit_t - enter_t) * 1000.0
        return None

    def total_latency_ms(self) -> Optional[float]:
        """Total pipeline latency from first entry to last exit."""
        if not self.stamps:
            return None
        first_enter = min(
            (s[1] for s in self.stamps if s[1] > 0), default=0.0
        )
        last_exit = max(
            (s[2] for s in self.stamps if s[2] > 0), default=0.0
        )
        if first_enter <= 0 or last_exit <= 0:
            return None
        return (last_exit - first_enter) * 1000.0

    def to_dict(self) -> Dict[str, Any]:
        """Export for diagnostics / Dreamview dashboard."""
        stages = {}
        for name, enter_t, exit_t in self.stamps:
            if enter_t > 0 and exit_t > 0:
                stages[name] = round((exit_t - enter_t) * 1000.0, 3)
        return {
            "message_id": self.message_id,
            "stages_ms": stages,
            "total_ms": round(self.total_latency_ms() or 0.0, 3),
            "creation_time": self.creation_time,
        }


# ── LatencyRecorder ──────────────────────────────────────────────────────────

class LatencyRecorder:
    """Per-component latency recorder.

    Apollo equivalent: ``LatencyRecorder`` class in latency_recorder.cc.
    Each component creates its own LatencyRecorder instance, records
    Proc() latency, and contributes stamps to PipelineLatencyTracker.

    Usage::

        recorder = LatencyRecorder("perception", history_size=1000)
        recorder.record(latency_ms=5.2)
        p95 = recorder.percentile(0.95)
        snap = recorder.snapshot()
    """

    def __init__(
        self,
        component_name: str,
        history_size: int = _DEFAULT_HISTORY_SIZE,
    ) -> None:
        self._name = component_name
        self._history: Deque[float] = deque(maxlen=history_size)
        self._sorted_cache: List[float] = []
        self._cache_dirty: bool = False
        self._total_count: int = 0
        self._total_sum_ms: float = 0.0
        self._max_ms: float = 0.0
        self._min_ms: float = float("inf")
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    def record(self, latency_ms: float) -> None:
        """Record a single Proc() latency measurement."""
        with self._lock:
            self._history.append(latency_ms)
            self._cache_dirty = True
            self._total_count += 1
            self._total_sum_ms += latency_ms
            if latency_ms > self._max_ms:
                self._max_ms = latency_ms
            if latency_ms < self._min_ms:
                self._min_ms = latency_ms

    def _rebuild_cache(self) -> None:
        """Rebuild sorted cache for percentile queries."""
        if self._cache_dirty:
            self._sorted_cache = sorted(self._history)
            self._cache_dirty = False

    def percentile(self, p: float) -> float:
        """Compute the p-th percentile (0.0–1.0) of recent latencies."""
        with self._lock:
            self._rebuild_cache()
            if not self._sorted_cache:
                return 0.0
            idx = min(
                int(p * len(self._sorted_cache)),
                len(self._sorted_cache) - 1,
            )
            return self._sorted_cache[idx]

    def p50(self) -> float:
        return self.percentile(0.50)

    def p95(self) -> float:
        return self.percentile(0.95)

    def p99(self) -> float:
        return self.percentile(0.99)

    def mean(self) -> float:
        with self._lock:
            if not self._history:
                return 0.0
            return sum(self._history) / len(self._history)

    def count(self) -> int:
        return self._total_count

    def snapshot(self) -> Dict[str, Any]:
        """Export recorder statistics."""
        with self._lock:
            self._rebuild_cache()
            n = len(self._sorted_cache)
            return {
                "component": self._name,
                "count": self._total_count,
                "recent_count": n,
                "mean_ms": round(
                    sum(self._history) / n if n else 0.0, 3
                ),
                "p50_ms": round(self.percentile(0.50), 3),
                "p95_ms": round(self.percentile(0.95), 3),
                "p99_ms": round(self.percentile(0.99), 3),
                "max_ms": round(self._max_ms, 3) if self._max_ms > 0 else 0.0,
                "min_ms": (
                    round(self._min_ms, 3)
                    if self._min_ms < float("inf")
                    else 0.0
                ),
            }

    def reset(self) -> None:
        """Reset all statistics."""
        with self._lock:
            self._history.clear()
            self._sorted_cache.clear()
            self._cache_dirty = False
            self._total_count = 0
            self._total_sum_ms = 0.0
            self._max_ms = 0.0
            self._min_ms = float("inf")


# ── PipelineLatencyTracker ───────────────────────────────────────────────────

class PipelineLatencyTracker:
    """Tracks end-to-end pipeline latency across all stages.

    Apollo equivalent: the aggregation logic in LatencyRecorder that
    computes total pipeline delay from perception → planning.

    Thread-safe singleton. Components call ``stamp_enter``/``stamp_exit``
    to record their contributions.

    Usage::

        tracker = PipelineLatencyTracker.instance()
        msg_id = tracker.begin_message()          # canbus creates record
        tracker.stamp_enter(msg_id, "perception")
        ...
        tracker.stamp_exit(msg_id, "perception")
        tracker.stamp_enter(msg_id, "prediction")
        ...
        summary = tracker.summary()
    """

    _instance: Optional[PipelineLatencyTracker] = None
    _lock_cls = threading.Lock()

    def __init__(self, history_size: int = 500) -> None:
        self._records: Dict[int, LatencyRecord] = {}
        self._completed: Deque[LatencyRecord] = deque(maxlen=history_size)
        self._next_id: int = 0
        self._lock = threading.Lock()
        self._history_size = history_size

    @classmethod
    def instance(cls) -> PipelineLatencyTracker:
        if cls._instance is None:
            with cls._lock_cls:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock_cls:
            cls._instance = None

    def begin_message(self) -> int:
        """Create a new latency record for a pipeline message.

        Called by canbus when it produces a new data frame.
        Returns a message_id for tracking.
        """
        with self._lock:
            msg_id = self._next_id
            self._next_id += 1
            record = LatencyRecord(
                message_id=msg_id,
                creation_time=time.time(),
            )
            self._records[msg_id] = record
            # Evict old incomplete records to prevent memory leak
            if len(self._records) > self._history_size * 2:
                oldest_ids = sorted(self._records.keys())[
                    : len(self._records) - self._history_size
                ]
                for old_id in oldest_ids:
                    self._records.pop(old_id, None)
            return msg_id

    def stamp_enter(self, message_id: int, stage: str) -> None:
        """Record entry into a pipeline stage."""
        with self._lock:
            record = self._records.get(message_id)
            if record:
                record.stamp_enter(stage)

    def stamp_exit(self, message_id: int, stage: str) -> None:
        """Record exit from a pipeline stage."""
        with self._lock:
            record = self._records.get(message_id)
            if record:
                record.stamp_exit(stage)

    def complete_message(self, message_id: int) -> Optional[LatencyRecord]:
        """Mark a message as complete and move to completed history."""
        with self._lock:
            record = self._records.pop(message_id, None)
            if record:
                self._completed.append(record)
            return record

    def summary(self) -> Dict[str, Any]:
        """Generate pipeline latency summary.

        Returns per-stage P50/P95/P99 and total pipeline latency stats.
        """
        with self._lock:
            if not self._completed:
                return {"total_messages": 0, "stages": {}, "pipeline": {}}

            stage_latencies: Dict[str, List[float]] = {}
            total_latencies: List[float] = []

            for record in self._completed:
                total = record.total_latency_ms()
                if total is not None:
                    total_latencies.append(total)
                for stage_name in _PIPELINE_STAGES:
                    lat = record.stage_latency_ms(stage_name)
                    if lat is not None:
                        stage_latencies.setdefault(stage_name, []).append(lat)

            def _stats(values: List[float]) -> Dict[str, float]:
                if not values:
                    return {"p50": 0, "p95": 0, "p99": 0, "mean": 0}
                s = sorted(values)
                n = len(s)
                return {
                    "p50": round(s[int(0.50 * n)] if n else 0, 3),
                    "p95": round(s[min(int(0.95 * n), n - 1)], 3),
                    "p99": round(s[min(int(0.99 * n), n - 1)], 3),
                    "mean": round(sum(s) / n, 3),
                    "count": n,
                }

            return {
                "total_messages": len(self._completed),
                "in_flight": len(self._records),
                "stages": {
                    stage: _stats(lats)
                    for stage, lats in stage_latencies.items()
                },
                "pipeline": _stats(total_latencies),
            }

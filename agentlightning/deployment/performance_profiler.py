"""
Performance Profiler — Profile inference pipeline performance.

Collects detailed timing, memory, and throughput metrics for each
stage of the inference pipeline. Generates profiling reports for
optimization guidance.

Location: agentlightning/deployment/performance_profiler.py

Reference (拿来主义):
  查看 agentlightning/inference/latency_monitor.py(M548) 上现有
  LatencyMonitor 的 per-stage 延迟追踪方式, 理解其模式, 特别是
  StageStats 如何独立于 Monitor 的告警逻辑。
  从 agentos/governance/telemetry_collector.py 这个好例子开始 — 它展示了
  遥测数据的采集→存储→导出 全链路。
  遵循该模式实现 PerformanceProfiler, 让开发者可以精确定位推理管线的
  性能瓶颈, 并能生成可操作的优化建议.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.deployment.performance_profiler.v1"

_DEFAULT_SAMPLE_SIZE: int = 500


class ProfileSample:
    """Single profiling sample."""

    __slots__ = ("stage", "latency_ms", "input_size", "output_size", "timestamp")

    def __init__(
        self, stage: str, latency_ms: float,
        input_size: int = 0, output_size: int = 0,
    ) -> None:
        self.stage = stage
        self.latency_ms = latency_ms
        self.input_size = input_size
        self.output_size = output_size
        self.timestamp = time.time()


class PerformanceProfiler:
    """Profiles inference pipeline performance.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self, sample_size: int = _DEFAULT_SAMPLE_SIZE) -> None:
        self.sample_size = sample_size
        self._samples: Dict[str, Deque[ProfileSample]] = defaultdict(
            lambda: deque(maxlen=sample_size)
        )
        self._throughput_counter: Dict[str, int] = defaultdict(int)
        self._throughput_window_start: float = time.time()
        self._total_profiles: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def record(
        self, stage: str, latency_ms: float,
        input_size: int = 0, output_size: int = 0,
    ) -> None:
        sample = ProfileSample(stage, latency_ms, input_size, output_size)
        self._samples[stage].append(sample)
        self._throughput_counter[stage] += 1
        self._total_profiles += 1

    def time_stage(self, stage: str) -> "_ProfileTimer":
        return _ProfileTimer(self, stage)

    def get_stage_profile(self, stage: str) -> Dict[str, Any]:
        samples = self._samples.get(stage)
        if not samples:
            return {"stage": stage, "count": 0}
        latencies = [s.latency_ms for s in samples]
        sorted_l = sorted(latencies)
        n = len(sorted_l)
        return {
            "stage": stage,
            "count": n,
            "mean_ms": round(sum(latencies) / n, 3),
            "p50_ms": round(sorted_l[n // 2], 3),
            "p95_ms": round(sorted_l[int(n * 0.95)], 3) if n > 1 else round(sorted_l[0], 3),
            "p99_ms": round(sorted_l[int(n * 0.99)], 3) if n > 1 else round(sorted_l[0], 3),
            "max_ms": round(max(latencies), 3),
            "min_ms": round(min(latencies), 3),
        }

    def get_full_profile(self) -> Dict[str, Any]:
        stages = {}
        total_mean = 0.0
        for stage in self._samples:
            profile = self.get_stage_profile(stage)
            stages[stage] = profile
            total_mean += profile.get("mean_ms", 0)
        return {
            "stages": stages,
            "total_mean_pipeline_ms": round(total_mean, 3),
            "total_profiles": self._total_profiles,
        }

    def get_bottleneck(self) -> Optional[str]:
        """Identify the slowest stage."""
        worst_stage = None
        worst_ms = 0.0
        for stage in self._samples:
            profile = self.get_stage_profile(stage)
            if profile.get("p95_ms", 0) > worst_ms:
                worst_ms = profile["p95_ms"]
                worst_stage = stage
        return worst_stage

    def get_throughput(self) -> Dict[str, float]:
        """Get requests per second per stage."""
        elapsed = time.time() - self._throughput_window_start
        if elapsed <= 0:
            return {}
        return {
            stage: round(count / elapsed, 2)
            for stage, count in self._throughput_counter.items()
        }

    def reset(self) -> None:
        self._samples.clear()
        self._throughput_counter.clear()
        self._throughput_window_start = time.time()
        self._total_profiles = 0

    def stages(self) -> List[str]:
        return list(self._samples.keys())

    def get_stats(self) -> Dict[str, Any]:
        return {
            "stage_count": len(self._samples),
            "total_profiles": self._total_profiles,
            "bottleneck": self.get_bottleneck(),
        }

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY, "type": event_type,
                    "timestamp": time.time(), "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)


class _ProfileTimer:
    def __init__(self, profiler: PerformanceProfiler, stage: str) -> None:
        self._profiler = profiler
        self._stage = stage
        self._start: float = 0.0

    def __enter__(self) -> "_ProfileTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        elapsed_ms = (time.monotonic() - self._start) * 1000.0
        self._profiler.record(self._stage, elapsed_ms)

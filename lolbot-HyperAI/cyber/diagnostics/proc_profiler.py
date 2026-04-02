"""
ProcProfiler — Per-component Proc() performance profiling.
=============================================================
lolbot-HyperAI · Cyber Framework

Records wall time, optional CPU time, and GC pauses for each
TimerComponent Proc() call.  Off by default; enabled per-component.

Architecture position:
    cyber/diagnostics/proc_profiler.py   ← YOU ARE HERE
    ├─ Wraps: TimerComponent.Proc() via monkey-patch or explicit call
    └─ Output: profile data for log_analyzer or CLI display

Design notes:
    - Minimal overhead when disabled (~1 if-check per Proc())
    - Uses time.perf_counter for wall time, time.process_time for CPU
    - Stores rolling window of 1000 samples per component
    - Can export as CSV for external flame-graph tools
"""

from __future__ import annotations
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

_WINDOW_SIZE = 1000


@dataclass
class ProcSample:
    """A single Proc() execution sample."""
    seq: int = 0
    wall_ms: float = 0.0
    cpu_ms: float = 0.0
    timestamp: float = 0.0


@dataclass
class ComponentProfile:
    """Accumulated profile for one component."""
    name: str = ""
    enabled: bool = False
    samples: Deque[ProcSample] = field(
        default_factory=lambda: deque(maxlen=_WINDOW_SIZE)
    )
    total_calls: int = 0

    def record(self, wall_ms: float, cpu_ms: float, seq: int) -> None:
        self.samples.append(ProcSample(
            seq=seq, wall_ms=wall_ms, cpu_ms=cpu_ms, timestamp=time.time(),
        ))
        self.total_calls += 1

    def summary(self) -> Dict[str, Any]:
        if not self.samples:
            return {"name": self.name, "total_calls": 0}
        walls = [s.wall_ms for s in self.samples]
        cpus = [s.cpu_ms for s in self.samples]
        return {
            "name": self.name,
            "total_calls": self.total_calls,
            "window_size": len(self.samples),
            "wall_mean_ms": round(sum(walls) / len(walls), 2),
            "wall_max_ms": round(max(walls), 2),
            "wall_p95_ms": round(sorted(walls)[int(len(walls) * 0.95)], 2) if len(walls) >= 20 else round(max(walls), 2),
            "cpu_mean_ms": round(sum(cpus) / len(cpus), 2),
        }


class ProcProfiler:
    """System-wide Proc() profiler.

    Usage::
        profiler = ProcProfiler()
        profiler.enable("canbus")
        # In component's _run_loop wrapper:
        profiler.begin("canbus")
        result = self.Proc()
        profiler.end("canbus", seq)
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, ComponentProfile] = {}

    def enable(self, component_name: str) -> None:
        if component_name not in self._profiles:
            self._profiles[component_name] = ComponentProfile(
                name=component_name
            )
        self._profiles[component_name].enabled = True

    def disable(self, component_name: str) -> None:
        if component_name in self._profiles:
            self._profiles[component_name].enabled = False

    def is_enabled(self, component_name: str) -> bool:
        p = self._profiles.get(component_name)
        return p.enabled if p else False

    def begin(self, component_name: str) -> Optional[Tuple[float, float]]:
        """Start timing. Returns (wall_start, cpu_start) or None if disabled."""
        if not self.is_enabled(component_name):
            return None
        return (time.perf_counter(), time.process_time())

    def end(
        self,
        component_name: str,
        start: Optional[Tuple[float, float]],
        seq: int = 0,
    ) -> None:
        """End timing and record sample."""
        if start is None:
            return
        wall_start, cpu_start = start
        wall_ms = (time.perf_counter() - wall_start) * 1000
        cpu_ms = (time.process_time() - cpu_start) * 1000

        profile = self._profiles.get(component_name)
        if profile and profile.enabled:
            profile.record(wall_ms, cpu_ms, seq)

    def summary(self) -> Dict[str, Dict[str, Any]]:
        return {name: p.summary() for name, p in self._profiles.items()}

    def export_csv(self, component_name: str) -> str:
        """Export samples as CSV for external tools."""
        profile = self._profiles.get(component_name)
        if not profile:
            return "seq,wall_ms,cpu_ms,timestamp\n"
        lines = ["seq,wall_ms,cpu_ms,timestamp"]
        for s in profile.samples:
            lines.append(f"{s.seq},{s.wall_ms:.3f},{s.cpu_ms:.3f},{s.timestamp:.3f}")
        return "\n".join(lines)

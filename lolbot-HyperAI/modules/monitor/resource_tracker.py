"""
ResourceTracker — CPU, memory, thread, and GC monitoring.
==========================================================

Lightweight background thread that samples process-level resource
usage metrics at 1Hz. Used by MonitorComponent to include resource
data in health reports.

Architecture position:
    modules/monitor/resource_tracker.py   ← YOU ARE HERE
    ├─ Queried by: modules/monitor/monitor_component.py
    ├─ Publishes to: cyber/statistics gauges
    └─ No external dependencies (uses /proc on Linux, os module)

Apollo reference:
    modules/monitor/hardware/resource_monitor.cc
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

_SAMPLE_INTERVAL_S: float = 1.0
_HISTORY_SIZE: int = 300  # 5 minutes at 1Hz
_PAGE_SIZE: int = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_CLK_TCK: int = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


@dataclass
class ResourceSample:
    """Single point-in-time resource measurement."""
    timestamp: float = 0.0
    cpu_percent: float = 0.0
    memory_rss_mb: float = 0.0
    memory_vms_mb: float = 0.0
    thread_count: int = 0
    fd_count: int = 0
    gc_gen0: int = 0
    gc_gen1: int = 0
    gc_gen2: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": round(self.timestamp, 3),
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_rss_mb": round(self.memory_rss_mb, 1),
            "memory_vms_mb": round(self.memory_vms_mb, 1),
            "thread_count": self.thread_count,
            "fd_count": self.fd_count,
            "gc_gen0": self.gc_gen0,
            "gc_gen1": self.gc_gen1,
            "gc_gen2": self.gc_gen2,
        }


class ResourceTracker:
    """Background resource monitor sampling at 1Hz.

    Usage::

        tracker = ResourceTracker()
        tracker.start()
        # ... later ...
        current = tracker.snapshot()
        history = tracker.history(60)  # last 60 samples
        tracker.stop()
    """

    def __init__(
        self,
        interval_s: float = _SAMPLE_INTERVAL_S,
        history_size: int = _HISTORY_SIZE,
    ) -> None:
        self._interval = interval_s
        self._history: Deque[ResourceSample] = deque(maxlen=history_size)
        self._latest: Optional[ResourceSample] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pid = os.getpid()

        # For CPU% calculation between samples
        self._prev_cpu_times: Optional[tuple] = None
        self._prev_sample_time: float = 0.0

    def start(self) -> None:
        """Start the background sampling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sample_loop,
            name="resource-tracker",
            daemon=True,
        )
        self._thread.start()
        logger.info("ResourceTracker started (pid=%d)", self._pid)

    def stop(self) -> None:
        """Stop the background sampling thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        logger.info("ResourceTracker stopped")

    def snapshot(self) -> Dict[str, Any]:
        """Get the most recent resource sample."""
        with self._lock:
            if self._latest:
                return self._latest.to_dict()
        return ResourceSample().to_dict()

    def history(self, count: int = 60) -> list:
        """Get recent history as list of dicts."""
        with self._lock:
            samples = list(self._history)
        return [s.to_dict() for s in samples[-count:]]

    def _sample_loop(self) -> None:
        """Background loop: sample resources every interval."""
        while not self._stop_event.is_set():
            try:
                sample = self._take_sample()
                with self._lock:
                    self._latest = sample
                    self._history.append(sample)
            except Exception as exc:
                logger.debug("Resource sample error: %s", exc)

            self._stop_event.wait(timeout=self._interval)

    def _take_sample(self) -> ResourceSample:
        """Collect one resource snapshot from /proc and gc."""
        sample = ResourceSample(timestamp=time.time())

        # Thread count
        sample.thread_count = threading.active_count()

        # GC stats
        gc_counts = gc.get_count()
        if len(gc_counts) >= 3:
            sample.gc_gen0 = gc_counts[0]
            sample.gc_gen1 = gc_counts[1]
            sample.gc_gen2 = gc_counts[2]

        # Memory from /proc/self/statm (Linux)
        try:
            with open(f"/proc/{self._pid}/statm", "r") as f:
                parts = f.read().split()
                if len(parts) >= 2:
                    sample.memory_vms_mb = (
                        int(parts[0]) * _PAGE_SIZE / (1024 * 1024)
                    )
                    sample.memory_rss_mb = (
                        int(parts[1]) * _PAGE_SIZE / (1024 * 1024)
                    )
        except (OSError, ValueError):
            pass

        # CPU from /proc/self/stat (Linux)
        try:
            with open(f"/proc/{self._pid}/stat", "r") as f:
                stat_line = f.read()
                # Fields after the comm field (enclosed in parens)
                close_paren = stat_line.rfind(")")
                if close_paren > 0:
                    fields = stat_line[close_paren + 2:].split()
                    if len(fields) >= 12:
                        utime = int(fields[11])
                        stime = int(fields[12])
                        now = time.monotonic()

                        if self._prev_cpu_times is not None:
                            dt = now - self._prev_sample_time
                            if dt > 0:
                                du = utime - self._prev_cpu_times[0]
                                ds = stime - self._prev_cpu_times[1]
                                cpu_seconds = (du + ds) / _CLK_TCK
                                sample.cpu_percent = (
                                    cpu_seconds / dt * 100.0
                                )

                        self._prev_cpu_times = (utime, stime)
                        self._prev_sample_time = now
        except (OSError, ValueError, IndexError):
            pass

        # File descriptor count
        try:
            fd_dir = f"/proc/{self._pid}/fd"
            sample.fd_count = len(os.listdir(fd_dir))
        except OSError:
            pass

        return sample

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def __repr__(self) -> str:
        return (
            f"<ResourceTracker running={self.is_running} "
            f"samples={len(self._history)}>"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended resource tracker with alerts, trends, and per-component
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class ResourceAlert:
    """Alert generated when resource usage exceeds threshold."""
    resource: str      # "cpu", "memory", "threads", "fd"
    severity: str      # "warning", "critical"
    value: float
    threshold: float
    message: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource": self.resource,
            "severity": self.severity,
            "value": round(self.value, 1),
            "threshold": self.threshold,
            "message": self.message,
        }


@dataclass
class ResourceThresholds:
    """Configurable thresholds for resource alerts."""
    cpu_warning_pct: float = 70.0
    cpu_critical_pct: float = 90.0
    memory_warning_mb: float = 512.0
    memory_critical_mb: float = 1024.0
    thread_warning: int = 50
    thread_critical: int = 100
    fd_warning: int = 500
    fd_critical: int = 900


class ResourceTrackerV2(ResourceTracker):
    """Extended resource tracker with alerts and trend analysis.

    Claude20: Adds configurable threshold alerts, resource trend
    detection, and per-component resource attribution (approximated
    via thread naming convention).

    All existing ResourceTracker methods preserved.
    """

    def __init__(
        self,
        interval_s: float = _SAMPLE_INTERVAL_S,
        history_size: int = _HISTORY_SIZE,
        thresholds: Optional[ResourceThresholds] = None,
    ) -> None:
        super().__init__(interval_s, history_size)
        self._thresholds = thresholds or ResourceThresholds()
        self._alerts: List[ResourceAlert] = []
        self._alert_cooldown_s: float = 30.0
        self._last_alert_time: Dict[str, float] = {}
        self._check_count: int = 0

    def check_alerts(self) -> List[ResourceAlert]:
        """Check current resource levels against thresholds.

        Claude20: Called by MonitorComponent periodically.
        Returns list of new alerts (may be empty).
        """
        self._check_count += 1
        sample = self._latest if hasattr(self, '_latest') else None
        if sample is None:
            return []

        with self._lock:
            current = self._latest

        if current is None:
            return []

        new_alerts: List[ResourceAlert] = []
        now = time.time()
        th = self._thresholds

        # CPU check
        if current.cpu_percent >= th.cpu_critical_pct:
            if self._can_alert("cpu_critical", now):
                new_alerts.append(ResourceAlert(
                    resource="cpu", severity="critical",
                    value=current.cpu_percent, threshold=th.cpu_critical_pct,
                    message=f"CPU at {current.cpu_percent:.1f}% (critical)",
                ))
        elif current.cpu_percent >= th.cpu_warning_pct:
            if self._can_alert("cpu_warning", now):
                new_alerts.append(ResourceAlert(
                    resource="cpu", severity="warning",
                    value=current.cpu_percent, threshold=th.cpu_warning_pct,
                    message=f"CPU at {current.cpu_percent:.1f}% (warning)",
                ))

        # Memory check
        if current.memory_rss_mb >= th.memory_critical_mb:
            if self._can_alert("mem_critical", now):
                new_alerts.append(ResourceAlert(
                    resource="memory", severity="critical",
                    value=current.memory_rss_mb, threshold=th.memory_critical_mb,
                    message=f"Memory at {current.memory_rss_mb:.0f}MB (critical)",
                ))
        elif current.memory_rss_mb >= th.memory_warning_mb:
            if self._can_alert("mem_warning", now):
                new_alerts.append(ResourceAlert(
                    resource="memory", severity="warning",
                    value=current.memory_rss_mb, threshold=th.memory_warning_mb,
                    message=f"Memory at {current.memory_rss_mb:.0f}MB (warning)",
                ))

        # Thread count check
        if current.thread_count >= th.thread_critical:
            if self._can_alert("thread_critical", now):
                new_alerts.append(ResourceAlert(
                    resource="threads", severity="critical",
                    value=current.thread_count, threshold=th.thread_critical,
                    message=f"Thread count: {current.thread_count} (critical)",
                ))
        elif current.thread_count >= th.thread_warning:
            if self._can_alert("thread_warning", now):
                new_alerts.append(ResourceAlert(
                    resource="threads", severity="warning",
                    value=current.thread_count, threshold=th.thread_warning,
                    message=f"Thread count: {current.thread_count} (warning)",
                ))

        # FD check
        if current.fd_count >= th.fd_critical:
            if self._can_alert("fd_critical", now):
                new_alerts.append(ResourceAlert(
                    resource="fd", severity="critical",
                    value=current.fd_count, threshold=th.fd_critical,
                    message=f"File descriptors: {current.fd_count} (critical)",
                ))

        self._alerts.extend(new_alerts)
        return new_alerts

    def _can_alert(self, key: str, now: float) -> bool:
        """Check alert cooldown."""
        last = self._last_alert_time.get(key, 0.0)
        if now - last < self._alert_cooldown_s:
            return False
        self._last_alert_time[key] = now
        return True

    def get_trend(self, metric: str = "cpu", window: int = 60) -> Dict[str, Any]:
        """Compute trend for a metric over the last window samples.

        Claude20: Returns direction, average, and rate of change.
        """
        with self._lock:
            samples = list(self._history)

        if len(samples) < 3:
            return {"direction": "unknown", "avg": 0.0, "rate": 0.0}

        recent = samples[-min(window, len(samples)):]
        values = []
        for s in recent:
            if metric == "cpu":
                values.append(s.cpu_percent)
            elif metric == "memory":
                values.append(s.memory_rss_mb)
            elif metric == "threads":
                values.append(float(s.thread_count))
            else:
                values.append(0.0)

        if len(values) < 2:
            return {"direction": "unknown", "avg": 0.0, "rate": 0.0}

        avg = sum(values) / len(values)
        first_half = sum(values[:len(values)//2]) / max(len(values)//2, 1)
        second_half = sum(values[len(values)//2:]) / max(len(values) - len(values)//2, 1)
        rate = second_half - first_half

        if rate > avg * 0.05:
            direction = "rising"
        elif rate < -avg * 0.05:
            direction = "falling"
        else:
            direction = "stable"

        return {
            "direction": direction,
            "avg": round(avg, 1),
            "rate": round(rate, 2),
            "samples": len(values),
        }

    def get_component_threads(self) -> Dict[str, int]:
        """Approximate per-component thread count.

        Claude20: Uses thread name convention (cyber-*) to attribute
        threads to components.
        """
        counts: Dict[str, int] = {}
        for t in threading.enumerate():
            name = t.name
            if name.startswith("cyber-"):
                comp = name[6:]  # Remove "cyber-" prefix
                counts[comp] = counts.get(comp, 0) + 1
            elif name.startswith("resource-"):
                counts["monitor"] = counts.get("monitor", 0) + 1
            elif name == "MainThread":
                counts["main"] = 1
            else:
                counts["other"] = counts.get("other", 0) + 1
        return counts

    def recent_alerts(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent resource alerts."""
        return [a.to_dict() for a in self._alerts[-count:]]

    def extended_stats(self) -> Dict[str, Any]:
        base = self.snapshot()
        base["alerts_total"] = len(self._alerts)
        base["check_count"] = self._check_count
        base["cpu_trend"] = self.get_trend("cpu", 30)
        base["memory_trend"] = self.get_trend("memory", 30)
        base["component_threads"] = self.get_component_threads()
        return base

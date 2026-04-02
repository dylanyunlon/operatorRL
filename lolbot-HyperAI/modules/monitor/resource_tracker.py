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

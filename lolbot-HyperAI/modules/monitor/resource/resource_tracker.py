"""
modules/monitor/resource/resource_tracker.py
System resource tracking. Verbatim from Claude25 monitor_component.py.
"""
from __future__ import annotations
import os
import time
from typing import Any, Dict


class ResourceTracker:
    """Tracks system resource usage (RSS, CPU)."""

    def __init__(self) -> None:
        self._last_rss_mb: float = 0.0
        self._peak_rss_mb: float = 0.0
        self._stopped: bool = False

    def check(self) -> Dict[str, Any]:
        if self._stopped:
            return {"rss_mb": self._last_rss_mb, "stopped": True}
        try:
            # /proc/self/status RSS
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        self._last_rss_mb = round(kb / 1024.0, 2)
                        break
        except (OSError, ValueError):
            pass
        if self._last_rss_mb > self._peak_rss_mb:
            self._peak_rss_mb = self._last_rss_mb
        return {
            "rss_mb": self._last_rss_mb,
            "peak_rss_mb": self._peak_rss_mb,
            "pid": os.getpid(),
        }

    def last_rss_mb(self) -> float:
        return self._last_rss_mb

    def stop(self) -> None:
        self._stopped = True

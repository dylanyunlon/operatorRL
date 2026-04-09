"""
modules/monitor/resource/health_tracker.py
Component health tracking. Verbatim from Claude25 monitor_component.py.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ComponentHealthEntry:
    name: str
    healthy: bool = True
    last_check_time: float = 0.0
    consecutive_failures: int = 0
    last_error: str = ""
    proc_count: int = 0
    avg_proc_ms: float = 0.0

    def update_from(self, health: Dict[str, Any]) -> None:
        self.last_check_time = time.time()
        self.healthy = health.get("healthy", True)
        if not self.healthy:
            self.consecutive_failures += 1
            self.last_error = str(health.get("details", {}).get("reason", "unknown"))
        else:
            self.consecutive_failures = 0
        self.proc_count = health.get("proc_count", self.proc_count)
        self.avg_proc_ms = health.get("avg_proc_ms", self.avg_proc_ms)


class ComponentHealthTracker:
    """Tracks health of all registered components."""

    def __init__(self) -> None:
        self._entries: Dict[str, ComponentHealthEntry] = {}

    def update(self, name: str, health: Dict[str, Any]) -> ComponentHealthEntry:
        if name not in self._entries:
            self._entries[name] = ComponentHealthEntry(name=name)
        entry = self._entries[name]
        entry.update_from(health)
        return entry

    def get(self, name: str) -> Optional[ComponentHealthEntry]:
        return self._entries.get(name)

    def all_entries(self) -> Dict[str, ComponentHealthEntry]:
        return dict(self._entries)

    def unhealthy(self) -> List[ComponentHealthEntry]:
        return [e for e in self._entries.values() if not e.healthy]

    def summary(self) -> Dict[str, Any]:
        total = len(self._entries)
        healthy_count = sum(1 for e in self._entries.values() if e.healthy)
        return {
            "total_components": total,
            "healthy": healthy_count,
            "unhealthy": total - healthy_count,
            "components": {
                n: {"healthy": e.healthy, "failures": e.consecutive_failures,
                    "proc_count": e.proc_count}
                for n, e in self._entries.items()
            },
        }

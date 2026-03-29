"""
Cross Module Health — Aggregate health status across all modules.

Location: agentos/governance/cross_module_health.py
"""

from __future__ import annotations
import logging, time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY: str = "agentos.governance.cross_module_health.v1"

class CrossModuleHealth:
    """Aggregate health status across modules with degradation strategy."""

    def __init__(self) -> None:
        self._module_status: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def report_health(self, module: str, healthy: bool, detail: str = "") -> None:
        self._module_status[module] = {"healthy": healthy, "detail": detail, "last_check": time.time()}
        self._fire_evolution("health_reported", {"module": module, "healthy": healthy})

    def get_health(self, module: str) -> dict[str, Any]:
        return self._module_status.get(module, {"healthy": False, "detail": "not_registered", "last_check": 0.0})

    def get_overall(self) -> dict[str, Any]:
        if not self._module_status:
            return {"status": "unknown", "healthy_count": 0, "total": 0}
        healthy = sum(1 for v in self._module_status.values() if v["healthy"])
        total = len(self._module_status)
        status = "healthy" if healthy == total else ("degraded" if healthy > 0 else "down")
        return {"status": status, "healthy_count": healthy, "total": total}

    def get_degraded_modules(self) -> list[str]:
        return [m for m, v in self._module_status.items() if not v["healthy"]]

    def module_count(self) -> int:
        return len(self._module_status)

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({"source": _EVOLUTION_KEY, "type": event_type, "timestamp": time.time(), "payload": payload})

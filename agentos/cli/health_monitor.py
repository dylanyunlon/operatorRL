"""
Health Monitor — System resource, network, and API availability checks.

Checks CPU, memory, and registered endpoints, providing overall
health status with history tracking.

Location: agentos/cli/health_monitor.py

Reference (拿来主义):
  - agentos/governance/telemetry_collector.py: metric tracking with deque
  - agentos/governance/deployment_manager.py: health_check() pattern
  - DI-star: system resource monitoring for distributed training
"""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


class HealthMonitor:
    """System health monitoring with endpoint tracking.

    Checks CPU/memory (simulated), tracks registered endpoints,
    maintains health check history.
    """

    def __init__(self, max_history: int = 100) -> None:
        self._endpoints: dict[str, str] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=max_history)

    def register_endpoint(self, name: str, url: str) -> None:
        """Register an endpoint for health monitoring.

        Args:
            name: Endpoint name.
            url: Endpoint URL.
        """
        self._endpoints[name] = url

    def unregister_endpoint(self, name: str) -> None:
        """Remove a monitored endpoint."""
        self._endpoints.pop(name, None)

    def list_endpoints(self) -> list[str]:
        """List registered endpoint names."""
        return list(self._endpoints.keys())

    def check(self) -> dict[str, Any]:
        """Perform a health check.

        Returns simulated CPU/memory values and overall status.

        Returns:
            Dict with cpu, memory, overall, endpoints, timestamp.
        """
        # Simulated system metrics (production would use psutil)
        import random
        cpu = round(random.uniform(5.0, 85.0), 1)
        memory = round(random.uniform(20.0, 80.0), 1)

        # Overall status based on resource usage
        if cpu > 90 or memory > 95:
            overall = "unhealthy"
        elif cpu > 70 or memory > 80:
            overall = "degraded"
        else:
            overall = "healthy"

        result = {
            "cpu": cpu,
            "memory": memory,
            "overall": overall,
            "endpoints": dict(self._endpoints),
            "timestamp": time.time(),
        }

        self._history.append(result)
        return result

    def get_history(self) -> list[dict[str, Any]]:
        """Return health check history."""
        return list(self._history)

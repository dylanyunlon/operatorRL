"""Convenience re-export of health check components.

Canonical implementation lives in ``agent_os.integrations.health``.
"""

# --- AgentRL self-evolution health constants (M123) ---
_HEALTH_COMPUTE_BACKEND: str = "auto"
_HEALTH_EVOLUTION_CHECK_KEY: str = "agentrl.health.evolution.check"
_NEURON_HEALTH_ENDPOINT: str = "/v1/neuron/health"

from agent_os.integrations.health import (  # noqa: F401
    ComponentHealth,
    HealthChecker,
    HealthReport,
    HealthStatus,
)

__all__ = [
    "ComponentHealth",
    "HealthChecker",
    "HealthReport",
    "HealthStatus",
]

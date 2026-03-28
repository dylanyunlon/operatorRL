"""
AgentOS Bridge - Integrates LoL agent with operatorRL's agent framework.

Wraps the LoL Strategy Agent as an AgentOS-managed agent with:
- Policy governance (rate limiting, PII protection)
- Audit logging for all advice generated
- Health monitoring and circuit breaking
- Graceful degradation when AgentOS is unavailable
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Conditional AgentOS imports
try:
    from agent_os.base_agent import AgentConfig, BaseAgent
    from agent_os.stateless import ExecutionContext, ExecutionResult
    from agent_os.health import HealthStatus
    AGENTOS_AVAILABLE = True
except ImportError:
    AGENTOS_AVAILABLE = False

    @dataclass
    class AgentConfig:
        agent_id: str = ""
        policies: list = field(default_factory=list)
        metadata: dict = field(default_factory=dict)

    @dataclass
    class ExecutionResult:
        success: bool = True
        data: Any = None
        error: Optional[str] = None

    class HealthStatus:
        HEALTHY = "healthy"
        DEGRADED = "degraded"
        UNHEALTHY = "unhealthy"


@dataclass
class BridgeConfig:
    """Configuration for the AgentOS bridge."""
    agent_id: str = "lol-fiddler-agent"
    policies: list[str] = field(default_factory=lambda: ["read_only", "no_pii", "rate_limit"])
    enable_audit: bool = True
    enable_health_check: bool = True
    health_check_interval: float = 30.0
    max_advice_per_minute: int = 30
    metadata: dict[str, str] = field(default_factory=lambda: {
        "type": "game_strategy",
        "game": "league_of_legends",
        "version": "0.2.0",
    })


class AuditLogger:
    """Structured audit logging for agent actions."""

    def __init__(self, agent_id: str) -> None:
        self._agent_id = agent_id
        self._entries: list[dict[str, Any]] = []
        self._logger = logging.getLogger(f"audit.{agent_id}")

    def log_advice(self, advice_data: dict[str, Any]) -> None:
        entry = {
            "timestamp": time.time(),
            "agent_id": self._agent_id,
            "action": "advice_generated",
            "data": advice_data,
        }
        self._entries.append(entry)
        self._logger.info("Advice: %s", advice_data.get("action", "unknown"))

    def log_state_access(self, endpoint: str, data_size: int) -> None:
        entry = {
            "timestamp": time.time(),
            "agent_id": self._agent_id,
            "action": "state_access",
            "endpoint": endpoint,
            "data_size": data_size,
        }
        self._entries.append(entry)

    def log_error(self, error: str, context: dict[str, Any] = {}) -> None:
        entry = {
            "timestamp": time.time(),
            "agent_id": self._agent_id,
            "action": "error",
            "error": error,
            "context": context,
        }
        self._entries.append(entry)
        self._logger.error("Error: %s", error)

    def get_entries(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._entries[-limit:]

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


class RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, max_per_minute: int = 30) -> None:
        self._max = max_per_minute
        self._timestamps: list[float] = []

    def allow(self) -> bool:
        now = time.time()
        cutoff = now - 60.0
        self._timestamps = [t for t in self._timestamps if t > cutoff]
        if len(self._timestamps) >= self._max:
            return False
        self._timestamps.append(now)
        return True

    @property
    def remaining(self) -> int:
        now = time.time()
        cutoff = now - 60.0
        current = sum(1 for t in self._timestamps if t > cutoff)
        return max(0, self._max - current)


class AgentOSBridge:
    """Bridge between LoL Strategy Agent and AgentOS framework.

    Provides:
    1. Policy-governed advice generation
    2. Structured audit logging
    3. Rate limiting for advice frequency
    4. Health monitoring integration
    5. Graceful degradation when AgentOS unavailable

    Example::

        bridge = AgentOSBridge(BridgeConfig())
        await bridge.initialize()

        # Before generating advice:
        if bridge.should_generate_advice():
            advice = generate_advice()
            bridge.record_advice(advice)
    """

    def __init__(self, config: Optional[BridgeConfig] = None) -> None:
        self._config = config or BridgeConfig()
        self._audit = AuditLogger(self._config.agent_id)
        self._rate_limiter = RateLimiter(self._config.max_advice_per_minute)
        self._health_status = "healthy"
        self._initialized = False
        self._agent_config: Optional[AgentConfig] = None
        self._last_health_check = 0.0

    async def initialize(self) -> bool:
        """Initialize AgentOS integration."""
        self._agent_config = AgentConfig(
            agent_id=self._config.agent_id,
            policies=self._config.policies,
            metadata=self._config.metadata,
        )

        if AGENTOS_AVAILABLE:
            logger.info("AgentOS available - full governance enabled")
        else:
            logger.warning("AgentOS not available - running in standalone mode")

        self._initialized = True
        return True

    def should_generate_advice(self) -> bool:
        """Check if we're allowed to generate advice (rate limit + policy)."""
        if not self._rate_limiter.allow():
            logger.debug("Rate limited - skipping advice generation")
            return False
        if self._health_status == "unhealthy":
            return False
        return True

    def record_advice(self, advice_data: dict[str, Any]) -> None:
        """Record generated advice for audit."""
        if self._config.enable_audit:
            # Scrub PII before logging
            scrubbed = self._scrub_pii(advice_data)
            self._audit.log_advice(scrubbed)

    def record_error(self, error: str, context: dict[str, Any] = {}) -> None:
        self._audit.log_error(error, context)

    def _scrub_pii(self, data: dict[str, Any]) -> dict[str, Any]:
        """Remove PII from data before logging (summoner names, etc.)."""
        scrubbed = dict(data)
        pii_keys = {"summoner_name", "riot_id", "player_name", "riotId", "summonerName"}
        for key in pii_keys:
            if key in scrubbed:
                scrubbed[key] = "[REDACTED]"
        return scrubbed

    async def check_health(self) -> str:
        """Perform health check."""
        now = time.time()
        if now - self._last_health_check < self._config.health_check_interval:
            return self._health_status

        self._last_health_check = now

        # Check rate limiter headroom
        if self._rate_limiter.remaining < 5:
            self._health_status = "degraded"
        else:
            self._health_status = "healthy"

        return self._health_status

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def health_status(self) -> str:
        return self._health_status

    @property
    def audit(self) -> AuditLogger:
        return self._audit

    @property
    def agentos_available(self) -> bool:
        return AGENTOS_AVAILABLE

    def get_stats(self) -> dict[str, Any]:
        return {
            "agent_id": self._config.agent_id,
            "initialized": self._initialized,
            "health": self._health_status,
            "agentos_available": AGENTOS_AVAILABLE,
            "rate_limit_remaining": self._rate_limiter.remaining,
            "audit_entries": self._audit.entry_count,
        }

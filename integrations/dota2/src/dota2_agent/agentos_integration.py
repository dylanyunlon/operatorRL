"""
AgentOS Integration — Governed Environment + Policy Checks.

Provides governance wrappers for Dota 2 agent actions, ensuring
policy compliance before execution. Implements operatorRL's
self-deploying, self-evolving governance model.

Location: integrations/dota2/src/dota2_agent/agentos_integration.py

Reference: operatorRL governance patterns + PARL agent_base.py.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "dota2_agent.agentos_integration.v1"


@dataclass
class ValidationResult:
    """Result of a policy validation check."""
    passed: bool
    failed_checks: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


class GovernedEnvironment:
    """Environment wrapper that enforces policy checks on actions."""

    def __init__(self, base_env: Any, policy_checks: list[tuple[str, Callable]]) -> None:
        self.base_env = base_env
        self.policy_checks = policy_checks
        self.governance_enabled: bool = True

    def step(self, action: dict[str, Any]) -> dict[str, Any]:
        """Step the environment with governance checks.

        Args:
            action: Action dict to validate and execute.

        Returns:
            Result dict with allowed flag.
        """
        if self.governance_enabled:
            for name, check_fn in self.policy_checks:
                if not check_fn(action):
                    return {
                        "allowed": False,
                        "blocked_by": name,
                        "action": action,
                    }

        return {
            "allowed": True,
            "action": action,
        }


class AgentOSIntegration:
    """AgentOS governance integration for Dota 2.

    Provides policy registration, action validation, and
    governed environment creation for safe agent deployment.
    """

    def __init__(self) -> None:
        self.governance_enabled: bool = True
        self.policy_checks: list[tuple[str, Callable]] = []
        self._governance_log: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_policy_check(self, name: str, check_fn: Callable) -> None:
        """Register a policy check function.

        Args:
            name: Check identifier.
            check_fn: Function that takes action dict, returns bool.
        """
        self.policy_checks.append((name, check_fn))

    def validate_action(self, action: dict[str, Any]) -> ValidationResult:
        """Validate an action against all registered checks.

        Args:
            action: Action dict to validate.

        Returns:
            ValidationResult with pass/fail status.
        """
        failed = []
        for name, check_fn in self.policy_checks:
            try:
                if not check_fn(action):
                    failed.append(name)
            except Exception as exc:
                logger.warning("Policy check '%s' raised: %s", name, exc)
                failed.append(name)

        result = ValidationResult(
            passed=len(failed) == 0,
            failed_checks=failed,
        )

        self._governance_log.append({
            "action": action,
            "passed": result.passed,
            "failed_checks": result.failed_checks,
            "timestamp": result.timestamp,
        })

        return result

    def create_governed_environment(
        self, base_env: Any
    ) -> GovernedEnvironment:
        """Create a governed wrapper around a base environment.

        Args:
            base_env: The underlying game environment (or None for stub).

        Returns:
            GovernedEnvironment instance.
        """
        return GovernedEnvironment(
            base_env=base_env,
            policy_checks=list(self.policy_checks),
        )

    def export_governance_log(self) -> list[dict[str, Any]]:
        """Export governance validation log.

        Returns:
            List of validation result dicts.
        """
        return list(self._governance_log)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

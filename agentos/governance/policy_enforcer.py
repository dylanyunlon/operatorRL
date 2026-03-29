"""
Policy Enforcer — Safety boundaries and fairness constraints.

Enforces action rate limits, fairness constraints, and anti-cheat
rules across all game agents.

Location: agentos/governance/policy_enforcer.py

Reference (拿来主义):
  - integrations/dota2/src/dota2_agent/agentos_integration.py: policy check pattern
  - operatorRL: governance/safety design from plan.md
  - PARL: agent constraint patterns
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.policy_enforcer.v1"


class PolicyEnforcer:
    """Enforces safety and fairness policies on agent actions.

    Maintains a set of named rules with limits, checking incoming
    actions against all active rules.

    Attributes:
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._rules: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_rule(self, name: str, limit: float) -> None:
        """Add a policy rule.

        Args:
            name: Rule identifier.
            limit: Maximum allowed value for this rule.
        """
        self._rules[name] = {"limit": limit, "created_at": time.time()}

    def remove_rule(self, name: str) -> None:
        """Remove a policy rule.

        Args:
            name: Rule identifier.
        """
        self._rules.pop(name, None)

    def rule_count(self) -> int:
        """Number of active rules."""
        return len(self._rules)

    def list_rules(self) -> list[dict[str, Any]]:
        """List all active rules."""
        return [{"name": n, "limit": r["limit"]} for n, r in self._rules.items()]

    def check(self, action: dict[str, Any]) -> dict[str, Any]:
        """Check an action against all active rules.

        For 'max_actions_per_second': checks action['rate'] vs limit.
        For 'fairness': checks action['advantage_score'] vs limit.
        Generic fallback: checks action['rate'] vs limit.

        Args:
            action: Action dict with type and relevant fields.

        Returns:
            Dict with 'allowed' bool and 'violations' list.
        """
        violations = []

        for name, rule in self._rules.items():
            limit = rule["limit"]

            if name == "max_actions_per_second":
                rate = action.get("rate", 0)
                if rate > limit:
                    violations.append({
                        "rule": name,
                        "value": rate,
                        "limit": limit,
                    })
            elif name == "fairness":
                adv = action.get("advantage_score", 0)
                if adv > limit:
                    violations.append({
                        "rule": name,
                        "value": adv,
                        "limit": limit,
                    })
            else:
                # Generic: check 'rate' field
                val = action.get("rate", 0)
                if val > limit:
                    violations.append({
                        "rule": name,
                        "value": val,
                        "limit": limit,
                    })

        allowed = len(violations) == 0
        result = {"allowed": allowed, "violations": violations}

        self._fire_evolution("policy_checked", {
            "action_type": action.get("type", "unknown"),
            "allowed": allowed,
            "violation_count": len(violations),
        })
        return result

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

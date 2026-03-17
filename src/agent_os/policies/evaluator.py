"""
Standalone policy evaluator for Agent-OS governance.

Evaluates declarative PolicyDocuments against an execution context dict,
returning a PolicyDecision with matched rule, action, and audit information.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .schema import PolicyAction, PolicyDocument, PolicyOperator, PolicyRule

logger = logging.getLogger(__name__)


class PolicyDecision(BaseModel):
    """Result of evaluating policies against an execution context."""

    allowed: bool = True
    matched_rule: str | None = None
    action: str = "allow"
    reason: str = "No rules matched; default action applied"
    audit_entry: dict[str, Any] = Field(default_factory=dict)


class PolicyEvaluator:
    """Evaluates a set of PolicyDocuments against execution contexts."""

    def __init__(self, policies: list[PolicyDocument] | None = None) -> None:
        self.policies: list[PolicyDocument] = policies or []

    def load_policies(self, directory: str | Path) -> None:
        """Load all YAML policy files from a directory."""
        directory = Path(directory)
        for path in sorted(directory.glob("*.yaml")):
            self.policies.append(PolicyDocument.from_yaml(path))
        for path in sorted(directory.glob("*.yml")):
            self.policies.append(PolicyDocument.from_yaml(path))

    def evaluate(
        self, 
        context: dict[str, Any],
        maturity_level: int = 0,  # M34: 成长阶段参数
    ) -> PolicyDecision:
        """Evaluate all loaded policy rules against the given context.

        Rules are sorted by priority (descending). The first matching rule
        determines the decision. If no rule matches, the default action from
        the first policy (or global allow) is used.
        
        Args:
            context: Execution context dict with fields like 'tool_name', 'token_count'
            maturity_level: M34 - 成长阶段级别 (命题7: 小学到大学)
                0=婴儿期(最严格), 6=研究生(最宽松)
                会影响规则的maturity_gates评估
        """
        try:
            all_rules: list[tuple[PolicyRule, PolicyDocument]] = []
            for doc in self.policies:
                for rule in doc.rules:
                    all_rules.append((rule, doc))

            # Sort by priority descending so highest priority is checked first
            all_rules.sort(key=lambda pair: pair[0].priority, reverse=True)

            for rule, doc in all_rules:
                # === M34: 检查maturity_gates ===
                gate_result = self._check_maturity_gates(rule, maturity_level)
                if gate_result.get("skip_rule"):
                    # 在当前maturity_level下跳过此规则
                    logger.debug(
                        f"Skipping rule '{rule.name}' due to maturity_gate "
                        f"(maturity_level={maturity_level})"
                    )
                    continue
                
                if _match_condition(rule.condition, context):
                    # M34: 使用gate的action_override如果有
                    effective_action = gate_result.get("action_override") or rule.action
                    effective_message = gate_result.get("message_override") or rule.message
                    
                    allowed = effective_action in (PolicyAction.ALLOW, PolicyAction.AUDIT)
                    return PolicyDecision(
                        allowed=allowed,
                        matched_rule=rule.name,
                        action=effective_action.value if hasattr(effective_action, 'value') else str(effective_action),
                        reason=effective_message or f"Matched rule '{rule.name}'",
                        audit_entry={
                            "policy": doc.name,
                            "rule": rule.name,
                            "action": effective_action.value if hasattr(effective_action, 'value') else str(effective_action),
                            "context_snapshot": context,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "maturity_level": maturity_level,  # M34: 记录成长阶段
                            "is_critical": getattr(rule, 'is_critical', False),
                        },
                    )

            # No rule matched — apply defaults
            default_action = PolicyAction.ALLOW
            if self.policies:
                default_action = self.policies[0].defaults.action
            allowed = default_action in (PolicyAction.ALLOW, PolicyAction.AUDIT)
            return PolicyDecision(
                allowed=allowed,
                action=default_action.value,
                reason="No rules matched; default action applied",
                audit_entry={
                    "policy": self.policies[0].name if self.policies else None,
                    "rule": None,
                    "action": default_action.value,
                    "context_snapshot": context,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "maturity_level": maturity_level,
                },
            )
        except Exception:
            logger.error(
                "Policy evaluation error — denying access (fail closed)",
                exc_info=True,
            )
            return PolicyDecision(
                allowed=False,
                action="deny",
                reason="Policy evaluation error — access denied (fail closed)",
                audit_entry={
                    "policy": None,
                    "rule": None,
                    "action": "deny",
                    "context_snapshot": context,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": True,
                    "maturity_level": maturity_level,
                },
            )

    def _check_maturity_gates(
        self, 
        rule: PolicyRule, 
        maturity_level: int
    ) -> dict[str, Any]:
        """检查规则的maturity_gates并返回适用的覆盖。
        
        Args:
            rule: 要检查的策略规则
            maturity_level: 当前成长阶段级别
            
        Returns:
            Dict containing:
                - skip_rule: 是否跳过此规则
                - action_override: 覆盖的action（如果有）
                - message_override: 覆盖的消息（如果有）
        """
        result: dict[str, Any] = {
            "skip_rule": False,
            "action_override": None,
            "message_override": None,
        }
        
        # M34: 关键规则不受maturity_level影响
        if getattr(rule, 'is_critical', False):
            return result
        
        # 检查maturity_gates
        maturity_gates = getattr(rule, 'maturity_gates', [])
        for gate in maturity_gates:
            if gate.min_level <= maturity_level <= gate.max_level:
                if gate.skip_rule:
                    result["skip_rule"] = True
                if gate.action_override:
                    result["action_override"] = gate.action_override
                if gate.message_override:
                    result["message_override"] = gate.message_override
                # 第一个匹配的gate生效
                break
        
        return result


def _match_condition(condition: Any, context: dict[str, Any]) -> bool:
    """Check whether a single PolicyCondition matches the context."""
    ctx_value = context.get(condition.field)
    if ctx_value is None:
        return False

    op = condition.operator
    target = condition.value

    if op == PolicyOperator.EQ:
        return ctx_value == target
    if op == PolicyOperator.NE:
        return ctx_value != target
    if op == PolicyOperator.GT:
        return ctx_value > target
    if op == PolicyOperator.LT:
        return ctx_value < target
    if op == PolicyOperator.GTE:
        return ctx_value >= target
    if op == PolicyOperator.LTE:
        return ctx_value <= target
    if op == PolicyOperator.IN:
        return ctx_value in target
    if op == PolicyOperator.CONTAINS:
        return target in ctx_value
    if op == PolicyOperator.MATCHES:
        return bool(re.search(str(target), str(ctx_value)))

    return False

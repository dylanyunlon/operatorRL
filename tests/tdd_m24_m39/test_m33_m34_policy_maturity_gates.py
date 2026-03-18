"""
TDD Tests for M33: MaturityGate in PolicyRule schema
              M34: PolicyEvaluator maturity_gates evaluation

TEST-DRIVEN DEVELOPMENT:
- M33: MaturityGate model with min_level, max_level, action_override, skip_rule
- M33: PolicyRule has maturity_gates list and is_critical flag
- M34: PolicyEvaluator.evaluate() accepts maturity_level parameter
- M34: _check_maturity_gates() respects critical rules
- M34: Gates can skip rules or override actions based on maturity level
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))


class TestM33MaturityGateSchema:
    """Tests for MaturityGate and PolicyRule schema."""

    def test_01_maturity_gate_importable(self):
        """MaturityGate must be importable from policies.schema."""
        from agent_os.policies.schema import MaturityGate
        assert MaturityGate is not None

    def test_02_maturity_gate_default_values(self):
        """MaturityGate must have sensible defaults."""
        from agent_os.policies.schema import MaturityGate
        gate = MaturityGate()
        assert gate.min_level == 0
        assert gate.max_level == 6
        assert gate.action_override is None
        assert gate.skip_rule is False
        assert gate.message_override == ""

    def test_03_maturity_gate_custom_values(self):
        """MaturityGate must accept custom values."""
        from agent_os.policies.schema import MaturityGate, PolicyAction
        gate = MaturityGate(
            min_level=4, max_level=6,
            action_override=PolicyAction.ALLOW,
            skip_rule=False,
            message_override="Allowed for high maturity"
        )
        assert gate.min_level == 4
        assert gate.max_level == 6
        assert gate.action_override == PolicyAction.ALLOW

    def test_04_policy_rule_has_maturity_gates(self):
        """PolicyRule must have maturity_gates field."""
        from agent_os.policies.schema import PolicyRule, PolicyCondition, PolicyOperator, PolicyAction
        rule = PolicyRule(
            name="test_rule",
            condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="dangerous"),
            action=PolicyAction.DENY,
        )
        assert hasattr(rule, 'maturity_gates')
        assert isinstance(rule.maturity_gates, list)

    def test_05_policy_rule_has_is_critical(self):
        """PolicyRule must have is_critical field."""
        from agent_os.policies.schema import PolicyRule, PolicyCondition, PolicyOperator, PolicyAction
        rule = PolicyRule(
            name="critical_rule",
            condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="nuke"),
            action=PolicyAction.DENY,
            is_critical=True,
        )
        assert rule.is_critical is True


class TestM34PolicyEvaluatorMaturity:
    """Tests for PolicyEvaluator maturity_gates evaluation."""

    def _make_doc_with_gate(self, skip_at_level=4, is_critical=False):
        """Helper to create a PolicyDocument with a maturity-gated rule."""
        from agent_os.policies.schema import (
            PolicyDocument, PolicyRule, PolicyCondition, PolicyOperator,
            PolicyAction, PolicyDefaults, MaturityGate,
        )
        gate = MaturityGate(min_level=skip_at_level, max_level=6, skip_rule=True)
        rule = PolicyRule(
            name="block_dangerous",
            condition=PolicyCondition(field="tool_name", operator=PolicyOperator.EQ, value="dangerous"),
            action=PolicyAction.DENY,
            maturity_gates=[gate],
            is_critical=is_critical,
        )
        return PolicyDocument(
            name="test_policy",
            rules=[rule],
            defaults=PolicyDefaults(action=PolicyAction.ALLOW),
        )

    def test_06_evaluate_accepts_maturity_level(self):
        """PolicyEvaluator.evaluate() must accept maturity_level parameter."""
        from agent_os.policies.evaluator import PolicyEvaluator
        evaluator = PolicyEvaluator()
        # Should not raise
        result = evaluator.evaluate({"tool_name": "safe"}, maturity_level=3)
        assert result.allowed is True

    def test_07_low_maturity_rule_enforced(self):
        """At low maturity, gated DENY rule should block."""
        from agent_os.policies.evaluator import PolicyEvaluator
        doc = self._make_doc_with_gate(skip_at_level=4)
        evaluator = PolicyEvaluator(policies=[doc])
        result = evaluator.evaluate({"tool_name": "dangerous"}, maturity_level=2)
        assert result.allowed is False
        assert result.matched_rule == "block_dangerous"

    def test_08_high_maturity_rule_skipped(self):
        """At high maturity (>= gate level), gated rule should be skipped → allowed."""
        from agent_os.policies.evaluator import PolicyEvaluator
        doc = self._make_doc_with_gate(skip_at_level=4)
        evaluator = PolicyEvaluator(policies=[doc])
        result = evaluator.evaluate({"tool_name": "dangerous"}, maturity_level=5)
        # Rule skipped, defaults to ALLOW
        assert result.allowed is True

    def test_09_critical_rule_never_skipped(self):
        """Critical rules must NEVER be skipped regardless of maturity."""
        from agent_os.policies.evaluator import PolicyEvaluator
        doc = self._make_doc_with_gate(skip_at_level=0, is_critical=True)
        evaluator = PolicyEvaluator(policies=[doc])
        result = evaluator.evaluate({"tool_name": "dangerous"}, maturity_level=6)
        # Critical rule should still fire
        assert result.allowed is False

    def test_10_maturity_recorded_in_audit(self):
        """maturity_level must be recorded in audit_entry."""
        from agent_os.policies.evaluator import PolicyEvaluator
        doc = self._make_doc_with_gate(skip_at_level=4)
        evaluator = PolicyEvaluator(policies=[doc])
        result = evaluator.evaluate({"tool_name": "dangerous"}, maturity_level=2)
        assert "maturity_level" in result.audit_entry
        assert result.audit_entry["maturity_level"] == 2

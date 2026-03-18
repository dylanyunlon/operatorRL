"""
TDD Tests for M31: StatelessKernel real_world_feedback field
              M32: Maturity-level policy degradation

TEST-DRIVEN DEVELOPMENT:
- M31: ExecutionResult must have real_world_feedback dict field
- M32: ExecutionContext must have maturity_level field
- M32: _check_policies must respect maturity_level:
  - maturity >= 4 allows non-critical blocked actions
  - maturity >= 5 grants auto-approval for non-critical
  - blocked_patterns always strict (no maturity bypass)
  - critical policies never degraded
"""

import os
import sys
import pytest
import asyncio

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))


class TestM31RealWorldFeedback:
    """Tests for M31: ExecutionResult.real_world_feedback."""

    def test_01_execution_result_has_real_world_feedback(self):
        """ExecutionResult must have real_world_feedback field."""
        from agent_os.stateless import ExecutionResult
        result = ExecutionResult(success=True, data=None)
        assert hasattr(result, 'real_world_feedback')

    def test_02_real_world_feedback_default_empty_dict(self):
        """real_world_feedback defaults to empty dict."""
        from agent_os.stateless import ExecutionResult
        result = ExecutionResult(success=True, data=None)
        assert result.real_world_feedback == {}

    def test_03_real_world_feedback_can_store_http_status(self):
        """real_world_feedback can store HTTP status codes."""
        from agent_os.stateless import ExecutionResult
        result = ExecutionResult(
            success=True, data=None,
            real_world_feedback={"http_status": 200, "response_body": "OK"}
        )
        assert result.real_world_feedback["http_status"] == 200


class TestM32MaturityLevel:
    """Tests for M32: maturity_level in ExecutionContext and policy checking."""

    def test_04_execution_context_has_maturity_level(self):
        """ExecutionContext must have maturity_level field."""
        from agent_os.stateless import ExecutionContext
        ctx = ExecutionContext(agent_id="test-agent")
        assert hasattr(ctx, 'maturity_level')
        assert ctx.maturity_level == 0  # default

    def test_05_maturity_level_in_to_dict(self):
        """maturity_level must appear in to_dict() output."""
        from agent_os.stateless import ExecutionContext
        ctx = ExecutionContext(agent_id="test", maturity_level=3)
        d = ctx.to_dict()
        assert "maturity_level" in d
        assert d["maturity_level"] == 3

    def test_06_low_maturity_blocks_action(self):
        """maturity_level=0 should NOT bypass blocked actions."""
        from agent_os.stateless import StatelessKernel
        kernel = StatelessKernel()
        result = kernel._check_policies(
            action="file_write",
            params={},
            policy_names=["read_only"],
            maturity_level=0,
        )
        assert result["allowed"] is False

    def test_07_high_maturity_allows_noncritical_action(self):
        """maturity_level=4+ should allow non-critical blocked actions."""
        from agent_os.stateless import StatelessKernel
        kernel = StatelessKernel()
        result = kernel._check_policies(
            action="file_write",
            params={},
            policy_names=["read_only"],
            maturity_level=4,
        )
        # read_only is not marked critical, so maturity=4 should allow
        assert result["allowed"] is True

    def test_08_critical_policy_never_bypassed(self):
        """Critical policies (strict, no_pii) must never be bypassed by maturity."""
        from agent_os.stateless import StatelessKernel
        kernel = StatelessKernel()
        # no_pii is considered critical
        result = kernel._check_policies(
            action="query",
            params={"data": "ssn: 123-45-6789"},
            policy_names=["no_pii"],
            maturity_level=6,  # max maturity
        )
        assert result["allowed"] is False

    def test_09_blocked_patterns_always_strict(self):
        """blocked_patterns should ALWAYS be enforced regardless of maturity."""
        from agent_os.stateless import StatelessKernel
        kernel = StatelessKernel()
        result = kernel._check_policies(
            action="search",
            params={"query": "credit_card number"},
            policy_names=["no_pii"],
            maturity_level=6,
        )
        assert result["allowed"] is False

    def test_10_maturity_5_auto_approval(self):
        """maturity_level=5+ should grant auto-approval for non-critical require_approval actions."""
        from agent_os.stateless import StatelessKernel
        kernel = StatelessKernel()
        # 'strict' policy requires approval for send_email, BUT 'strict' is critical
        # So let's test with a custom non-critical policy
        kernel.policies["custom_approval"] = {
            "require_approval": ["deploy"],
        }
        result = kernel._check_policies(
            action="deploy",
            params={},
            policy_names=["custom_approval"],
            maturity_level=5,
        )
        assert result["allowed"] is True

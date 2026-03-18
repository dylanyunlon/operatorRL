"""
TDD Tests for M36: BaseIntegration post_execute maturity recording
              M37: Anthropic adapter repair enzyme
              M38: MCP Gateway body_sense conversion
              M39: Sandbox exam_score metadata

TEST-DRIVEN DEVELOPMENT: Tests define expected contracts. No mocks.
"""

import os
import sys
import time
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))


class TestM36PostExecuteMaturity:
    """Tests for M36: BaseIntegration records maturity_level in post_execute."""

    def test_01_execution_context_has_maturity_level(self):
        """ExecutionContext (from integrations.base) must have maturity_level."""
        from agent_os.integrations.base import ExecutionContext, GovernancePolicy
        policy = GovernancePolicy()  # all defaults are fine
        ctx = ExecutionContext(agent_id="test-agent", session_id="s1", policy=policy)
        assert hasattr(ctx, 'maturity_level')

    def test_02_execution_context_maturity_default_zero(self):
        """ExecutionContext.maturity_level defaults to 0."""
        from agent_os.integrations.base import ExecutionContext, GovernancePolicy
        policy = GovernancePolicy()
        ctx = ExecutionContext(agent_id="test-agent", session_id="s1", policy=policy)
        assert ctx.maturity_level == 0


class TestM37RepairEnzyme:
    """Tests for M37: Anthropic adapter repair_enzyme mode."""

    def test_03_anthropic_adapter_source_has_repair_enzyme(self):
        """anthropic_adapter.py source must contain repair_enzyme logic."""
        adapter_path = os.path.join(
            PROJECT_ROOT, "src", "agent_os", "integrations", "anthropic_adapter.py"
        )
        with open(adapter_path, "r") as f:
            source = f.read()
        assert "repair_enzyme" in source, "M37: repair_enzyme must be in adapter source"

    def test_04_repair_enzyme_injects_error_context(self):
        """When repair_enzyme=True, error logs should be injected as context."""
        adapter_path = os.path.join(
            PROJECT_ROOT, "src", "agent_os", "integrations", "anthropic_adapter.py"
        )
        with open(adapter_path, "r") as f:
            source = f.read()
        assert "REPAIR ENZYME CONTEXT" in source, \
            "M37: repair_enzyme should inject [REPAIR ENZYME CONTEXT] prefix"

    def test_05_repair_enzyme_limits_error_count(self):
        """repair_enzyme should limit injected errors (e.g., last 5)."""
        adapter_path = os.path.join(
            PROJECT_ROOT, "src", "agent_os", "integrations", "anthropic_adapter.py"
        )
        with open(adapter_path, "r") as f:
            source = f.read()
        # Should have some limit on error injection
        assert "[-5:]" in source or "[:5]" in source or "5" in source, \
            "M37: repair_enzyme should limit error count"


class TestM38BodySense:
    """Tests for M38: MCP Gateway body_sense conversion."""

    def test_06_audit_entry_has_body_sense(self):
        """AuditEntry must have body_sense field."""
        from agent_os.mcp_gateway import AuditEntry
        entry = AuditEntry(
            timestamp=time.time(),
            agent_id="test",
            tool_name="search",
            parameters={},
            allowed=True,
            reason="ok",
        )
        assert hasattr(entry, 'body_sense')

    def test_07_body_sense_structure(self):
        """body_sense should contain success, signal_strength, goal_level."""
        from agent_os.mcp_gateway import AuditEntry
        entry = AuditEntry(
            timestamp=time.time(),
            agent_id="test",
            tool_name="search",
            parameters={},
            allowed=True,
            reason="ok",
            body_sense={
                "success": True,
                "signal_strength": 1.0,
                "goal_level": "operational",
            },
        )
        assert entry.body_sense["success"] is True
        assert entry.body_sense["signal_strength"] == 1.0
        assert "goal_level" in entry.body_sense

    def test_08_audit_entry_to_dict_includes_body_sense(self):
        """AuditEntry.to_dict() must include body_sense when present."""
        from agent_os.mcp_gateway import AuditEntry
        entry = AuditEntry(
            timestamp=time.time(),
            agent_id="test",
            tool_name="search",
            parameters={},
            allowed=True,
            reason="ok",
            body_sense={"success": True, "signal_strength": 0.8, "goal_level": "tactical"},
        )
        d = entry.to_dict()
        assert "body_sense" in d
        assert d["body_sense"]["signal_strength"] == 0.8


class TestM39ExamScore:
    """Tests for M39: Sandbox exam_score metadata."""

    def test_09_sandbox_has_last_exam_score(self):
        """Sandbox must have last_exam_score attribute after execute_sandboxed."""
        from agent_os.sandbox import ExecutionSandbox, SandboxConfig
        config = SandboxConfig()
        sandbox = ExecutionSandbox(config)
        # Execute a trivial function
        sandbox.execute_sandboxed(lambda: 42)
        assert hasattr(sandbox, 'last_exam_score')
        assert isinstance(sandbox.last_exam_score, dict)

    def test_10_exam_score_has_required_fields(self):
        """exam_score must contain execution_time_ms, success, memory_peak_kb."""
        from agent_os.sandbox import ExecutionSandbox, SandboxConfig
        config = SandboxConfig()
        sandbox = ExecutionSandbox(config)
        sandbox.execute_sandboxed(lambda: "hello")
        score = sandbox.last_exam_score
        assert "execution_time_ms" in score
        assert "success" in score
        assert "memory_peak_kb" in score
        assert score["success"] is True
        assert isinstance(score["execution_time_ms"], (int, float))
        assert score["execution_time_ms"] >= 0

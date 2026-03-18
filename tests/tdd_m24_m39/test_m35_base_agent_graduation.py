"""
TDD Tests for M35: BaseAgent maturity_level and _check_graduation

TEST-DRIVEN DEVELOPMENT:
- BaseAgent has maturity_level property (0-6)
- maturity_level setter clamps to [0, 6]
- _check_graduation checks last 10 audit entries
- Requires success_rate > 80% to level up
- Already at max level (6) returns False
- Less than 10 entries returns False
"""

import os
import sys
import pytest
import asyncio
from dataclasses import dataclass
from typing import Any

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))


def _make_agent():
    """Create a minimal BaseAgent subclass for testing."""
    from agent_os.base_agent import BaseAgent, AgentConfig
    from agent_os.stateless import ExecutionResult

    class TestAgent(BaseAgent):
        async def _execute_action(self, action: str, params: dict) -> Any:
            return {"data": "ok"}
        
        async def run(self, *args, **kwargs) -> ExecutionResult:
            return ExecutionResult(success=True, data="ok")

    config = AgentConfig(
        agent_id="test-grad-agent",
        policies=["read_only"],
    )
    return TestAgent(config)


class TestM35MaturityProperty:
    """Tests for BaseAgent.maturity_level property."""

    def test_01_maturity_level_default_zero(self):
        """maturity_level defaults to 0."""
        agent = _make_agent()
        assert agent.maturity_level == 0

    def test_02_maturity_level_settable(self):
        """maturity_level can be set."""
        agent = _make_agent()
        agent.maturity_level = 3
        assert agent.maturity_level == 3

    def test_03_maturity_level_clamped_upper(self):
        """maturity_level is clamped to max 6."""
        agent = _make_agent()
        agent.maturity_level = 100
        assert agent.maturity_level == 6

    def test_04_maturity_level_clamped_lower(self):
        """maturity_level is clamped to min 0."""
        agent = _make_agent()
        agent.maturity_level = -5
        assert agent.maturity_level == 0

    def test_05_maturity_level_all_valid_values(self):
        """All values 0-6 should be accepted as-is."""
        agent = _make_agent()
        for level in range(7):
            agent.maturity_level = level
            assert agent.maturity_level == level


class TestM35CheckGraduation:
    """Tests for BaseAgent._check_graduation method."""

    def _make_audit_entry(self, decision="allow"):
        """Create a minimal audit entry."""
        @dataclass
        class FakeAuditEntry:
            decision: str
        return FakeAuditEntry(decision=decision)

    def test_06_check_graduation_exists(self):
        """_check_graduation method must exist."""
        agent = _make_agent()
        assert hasattr(agent, '_check_graduation')
        assert callable(agent._check_graduation)

    def test_07_not_enough_entries_returns_false(self):
        """Less than 10 audit entries should return False."""
        agent = _make_agent()
        agent._audit_log = [self._make_audit_entry("allow") for _ in range(5)]
        assert agent._check_graduation() is False

    def test_08_high_success_rate_graduates(self):
        """10+ entries with >80% success should return True and increment level."""
        agent = _make_agent()
        agent.maturity_level = 2
        # 9 allow + 1 deny = 90% success rate
        agent._audit_log = (
            [self._make_audit_entry("allow") for _ in range(9)] +
            [self._make_audit_entry("deny")]
        )
        result = agent._check_graduation()
        assert result is True
        assert agent.maturity_level == 3

    def test_09_low_success_rate_no_graduation(self):
        """10 entries with <=80% success should return False."""
        agent = _make_agent()
        agent.maturity_level = 1
        # 7 allow + 3 deny = 70% success rate
        agent._audit_log = (
            [self._make_audit_entry("allow") for _ in range(7)] +
            [self._make_audit_entry("deny") for _ in range(3)]
        )
        result = agent._check_graduation()
        assert result is False
        assert agent.maturity_level == 1  # unchanged

    def test_10_max_level_no_graduation(self):
        """Already at max level (6) should return False."""
        agent = _make_agent()
        agent.maturity_level = 6
        agent._audit_log = [self._make_audit_entry("allow") for _ in range(20)]
        result = agent._check_graduation()
        assert result is False
        assert agent.maturity_level == 6

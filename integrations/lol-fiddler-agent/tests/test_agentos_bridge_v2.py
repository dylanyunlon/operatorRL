"""
TDD Tests for M256: AgentOSBridgeV2 — GovernedEnvironment integration for LoL.

10 tests: construction, execute, governance checks, reward signal,
audit logging, health monitoring, graceful degradation.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestAgentOSBridgeV2Construction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        assert bridge is not None

    def test_has_execute(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        assert callable(getattr(bridge, "execute", None))


class TestAgentOSBridgeV2Execute:
    def test_execute_returns_result(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        result = bridge.execute({"action": "analyze", "data": {}})
        assert result is not None
        assert hasattr(result, "success") or "success" in result

    def test_execute_with_violation(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        result = bridge.execute({"action": "__test_violation__"})
        assert result is not None


class TestAgentOSBridgeV2Governance:
    def test_policy_check(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        violations = bridge.check_policies({"action": "analyze"})
        assert isinstance(violations, list)

    def test_reward_signal(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        bridge.execute({"action": "analyze", "data": {}})
        reward = bridge.get_last_reward()
        assert isinstance(reward, (int, float))


class TestAgentOSBridgeV2Audit:
    def test_audit_log_recorded(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        bridge.execute({"action": "analyze", "data": {}})
        assert len(bridge.audit_log) >= 1

    def test_audit_entry_has_timestamp(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        bridge.execute({"action": "analyze", "data": {}})
        entry = bridge.audit_log[0]
        assert "timestamp" in entry


class TestAgentOSBridgeV2Health:
    def test_health_check(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import AgentOSBridgeV2
        bridge = AgentOSBridgeV2()
        status = bridge.health_status()
        assert status in ("healthy", "degraded", "unhealthy")

    def test_evolution_key(self):
        from lol_fiddler_agent.integrations.agentos_bridge_v2 import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

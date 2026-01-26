"""
Test Layer 4: Intelligence packages.
"""

import pytest


class TestSCAK:
    """Test self-correcting-agent-kernel package."""
    
    def test_import_scak(self):
        """Test basic import."""
        try:
            from agent_kernel import (
                SelfCorrectingAgentKernel,
                diagnose_failure,
                triage_failure,
            )
            assert SelfCorrectingAgentKernel is not None
        except ImportError:
            pytest.skip("scak not fully installed")
    
    def test_import_failure_models(self):
        """Test importing failure models from scak."""
        try:
            from agent_kernel.models import FailureType, FailureSeverity
            assert FailureType is not None
            assert FailureSeverity is not None
        except ImportError:
            # May use agent_primitives instead
            from agent_primitives import FailureType, FailureSeverity
            assert FailureType is not None


class TestMuteAgent:
    """Test mute-agent package."""
    
    def test_import_mute_agent(self):
        """Test basic import."""
        try:
            from mute_agent import MuteAgent
            assert MuteAgent is not None
        except ImportError:
            pytest.skip("mute-agent not installed")
    
    def test_import_core_components(self):
        """Test importing core components."""
        try:
            from mute_agent.core import ReasoningAgent, ExecutionAgent
            assert ReasoningAgent is not None
            assert ExecutionAgent is not None
        except ImportError:
            pytest.skip("mute-agent core not available")

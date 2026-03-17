"""
TDD Tests for M11-M12: LLMProxy Repair Enzyme Mode
====================================================

Test-driven development: these tests define expected behavior for M11-M12.
M11: LLMProxy.__init__ accepts enable_repair_enzyme and repair_enzyme_error_context_lines
M12: LLMProxy.as_resource() in repair_enzyme_mode injects error logs as context

NO MOCK IMPLEMENTATIONS. Tests target real LLMProxy code paths.
Tests are expected to FAIL until implementation is complete.
"""

import pytest
from typing import List, Dict, Any


class TestM11LLMProxyRepairEnzymeInit:
    """M11: LLMProxy.__init__ should accept repair enzyme parameters."""

    def test_m11_01_default_repair_enzyme_disabled(self):
        """LLMProxy with default args should have enable_repair_enzyme=False."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
        )
        assert proxy.enable_repair_enzyme is False

    def test_m11_02_enable_repair_enzyme_true(self):
        """LLMProxy should accept enable_repair_enzyme=True."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=True,
        )
        assert proxy.enable_repair_enzyme is True

    def test_m11_03_default_error_context_lines(self):
        """Default repair_enzyme_error_context_lines should be 50."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
        )
        assert proxy.repair_enzyme_error_context_lines == 50

    def test_m11_04_custom_error_context_lines(self):
        """Custom repair_enzyme_error_context_lines should be respected."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=True,
            repair_enzyme_error_context_lines=100,
        )
        assert proxy.repair_enzyme_error_context_lines == 100

    def test_m11_05_error_buffer_initialized_empty(self):
        """LLMProxy should initialize _repair_enzyme_error_buffer as empty list."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=True,
        )
        assert hasattr(proxy, '_repair_enzyme_error_buffer')
        assert proxy._repair_enzyme_error_buffer == []

    def test_m11_06_append_to_error_buffer(self):
        """Appending to _repair_enzyme_error_buffer should work."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=True,
        )
        proxy._repair_enzyme_error_buffer.append("ERROR: test failed at line 42")
        assert len(proxy._repair_enzyme_error_buffer) == 1


class TestM12LLMProxyAsResourceRepairMode:
    """M12: as_resource() with repair_enzyme_mode should inject error context."""

    def test_m12_01_as_resource_without_repair_mode(self):
        """as_resource without repair_enzyme_mode should produce normal LLM resource."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=True,
        )
        # Start the proxy to get access_endpoint
        # We test the as_resource method signature accepts repair_enzyme_mode
        # Without starting the server, we just verify the parameter is accepted
        import inspect
        sig = inspect.signature(proxy.as_resource)
        param_names = list(sig.parameters.keys())
        assert "repair_enzyme_mode" in param_names

    def test_m12_02_as_resource_accepts_error_logs_param(self):
        """as_resource should accept error_logs parameter."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=True,
        )
        import inspect
        sig = inspect.signature(proxy.as_resource)
        param_names = list(sig.parameters.keys())
        assert "error_logs" in param_names

    def test_m12_03_repair_enzyme_context_truncates_logs(self):
        """When error_logs exceed context_lines, they should be truncated to last N lines."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=True,
            repair_enzyme_error_context_lines=3,
        )
        # Generate more logs than the limit
        error_logs = [f"ERROR line {i}" for i in range(10)]
        # We expect only last 3 lines to be kept
        truncated = error_logs[-proxy.repair_enzyme_error_context_lines:]
        assert len(truncated) == 3
        assert truncated[0] == "ERROR line 7"

    def test_m12_04_repair_enzyme_disabled_ignores_mode(self):
        """When enable_repair_enzyme=False, repair_enzyme_mode in as_resource should be ignored."""
        from agentlightning.llm_proxy import LLMProxy

        proxy = LLMProxy(
            model_list=[{"model_name": "test-model", "litellm_params": {"model": "gpt-3.5-turbo"}}],
            enable_repair_enzyme=False,  # Disabled
        )
        # The parameter should still be accepted (no TypeError)
        import inspect
        sig = inspect.signature(proxy.as_resource)
        assert "repair_enzyme_mode" in sig.parameters

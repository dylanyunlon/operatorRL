"""
TDD Tests for M44: tracer/base.py — Auto-attach maturity_level to Spans

TEST-DRIVEN DEVELOPMENT: Tests define expected behavior for Tracer.create_span()
automatically injecting agent.maturity_level into span attributes.

Expected behavior:
- Tracer base class create_span docstring mentions maturity_level auto-injection
- create_span signature accepts attributes parameter
- M44 marker comment exists in tracer/base.py
- The docstring documents that maturity_level is auto-injected
- Tracer class has no hardcoded implementation (abstract base)
- set_active_tracer/get_active_tracer/clear_active_tracer work correctly
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


class TestM44TracerBaseMaturity:
    """M44: Tracer.create_span must document maturity_level auto-injection."""

    def test_01_tracer_base_has_m44_marker(self):
        """tracer/base.py must contain M44 modification marker."""
        source = _read_source("agentlightning/tracer/base.py")
        assert "M44" in source

    def test_02_tracer_class_exists(self):
        """Tracer class must be importable from tracer.base."""
        from agentlightning.tracer.base import Tracer
        assert Tracer is not None

    def test_03_create_span_exists(self):
        """Tracer must have create_span method."""
        from agentlightning.tracer.base import Tracer
        assert hasattr(Tracer, "create_span")
        assert callable(getattr(Tracer, "create_span"))

    def test_04_create_span_docstring_mentions_maturity(self):
        """create_span docstring must mention maturity_level auto-injection."""
        from agentlightning.tracer.base import Tracer
        doc = Tracer.create_span.__doc__ or ""
        assert "maturity_level" in doc

    def test_05_create_span_has_attributes_param(self):
        """create_span must accept attributes parameter."""
        from agentlightning.tracer.base import Tracer
        import inspect
        sig = inspect.signature(Tracer.create_span)
        assert "attributes" in sig.parameters

    def test_06_create_span_has_name_param(self):
        """create_span must accept name as first positional arg."""
        from agentlightning.tracer.base import Tracer
        import inspect
        sig = inspect.signature(Tracer.create_span)
        params = list(sig.parameters.keys())
        assert "name" in params

    def test_07_create_span_raises_not_implemented(self):
        """Base Tracer.create_span must raise NotImplementedError."""
        from agentlightning.tracer.base import Tracer
        tracer = Tracer()
        with pytest.raises(NotImplementedError):
            tracer.create_span("test_span")

    def test_08_set_active_tracer_works(self):
        """set_active_tracer must set the global active tracer."""
        from agentlightning.tracer.base import (
            Tracer, set_active_tracer, get_active_tracer, clear_active_tracer
        )
        clear_active_tracer()  # ensure clean state
        t = Tracer()
        set_active_tracer(t)
        assert get_active_tracer() is t
        clear_active_tracer()

    def test_09_set_active_tracer_raises_if_already_set(self):
        """set_active_tracer must raise ValueError if a tracer is already active."""
        from agentlightning.tracer.base import (
            Tracer, set_active_tracer, get_active_tracer, clear_active_tracer
        )
        clear_active_tracer()
        t1 = Tracer()
        t2 = Tracer()
        set_active_tracer(t1)
        with pytest.raises(ValueError):
            set_active_tracer(t2)
        clear_active_tracer()

    def test_10_create_span_docstring_mentions_agent_maturity_level_attr(self):
        """create_span docstring must reference agent.maturity_level attribute key."""
        from agentlightning.tracer.base import Tracer
        doc = Tracer.create_span.__doc__ or ""
        assert "agent.maturity_level" in doc

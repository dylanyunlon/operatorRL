"""
TDD Tests for M47: types/core.py — Span Type Hints for Emergent Signals

TEST-DRIVEN DEVELOPMENT: Tests verify Span model includes proper type hints
for agent_os.emergent_signal and agent_os.repair_enzyme_triggered fields.

Expected behavior:
- Span has maturity_level field (int, default 0)
- Span has emergent_signal field (Optional[str], default None)
- Span has emergent_score field (float, default 0.0)
- Rollout has maturity_level, emergent_signals, growth_stage fields
- Type comments document agent_os.emergent_signal attribute in Rollout
- Type comments document agent_os.repair_enzyme_triggered attribute
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


class TestM47SpanTypeHints:
    """M47: Span and Rollout must have emergent_signal/repair_enzyme type hints."""

    def test_01_span_has_maturity_level_field(self):
        """Span model must include maturity_level as int with default 0."""
        from agentlightning.types.tracer import Span
        fields = Span.model_fields
        assert "maturity_level" in fields
        assert fields["maturity_level"].default == 0

    def test_02_span_has_emergent_signal_field(self):
        """Span model must include emergent_signal as Optional[str]."""
        from agentlightning.types.tracer import Span
        fields = Span.model_fields
        assert "emergent_signal" in fields
        assert fields["emergent_signal"].default is None

    def test_03_span_has_emergent_score_field(self):
        """Span model must include emergent_score as float with default 0.0."""
        from agentlightning.types.tracer import Span
        fields = Span.model_fields
        assert "emergent_score" in fields
        assert fields["emergent_score"].default == 0.0

    def test_04_span_source_mentions_agent_os_emergent_signal(self):
        """types/tracer.py or types/core.py must document agent_os.emergent_signal."""
        source_tracer = _read_source("agentlightning/types/tracer.py")
        source_core = _read_source("agentlightning/types/core.py")
        combined = source_tracer + source_core
        assert "agent_os.emergent_signal" in combined

    def test_05_source_mentions_repair_enzyme_triggered(self):
        """Source must document agent_os.repair_enzyme_triggered attribute."""
        source_core = _read_source("agentlightning/types/core.py")
        assert "repair_enzyme_triggered" in source_core

    def test_06_rollout_has_maturity_level(self):
        """Rollout model must have maturity_level field."""
        from agentlightning.types.core import Rollout
        assert "maturity_level" in Rollout.model_fields

    def test_07_rollout_has_growth_stage(self):
        """Rollout model must have growth_stage field."""
        from agentlightning.types.core import Rollout
        assert "growth_stage" in Rollout.model_fields

    def test_08_rollout_has_emergent_signals(self):
        """Rollout model must have emergent_signals count field."""
        from agentlightning.types.core import Rollout
        assert "emergent_signals" in Rollout.model_fields

    def test_09_span_m47_marker_in_source(self):
        """types/tracer.py must contain M47 or M44 marker for span fields."""
        source = _read_source("agentlightning/types/tracer.py")
        # M44-M47 markers should be present
        assert "M44" in source or "M47" in source

    def test_10_rollout_m47_marker_in_source(self):
        """types/core.py must contain M47 marker for Rollout type comments."""
        source = _read_source("agentlightning/types/core.py")
        assert "M47" in source

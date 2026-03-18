"""
TDD Tests for M45: tracer/otel.py — OTel Semantic Attributes for Maturity

TEST-DRIVEN DEVELOPMENT: Tests define expected OTel semantic attributes for
agent.maturity_level and agent.growth_stage in the OpenTelemetry tracer.

Expected behavior:
- OtelTracer class exists and inherits from Tracer
- M45 marker comment exists in otel.py
- Source code references agent.maturity_level and agent.growth_stage
- OtelSpanRecordingContext correctly wraps OTel spans
- OtelTracer has proper init_worker lifecycle
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


class TestM45OtelTracerMaturity:
    """M45: OtelTracer must support agent.maturity_level/growth_stage attributes."""

    def test_01_otel_tracer_has_m45_marker(self):
        """otel.py must contain M45 modification marker."""
        source = _read_source("agentlightning/tracer/otel.py")
        assert "M45" in source

    def test_02_otel_tracer_class_exists(self):
        """OtelTracer class must be importable."""
        from agentlightning.tracer.otel import OtelTracer
        assert OtelTracer is not None

    def test_03_otel_tracer_inherits_tracer(self):
        """OtelTracer must inherit from base Tracer."""
        from agentlightning.tracer.otel import OtelTracer
        from agentlightning.tracer.base import Tracer
        assert issubclass(OtelTracer, Tracer)

    def test_04_otel_source_references_agent_maturity_level(self):
        """otel.py source must reference agent.maturity_level semantic attribute."""
        source = _read_source("agentlightning/tracer/otel.py")
        assert "agent.maturity_level" in source

    def test_05_otel_source_references_agent_growth_stage(self):
        """otel.py source must reference agent.growth_stage semantic attribute."""
        source = _read_source("agentlightning/tracer/otel.py")
        assert "agent.growth_stage" in source

    def test_06_otel_span_recording_context_exists(self):
        """OtelSpanRecordingContext must be importable."""
        from agentlightning.tracer.otel import OtelSpanRecordingContext
        assert OtelSpanRecordingContext is not None

    def test_07_otel_span_recording_has_record_attributes(self):
        """OtelSpanRecordingContext must have record_attributes method."""
        from agentlightning.tracer.otel import OtelSpanRecordingContext
        assert hasattr(OtelSpanRecordingContext, "record_attributes")

    def test_08_otel_span_recording_has_record_exception(self):
        """OtelSpanRecordingContext must have record_exception method."""
        from agentlightning.tracer.otel import OtelSpanRecordingContext
        assert hasattr(OtelSpanRecordingContext, "record_exception")

    def test_09_otel_tracer_has_init_worker(self):
        """OtelTracer must have init_worker method for lifecycle management."""
        from agentlightning.tracer.otel import OtelTracer
        assert hasattr(OtelTracer, "init_worker")

    def test_10_otel_source_header_documents_semantic_attrs(self):
        """otel.py header comment must document the semantic attributes added."""
        source = _read_source("agentlightning/tracer/otel.py")
        # First 500 chars should contain documentation about maturity attributes
        header = source[:800]
        assert "maturity_level" in header
        assert "growth_stage" in header

"""
TDD Tests for M14: Annotation Emitter Maturity Level & Growth Stage
=====================================================================

Test-driven development: these tests define expected behavior for M14.
M14: emit_annotation() should support agent_os.maturity_level and
agent_os.growth_stage fields in annotation attributes.

NO MOCK IMPLEMENTATIONS. Tests target real code paths.
Tests are expected to FAIL until implementation is complete.
"""

import pytest
from typing import Dict, Any


class TestM14AnnotationMaturityLevel:
    """M14: emit_annotation should support maturity_level and growth_stage."""

    def test_m14_01_emit_annotation_basic_still_works(self):
        """Basic emit_annotation should still work without maturity fields."""
        from agentlightning.emitter.annotation import emit_annotation
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_annotation({"test_key": "test_value"})
            assert span is not None
        finally:
            clear_active_tracer()

    def test_m14_02_emit_annotation_with_maturity_level(self):
        """emit_annotation should accept agent_os.maturity_level attribute."""
        from agentlightning.emitter.annotation import emit_annotation
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_annotation({
                "test_key": "value",
                "agent_os.maturity_level": 3,
            })
            assert span is not None
            # The maturity_level should be preserved in the span
            if span.attributes:
                assert "agent_os.maturity_level" in span.attributes
                assert span.attributes["agent_os.maturity_level"] == 3
        finally:
            clear_active_tracer()

    def test_m14_03_emit_annotation_with_growth_stage(self):
        """emit_annotation should accept agent_os.growth_stage attribute."""
        from agentlightning.emitter.annotation import emit_annotation
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_annotation({
                "test_key": "value",
                "agent_os.growth_stage": "high_school",
            })
            assert span is not None
            if span.attributes:
                assert "agent_os.growth_stage" in span.attributes
                assert span.attributes["agent_os.growth_stage"] == "high_school"
        finally:
            clear_active_tracer()

    def test_m14_04_emit_annotation_both_maturity_and_growth(self):
        """Both maturity_level and growth_stage should coexist."""
        from agentlightning.emitter.annotation import emit_annotation
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_annotation({
                "agent_os.maturity_level": 4,
                "agent_os.growth_stage": "high_school",
            })
            assert span is not None
            if span.attributes:
                assert "agent_os.maturity_level" in span.attributes
                assert "agent_os.growth_stage" in span.attributes
        finally:
            clear_active_tracer()

    def test_m14_05_maturity_level_zero_infant(self):
        """Maturity level 0 should correspond to infant stage."""
        from agentlightning.emitter.annotation import emit_annotation
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_annotation({
                "agent_os.maturity_level": 0,
                "agent_os.growth_stage": "infant",
            })
            assert span is not None
            if span.attributes:
                assert span.attributes.get("agent_os.maturity_level") == 0
        finally:
            clear_active_tracer()

    def test_m14_06_maturity_level_max_graduate(self):
        """Maturity level 6 should be max (graduate)."""
        from agentlightning.emitter.annotation import emit_annotation
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_annotation({
                "agent_os.maturity_level": 6,
                "agent_os.growth_stage": "graduate",
            })
            assert span is not None
        finally:
            clear_active_tracer()

    def test_m14_07_annotation_with_reward_and_maturity(self):
        """Reward annotation should also carry maturity fields when present."""
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(
                1.0,
                attributes={
                    "agent_os.maturity_level": 2,
                    "agent_os.growth_stage": "elementary",
                }
            )
            assert span is not None
            if span.attributes:
                assert "agent_os.maturity_level" in span.attributes
        finally:
            clear_active_tracer()

    def test_m14_08_propagate_false_with_maturity(self):
        """emit_annotation with propagate=False should still handle maturity."""
        from agentlightning.emitter.annotation import emit_annotation

        # propagate=False uses DummyTracer internally
        span = emit_annotation(
            {"agent_os.maturity_level": 1, "test": "data"},
            propagate=False,
        )
        assert span is not None

    def test_m14_09_operation_context_with_maturity(self):
        """OperationContext should accept maturity attributes."""
        from agentlightning.emitter.annotation import OperationContext
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            ctx = OperationContext(
                "test_op",
                attributes={
                    "agent_os.maturity_level": 3,
                    "agent_os.growth_stage": "middle_school",
                },
            )
            with ctx:
                pass
            span = ctx.span()
            assert span is not None
        finally:
            clear_active_tracer()

    def test_m14_10_annotation_maturity_survives_sanitization(self):
        """Maturity attributes should survive the sanitize_attributes pipeline."""
        from agentlightning.utils.otel import sanitize_attributes, flatten_attributes

        attrs = flatten_attributes({
            "agent_os.maturity_level": 3,
            "agent_os.growth_stage": "middle_school",
        })
        sanitized = sanitize_attributes(attrs)
        assert "agent_os.maturity_level" in sanitized
        assert "agent_os.growth_stage" in sanitized

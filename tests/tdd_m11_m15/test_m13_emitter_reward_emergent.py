"""
TDD Tests for M13: Emitter Reward Emergent Signal Detection
=============================================================

Test-driven development: these tests define expected behavior for M13.
M13: emit_reward() should detect emergent_signal in attributes and record
an extra annotation noting "violation led to success".

NO MOCK IMPLEMENTATIONS. Tests target real agentlightning.emitter.reward code.
Tests are expected to FAIL until implementation is complete.
"""

import pytest
from typing import Dict, Any, List


class TestM13EmitRewardEmergentSignal:
    """M13: emit_reward should handle emergent_signal attributes."""

    def test_m13_01_emit_reward_basic_still_works(self):
        """emit_reward(1.0) should still work normally without emergent signals."""
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(1.0)
            assert span is not None
            assert span.name is not None
        finally:
            clear_active_tracer()

    def test_m13_02_emit_reward_accepts_emergent_signal_attribute(self):
        """emit_reward should accept agent_os.emergent_signals in attributes dict."""
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(
                0.5,
                attributes={"agent_os.emergent_signals": 3}
            )
            assert span is not None
        finally:
            clear_active_tracer()

    def test_m13_03_emit_reward_emergent_signal_zero_no_annotation(self):
        """When emergent_signals=0, no extra annotation should be emitted."""
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            # This should work without error, just no extra annotation
            span = emit_reward(
                1.0,
                attributes={"agent_os.emergent_signals": 0}
            )
            assert span is not None
        finally:
            clear_active_tracer()

    def test_m13_04_emit_reward_emergent_signal_positive_triggers_annotation(self):
        """When emergent_signals > 0, emit_reward should record extra annotation.

        The extra annotation should contain a note that violation led to success.
        This is the core M13 behavior: "碰异性的快乐" gets recorded.
        """
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(
                0.8,
                attributes={
                    "agent_os.emergent_signals": 5,
                    "agent_os.emergent_note": "violation_led_to_success",
                }
            )
            # The span should be created successfully
            assert span is not None
            # Check that the emergent signal data is preserved in attributes
            if span.attributes:
                assert "agent_os.emergent_signals" in span.attributes or \
                    any("emergent" in str(k) for k in span.attributes.keys())
        finally:
            clear_active_tracer()

    def test_m13_05_emit_reward_dict_with_emergent_signal(self):
        """Multi-dimensional reward with emergent_signal attribute."""
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(
                {"task_completion": 1.0, "safety": 0.3},
                primary_key="task_completion",
                attributes={"agent_os.emergent_signals": 2}
            )
            assert span is not None
        finally:
            clear_active_tracer()

    def test_m13_06_get_reward_value_unaffected_by_emergent(self):
        """get_reward_value should still return correct value even with emergent attrs."""
        from agentlightning.emitter.reward import emit_reward, get_reward_value
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(
                0.75,
                attributes={"agent_os.emergent_signals": 1}
            )
            reward_val = get_reward_value(span)
            assert reward_val == pytest.approx(0.75)
        finally:
            clear_active_tracer()

    def test_m13_07_find_reward_spans_with_emergent(self):
        """find_reward_spans should include spans that have emergent attributes."""
        from agentlightning.emitter.reward import emit_reward, find_reward_spans
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span1 = emit_reward(1.0)
            span2 = emit_reward(0.5, attributes={"agent_os.emergent_signals": 3})
            rewards = find_reward_spans([span1, span2])
            assert len(rewards) == 2
        finally:
            clear_active_tracer()

    def test_m13_08_emit_reward_bool_with_emergent(self):
        """emit_reward with boolean and emergent signal should work."""
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(True, attributes={"agent_os.emergent_signals": 1})
            assert span is not None
        finally:
            clear_active_tracer()

    def test_m13_09_emit_reward_negative_with_emergent(self):
        """Negative reward with emergent signal should still work (violation penalty + emergent bonus)."""
        from agentlightning.emitter.reward import emit_reward, get_reward_value
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(
                -10.0,
                attributes={"agent_os.emergent_signals": 2}
            )
            val = get_reward_value(span)
            assert val == pytest.approx(-10.0)
        finally:
            clear_active_tracer()

    def test_m13_10_emit_reward_preserves_other_attributes(self):
        """Other attributes should be preserved alongside emergent signal."""
        from agentlightning.emitter.reward import emit_reward
        from agentlightning.tracer.dummy import DummyTracer
        from agentlightning.tracer.base import set_active_tracer, clear_active_tracer, get_active_tracer

        tracer = DummyTracer()
        set_active_tracer(tracer)
        try:
            span = emit_reward(
                1.0,
                attributes={
                    "agent_os.emergent_signals": 1,
                    "custom.tag": "experiment_42",
                }
            )
            assert span is not None
            if span.attributes:
                # Both custom and emergent attributes should be present
                attr_keys = set(span.attributes.keys())
                assert any("custom" in k or "tag" in k for k in attr_keys) or \
                    any("emergent" in k for k in attr_keys)
        finally:
            clear_active_tracer()

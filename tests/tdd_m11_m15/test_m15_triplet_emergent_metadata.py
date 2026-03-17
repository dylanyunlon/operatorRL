"""
TDD Tests for M15: Triplet Adapter Emergent Signal Metadata Preservation
==========================================================================

Test-driven development: these tests define expected behavior for M15.
M15: When trace contains emergent_signal markers, the generated Triplet's
metadata should preserve that signal for downstream training algorithms.

NO MOCK IMPLEMENTATIONS. Tests target real Triplet and adapter code.
Tests are expected to FAIL until implementation is complete.
"""

import pytest
from typing import Dict, Any, List


class TestM15TripletEmergentSignalMetadata:
    """M15: Triplet metadata should preserve emergent_signal from trace."""

    def test_m15_01_triplet_metadata_accepts_emergent_field(self):
        """Triplet metadata dict should accept agent_os.emergent_signals key."""
        from agentlightning.types import Triplet

        t = Triplet(
            prompt={"text": "hello"},
            response={"text": "world"},
            reward=1.0,
            metadata={
                "agent_os.emergent_signals": 3,
                "response_id": "resp-123",
            },
        )
        assert t.metadata["agent_os.emergent_signals"] == 3

    def test_m15_02_triplet_metadata_emergent_signal_zero(self):
        """Triplet with emergent_signals=0 should be valid."""
        from agentlightning.types import Triplet

        t = Triplet(
            prompt={},
            response={},
            metadata={"agent_os.emergent_signals": 0},
        )
        assert t.metadata.get("agent_os.emergent_signals") == 0

    def test_m15_03_triplet_default_no_emergent(self):
        """Default Triplet should NOT have emergent_signals in metadata."""
        from agentlightning.types import Triplet

        t = Triplet(prompt={}, response={})
        assert "agent_os.emergent_signals" not in t.metadata

    def test_m15_04_triplet_model_copy_preserves_emergent(self):
        """model_copy(update=reward) should preserve emergent metadata."""
        from agentlightning.types import Triplet

        t = Triplet(
            prompt={"text": "hello"},
            response={"text": "world"},
            reward=None,
            metadata={
                "agent_os.emergent_signals": 2,
                "agent_os.emergent_note": "violation_led_to_success",
            },
        )
        t2 = t.model_copy(update={"reward": 0.9})
        assert t2.reward == 0.9
        assert t2.metadata["agent_os.emergent_signals"] == 2
        assert t2.metadata["agent_os.emergent_note"] == "violation_led_to_success"

    def test_m15_05_triplet_serialization_with_emergent(self):
        """Triplet.model_dump() should include emergent metadata."""
        from agentlightning.types import Triplet

        t = Triplet(
            prompt={"token_ids": [1, 2, 3]},
            response={"token_ids": [4, 5]},
            reward=0.5,
            metadata={
                "agent_os.emergent_signals": 1,
                "response_id": "resp-456",
            },
        )
        dumped = t.model_dump()
        assert dumped["metadata"]["agent_os.emergent_signals"] == 1
        assert dumped["metadata"]["response_id"] == "resp-456"

    def test_m15_06_triplet_from_dict_with_emergent(self):
        """Triplet.model_validate should accept emergent metadata from dict."""
        from agentlightning.types import Triplet

        data = {
            "prompt": {"text": "test"},
            "response": {"text": "reply"},
            "reward": 0.7,
            "metadata": {
                "agent_os.emergent_signals": 4,
                "agent_os.repair_enzyme_triggered": True,
            },
        }
        t = Triplet.model_validate(data)
        assert t.metadata["agent_os.emergent_signals"] == 4
        assert t.metadata["agent_os.repair_enzyme_triggered"] is True

    def test_m15_07_triplet_list_with_mixed_emergent(self):
        """A list of Triplets can have some with emergent signals and some without."""
        from agentlightning.types import Triplet

        triplets = [
            Triplet(prompt={}, response={}, reward=1.0, metadata={}),
            Triplet(prompt={}, response={}, reward=0.5, metadata={"agent_os.emergent_signals": 2}),
            Triplet(prompt={}, response={}, reward=-1.0, metadata={}),
        ]
        emergent_triplets = [t for t in triplets if t.metadata.get("agent_os.emergent_signals", 0) > 0]
        assert len(emergent_triplets) == 1
        assert emergent_triplets[0].reward == 0.5

    def test_m15_08_triplet_metadata_with_maturity_and_emergent(self):
        """Triplet metadata can carry both maturity_level and emergent_signals."""
        from agentlightning.types import Triplet

        t = Triplet(
            prompt={"text": "input"},
            response={"text": "output"},
            metadata={
                "agent_os.emergent_signals": 1,
                "agent_os.maturity_level": 3,
                "agent_os.growth_stage": "middle_school",
            },
        )
        assert t.metadata["agent_os.emergent_signals"] == 1
        assert t.metadata["agent_os.maturity_level"] == 3

    def _make_span(self, attributes, name="openai.chat.completion"):
        """Helper to create a fully valid Span for testing."""
        from agentlightning.types import Span
        return Span(
            rollout_id="test-rollout",
            attempt_id="test-attempt",
            sequence_id=0,
            trace_id="abcdef1234567890",
            span_id="1234567890abcdef",
            parent_id=None,
            name=name,
            status={"status_code": "OK"},
            attributes=attributes,
            events=[],
            links=[],
            start_time=1000000000,
            end_time=2000000000,
            context={
                "trace_id": "abcdef1234567890",
                "span_id": "1234567890abcdef",
                "is_remote": False,
                "trace_state": {},
            },
            parent=None,
            resource={"attributes": {}, "schema_url": ""},
        )

    def test_m15_09_span_to_triplet_preserves_emergent_in_span_attributes(self):
        """When a span has emergent attributes, span_to_triplet should carry them to metadata.

        This tests the actual adapter code path. The TraceTree.span_to_triplet
        method should check for agent_os.emergent_signals in span.attributes
        and copy it to the triplet metadata.
        """
        from agentlightning.adapter.triplet import TraceTree

        span = self._make_span({
            "gen_ai.request.model": "gpt-4",
            "gen_ai.response.id": "resp-789",
            "gen_ai.prompt_token_ids": [1, 2, 3],
            "gen_ai.response_token_ids": [4, 5, 6],
            "agent_os.emergent_signals": 2,
        })

        tree = TraceTree(id="test-id-1", span=span)
        triplet = tree.span_to_triplet(span, agent_name="test-agent")

        # M15 requirement: emergent_signals should be in triplet metadata
        assert "agent_os.emergent_signals" in triplet.metadata, \
            "M15: span_to_triplet must copy agent_os.emergent_signals to triplet metadata"
        assert triplet.metadata["agent_os.emergent_signals"] == 2

    def test_m15_10_span_to_triplet_no_emergent_no_metadata_pollution(self):
        """When span has no emergent attributes, metadata should NOT contain emergent key."""
        from agentlightning.adapter.triplet import TraceTree

        span = self._make_span({
            "gen_ai.request.model": "gpt-4",
            "gen_ai.response.id": "resp-999",
            "gen_ai.prompt_token_ids": [1, 2],
            "gen_ai.response_token_ids": [3, 4],
        })

        tree = TraceTree(id="test-id-2", span=span)
        triplet = tree.span_to_triplet(span, agent_name="test-agent")

        # Without emergent attributes in span, triplet metadata should be clean
        assert "agent_os.emergent_signals" not in triplet.metadata

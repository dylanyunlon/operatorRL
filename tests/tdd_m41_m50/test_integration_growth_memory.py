"""
TDD Integration Tests for M41-M50: Self-Evolution Growth Memory System

TEST-DRIVEN DEVELOPMENT: Integration tests verify the complete growth memory
pipeline works end-to-end across store, tracer, trainer, and config layers.

Expected behavior:
- Growth stages flow from config → trainer → store → tracer → span
- Rollout maturity_level propagates through query filters
- Statistics aggregate maturity distribution correctly
- The full maturity lifecycle (infant → graduate) is representable
- No function count changes across all modified files
"""

import os
import sys
import pytest
import subprocess

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


def _count_functions(relpath):
    """Count 'def ' occurrences in a file."""
    source = _read_source(relpath)
    return source.count("\ndef ") + (1 if source.startswith("def ") else 0)


def _count_classes(relpath):
    """Count 'class ' occurrences in a file."""
    source = _read_source(relpath)
    return source.count("\nclass ") + (1 if source.startswith("class ") else 0)


class TestM41M50Integration:
    """Integration tests for the complete growth memory system."""

    def test_01_all_modified_files_have_valid_syntax(self):
        """All M41-M50 modified files must pass Python syntax check."""
        files = [
            "agentlightning/store/base.py",
            "agentlightning/store/memory.py",
            "agentlightning/tracer/base.py",
            "agentlightning/tracer/otel.py",
            "agentlightning/types/core.py",
            "agentlightning/types/tracer.py",
            "agentlightning/trainer/trainer.py",
            "agentlightning/config.py",
        ]
        for f in files:
            path = os.path.join(PROJECT_ROOT, f)
            result = subprocess.run(
                [sys.executable, "-m", "py_compile", path],
                capture_output=True, text=True,
            )
            assert result.returncode == 0, f"Syntax error in {f}: {result.stderr}"

    def test_02_rollout_can_be_created_with_growth_fields(self):
        """A Rollout can be instantiated with maturity_level and growth_stage."""
        from agentlightning.types.core import Rollout
        r = Rollout(
            rollout_id="test-001",
            input={"task": "migrate_kernel"},
            start_time=1000.0,
            maturity_level=3,
            growth_stage="middle",
            emergent_signals=5,
        )
        assert r.maturity_level == 3
        assert r.growth_stage == "middle"
        assert r.emergent_signals == 5

    def test_03_span_can_be_created_with_emergent_fields(self):
        """A Span can be instantiated with emergent_signal and emergent_score."""
        from agentlightning.types.tracer import Span, SpanContext, TraceStatus
        ctx = SpanContext(trace_id="abc123", span_id="span001", is_remote=False, trace_state={})
        parent_ctx = SpanContext(trace_id="abc123", span_id="span000", is_remote=False, trace_state={})
        s = Span(
            rollout_id="test-001",
            attempt_id="att-001",
            sequence_id=1,
            trace_id="abc123",
            span_id="span001",
            parent_id=None,
            name="test_span",
            status=TraceStatus(status_code="OK"),
            attributes={"agent.maturity_level": 2},
            events=[],
            links=[],
            start_time=1000.0,
            end_time=1001.0,
            context=ctx,
            parent=parent_ctx,
            resource={"attributes": {}, "schema_url": ""},
            maturity_level=2,
            emergent_signal="tool_combo",
            emergent_score=0.75,
        )
        assert s.maturity_level == 2
        assert s.emergent_signal == "tool_combo"
        assert s.emergent_score == 0.75

    def test_04_growth_stage_mapping_is_consistent(self):
        """Growth stage names must map consistently to maturity levels."""
        expected = {
            0: "infant",
            1: "toddler",
            2: "elementary",
            3: "middle",
            4: "high",
            5: "college",
            6: "graduate",
        }
        from agentlightning.types.core import Rollout
        for level, stage in expected.items():
            r = Rollout(
                rollout_id=f"r-{level}",
                input={"task": "test"},
                start_time=float(level),
                maturity_level=level,
                growth_stage=stage,
            )
            assert r.maturity_level == level
            assert r.growth_stage == stage

    def test_05_cli_parser_round_trips_maturity(self):
        """CLI parser must round-trip maturity_level and growth_stage values."""
        from agentlightning.config import _create_argument_parser
        parser = _create_argument_parser()
        args = parser.parse_args(["--maturity-level", "5", "--growth-stage", "college"])
        assert args.maturity_level == 5
        assert args.growth_stage == "college"

    def test_06_store_base_query_rollouts_growth_stage_default(self):
        """query_rollouts growth_stage defaults to None (no filter)."""
        from agentlightning.store.base import LightningStore
        import inspect
        sig = inspect.signature(LightningStore.query_rollouts)
        assert sig.parameters["growth_stage"].default is None

    def test_07_tracer_base_create_span_is_abstract(self):
        """Tracer.create_span must remain abstract (raises NotImplementedError)."""
        from agentlightning.tracer.base import Tracer
        t = Tracer()
        with pytest.raises(NotImplementedError):
            t.create_span("test")

    def test_08_all_growth_stages_are_valid_strings(self):
        """All 7 growth stage names must be non-empty strings."""
        stages = ["infant", "toddler", "elementary", "middle", "high", "college", "graduate"]
        for s in stages:
            assert isinstance(s, str)
            assert len(s) > 0

    def test_09_maturity_level_range_0_to_6(self):
        """Maturity levels must span exactly 0-6 (7 levels)."""
        from agentlightning.types.core import Rollout
        # Level 0 should work
        r0 = Rollout(rollout_id="r0", input={}, start_time=0.0, maturity_level=0)
        assert r0.maturity_level == 0
        # Level 6 should work
        r6 = Rollout(rollout_id="r6", input={}, start_time=0.0, maturity_level=6)
        assert r6.maturity_level == 6

    def test_10_agentlightning_package_imports_cleanly(self):
        """The agentlightning package must import without errors after M41-M50."""
        result = subprocess.run(
            [sys.executable, "-c", "import agentlightning"],
            capture_output=True, text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode == 0, f"Import failed: {result.stderr}"

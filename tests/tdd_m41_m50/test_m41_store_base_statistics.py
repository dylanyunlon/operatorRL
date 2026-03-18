"""
TDD Tests for M41: store/base.py — Maturity/Growth Metadata Indexing

TEST-DRIVEN DEVELOPMENT: These tests define expected behavior for maturity_level
and growth_stage metadata indexing in LightningStoreStatistics BEFORE implementation.
No mock implementations. We test real class definitions and their field structures.

Expected behavior:
- LightningStoreStatistics includes maturity_level_distribution field
- LightningStoreStatistics includes emergent_signal_count field
- LightningStoreStatistics includes emergent_signals_by_type field
- LightningStoreStatistics includes avg_maturity_level field
- InMemoryLightningStore.statistics() returns these new fields with real data
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


class TestM41StoreStatisticsFields:
    """M41: LightningStoreStatistics must include growth-stage fields."""

    def test_01_statistics_has_maturity_level_distribution(self):
        """LightningStoreStatistics TypedDict must include maturity_level_distribution."""
        source = _read_source("agentlightning/store/base.py")
        assert "maturity_level_distribution" in source
        # Must be Dict[int, int] type annotation
        assert "Dict[int, int]" in source

    def test_02_statistics_has_emergent_signal_count(self):
        """LightningStoreStatistics must include emergent_signal_count as int."""
        source = _read_source("agentlightning/store/base.py")
        assert "emergent_signal_count" in source

    def test_03_statistics_has_emergent_signals_by_type(self):
        """LightningStoreStatistics must include emergent_signals_by_type Dict."""
        source = _read_source("agentlightning/store/base.py")
        assert "emergent_signals_by_type" in source
        assert "Dict[str, int]" in source

    def test_04_statistics_has_avg_maturity_level(self):
        """LightningStoreStatistics must include avg_maturity_level as float."""
        source = _read_source("agentlightning/store/base.py")
        assert "avg_maturity_level" in source

    def test_05_base_store_statistics_returns_name(self):
        """LightningStore.statistics() base impl must return at least 'name' key."""
        from agentlightning.store.base import LightningStore
        import asyncio
        store = LightningStore()
        stats = asyncio.get_event_loop().run_until_complete(store.statistics())
        assert "name" in stats
        assert stats["name"] == "LightningStore"

    def test_06_statistics_fields_are_typed_dict_keys(self):
        """All M41 fields must be valid TypedDict keys in LightningStoreStatistics."""
        from agentlightning.store.base import LightningStoreStatistics
        import typing
        hints = typing.get_type_hints(LightningStoreStatistics)
        assert "maturity_level_distribution" in hints
        assert "emergent_signal_count" in hints
        assert "emergent_signals_by_type" in hints
        assert "avg_maturity_level" in hints

    def test_07_store_base_add_span_docstring_mentions_maturity(self):
        """add_span docstring or M41 comment must reference maturity_level indexing."""
        source = _read_source("agentlightning/store/base.py")
        # Either docstring or comment should mention maturity in span context
        assert "maturity" in source.lower()

    def test_08_query_rollouts_signature_has_growth_stage(self):
        """query_rollouts must accept growth_stage as a keyword argument (M42 prep)."""
        from agentlightning.store.base import LightningStore
        import inspect
        sig = inspect.signature(LightningStore.query_rollouts)
        assert "growth_stage" in sig.parameters

    def test_09_query_rollouts_growth_stage_default_none(self):
        """query_rollouts growth_stage parameter must default to None."""
        from agentlightning.store.base import LightningStore
        import inspect
        sig = inspect.signature(LightningStore.query_rollouts)
        param = sig.parameters["growth_stage"]
        assert param.default is None

    def test_10_statistics_maturity_distribution_type_is_dict_int_int(self):
        """maturity_level_distribution must be typed as Dict[int, int]."""
        from agentlightning.store.base import LightningStoreStatistics
        import typing
        hints = typing.get_type_hints(LightningStoreStatistics)
        ann = hints["maturity_level_distribution"]
        # Should be Dict[int, int]
        origin = typing.get_origin(ann)
        args = typing.get_args(ann)
        assert origin is dict
        assert args == (int, int)

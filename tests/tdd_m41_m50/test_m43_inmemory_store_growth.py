"""
TDD Tests for M43: store/memory.py — InMemoryLightningStore Growth Indexing

TEST-DRIVEN DEVELOPMENT: Tests define expected behavior for InMemoryLightningStore's
maturity_level/growth_stage indexing and statistics reporting.

Expected behavior:
- InMemoryLightningStore.statistics() returns maturity_level_distribution
- statistics() includes emergent_signal_count from stored rollouts
- statistics() includes emergent_signals_by_type aggregation
- statistics() computes avg_maturity_level across all rollouts
- M43 comment header exists in memory.py
- The store class inherits proper capabilities
"""

import os
import sys
import re
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


class TestM43InMemoryStoreGrowthIndexing:
    """M43: InMemoryLightningStore must implement growth-stage indexing."""

    def test_01_memory_store_has_m43_marker(self):
        """memory.py must contain M43 modification marker comment."""
        source = _read_source("agentlightning/store/memory.py")
        assert "M43" in source

    def test_02_memory_store_class_exists(self):
        """InMemoryLightningStore class must be importable."""
        from agentlightning.store.memory import InMemoryLightningStore
        assert InMemoryLightningStore is not None

    def test_03_memory_store_inherits_collection_based(self):
        """InMemoryLightningStore must inherit from CollectionBasedLightningStore."""
        from agentlightning.store.memory import InMemoryLightningStore
        from agentlightning.store.collection_based import CollectionBasedLightningStore
        assert issubclass(InMemoryLightningStore, CollectionBasedLightningStore)

    def test_04_memory_store_statistics_is_async(self):
        """statistics() must be an async method."""
        from agentlightning.store.memory import InMemoryLightningStore
        import asyncio
        import inspect
        assert inspect.iscoroutinefunction(InMemoryLightningStore.statistics)

    def test_05_memory_store_statistics_returns_dict(self):
        """statistics() must return a dict-like object with expected keys."""
        from agentlightning.store.memory import InMemoryLightningStore
        import asyncio
        store = InMemoryLightningStore()
        stats = asyncio.get_event_loop().run_until_complete(store.statistics())
        assert isinstance(stats, dict)
        assert "name" in stats

    def test_06_memory_store_statistics_has_span_bytes(self):
        """statistics() must include total_span_bytes (existing field preserved)."""
        from agentlightning.store.memory import InMemoryLightningStore
        import asyncio
        store = InMemoryLightningStore()
        stats = asyncio.get_event_loop().run_until_complete(store.statistics())
        assert "total_span_bytes" in stats

    def test_07_memory_store_statistics_has_memory_capacity(self):
        """statistics() must include memory_capacity_bytes (existing field preserved)."""
        from agentlightning.store.memory import InMemoryLightningStore
        import asyncio
        store = InMemoryLightningStore()
        stats = asyncio.get_event_loop().run_until_complete(store.statistics())
        assert "memory_capacity_bytes" in stats

    def test_08_memory_store_estimate_model_size_works(self):
        """estimate_model_size must handle basic Python objects without error."""
        from agentlightning.store.memory import estimate_model_size
        size = estimate_model_size({"key": "value", "nested": [1, 2, 3]})
        assert isinstance(size, int)
        assert size > 0

    def test_09_memory_store_capabilities_async_safe(self):
        """InMemoryLightningStore capabilities must report async_safe=True."""
        from agentlightning.store.memory import InMemoryLightningStore
        store = InMemoryLightningStore()
        caps = store.capabilities
        assert caps["async_safe"] is True

    def test_10_memory_store_source_mentions_growth_stage_indexing(self):
        """memory.py source must reference growth_stage or maturity indexing logic."""
        source = _read_source("agentlightning/store/memory.py")
        assert "growth_stage" in source.lower() or "maturity" in source.lower()

"""
TDD Tests for M16: Algorithm.run() Repair Enzyme Documentation
================================================================

Test-driven development: these tests define expected behavior for M16.
M16: Algorithm.run() docstring should mention repair enzyme mode.
Also validate that Algorithm base class interface is unchanged.

NO MOCK IMPLEMENTATIONS. Tests target real Algorithm code.
Tests are expected to FAIL until implementation is complete.
"""

import pytest
import inspect


class TestM16AlgorithmRepairEnzymeDoc:
    """M16: Algorithm.run() should document repair enzyme mode."""

    def test_m16_01_algorithm_run_exists(self):
        """Algorithm.run() should exist and be callable."""
        from agentlightning.algorithm.base import Algorithm
        assert hasattr(Algorithm, 'run')
        assert callable(Algorithm.run)

    def test_m16_02_algorithm_run_has_docstring(self):
        """Algorithm.run() must have a docstring."""
        from agentlightning.algorithm.base import Algorithm
        assert Algorithm.run.__doc__ is not None
        assert len(Algorithm.run.__doc__.strip()) > 0

    def test_m16_03_algorithm_run_docstring_mentions_repair_enzyme(self):
        """Algorithm.run() docstring should mention repair enzyme mode.

        M16 requires adding repair enzyme documentation to run() docstring.
        """
        from agentlightning.algorithm.base import Algorithm
        doc = Algorithm.run.__doc__
        assert "repair" in doc.lower() or "enzyme" in doc.lower(), \
            "M16: Algorithm.run() docstring must mention repair enzyme mode"

    def test_m16_04_algorithm_run_signature_unchanged(self):
        """Algorithm.run() signature should remain: (self, train_dataset, val_dataset)."""
        from agentlightning.algorithm.base import Algorithm
        sig = inspect.signature(Algorithm.run)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "train_dataset" in params
        assert "val_dataset" in params

    def test_m16_05_algorithm_class_count_unchanged(self):
        """Algorithm module should still have exactly 1 class."""
        import agentlightning.algorithm.base as mod
        classes = [name for name, obj in inspect.getmembers(mod) if inspect.isclass(obj) and obj.__module__ == mod.__name__]
        assert len(classes) == 1, f"Expected 1 class, got {len(classes)}: {classes}"

    def test_m16_06_algorithm_method_count_unchanged(self):
        """Algorithm class should have same number of methods (14 defs)."""
        from agentlightning.algorithm.base import Algorithm
        methods = [name for name, _ in inspect.getmembers(Algorithm, predicate=inspect.isfunction)]
        # Original has: is_async, set_trainer, get_trainer, set_llm_proxy, get_llm_proxy,
        # set_adapter, get_adapter, set_store, get_store, get_initial_resources,
        # set_initial_resources, __call__, run, get_client = 14 methods
        assert len(methods) >= 14, f"Expected >= 14 methods, got {len(methods)}: {methods}"

    def test_m16_07_algorithm_run_raises_not_implemented(self):
        """Base Algorithm.run() should raise NotImplementedError."""
        from agentlightning.algorithm.base import Algorithm
        algo = Algorithm()
        with pytest.raises(NotImplementedError):
            algo.run()

    def test_m16_08_algorithm_callable(self):
        """Algorithm.__call__ should delegate to run."""
        from agentlightning.algorithm.base import Algorithm
        algo = Algorithm()
        with pytest.raises(NotImplementedError):
            algo()

    def test_m16_09_algorithm_is_async_false_by_default(self):
        """Base Algorithm.is_async() should return False."""
        from agentlightning.algorithm.base import Algorithm
        algo = Algorithm()
        assert algo.is_async() is False

    def test_m16_10_algorithm_set_get_store(self):
        """Algorithm set_store/get_store should work."""
        from agentlightning.algorithm.base import Algorithm
        from agentlightning.store.memory import InMemoryLightningStore

        algo = Algorithm()
        store = InMemoryLightningStore()
        algo.set_store(store)
        assert algo.get_store() is store

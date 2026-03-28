"""
TDD Tests for M259: model_manager.py — hot-swap support for self-evolution.

Tests the EXISTING model_registry.py patterns + hot-swap lifecycle.
10 tests.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestModelManagerConstruction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        assert mgr is not None

    def test_has_load_model(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        assert callable(getattr(mgr, "load_model", None))


class TestModelManagerRegistry:
    def test_register_model(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        mgr.register("test_model", version="1.0", model_type="builtin")
        assert mgr.get_model_info("test_model") is not None

    def test_list_models(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        mgr.register("m1", version="1.0", model_type="builtin")
        mgr.register("m2", version="1.0", model_type="onnx")
        models = mgr.list_models()
        assert len(models) >= 2

    def test_active_model(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        mgr.register("m1", version="1.0", model_type="builtin")
        mgr.set_active("m1")
        assert mgr.active_model_id == "m1"


class TestModelManagerHotSwap:
    def test_hot_swap(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        mgr.register("old", version="1.0", model_type="builtin")
        mgr.register("new", version="2.0", model_type="builtin")
        mgr.set_active("old")
        mgr.hot_swap("new")
        assert mgr.active_model_id == "new"

    def test_hot_swap_records_history(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        mgr.register("old", version="1.0", model_type="builtin")
        mgr.register("new", version="2.0", model_type="builtin")
        mgr.set_active("old")
        mgr.hot_swap("new")
        assert len(mgr.swap_history) >= 1

    def test_rollback(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        mgr.register("old", version="1.0", model_type="builtin")
        mgr.register("new", version="2.0", model_type="builtin")
        mgr.set_active("old")
        mgr.hot_swap("new")
        mgr.rollback()
        assert mgr.active_model_id == "old"


class TestModelManagerEvolution:
    def test_performance_tracker(self):
        from lol_fiddler_agent.ml.model_manager import ModelManager
        mgr = ModelManager()
        mgr.register("m1", version="1.0", model_type="builtin")
        mgr.record_performance("m1", predicted=0.7, actual=1.0, latency_ms=5.0)
        perf = mgr.get_performance("m1")
        assert perf is not None

    def test_evolution_key(self):
        from lol_fiddler_agent.ml.model_manager import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

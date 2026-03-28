"""
TDD Tests for M257: prediction.py — ML prediction with AgentLightning self-evolution.

Tests the existing prediction_engine.py with added evolution hooks.
We do NOT add/remove functions — only add evolution_callback attribute.
10 tests.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestPredictionEvolution:
    def test_builtin_model_import(self):
        from lol_fiddler_agent.ml.prediction_engine import BuiltinLogisticModel
        model = BuiltinLogisticModel()
        assert model is not None

    def test_prediction_result_dataclass(self):
        from lol_fiddler_agent.ml.prediction_engine import PredictionResult
        r = PredictionResult(
            win_probability=0.65,
            confidence=0.8,
            model_name="test",
            features_used=3,
            inference_time_ms=1.0,
        )
        assert r.outcome_label == "likely_win"

    def test_prediction_engine_construct(self):
        from lol_fiddler_agent.ml.prediction import PredictionEngineV2
        engine = PredictionEngineV2()
        assert engine is not None

    def test_engine_has_evolution_callback(self):
        from lol_fiddler_agent.ml.prediction import PredictionEngineV2
        engine = PredictionEngineV2()
        assert hasattr(engine, "evolution_callback")

    def test_engine_predict(self):
        from lol_fiddler_agent.ml.prediction import PredictionEngineV2
        engine = PredictionEngineV2()
        features = {"f1_deaths_per_min": 0.5, "f2_ka_per_min": 1.0, "f3_level_per_min": 0.8}
        result = engine.predict(features)
        assert result is not None
        assert 0.0 <= result.win_probability <= 1.0

    def test_engine_predict_triggers_callback(self):
        from lol_fiddler_agent.ml.prediction import PredictionEngineV2
        engine = PredictionEngineV2()
        records = []
        engine.evolution_callback = lambda d: records.append(d)
        features = {"f1_deaths_per_min": 0.3, "f2_ka_per_min": 1.5, "f3_level_per_min": 0.9}
        engine.predict(features)
        assert len(records) >= 1

    def test_engine_model_swap(self):
        from lol_fiddler_agent.ml.prediction import PredictionEngineV2
        engine = PredictionEngineV2()
        engine.set_model("builtin")
        assert engine.active_model_name == "builtin"

    def test_engine_training_data_collection(self):
        from lol_fiddler_agent.ml.prediction import PredictionEngineV2
        engine = PredictionEngineV2()
        engine.enable_training_collection(True)
        features = {"f1_deaths_per_min": 0.2}
        engine.predict(features)
        assert len(engine.training_buffer) >= 1

    def test_engine_stats(self):
        from lol_fiddler_agent.ml.prediction import PredictionEngineV2
        engine = PredictionEngineV2()
        stats = engine.get_stats()
        assert "predictions_count" in stats

    def test_evolution_key(self):
        from lol_fiddler_agent.ml.prediction import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

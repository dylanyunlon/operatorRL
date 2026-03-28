"""
TDD Tests for M258: feature_extractor.py — with training data pipeline hooks.

Tests the EXISTING feature_extractor.py functions + new pipeline integration.
10 tests — no function addition/removal.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestFeatureExtractorExisting:
    def test_import_feature_names(self):
        from lol_fiddler_agent.ml.feature_extractor import FEATURE_NAMES
        assert isinstance(FEATURE_NAMES, list)
        assert len(FEATURE_NAMES) >= 3

    def test_extract_features(self):
        from lol_fiddler_agent.ml.feature_extractor import extract_features
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        snapshot = GameSnapshot(game_time=600.0, active_player_name="Test")
        features = extract_features(snapshot)
        assert isinstance(features, dict)

    def test_normalize_features(self):
        from lol_fiddler_agent.ml.feature_extractor import normalize_features
        raw = {"f1_deaths_per_min": 1.0, "f2_ka_per_min": 2.0, "f3_level_per_min": 0.5}
        normed = normalize_features(raw)
        assert isinstance(normed, dict)
        for v in normed.values():
            assert 0.0 <= v <= 1.0

    def test_compute_win_probability(self):
        from lol_fiddler_agent.ml.feature_extractor import compute_win_probability
        features = {"f1_deaths_per_min": 0.3, "f2_ka_per_min": 1.5, "f3_level_per_min": 0.9}
        prob = compute_win_probability(features)
        assert 0.0 <= prob <= 1.0


class TestFeatureExtractorPipeline:
    def test_pipeline_export_format(self):
        """Features should export in AgentLightning-compatible format."""
        from lol_fiddler_agent.ml.feature_extractor import extract_features, features_to_triplet
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        snapshot = GameSnapshot(game_time=300.0, active_player_name="P1")
        features = extract_features(snapshot)
        triplet = features_to_triplet(features, action="farm", reward=0.5)
        assert "state" in triplet
        assert "action" in triplet
        assert "reward" in triplet

    def test_pipeline_batch_export(self):
        from lol_fiddler_agent.ml.feature_extractor import batch_features_to_dataset
        batch = [
            {"state": [0.1, 0.2], "action": "farm", "reward": 0.5},
            {"state": [0.3, 0.4], "action": "fight", "reward": -0.2},
        ]
        dataset = batch_features_to_dataset(batch)
        assert isinstance(dataset, dict)
        assert "states" in dataset
        assert len(dataset["states"]) == 2


class TestFeatureExtractorIncremental:
    def test_incremental_update(self):
        from lol_fiddler_agent.ml.feature_extractor import IncrementalFeatureTracker
        tracker = IncrementalFeatureTracker()
        tracker.update({"kills": 1, "deaths": 0, "assists": 2, "level": 5, "cs": 80}, game_time=300.0)
        features = tracker.current_features()
        assert "f2_ka_per_min" in features

    def test_incremental_reset(self):
        from lol_fiddler_agent.ml.feature_extractor import IncrementalFeatureTracker
        tracker = IncrementalFeatureTracker()
        tracker.update({"kills": 5}, game_time=600.0)
        tracker.reset()
        features = tracker.current_features()
        assert features.get("f2_ka_per_min", 0) == 0.0

    def test_feature_spec_list(self):
        from lol_fiddler_agent.ml.feature_extractor import FEATURE_SPECS
        assert isinstance(FEATURE_SPECS, list)
        assert len(FEATURE_SPECS) >= 3

    def test_evolution_key(self):
        from lol_fiddler_agent.ml.feature_extractor import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

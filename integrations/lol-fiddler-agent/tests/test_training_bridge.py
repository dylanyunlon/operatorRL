"""
TDD Tests for M260: training_bridge.py — AgentLightning trainer bridge.

10 tests: construction, collect spans, emit to store, training loop hooks,
reward aggregation, checkpoint management.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestTrainingBridgeConstruction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge
        bridge = TrainingBridge()
        assert bridge is not None

    def test_has_collect_span(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge
        bridge = TrainingBridge()
        assert callable(getattr(bridge, "collect_span", None))


class TestTrainingBridgeSpans:
    def test_collect_span(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge
        bridge = TrainingBridge()
        span = {
            "state": [0.1, 0.2, 0.3],
            "action": "farm",
            "reward": 0.5,
            "game_time": 300.0,
        }
        bridge.collect_span(span)
        assert len(bridge.span_buffer) >= 1

    def test_flush_spans(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge
        bridge = TrainingBridge()
        bridge.collect_span({"state": [0.1], "action": "a", "reward": 0.1, "game_time": 100.0})
        bridge.collect_span({"state": [0.2], "action": "b", "reward": 0.2, "game_time": 200.0})
        flushed = bridge.flush()
        assert len(flushed) == 2
        assert len(bridge.span_buffer) == 0


class TestTrainingBridgeReward:
    def test_aggregate_episode_reward(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge
        bridge = TrainingBridge()
        bridge.collect_span({"state": [0.1], "action": "a", "reward": 0.5, "game_time": 100.0})
        bridge.collect_span({"state": [0.2], "action": "b", "reward": -0.2, "game_time": 200.0})
        total = bridge.aggregate_reward()
        assert abs(total - 0.3) < 1e-6

    def test_reward_discount(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge, TrainingBridgeConfig
        cfg = TrainingBridgeConfig(discount_factor=0.9)
        bridge = TrainingBridge(config=cfg)
        bridge.collect_span({"state": [0.1], "action": "a", "reward": 1.0, "game_time": 100.0})
        bridge.collect_span({"state": [0.2], "action": "b", "reward": 1.0, "game_time": 200.0})
        discounted = bridge.compute_discounted_returns()
        assert isinstance(discounted, list)
        assert len(discounted) == 2


class TestTrainingBridgeCheckpoint:
    def test_checkpoint_data(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge
        bridge = TrainingBridge()
        bridge.collect_span({"state": [0.1], "action": "a", "reward": 0.5, "game_time": 100.0})
        ckpt = bridge.to_checkpoint()
        assert isinstance(ckpt, dict)
        assert "spans" in ckpt

    def test_restore_from_checkpoint(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridge
        bridge = TrainingBridge()
        ckpt = {"spans": [{"state": [0.1], "action": "a", "reward": 0.5, "game_time": 100.0}]}
        bridge.from_checkpoint(ckpt)
        assert len(bridge.span_buffer) == 1


class TestTrainingBridgeEvolution:
    def test_evolution_key(self):
        from lol_fiddler_agent.ml.training_bridge import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

    def test_config_defaults(self):
        from lol_fiddler_agent.ml.training_bridge import TrainingBridgeConfig
        cfg = TrainingBridgeConfig()
        assert cfg.discount_factor > 0
        assert cfg.max_buffer_size > 0

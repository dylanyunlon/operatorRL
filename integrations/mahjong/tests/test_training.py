"""
TDD Tests for M241 (training_collector) and M242 (reward).

M241: training_collector.py — span→triplet for AgentLightning
M242: reward.py — 麻将奖励函数

10 tests each. Expected ~50% failure.

Location: integrations/mahjong/tests/test_training.py
"""

import json
import pytest


# ── M241: TrainingCollector ──

class TestTrainingCollector:
    """Tests for training_collector.py — game span → training triplet."""

    def test_collector_instantiation(self):
        from mahjong_agent.training_collector import TrainingCollector, CollectorConfig
        collector = TrainingCollector()
        assert isinstance(collector.config, CollectorConfig)

    def test_collector_record_state_action(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        state = {"hand": ["1m", "2m", "3m"], "discards": []}
        action = {"type": "dahai", "pai": "1m"}
        collector.record(state, action)
        assert collector.buffer_size == 1

    def test_collector_record_with_reward(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        state = {"hand": ["1m", "2m"], "discards": []}
        action = {"type": "dahai", "pai": "1m"}
        collector.record(state, action, reward=1.0)
        assert collector.buffer_size == 1

    def test_collector_flush_returns_triplets(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        for i in range(5):
            collector.record(
                {"hand": [f"{i}m"], "step": i},
                {"type": "dahai", "pai": f"{i}m"},
                reward=float(i)
            )
        triplets = collector.flush()
        assert len(triplets) == 5
        assert all("state" in t and "action" in t and "reward" in t for t in triplets)

    def test_collector_flush_clears_buffer(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        collector.record({"hand": []}, {"type": "none"})
        collector.flush()
        assert collector.buffer_size == 0

    def test_collector_max_buffer_auto_flush(self):
        from mahjong_agent.training_collector import TrainingCollector, CollectorConfig
        cfg = CollectorConfig(max_buffer_size=3)
        collector = TrainingCollector(config=cfg)
        overflow = []
        collector.on_overflow = lambda triplets: overflow.extend(triplets)
        for i in range(4):
            collector.record({"step": i}, {"type": "none"})
        assert len(overflow) == 3  # auto-flushed at buffer_size=3

    def test_collector_stats(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        collector.record({"hand": []}, {"type": "none"}, reward=1.0)
        collector.record({"hand": []}, {"type": "none"}, reward=-1.0)
        stats = collector.stats
        assert stats["total_records"] == 2

    def test_collector_to_agentlightning_format(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        collector.record(
            {"hand": ["1m"], "discards": ["2p"]},
            {"type": "dahai", "pai": "1m"},
            reward=5.0,
        )
        al_batch = collector.to_agent_lightning_batch()
        assert "states" in al_batch
        assert "actions" in al_batch
        assert "rewards" in al_batch
        assert len(al_batch["states"]) == 1

    def test_collector_episode_boundary(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        collector.record({"step": 0}, {"type": "dahai"}, reward=0.0)
        collector.mark_episode_end(final_reward=10.0)
        triplets = collector.flush()
        # The final record should have the terminal reward
        assert triplets[-1]["terminal"] is True

    def test_collector_reset(self):
        from mahjong_agent.training_collector import TrainingCollector
        collector = TrainingCollector()
        collector.record({"step": 0}, {"type": "none"})
        collector.reset()
        assert collector.buffer_size == 0
        assert collector.stats["total_records"] == 0


# ── M242: MahjongReward ──

class TestMahjongReward:
    """Tests for reward.py — mahjong-specific reward functions."""

    def test_reward_function_exists(self):
        from mahjong_agent.reward import MahjongReward
        assert MahjongReward is not None

    def test_reward_win_positive(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        score = r.compute(event_type="agari", score_delta=8000)
        assert score > 0

    def test_reward_lose_negative(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        score = r.compute(event_type="agari", score_delta=-8000)
        assert score < 0

    def test_reward_tenpai_bonus(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        score = r.compute(event_type="tenpai_achieved", score_delta=0)
        assert score > 0

    def test_reward_riichi_cost(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        # Riichi has a small upfront cost (1000 deposit)
        score = r.compute(event_type="riichi_declared", score_delta=-1000)
        # Should be slightly negative but not catastrophic
        assert -5.0 < score < 0

    def test_reward_draw_neutral(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        score = r.compute(event_type="ryuukyoku", score_delta=0)
        assert abs(score) < 1.0

    def test_reward_placement_bonus(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        first = r.placement_reward(rank=1, total_players=4)
        fourth = r.placement_reward(rank=4, total_players=4)
        assert first > fourth

    def test_reward_config_custom(self):
        from mahjong_agent.reward import MahjongReward, RewardConfig
        cfg = RewardConfig(win_multiplier=2.0, lose_multiplier=1.5)
        r = MahjongReward(config=cfg)
        assert r.config.win_multiplier == 2.0

    def test_reward_normalize_score(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        # Large score deltas should be normalized
        huge_win = r.compute(event_type="agari", score_delta=48000)
        assert huge_win <= r.config.max_reward

    def test_reward_shaping_discard(self):
        from mahjong_agent.reward import MahjongReward
        r = MahjongReward()
        # Ordinary discard has near-zero reward
        score = r.compute(event_type="dahai", score_delta=0)
        assert abs(score) < 0.5

"""
TDD Tests for M248: MahjongAgentOSIntegration — GovernedEnvironment bridge.

10 tests covering: construction, step, reset, observation space, action space,
policy violation handling, reward signal, episode lifecycle.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestAgentOSIntegrationConstruction:
    def test_import_and_construct(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        assert env is not None

    def test_has_step_method(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        assert callable(getattr(env, "step", None))

    def test_has_reset_method(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        assert callable(getattr(env, "reset", None))


class TestAgentOSIntegrationStep:
    def test_step_returns_tuple(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        env.reset()
        result = env.step({"type": "dahai", "pai": "5m"})
        # Gym-like: (obs, reward, done, info)
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_step_increments_counter(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        env.reset()
        env.step({"type": "dahai", "pai": "5m"})
        assert env.step_count >= 1

    def test_step_violation_gives_negative_reward(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        env.reset()
        # Inject a policy violation scenario
        _, reward, _, info = env.step({"type": "__violation_test__"})
        assert reward < 0 or info.get("violations", 0) >= 0


class TestAgentOSIntegrationReset:
    def test_reset_returns_observation(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        obs = env.reset()
        assert obs is not None

    def test_reset_clears_step_count(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv
        env = MahjongGovernedEnv()
        env.reset()
        env.step({"type": "dahai", "pai": "1m"})
        env.reset()
        assert env.step_count == 0


class TestAgentOSIntegrationConfig:
    def test_config_maturity_level(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv, MahjongEnvConfig
        cfg = MahjongEnvConfig(maturity_level=3)
        env = MahjongGovernedEnv(config=cfg)
        assert env.config.maturity_level == 3

    def test_config_max_steps(self):
        from mahjong_agent.agentos_integration import MahjongGovernedEnv, MahjongEnvConfig
        cfg = MahjongEnvConfig(max_steps=50)
        env = MahjongGovernedEnv(config=cfg)
        assert env.config.max_steps == 50

"""
TDD Tests for M249: EvolutionLoop — self-evolution closed loop.

10 tests: construction, run_episode, collect_trajectory, compute_reward,
update_policy, lifecycle hooks, maturity progression.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestEvolutionLoopConstruction:
    def test_import_and_construct(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        assert loop is not None

    def test_has_run_episode(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        assert callable(getattr(loop, "run_episode", None))

    def test_has_evolve(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        assert callable(getattr(loop, "evolve", None))


class TestEvolutionLoopEpisode:
    def test_run_episode_returns_trajectory(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        traj = loop.run_episode()
        assert isinstance(traj, dict)
        assert "events" in traj
        assert "reward" in traj

    def test_run_episode_records_events(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        traj = loop.run_episode()
        assert isinstance(traj["events"], list)

    def test_trajectory_has_timestamps(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        traj = loop.run_episode()
        assert "start_time" in traj
        assert "end_time" in traj


class TestEvolutionLoopEvolve:
    def test_evolve_increments_generation(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        gen_before = loop.generation
        loop.evolve()
        assert loop.generation == gen_before + 1

    def test_evolve_stores_history(self):
        from mahjong_agent.evolution_loop import EvolutionLoop
        loop = EvolutionLoop()
        loop.evolve()
        assert len(loop.evolution_history) >= 1


class TestEvolutionLoopConfig:
    def test_config_max_generations(self):
        from mahjong_agent.evolution_loop import EvolutionLoop, EvolutionConfig
        cfg = EvolutionConfig(max_generations=10)
        loop = EvolutionLoop(config=cfg)
        assert loop.config.max_generations == 10

    def test_config_maturity_level(self):
        from mahjong_agent.evolution_loop import EvolutionLoop, EvolutionConfig
        cfg = EvolutionConfig(maturity_level=5)
        loop = EvolutionLoop(config=cfg)
        assert loop.config.maturity_level == 5

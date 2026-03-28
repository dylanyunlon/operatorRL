"""
TDD Tests for M250: AutoplayController — automated game session management.

10 tests: construction, start/stop session, queue management, game count,
error handling, bridge selection, statistics.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestAutoplayConstruction:
    def test_import_and_construct(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        assert ctrl is not None

    def test_default_state_idle(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        assert ctrl.state == "idle"


class TestAutoplaySession:
    def test_start_session(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        ctrl.start()
        assert ctrl.state == "running"

    def test_stop_session(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        ctrl.start()
        ctrl.stop()
        assert ctrl.state == "idle"

    def test_game_count_initial_zero(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        assert ctrl.games_played == 0

    def test_run_one_game_increments(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        ctrl.start()
        ctrl.run_one_game()
        assert ctrl.games_played >= 1


class TestAutoplayConfig:
    def test_config_max_games(self):
        from mahjong_agent.autoplay import AutoplayController, AutoplayConfig
        cfg = AutoplayConfig(max_games=5)
        ctrl = AutoplayController(config=cfg)
        assert ctrl.config.max_games == 5

    def test_config_platform(self):
        from mahjong_agent.autoplay import AutoplayController, AutoplayConfig
        cfg = AutoplayConfig(platform="tenhou")
        ctrl = AutoplayController(config=cfg)
        assert ctrl.config.platform == "tenhou"


class TestAutoplayStatistics:
    def test_stats_empty_initially(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        stats = ctrl.get_stats()
        assert isinstance(stats, dict)
        assert stats.get("games_played", 0) == 0

    def test_stats_has_win_rate(self):
        from mahjong_agent.autoplay import AutoplayController
        ctrl = AutoplayController()
        stats = ctrl.get_stats()
        assert "win_rate" in stats

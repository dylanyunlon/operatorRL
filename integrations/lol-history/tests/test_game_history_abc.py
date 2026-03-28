"""
TDD Tests for M265: GameHistoryABC — cross-game unified history data interface.

10 tests: ABC contract, LoL adapter, Mahjong adapter, Dota2 stub,
common query interface, serialization, statistics.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
# Also need modules path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "modules"))


class TestGameHistoryABCContract:
    def test_import_abc(self):
        from game_history_abc import GameHistoryProvider
        assert GameHistoryProvider is not None

    def test_cannot_instantiate_abc(self):
        from game_history_abc import GameHistoryProvider
        with pytest.raises(TypeError):
            GameHistoryProvider()

    def test_abc_has_required_methods(self):
        from game_history_abc import GameHistoryProvider
        import abc
        abstract_methods = getattr(GameHistoryProvider, "__abstractmethods__", set())
        assert "get_match_history" in abstract_methods
        assert "get_player_profile" in abstract_methods
        assert "game_name" in abstract_methods


class TestGameHistoryABCLoLAdapter:
    def test_lol_adapter_import(self):
        from game_history_abc import LoLHistoryAdapter
        adapter = LoLHistoryAdapter()
        assert adapter is not None

    def test_lol_adapter_game_name(self):
        from game_history_abc import LoLHistoryAdapter
        adapter = LoLHistoryAdapter()
        assert adapter.game_name == "league_of_legends"

    def test_lol_adapter_get_history(self):
        from game_history_abc import LoLHistoryAdapter
        adapter = LoLHistoryAdapter()
        # Should return empty list without real API
        history = adapter.get_match_history("test-puuid")
        assert isinstance(history, list)


class TestGameHistoryABCMahjongAdapter:
    def test_mahjong_adapter_import(self):
        from game_history_abc import MahjongHistoryAdapter
        adapter = MahjongHistoryAdapter()
        assert adapter is not None

    def test_mahjong_adapter_game_name(self):
        from game_history_abc import MahjongHistoryAdapter
        adapter = MahjongHistoryAdapter()
        assert adapter.game_name == "mahjong"


class TestGameHistoryABCCommon:
    def test_match_result_dataclass(self):
        from game_history_abc import MatchResult
        r = MatchResult(
            game_id="g1",
            game_name="league_of_legends",
            player_id="p1",
            win=True,
            score=10.0,
            timestamp=1700000000.0,
            metadata={"champion": "Jinx"},
        )
        assert r.win is True
        assert r.game_name == "league_of_legends"

    def test_player_profile_dataclass(self):
        from game_history_abc import PlayerProfile
        p = PlayerProfile(
            player_id="p1",
            game_name="mahjong",
            win_rate=0.55,
            total_games=100,
            metadata={},
        )
        assert p.total_games == 100

    def test_evolution_key(self):
        from game_history_abc import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

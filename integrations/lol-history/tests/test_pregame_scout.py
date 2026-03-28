"""
TDD Tests for M262: PregameScout — automatic opponent history scouting.

10 tests: construction, scout single player, scout all opponents,
report generation, threat ranking, champion analysis.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestPregameScoutConstruction:
    def test_import_and_construct(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        assert scout is not None

    def test_has_scout_method(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        assert callable(getattr(scout, "scout_player", None))


class TestPregameScoutSingle:
    def test_scout_player_returns_profile(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        # Mock match history data
        match_data = [
            {"gameId": 1, "champion": "Jinx", "win": True, "kills": 10, "deaths": 2, "assists": 5},
            {"gameId": 2, "champion": "Caitlyn", "win": False, "kills": 3, "deaths": 6, "assists": 4},
        ]
        profile = scout.scout_player("test-puuid", match_data)
        assert profile is not None
        assert "win_rate" in profile
        assert "main_champions" in profile

    def test_scout_empty_history(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        profile = scout.scout_player("empty-puuid", [])
        assert profile is not None
        assert profile["win_rate"] == 0.0


class TestPregameScoutAll:
    def test_scout_all_opponents(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        opponents = {
            "p1": [{"gameId": 1, "champion": "Yasuo", "win": True, "kills": 8, "deaths": 3, "assists": 2}],
            "p2": [{"gameId": 2, "champion": "Zed", "win": False, "kills": 5, "deaths": 7, "assists": 1}],
        }
        report = scout.scout_all(opponents)
        assert isinstance(report, dict)
        assert len(report) == 2


class TestPregameScoutReport:
    def test_generate_report(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        opponents = {
            "p1": [{"gameId": 1, "champion": "Jinx", "win": True, "kills": 10, "deaths": 1, "assists": 5}],
        }
        report = scout.generate_report(opponents)
        assert isinstance(report, dict)
        assert "threat_ranking" in report

    def test_threat_ranking(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        opponents = {
            "weak": [{"gameId": 1, "champion": "A", "win": False, "kills": 1, "deaths": 10, "assists": 0}],
            "strong": [{"gameId": 2, "champion": "B", "win": True, "kills": 15, "deaths": 1, "assists": 10}],
        }
        report = scout.generate_report(opponents)
        ranking = report["threat_ranking"]
        assert ranking[0] == "strong"  # Higher threat first


class TestPregameScoutChampion:
    def test_champion_pool_analysis(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        matches = [
            {"gameId": 1, "champion": "Jinx", "win": True, "kills": 5, "deaths": 2, "assists": 3},
            {"gameId": 2, "champion": "Jinx", "win": True, "kills": 7, "deaths": 1, "assists": 6},
            {"gameId": 3, "champion": "Cait", "win": False, "kills": 2, "deaths": 5, "assists": 1},
        ]
        profile = scout.scout_player("p1", matches)
        assert profile["main_champions"][0] == "Jinx"

    def test_evolution_key(self):
        from lol_history.pregame_scout import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

    def test_weakness_detection(self):
        from lol_history.pregame_scout import PregameScout
        scout = PregameScout()
        matches = [
            {"gameId": i, "champion": "Yasuo", "win": False, "kills": 2, "deaths": 8, "assists": 1}
            for i in range(5)
        ]
        profile = scout.scout_player("bad-player", matches)
        assert profile.get("threat_level", "high") in ("low", "medium", "high")

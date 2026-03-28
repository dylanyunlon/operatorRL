"""
TDD Tests for LoL Historical Battle Data module (Seraphine-inspired).

M243: lol_history/__init__.py + HistoryClient
M244: lol_history/match_analyzer.py — historical match analysis for pre-game intel  
M245: lol_history/player_profiler.py — opponent profiling from historical data

Seraphine reference: app/lol/connector.py endpoints
  - /lol-match-history/v1/products/lol/{puuid}/matches
  - /lol-match-history/v1/games/{gameId}
  - /lol-ranked/v1/ranked-stats/{puuid}
  - /match-history-query/v1/products/lol/player/{puuid}/SUMMARY (SGP)

10 tests each for HistoryClient, MatchAnalyzer, PlayerProfiler.
Expected ~50% failure.

Location: integrations/lol-history/tests/test_history.py
"""

import json
import pytest


# ── M243: HistoryClient ──

class TestHistoryClientInit:
    """Tests for lol_history/__init__.py module constants."""

    def test_version_exists(self):
        from lol_history import __version__
        assert isinstance(__version__, str)

    def test_evolution_key(self):
        from lol_history import _EVOLUTION_KEY
        assert "lol_history" in _EVOLUTION_KEY


class TestHistoryClient:
    """Tests for LCU-based historical match data retrieval."""

    def test_client_instantiation(self):
        from lol_history import HistoryClient, HistoryConfig
        client = HistoryClient()
        assert isinstance(client.config, HistoryConfig)

    def test_client_custom_config(self):
        from lol_history import HistoryClient, HistoryConfig
        cfg = HistoryConfig(
            lcu_host="127.0.0.1",
            lcu_port=2999,
            max_retries=5,
            timeout=15.0,
        )
        client = HistoryClient(config=cfg)
        assert client.config.lcu_port == 2999
        assert client.config.max_retries == 5

    def test_client_build_match_history_url(self):
        from lol_history import HistoryClient
        client = HistoryClient()
        puuid = "abc-123-def"
        url = client._build_match_history_url(puuid, beg_index=0, end_index=9)
        assert puuid in url
        assert "matches" in url

    def test_client_build_game_detail_url(self):
        from lol_history import HistoryClient
        client = HistoryClient()
        url = client._build_game_detail_url(game_id=12345)
        assert "12345" in url
        assert "games" in url

    def test_client_build_ranked_stats_url(self):
        from lol_history import HistoryClient
        client = HistoryClient()
        puuid = "xyz-789"
        url = client._build_ranked_stats_url(puuid)
        assert puuid in url
        assert "ranked" in url

    def test_client_build_sgp_url(self):
        from lol_history import HistoryClient
        client = HistoryClient()
        puuid = "sgp-test-puuid"
        url = client._build_sgp_match_url(puuid, beg_index=0, count=10)
        assert puuid in url
        assert "SUMMARY" in url

    def test_client_parse_match_list_response(self):
        from lol_history import HistoryClient
        client = HistoryClient()
        raw_response = {
            "games": {
                "games": [
                    {
                        "gameId": 1001,
                        "gameCreation": 1700000000000,
                        "gameDuration": 1800,
                        "queueId": 420,
                        "participants": [{"championId": 1, "stats": {"win": True}}],
                    }
                ]
            }
        }
        matches = client.parse_match_list(raw_response)
        assert len(matches) == 1
        assert matches[0]["game_id"] == 1001

    def test_client_parse_game_detail_response(self):
        from lol_history import HistoryClient
        client = HistoryClient()
        raw_detail = {
            "gameId": 2001,
            "gameDuration": 2400,
            "teams": [
                {"teamId": 100, "win": "Win"},
                {"teamId": 200, "win": "Fail"},
            ],
            "participants": [
                {
                    "participantId": 1,
                    "championId": 86,
                    "stats": {
                        "kills": 10,
                        "deaths": 3,
                        "assists": 7,
                        "totalDamageDealt": 120000,
                        "goldEarned": 15000,
                        "win": True,
                    },
                }
            ],
        }
        detail = client.parse_game_detail(raw_detail)
        assert detail["game_id"] == 2001
        assert detail["duration_seconds"] == 2400
        assert len(detail["participants"]) == 1
        assert detail["participants"][0]["kills"] == 10


# ── M244: MatchAnalyzer ──

class TestMatchAnalyzer:
    """Tests for match_analyzer.py — historical match analysis for pre-game intel."""

    def test_analyzer_instantiation(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        assert analyzer is not None

    def test_analyzer_compute_winrate(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"game_id": 1, "win": True, "champion_id": 86},
            {"game_id": 2, "win": False, "champion_id": 86},
            {"game_id": 3, "win": True, "champion_id": 86},
        ]
        winrate = analyzer.compute_winrate(matches)
        assert abs(winrate - 0.6667) < 0.01

    def test_analyzer_compute_winrate_empty(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        winrate = analyzer.compute_winrate([])
        assert winrate == 0.0

    def test_analyzer_champion_stats(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"game_id": 1, "win": True, "champion_id": 86, "kills": 10, "deaths": 2, "assists": 5},
            {"game_id": 2, "win": False, "champion_id": 86, "kills": 3, "deaths": 7, "assists": 1},
            {"game_id": 3, "win": True, "champion_id": 222, "kills": 15, "deaths": 1, "assists": 10},
        ]
        stats = analyzer.champion_stats(matches)
        assert 86 in stats
        assert 222 in stats
        assert stats[86]["games"] == 2
        assert stats[222]["games"] == 1

    def test_analyzer_recent_form(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"game_id": i, "win": i % 2 == 0, "champion_id": 86}
            for i in range(10)
        ]
        form = analyzer.recent_form(matches, last_n=5)
        assert "wins" in form
        assert "losses" in form
        assert form["wins"] + form["losses"] == 5

    def test_analyzer_kda_ratio(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"kills": 10, "deaths": 5, "assists": 10},
        ]
        kda = analyzer.compute_kda(matches)
        assert abs(kda - 4.0) < 0.01  # (10+10)/5

    def test_analyzer_kda_zero_deaths(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"kills": 10, "deaths": 0, "assists": 5},
        ]
        kda = analyzer.compute_kda(matches)
        assert kda == float("inf") or kda >= 15.0  # Perfect KDA

    def test_analyzer_preferred_role(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"role": "SOLO", "lane": "TOP"},
            {"role": "SOLO", "lane": "TOP"},
            {"role": "CARRY", "lane": "BOTTOM"},
        ]
        role = analyzer.preferred_role(matches)
        assert role == "TOP"

    def test_analyzer_game_duration_stats(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"duration_seconds": 1800},
            {"duration_seconds": 2400},
            {"duration_seconds": 1200},
        ]
        stats = analyzer.duration_stats(matches)
        assert stats["avg"] == 1800.0
        assert stats["min"] == 1200
        assert stats["max"] == 2400

    def test_analyzer_streak_detection(self):
        from lol_history.match_analyzer import MatchAnalyzer
        analyzer = MatchAnalyzer()
        matches = [
            {"win": True}, {"win": True}, {"win": True},
            {"win": False}, {"win": False},
        ]
        streak = analyzer.current_streak(matches)
        assert streak["type"] == "win"
        assert streak["count"] == 3


# ── M245: PlayerProfiler ──

class TestPlayerProfiler:
    """Tests for player_profiler.py — opponent profiling from historical data."""

    def test_profiler_instantiation(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        assert profiler is not None

    def test_profiler_build_profile_from_matches(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        matches = [
            {
                "game_id": 1, "win": True, "champion_id": 86,
                "kills": 10, "deaths": 2, "assists": 5,
                "role": "SOLO", "lane": "TOP",
                "duration_seconds": 1800,
            },
            {
                "game_id": 2, "win": False, "champion_id": 86,
                "kills": 3, "deaths": 7, "assists": 1,
                "role": "SOLO", "lane": "TOP",
                "duration_seconds": 2100,
            },
        ]
        profile = profiler.build_profile(puuid="test-puuid", matches=matches)
        assert profile["puuid"] == "test-puuid"
        assert "winrate" in profile
        assert "preferred_role" in profile
        assert "champion_pool" in profile

    def test_profiler_threat_level_classification(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        # High winrate + high KDA = high threat
        profile = {
            "winrate": 0.75,
            "kda": 5.0,
            "games_played": 100,
        }
        threat = profiler.classify_threat(profile)
        assert threat in ("low", "medium", "high", "extreme")
        assert threat in ("high", "extreme")

    def test_profiler_threat_level_low(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        profile = {
            "winrate": 0.35,
            "kda": 1.2,
            "games_played": 10,
        }
        threat = profiler.classify_threat(profile)
        assert threat == "low"

    def test_profiler_champion_comfort_score(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        champion_stats = {
            "games": 50,
            "winrate": 0.65,
            "avg_kda": 4.0,
        }
        comfort = profiler.champion_comfort_score(champion_stats)
        assert 0.0 <= comfort <= 1.0
        assert comfort > 0.5  # High comfort

    def test_profiler_weakness_detection(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        matches = [
            {"win": False, "champion_id": 86, "deaths": 10, "kills": 1, "assists": 0,
             "duration_seconds": 1200, "role": "SOLO", "lane": "TOP"},
            {"win": False, "champion_id": 86, "deaths": 8, "kills": 2, "assists": 1,
             "duration_seconds": 1500, "role": "SOLO", "lane": "TOP"},
        ]
        weaknesses = profiler.detect_weaknesses(matches)
        assert isinstance(weaknesses, list)
        assert len(weaknesses) > 0  # Should detect high death rate

    def test_profiler_playstyle_classification(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        matches = [
            {"kills": 15, "deaths": 3, "assists": 2, "cs_per_min": 8.0,
             "damage_share": 0.35, "gold_share": 0.30},
        ]
        style = profiler.classify_playstyle(matches)
        assert style in ("aggressive", "passive", "balanced", "supportive")

    def test_profiler_empty_matches(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        profile = profiler.build_profile(puuid="empty-puuid", matches=[])
        assert profile["puuid"] == "empty-puuid"
        assert profile["winrate"] == 0.0
        assert profile["games_played"] == 0

    def test_profiler_to_pre_game_report(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        profile = {
            "puuid": "test",
            "winrate": 0.55,
            "kda": 3.0,
            "preferred_role": "MID",
            "champion_pool": [{"champion_id": 7, "games": 30, "winrate": 0.60}],
            "threat_level": "medium",
            "games_played": 50,
        }
        report = profiler.to_pre_game_report(profile)
        assert isinstance(report, str)
        assert "test" in report or "MID" in report

    def test_profiler_merge_multiple_sources(self):
        from lol_history.player_profiler import PlayerProfiler
        profiler = PlayerProfiler()
        lcu_matches = [{"game_id": 1, "win": True, "champion_id": 86,
                        "kills": 5, "deaths": 2, "assists": 3,
                        "role": "SOLO", "lane": "TOP", "duration_seconds": 1800}]
        sgp_matches = [{"game_id": 2, "win": False, "champion_id": 222,
                        "kills": 8, "deaths": 4, "assists": 6,
                        "role": "CARRY", "lane": "BOTTOM", "duration_seconds": 2000}]
        merged = profiler.merge_match_sources(lcu_matches, sgp_matches)
        assert len(merged) == 2
        game_ids = {m["game_id"] for m in merged}
        assert game_ids == {1, 2}

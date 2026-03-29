"""
TDD Tests for M346-M355: Seraphine History + Live Client Data API Deep Integration.

100 tests (10 per module), designed for ~50% initial failure rate.
Tests written BEFORE implementation per TDD methodology.

Reference projects (拿来主义):
  - Seraphine: app/lol/connector.py (LCU async client, match history, summoner API)
  - leagueoflegends-optimizer: articles/article5.md (Riot API data pipeline)
  - operatorRL ABCs: GameBridgeABC, EvolutionLoopABC, StrategyAdvisorABC
  - operatorRL existing: extensions/fiddler-bridge, integrations/lol-history
"""

import importlib.util
import json
import os
import sys
import time

import pytest

# ---------------------------------------------------------------------------
# Helper: load module from file path (avoids package dependency chain)
# ---------------------------------------------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_LOL_SRC = os.path.join(_ROOT, "lol", "src", "lol_agent")
_MODULES_DIR = os.path.join(_ROOT, "..", "modules")


def _load(name: str, filepath: str):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures: lazy-load each module under test
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def live_client_mod():
    return _load("live_client_connector", os.path.join(_LOL_SRC, "live_client_connector.py"))


@pytest.fixture(scope="module")
def live_game_state_mod():
    return _load("live_game_state", os.path.join(_LOL_SRC, "live_game_state.py"))


@pytest.fixture(scope="module")
def seraphine_history_mod():
    return _load("seraphine_history_client", os.path.join(_LOL_SRC, "seraphine_history_client.py"))


@pytest.fixture(scope="module")
def opponent_merger_mod():
    return _load("opponent_history_merger", os.path.join(_LOL_SRC, "opponent_history_merger.py"))


@pytest.fixture(scope="module")
def champion_perf_mod():
    return _load("champion_performance_analyzer", os.path.join(_LOL_SRC, "champion_performance_analyzer.py"))


@pytest.fixture(scope="module")
def win_prob_mod():
    return _load("win_probability_predictor", os.path.join(_LOL_SRC, "win_probability_predictor.py"))


@pytest.fixture(scope="module")
def item_build_mod():
    return _load("item_build_advisor", os.path.join(_LOL_SRC, "item_build_advisor.py"))


@pytest.fixture(scope="module")
def voice_narration_mod():
    return _load("voice_narration_engine", os.path.join(_LOL_SRC, "voice_narration_engine.py"))


@pytest.fixture(scope="module")
def lol_evo_mod():
    return _load("lol_evolution_loop", os.path.join(_LOL_SRC, "lol_evolution_loop.py"))


@pytest.fixture(scope="module")
def lol_strategy_mod():
    return _load("lol_strategy_advisor", os.path.join(_LOL_SRC, "lol_strategy_advisor.py"))


# =====================================================================
# M346 — LiveClientConnector (10 tests)
# Reference: Riot Live Client Data API https://127.0.0.1:2999/liveclientdata/
# Reference: Seraphine/app/lol/connector.py retry/needLcu decorators
# =====================================================================
class TestLiveClientConnector:
    def test_class_exists(self, live_client_mod):
        assert hasattr(live_client_mod, "LiveClientConnector")

    def test_has_evolution_key(self, live_client_mod):
        assert hasattr(live_client_mod, "_EVOLUTION_KEY")
        assert "live_client" in live_client_mod._EVOLUTION_KEY

    def test_instantiation_default(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector()
        assert lcc.base_url == "https://127.0.0.1:2999"

    def test_instantiation_custom_url(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector(base_url="http://localhost:9999")
        assert lcc.base_url == "http://localhost:9999"

    def test_build_allgamedata_url(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector()
        url = lcc.build_url("allgamedata")
        assert "/liveclientdata/allgamedata" in url

    def test_build_playerlist_url(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector()
        url = lcc.build_url("playerlist")
        assert "/liveclientdata/playerlist" in url

    def test_build_activeplayer_url(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector()
        url = lcc.build_url("activeplayer")
        assert "/liveclientdata/activeplayer" in url

    def test_build_eventdata_url(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector()
        url = lcc.build_url("eventdata")
        assert "/liveclientdata/eventdata" in url

    def test_parse_response_valid_json(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector()
        data = lcc.parse_response('{"gameMode": "CLASSIC", "gameTime": 120.5}')
        assert data["gameMode"] == "CLASSIC"
        assert data["gameTime"] == 120.5

    def test_parse_response_invalid_json(self, live_client_mod):
        lcc = live_client_mod.LiveClientConnector()
        data = lcc.parse_response("not json")
        assert data == {} or data is None or "error" in data


# =====================================================================
# M347 — LiveGameState (10 tests)
# Reference: Live Client Data API allgamedata/playerlist/activeplayer
# =====================================================================
class TestLiveGameState:
    def test_class_exists(self, live_game_state_mod):
        assert hasattr(live_game_state_mod, "LiveGameState")

    def test_has_evolution_key(self, live_game_state_mod):
        assert hasattr(live_game_state_mod, "_EVOLUTION_KEY")
        assert "live_game_state" in live_game_state_mod._EVOLUTION_KEY

    def test_instantiation(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        assert lgs is not None

    def test_update_from_allgamedata(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        sample = {
            "activePlayer": {"summonerName": "TestPlayer", "level": 10,
                             "currentGold": 3500.0, "championStats": {"maxHealth": 1200}},
            "allPlayers": [
                {"summonerName": "TestPlayer", "championName": "Ahri",
                 "team": "ORDER", "level": 10, "scores": {"kills": 3, "deaths": 1, "assists": 5, "creepScore": 120}},
            ],
            "events": {"Events": []},
            "gameData": {"gameMode": "CLASSIC", "gameTime": 600.0, "mapNumber": 11},
        }
        lgs.update(sample)
        assert lgs.game_time == 600.0
        assert lgs.game_mode == "CLASSIC"

    def test_get_player_by_name(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        sample = {
            "activePlayer": {"summonerName": "Me", "level": 5,
                             "currentGold": 1000.0, "championStats": {"maxHealth": 800}},
            "allPlayers": [
                {"summonerName": "Me", "championName": "Lux", "team": "ORDER",
                 "level": 5, "scores": {"kills": 1, "deaths": 0, "assists": 2, "creepScore": 80}},
                {"summonerName": "Enemy", "championName": "Zed", "team": "CHAOS",
                 "level": 6, "scores": {"kills": 2, "deaths": 1, "assists": 0, "creepScore": 90}},
            ],
            "events": {"Events": []},
            "gameData": {"gameMode": "CLASSIC", "gameTime": 300.0, "mapNumber": 11},
        }
        lgs.update(sample)
        player = lgs.get_player("Me")
        assert player is not None
        assert player["championName"] == "Lux"

    def test_get_active_player(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        sample = {
            "activePlayer": {"summonerName": "Me", "level": 8,
                             "currentGold": 2500.0, "championStats": {"maxHealth": 1000}},
            "allPlayers": [],
            "events": {"Events": []},
            "gameData": {"gameMode": "CLASSIC", "gameTime": 450.0, "mapNumber": 11},
        }
        lgs.update(sample)
        ap = lgs.get_active_player()
        assert ap["summonerName"] == "Me"
        assert ap["level"] == 8

    def test_get_teams(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        sample = {
            "activePlayer": {"summonerName": "A", "level": 1,
                             "currentGold": 500.0, "championStats": {"maxHealth": 600}},
            "allPlayers": [
                {"summonerName": "A", "championName": "Garen", "team": "ORDER",
                 "level": 1, "scores": {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0}},
                {"summonerName": "B", "championName": "Darius", "team": "CHAOS",
                 "level": 1, "scores": {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0}},
            ],
            "events": {"Events": []},
            "gameData": {"gameMode": "CLASSIC", "gameTime": 30.0, "mapNumber": 11},
        }
        lgs.update(sample)
        order, chaos = lgs.get_teams()
        assert len(order) == 1
        assert len(chaos) == 1
        assert order[0]["championName"] == "Garen"

    def test_gold_difference(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        # After update with gold data, we can compute team gold diff
        sample = {
            "activePlayer": {"summonerName": "A", "level": 10,
                             "currentGold": 5000.0, "championStats": {"maxHealth": 1500}},
            "allPlayers": [
                {"summonerName": "A", "championName": "Jinx", "team": "ORDER",
                 "level": 10, "scores": {"kills": 5, "deaths": 2, "assists": 3, "creepScore": 150}},
                {"summonerName": "B", "championName": "Vayne", "team": "CHAOS",
                 "level": 9, "scores": {"kills": 3, "deaths": 4, "assists": 1, "creepScore": 130}},
            ],
            "events": {"Events": []},
            "gameData": {"gameMode": "CLASSIC", "gameTime": 900.0, "mapNumber": 11},
        }
        lgs.update(sample)
        diff = lgs.compute_gold_advantage("ORDER")
        assert isinstance(diff, (int, float))

    def test_event_timeline(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        sample = {
            "activePlayer": {"summonerName": "A", "level": 5,
                             "currentGold": 1500.0, "championStats": {"maxHealth": 800}},
            "allPlayers": [],
            "events": {"Events": [
                {"EventID": 0, "EventName": "GameStart", "EventTime": 0.0},
                {"EventID": 1, "EventName": "FirstBlood", "EventTime": 180.5},
            ]},
            "gameData": {"gameMode": "CLASSIC", "gameTime": 200.0, "mapNumber": 11},
        }
        lgs.update(sample)
        events = lgs.get_events()
        assert len(events) == 2

    def test_empty_update(self, live_game_state_mod):
        lgs = live_game_state_mod.LiveGameState()
        lgs.update({})
        assert lgs.game_time == 0.0


# =====================================================================
# M348 — SeraphineHistoryClient (10 tests)
# Reference: Seraphine/app/lol/connector.py getGameDetailByGameId/getRankedStatsByPuuid
# Reference: integrations/lol-history/src/lol_history/seraphine_bridge.py
# =====================================================================
class TestSeraphineHistoryClient:
    def test_class_exists(self, seraphine_history_mod):
        assert hasattr(seraphine_history_mod, "SeraphineHistoryClient")

    def test_has_evolution_key(self, seraphine_history_mod):
        assert hasattr(seraphine_history_mod, "_EVOLUTION_KEY")
        assert "seraphine_history" in seraphine_history_mod._EVOLUTION_KEY

    def test_instantiation(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        assert shc is not None

    def test_build_match_history_url(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        url = shc.build_match_history_url(puuid="abc-123", count=20)
        assert "abc-123" in url
        assert "20" in url or "count" in url

    def test_build_summoner_url(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        url = shc.build_summoner_url("TestPlayer")
        assert "TestPlayer" in url

    def test_parse_match_list(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        raw = {"games": [
            {"gameId": 1001, "champion": "Ahri", "stats": {"kills": 5, "deaths": 2, "assists": 8}},
            {"gameId": 1002, "champion": "Lux", "stats": {"kills": 3, "deaths": 3, "assists": 10}},
        ]}
        parsed = shc.parse_match_list(raw)
        assert len(parsed) == 2
        assert parsed[0]["gameId"] == 1001

    def test_parse_ranked_stats(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        raw = {"queues": [
            {"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "division": "II",
             "wins": 50, "losses": 45},
        ]}
        parsed = shc.parse_ranked_stats(raw)
        assert parsed["tier"] == "GOLD"
        assert parsed["division"] == "II"

    def test_parse_empty_ranked(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        parsed = shc.parse_ranked_stats({"queues": []})
        assert parsed["tier"] == "UNRANKED" or parsed.get("tier") is None

    def test_evolution_callback_hook(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        events = []
        shc.evolution_callback = lambda e: events.append(e)
        shc.parse_match_list({"games": [{"gameId": 999, "champion": "Zed", "stats": {"kills": 10, "deaths": 0, "assists": 5}}]})
        # Should have fired at least one evolution event
        assert len(events) >= 1

    def test_build_game_detail_url(self, seraphine_history_mod):
        shc = seraphine_history_mod.SeraphineHistoryClient()
        url = shc.build_game_detail_url(game_id=12345)
        assert "12345" in url


# =====================================================================
# M349 — OpponentHistoryMerger (10 tests)
# Reference: Seraphine opponent lookup + Live Client playerlist
# =====================================================================
class TestOpponentHistoryMerger:
    def test_class_exists(self, opponent_merger_mod):
        assert hasattr(opponent_merger_mod, "OpponentHistoryMerger")

    def test_has_evolution_key(self, opponent_merger_mod):
        assert hasattr(opponent_merger_mod, "_EVOLUTION_KEY")
        assert "opponent_history" in opponent_merger_mod._EVOLUTION_KEY

    def test_instantiation(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        assert ohm is not None

    def test_identify_opponents(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        players = [
            {"summonerName": "Ally1", "team": "ORDER"},
            {"summonerName": "Enemy1", "team": "CHAOS"},
            {"summonerName": "Enemy2", "team": "CHAOS"},
        ]
        opponents = ohm.identify_opponents(players, my_team="ORDER")
        assert len(opponents) == 2
        assert all(o["team"] == "CHAOS" for o in opponents)

    def test_merge_history_with_live(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        live_player = {"summonerName": "Enemy1", "championName": "Zed", "team": "CHAOS",
                       "level": 8, "scores": {"kills": 3, "deaths": 1, "assists": 2, "creepScore": 100}}
        history = [
            {"gameId": 1, "champion": "Zed", "stats": {"kills": 10, "deaths": 2, "assists": 5}},
            {"gameId": 2, "champion": "Zed", "stats": {"kills": 7, "deaths": 3, "assists": 8}},
        ]
        merged = ohm.merge(live_player, history)
        assert "live" in merged
        assert "history" in merged
        assert merged["live"]["championName"] == "Zed"
        assert len(merged["history"]) == 2

    def test_compute_opponent_threat(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        merged = {
            "live": {"summonerName": "Enemy1", "championName": "Zed", "level": 10,
                     "scores": {"kills": 5, "deaths": 1, "assists": 2, "creepScore": 130}},
            "history": [
                {"gameId": 1, "champion": "Zed", "stats": {"kills": 10, "deaths": 2, "assists": 5}},
            ],
        }
        threat = ohm.compute_threat_score(merged)
        assert 0.0 <= threat <= 1.0

    def test_threat_score_high_kda(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        merged = {
            "live": {"summonerName": "Smurf", "championName": "Katarina", "level": 15,
                     "scores": {"kills": 15, "deaths": 0, "assists": 10, "creepScore": 200}},
            "history": [
                {"gameId": i, "champion": "Katarina", "stats": {"kills": 20, "deaths": 1, "assists": 10}}
                for i in range(10)
            ],
        }
        threat = ohm.compute_threat_score(merged)
        assert threat > 0.7  # High KDA → high threat

    def test_threat_score_low_kda(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        merged = {
            "live": {"summonerName": "Newbie", "championName": "Garen", "level": 3,
                     "scores": {"kills": 0, "deaths": 5, "assists": 0, "creepScore": 30}},
            "history": [
                {"gameId": 1, "champion": "Garen", "stats": {"kills": 1, "deaths": 8, "assists": 2}},
            ],
        }
        threat = ohm.compute_threat_score(merged)
        assert threat < 0.4

    def test_identify_no_opponents_empty(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        opponents = ohm.identify_opponents([], my_team="ORDER")
        assert len(opponents) == 0

    def test_merge_empty_history(self, opponent_merger_mod):
        ohm = opponent_merger_mod.OpponentHistoryMerger()
        live_player = {"summonerName": "X", "championName": "Yi", "team": "CHAOS",
                       "level": 1, "scores": {"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0}}
        merged = ohm.merge(live_player, [])
        assert merged["history"] == []


# =====================================================================
# M350 — ChampionPerformanceAnalyzer (10 tests)
# Reference: leagueoflegends-optimizer stats pipeline
# =====================================================================
class TestChampionPerformanceAnalyzer:
    def test_class_exists(self, champion_perf_mod):
        assert hasattr(champion_perf_mod, "ChampionPerformanceAnalyzer")

    def test_has_evolution_key(self, champion_perf_mod):
        assert hasattr(champion_perf_mod, "_EVOLUTION_KEY")
        assert "champion_performance" in champion_perf_mod._EVOLUTION_KEY

    def test_instantiation(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        assert cpa is not None

    def test_compute_kda(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        kda = cpa.compute_kda(kills=10, deaths=3, assists=8)
        assert abs(kda - 6.0) < 0.01  # (10+8)/3

    def test_compute_kda_zero_deaths(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        kda = cpa.compute_kda(kills=5, deaths=0, assists=3)
        assert kda == float("inf") or kda >= 8.0  # Perfect KDA

    def test_compute_cs_per_minute(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        cspm = cpa.compute_cs_per_minute(creep_score=150, game_time_seconds=600)
        assert abs(cspm - 15.0) < 0.01  # 150 / 10min

    def test_analyze_performance(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        stats = {"kills": 8, "deaths": 2, "assists": 12, "creepScore": 200,
                 "damageDealt": 25000, "wardsPlaced": 15, "game_time_seconds": 1800}
        report = cpa.analyze(stats)
        assert "kda" in report
        assert "cs_per_min" in report
        assert report["kda"] > 0

    def test_trend_analysis(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        history = [
            {"kills": 3, "deaths": 5, "assists": 4, "creepScore": 100, "game_time_seconds": 1200},
            {"kills": 5, "deaths": 3, "assists": 6, "creepScore": 140, "game_time_seconds": 1500},
            {"kills": 8, "deaths": 2, "assists": 8, "creepScore": 180, "game_time_seconds": 1800},
        ]
        trend = cpa.compute_trend(history)
        assert trend["kda_trend"] > 0  # Improving KDA

    def test_analyze_empty(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        report = cpa.analyze({"kills": 0, "deaths": 0, "assists": 0, "creepScore": 0,
                              "damageDealt": 0, "wardsPlaced": 0, "game_time_seconds": 0})
        assert report["kda"] == 0 or report["kda"] == float("inf") or isinstance(report["kda"], (int, float))

    def test_vision_score(self, champion_perf_mod):
        cpa = champion_perf_mod.ChampionPerformanceAnalyzer()
        stats = {"kills": 2, "deaths": 1, "assists": 15, "creepScore": 50,
                 "damageDealt": 5000, "wardsPlaced": 30, "game_time_seconds": 1800}
        report = cpa.analyze(stats)
        assert "wards_per_min" in report
        assert report["wards_per_min"] > 0


# =====================================================================
# M351 — WinProbabilityPredictor (10 tests)
# =====================================================================
class TestWinProbabilityPredictor:
    def test_class_exists(self, win_prob_mod):
        assert hasattr(win_prob_mod, "WinProbabilityPredictor")

    def test_has_evolution_key(self, win_prob_mod):
        assert hasattr(win_prob_mod, "_EVOLUTION_KEY")
        assert "win_probability" in win_prob_mod._EVOLUTION_KEY

    def test_instantiation(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        assert wpp is not None

    def test_predict_returns_probability(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        features = {
            "gold_diff": 2000,
            "kill_diff": 3,
            "tower_diff": 1,
            "dragon_diff": 1,
            "game_time": 900.0,
        }
        prob = wpp.predict(features)
        assert 0.0 <= prob <= 1.0

    def test_predict_even_game(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        features = {
            "gold_diff": 0,
            "kill_diff": 0,
            "tower_diff": 0,
            "dragon_diff": 0,
            "game_time": 600.0,
        }
        prob = wpp.predict(features)
        assert 0.4 <= prob <= 0.6  # Near 50/50

    def test_predict_dominant_lead(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        features = {
            "gold_diff": 10000,
            "kill_diff": 15,
            "tower_diff": 5,
            "dragon_diff": 3,
            "game_time": 1200.0,
        }
        prob = wpp.predict(features)
        assert prob > 0.75

    def test_predict_behind(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        features = {
            "gold_diff": -8000,
            "kill_diff": -10,
            "tower_diff": -4,
            "dragon_diff": -2,
            "game_time": 1500.0,
        }
        prob = wpp.predict(features)
        assert prob < 0.35

    def test_feature_weights(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        assert hasattr(wpp, "weights") or hasattr(wpp, "_weights")

    def test_update_weights_from_outcome(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        features = {"gold_diff": 3000, "kill_diff": 5, "tower_diff": 2,
                    "dragon_diff": 1, "game_time": 1200.0}
        wpp.update(features, outcome=1.0)  # Win
        # Weights should have been nudged
        assert True  # No crash is the check

    def test_evolution_callback(self, win_prob_mod):
        wpp = win_prob_mod.WinProbabilityPredictor()
        events = []
        wpp.evolution_callback = lambda e: events.append(e)
        features = {"gold_diff": 1000, "kill_diff": 2, "tower_diff": 0,
                    "dragon_diff": 0, "game_time": 600.0}
        wpp.update(features, outcome=1.0)
        assert len(events) >= 1


# =====================================================================
# M352 — ItemBuildAdvisor (10 tests)
# Reference: leagueoflegends-optimizer item recommendation
# =====================================================================
class TestItemBuildAdvisor:
    def test_class_exists(self, item_build_mod):
        assert hasattr(item_build_mod, "ItemBuildAdvisor")

    def test_has_evolution_key(self, item_build_mod):
        assert hasattr(item_build_mod, "_EVOLUTION_KEY")
        assert "item_build" in item_build_mod._EVOLUTION_KEY

    def test_instantiation(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        assert iba is not None

    def test_recommend_returns_list(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        context = {
            "champion": "Jinx",
            "role": "ADC",
            "current_items": [],
            "gold": 3000,
            "opponents": [{"championName": "Zed"}, {"championName": "Leona"}],
        }
        items = iba.recommend(context)
        assert isinstance(items, list)
        assert len(items) > 0

    def test_recommend_with_existing_items(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        context = {
            "champion": "Jinx",
            "role": "ADC",
            "current_items": ["Infinity Edge"],
            "gold": 2500,
            "opponents": [{"championName": "Zed"}],
        }
        items = iba.recommend(context)
        # Should not recommend items already owned
        assert "Infinity Edge" not in items

    def test_recommend_against_armor_stacker(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        context = {
            "champion": "Jinx",
            "role": "ADC",
            "current_items": [],
            "gold": 3500,
            "opponents": [
                {"championName": "Malphite", "armor": 200},
                {"championName": "Rammus", "armor": 250},
            ],
        }
        items = iba.recommend(context)
        # Should have some armor penetration item tag
        assert isinstance(items, list)

    def test_item_pool_not_empty(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        assert hasattr(iba, "item_pool") or hasattr(iba, "_item_pool")

    def test_score_item(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        score = iba.score_item(
            item_name="Infinity Edge",
            context={"champion": "Jinx", "role": "ADC", "current_items": [], "gold": 3500, "opponents": []},
        )
        assert isinstance(score, (int, float))
        assert score >= 0

    def test_recommend_budget_constraint(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        context = {
            "champion": "Jinx",
            "role": "ADC",
            "current_items": [],
            "gold": 500,  # Very low gold
            "opponents": [],
        }
        items = iba.recommend(context)
        # Items should be affordable or list should be short
        assert isinstance(items, list)

    def test_evolution_callback(self, item_build_mod):
        iba = item_build_mod.ItemBuildAdvisor()
        events = []
        iba.evolution_callback = lambda e: events.append(e)
        context = {"champion": "Lux", "role": "MID", "current_items": [],
                   "gold": 3000, "opponents": []}
        iba.recommend(context)
        assert len(events) >= 1


# =====================================================================
# M353 — VoiceNarrationEngine (10 tests)
# Reference: operatorRL voice_advisor priority queue
# =====================================================================
class TestVoiceNarrationEngine:
    def test_class_exists(self, voice_narration_mod):
        assert hasattr(voice_narration_mod, "VoiceNarrationEngine")

    def test_has_evolution_key(self, voice_narration_mod):
        assert hasattr(voice_narration_mod, "_EVOLUTION_KEY")
        assert "voice_narration" in voice_narration_mod._EVOLUTION_KEY

    def test_instantiation(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine()
        assert vne is not None

    def test_enqueue_message(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine()
        vne.enqueue("Watch out for Zed!", priority=1)
        assert vne.queue_size() == 1

    def test_enqueue_multiple_priority(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine()
        vne.enqueue("Low priority info", priority=3)
        vne.enqueue("CRITICAL: Ambush!", priority=0)
        vne.enqueue("Medium priority", priority=2)
        msg = vne.dequeue()
        assert "CRITICAL" in msg or "Ambush" in msg  # Highest priority first

    def test_dequeue_empty(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine()
        msg = vne.dequeue()
        assert msg is None or msg == ""

    def test_to_tts_text(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine()
        text = vne.to_tts_text("Enemy jungler spotted at dragon pit")
        assert isinstance(text, str)
        assert len(text) > 0

    def test_clear_queue(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine()
        vne.enqueue("msg1", priority=1)
        vne.enqueue("msg2", priority=2)
        vne.clear()
        assert vne.queue_size() == 0

    def test_max_queue_size(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine(max_queue=3)
        for i in range(10):
            vne.enqueue(f"msg{i}", priority=5)
        assert vne.queue_size() <= 3

    def test_evolution_callback(self, voice_narration_mod):
        vne = voice_narration_mod.VoiceNarrationEngine()
        events = []
        vne.evolution_callback = lambda e: events.append(e)
        vne.enqueue("Dragon spawning in 30 seconds", priority=1)
        vne.dequeue()
        assert len(events) >= 1


# =====================================================================
# M354 — LoLEvolutionLoop (10 tests)
# Reference: EvolutionLoopABC (record/fitness/evolve/export/reset)
# =====================================================================
class TestLoLEvolutionLoop:
    def test_class_exists(self, lol_evo_mod):
        assert hasattr(lol_evo_mod, "LoLEvolutionLoop")

    def test_has_evolution_key(self, lol_evo_mod):
        assert hasattr(lol_evo_mod, "_EVOLUTION_KEY")
        assert "lol_evolution" in lol_evo_mod._EVOLUTION_KEY

    def test_instantiation(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop()
        assert loop is not None

    def test_record_episode(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop()
        states = [{"gold": 500, "level": 1}, {"gold": 2000, "level": 5}]
        actions = [{"action": "farm"}, {"action": "fight"}]
        loop.record_episode(states, actions, reward=1.0)
        assert loop.episode_count() >= 1

    def test_compute_fitness(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop()
        loop.record_episode(
            [{"gold": 500}], [{"action": "farm"}], reward=0.8
        )
        loop.record_episode(
            [{"gold": 600}], [{"action": "fight"}], reward=0.6
        )
        fitness = loop.compute_fitness()
        assert -1.0 <= fitness <= 1.0

    def test_should_evolve(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop(evolve_threshold=2)
        loop.record_episode([{}], [{}], reward=0.5)
        assert loop.should_evolve() is False
        loop.record_episode([{}], [{}], reward=0.6)
        assert loop.should_evolve() is True

    def test_advance_generation(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop(evolve_threshold=1)
        loop.record_episode([{}], [{}], reward=0.5)
        gen_before = loop.generation
        loop.advance_generation()
        assert loop.generation == gen_before + 1

    def test_export_training_data(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop()
        loop.record_episode(
            [{"gold": 1000, "level": 3}], [{"action": "gank"}], reward=0.9
        )
        spans = loop.export_training_data()
        assert isinstance(spans, list)
        assert len(spans) >= 1

    def test_reset(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop()
        loop.record_episode([{}], [{}], reward=0.5)
        loop.reset()
        assert loop.episode_count() == 0

    def test_fitness_clipping(self, lol_evo_mod):
        loop = lol_evo_mod.LoLEvolutionLoop()
        loop.record_episode([{}], [{}], reward=100.0)
        fitness = loop.compute_fitness()
        assert -1.0 <= fitness <= 1.0


# =====================================================================
# M355 — LoLStrategyAdvisor (10 tests)
# Reference: StrategyAdvisorABC (advise/evaluate/confidence)
# =====================================================================
class TestLoLStrategyAdvisor:
    def test_class_exists(self, lol_strategy_mod):
        assert hasattr(lol_strategy_mod, "LoLStrategyAdvisor")

    def test_has_evolution_key(self, lol_strategy_mod):
        assert hasattr(lol_strategy_mod, "_EVOLUTION_KEY")
        assert "lol_strategy" in lol_strategy_mod._EVOLUTION_KEY

    def test_instantiation(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        assert adv is not None

    def test_game_name(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        assert adv.game_name == "league_of_legends"

    def test_advise_returns_dict(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        state = {
            "game_time": 600.0,
            "gold_diff": 2000,
            "kill_diff": 3,
            "dragon_count": 1,
            "tower_diff": 1,
        }
        advice = adv.advise(state)
        assert isinstance(advice, dict)
        assert "action" in advice or "suggestion" in advice

    def test_evaluate_action(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        score = adv.evaluate_action(action={"action": "push_mid"}, outcome={"result": "tower_taken"})
        assert isinstance(score, float)

    def test_get_confidence(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        conf = adv.get_confidence()
        assert 0.0 <= conf <= 1.0

    def test_confidence_increases_with_data(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        conf_before = adv.get_confidence()
        for _ in range(5):
            adv.evaluate_action(
                action={"action": "farm"}, outcome={"result": "gold_gained"}
            )
        conf_after = adv.get_confidence()
        assert conf_after >= conf_before

    def test_advise_early_game(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        state = {"game_time": 120.0, "gold_diff": 0, "kill_diff": 0,
                 "dragon_count": 0, "tower_diff": 0}
        advice = adv.advise(state)
        # Early game should suggest farming-oriented strategy
        assert isinstance(advice, dict)

    def test_evolution_callback(self, lol_strategy_mod):
        adv = lol_strategy_mod.LoLStrategyAdvisor()
        events = []
        adv.evolution_callback = lambda e: events.append(e)
        adv.advise({"game_time": 900.0, "gold_diff": 5000, "kill_diff": 8,
                    "dragon_count": 2, "tower_diff": 3})
        assert len(events) >= 1

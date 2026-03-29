"""
TDD Tests for M366-M375: LoL Real-Time Assistant End-to-End System.

100 tests (10 per module), designed for ~50% initial failure rate.
Tests written BEFORE implementation per TDD methodology.

Reference projects (拿来主义):
  - Akagi: Controller.react() event loop → M366 game_session_manager
  - LeagueAI: input_output screen capture → M367 real_time_poller
  - PARL: AgentBase lifecycle → M375 orchestrator
  - DI-star: BaseLearner hooks → M368 decision engine
  - Seraphine: LCU connector → M370 pregame scout
  - dota2bot-OpenHyperAI: mode_*.lua → M368 decision engine
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
def session_mgr_mod():
    return _load("game_session_manager", os.path.join(_LOL_SRC, "game_session_manager.py"))


@pytest.fixture(scope="module")
def poller_mod():
    return _load("real_time_poller", os.path.join(_LOL_SRC, "real_time_poller.py"))


@pytest.fixture(scope="module")
def decision_mod():
    return _load("decision_engine", os.path.join(_LOL_SRC, "decision_engine.py"))


@pytest.fixture(scope="module")
def feedback_mod():
    return _load("feedback_recorder", os.path.join(_LOL_SRC, "feedback_recorder.py"))


@pytest.fixture(scope="module")
def scout_mod():
    return _load("pregame_scout_engine", os.path.join(_LOL_SRC, "pregame_scout_engine.py"))


@pytest.fixture(scope="module")
def timer_mod():
    return _load("objective_timer", os.path.join(_LOL_SRC, "objective_timer.py"))


@pytest.fixture(scope="module")
def comp_mod():
    return _load("team_comp_evaluator", os.path.join(_LOL_SRC, "team_comp_evaluator.py"))


@pytest.fixture(scope="module")
def danger_mod():
    return _load("danger_zone_detector", os.path.join(_LOL_SRC, "danger_zone_detector.py"))


@pytest.fixture(scope="module")
def postgame_mod():
    return _load("post_game_analyzer", os.path.join(_LOL_SRC, "post_game_analyzer.py"))


@pytest.fixture(scope="module")
def orch_mod():
    return _load("lol_agent_orchestrator", os.path.join(_LOL_SRC, "lol_agent_orchestrator.py"))


# ===================================================================
# M366: GameSessionManager — 10 tests
# ===================================================================
class TestGameSessionManager:
    """State machine: loading → running → ended."""

    def test_initial_state_is_idle(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        assert mgr.state == "idle"

    def test_transition_idle_to_loading(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        mgr.transition("loading")
        assert mgr.state == "loading"

    def test_transition_loading_to_running(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        mgr.transition("loading")
        mgr.transition("running")
        assert mgr.state == "running"

    def test_transition_running_to_ended(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        mgr.transition("loading")
        mgr.transition("running")
        mgr.transition("ended")
        assert mgr.state == "ended"

    def test_invalid_transition_raises(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        with pytest.raises(ValueError):
            mgr.transition("ended")  # idle → ended not allowed

    def test_reset_returns_to_idle(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        mgr.transition("loading")
        mgr.transition("running")
        mgr.reset()
        assert mgr.state == "idle"

    def test_get_duration_while_running(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        mgr.transition("loading")
        mgr.transition("running")
        dur = mgr.get_duration()
        assert isinstance(dur, float)
        assert dur >= 0.0

    def test_get_duration_idle_returns_zero(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        assert mgr.get_duration() == 0.0

    def test_history_tracks_transitions(self, session_mgr_mod):
        mgr = session_mgr_mod.GameSessionManager()
        mgr.transition("loading")
        mgr.transition("running")
        h = mgr.get_history()
        assert len(h) >= 2
        assert h[0]["to"] == "loading"
        assert h[1]["to"] == "running"

    def test_evolution_callback_fires(self, session_mgr_mod):
        events = []
        mgr = session_mgr_mod.GameSessionManager()
        mgr.evolution_callback = lambda e: events.append(e)
        mgr.transition("loading")
        assert len(events) == 1
        assert events[0]["type"] == "state_transition"


# ===================================================================
# M367: RealTimePoller — 10 tests
# ===================================================================
class TestRealTimePoller:
    """Periodic Live Client API polling with retry."""

    def test_init_default_interval(self, poller_mod):
        p = poller_mod.RealTimePoller()
        assert p.interval > 0

    def test_init_custom_interval(self, poller_mod):
        p = poller_mod.RealTimePoller(interval=0.5)
        assert p.interval == 0.5

    def test_register_handler(self, poller_mod):
        p = poller_mod.RealTimePoller()
        p.register_handler("allgamedata", lambda d: None)
        assert "allgamedata" in p.list_handlers()

    def test_unregister_handler(self, poller_mod):
        p = poller_mod.RealTimePoller()
        p.register_handler("allgamedata", lambda d: None)
        p.unregister_handler("allgamedata")
        assert "allgamedata" not in p.list_handlers()

    def test_simulate_poll_calls_handler(self, poller_mod):
        results = []
        p = poller_mod.RealTimePoller()
        p.register_handler("allgamedata", lambda d: results.append(d))
        p.simulate_poll("allgamedata", {"gameData": {"gameTime": 100}})
        assert len(results) == 1

    def test_poll_count_increments(self, poller_mod):
        p = poller_mod.RealTimePoller()
        p.register_handler("test", lambda d: None)
        p.simulate_poll("test", {})
        p.simulate_poll("test", {})
        assert p.poll_count >= 2

    def test_error_count_on_handler_error(self, poller_mod):
        def bad_handler(d):
            raise RuntimeError("fail")
        p = poller_mod.RealTimePoller()
        p.register_handler("test", bad_handler)
        p.simulate_poll("test", {})  # should not raise
        assert p.error_count >= 1

    def test_get_stats(self, poller_mod):
        p = poller_mod.RealTimePoller()
        stats = p.get_stats()
        assert "poll_count" in stats
        assert "error_count" in stats

    def test_is_running_default_false(self, poller_mod):
        p = poller_mod.RealTimePoller()
        assert p.is_running is False

    def test_evolution_callback(self, poller_mod):
        events = []
        p = poller_mod.RealTimePoller()
        p.evolution_callback = lambda e: events.append(e)
        p.register_handler("test", lambda d: None)
        p.simulate_poll("test", {"key": "val"})
        assert len(events) >= 1


# ===================================================================
# M368: DecisionEngine — 10 tests
# ===================================================================
class TestDecisionEngine:
    """Aggregate state/history/threat → strategy output."""

    def test_decide_returns_dict(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({"game_time": 300, "gold_diff": 500})
        assert isinstance(result, dict)
        assert "action" in result

    def test_decide_early_game(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({"game_time": 100, "gold_diff": 0, "kill_diff": 0})
        assert result["phase"] == "early"

    def test_decide_mid_game(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({"game_time": 1000, "gold_diff": 0})
        assert result["phase"] == "mid"

    def test_decide_late_game(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({"game_time": 2000, "gold_diff": 0})
        assert result["phase"] == "late"

    def test_decide_with_threat_info(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({
            "game_time": 600,
            "gold_diff": -2000,
            "threats": [{"name": "Zed", "threat_score": 8.0}],
        })
        assert "action" in result

    def test_decide_with_objectives(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({
            "game_time": 1200,
            "dragon_available": True,
            "gold_diff": 3000,
        })
        assert "action" in result

    def test_get_decision_history(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        eng.decide({"game_time": 100})
        eng.decide({"game_time": 200})
        h = eng.get_history()
        assert len(h) >= 2

    def test_decide_empty_state_no_crash(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({})
        assert "action" in result

    def test_confidence_in_result(self, decision_mod):
        eng = decision_mod.DecisionEngine()
        result = eng.decide({"game_time": 500, "gold_diff": 5000})
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0

    def test_evolution_callback(self, decision_mod):
        events = []
        eng = decision_mod.DecisionEngine()
        eng.evolution_callback = lambda e: events.append(e)
        eng.decide({"game_time": 100})
        assert len(events) >= 1


# ===================================================================
# M369: FeedbackRecorder — 10 tests
# ===================================================================
class TestFeedbackRecorder:
    """Records user action vs advice deviation."""

    def test_record_feedback(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record(advice="push_lane", action="safe_farm", game_time=120.0)
        assert rec.total_count() == 1

    def test_record_matching_action(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record(advice="push_lane", action="push_lane", game_time=120.0)
        assert rec.match_rate() == 1.0

    def test_match_rate_zero(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record(advice="a", action="b", game_time=1.0)
        assert rec.match_rate() == 0.0

    def test_match_rate_empty(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        assert rec.match_rate() == 0.0  # no ZeroDivisionError

    def test_get_all_records(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record("a", "b", 1.0)
        rec.record("c", "c", 2.0)
        recs = rec.get_records()
        assert len(recs) == 2

    def test_get_deviations_only(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record("a", "b", 1.0)
        rec.record("c", "c", 2.0)
        devs = rec.get_deviations()
        assert len(devs) == 1
        assert devs[0]["advice"] == "a"

    def test_clear(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record("a", "b", 1.0)
        rec.clear()
        assert rec.total_count() == 0

    def test_deviation_score(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record("a", "b", 1.0)
        rec.record("c", "c", 2.0)
        rec.record("d", "e", 3.0)
        score = rec.deviation_score()
        # 2 deviations out of 3 → ~0.667
        assert 0.6 < score < 0.7

    def test_export_summary(self, feedback_mod):
        rec = feedback_mod.FeedbackRecorder()
        rec.record("a", "a", 1.0)
        summary = rec.export_summary()
        assert "total" in summary
        assert "match_rate" in summary

    def test_evolution_callback(self, feedback_mod):
        events = []
        rec = feedback_mod.FeedbackRecorder()
        rec.evolution_callback = lambda e: events.append(e)
        rec.record("a", "b", 1.0)
        assert len(events) >= 1


# ===================================================================
# M370: PregameScoutEngine — 10 tests
# ===================================================================
class TestPregameScoutEngine:
    """Loading-screen full opponent history scan."""

    def test_scout_empty_list(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        result = scout.scout([])
        assert result == []

    def test_scout_single_opponent(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        result = scout.scout([
            {"summoner_name": "Player1", "champion": "Zed", "history": []}
        ])
        assert len(result) == 1
        assert "threat_level" in result[0]

    def test_scout_multiple_opponents(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        opponents = [
            {"summoner_name": f"P{i}", "champion": f"Champ{i}", "history": []}
            for i in range(5)
        ]
        result = scout.scout(opponents)
        assert len(result) == 5

    def test_threat_level_with_high_kda(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        result = scout.scout([{
            "summoner_name": "Smurf",
            "champion": "Yasuo",
            "history": [{"kda": 12.0, "cs_per_min": 9.0, "win": True}] * 5,
        }])
        assert result[0]["threat_level"] > 5.0

    def test_threat_level_with_low_kda(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        result = scout.scout([{
            "summoner_name": "Newbie",
            "champion": "Garen",
            "history": [{"kda": 0.5, "cs_per_min": 3.0, "win": False}] * 3,
        }])
        assert result[0]["threat_level"] < 5.0

    def test_scout_no_history_default_threat(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        result = scout.scout([{
            "summoner_name": "Unknown",
            "champion": "Aatrox",
            "history": [],
        }])
        assert result[0]["threat_level"] == 5.0  # default/neutral

    def test_scout_returns_champion_in_report(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        result = scout.scout([{
            "summoner_name": "X",
            "champion": "Lee Sin",
            "history": [],
        }])
        assert result[0]["champion"] == "Lee Sin"

    def test_scout_sorted_by_threat(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        opponents = [
            {"summoner_name": "Low", "champion": "A", "history": [{"kda": 1.0, "cs_per_min": 4.0, "win": False}]},
            {"summoner_name": "High", "champion": "B", "history": [{"kda": 15.0, "cs_per_min": 10.0, "win": True}] * 5},
        ]
        result = scout.scout(opponents)
        # Result should be sorted highest threat first
        assert result[0]["threat_level"] >= result[1]["threat_level"]

    def test_get_report(self, scout_mod):
        scout = scout_mod.PregameScoutEngine()
        scout.scout([{"summoner_name": "X", "champion": "Y", "history": []}])
        report = scout.get_report()
        assert isinstance(report, dict)
        assert "opponents" in report

    def test_evolution_callback(self, scout_mod):
        events = []
        scout = scout_mod.PregameScoutEngine()
        scout.evolution_callback = lambda e: events.append(e)
        scout.scout([{"summoner_name": "X", "champion": "Y", "history": []}])
        assert len(events) >= 1


# ===================================================================
# M371: ObjectiveTimer — 10 tests
# ===================================================================
class TestObjectiveTimer:
    """Dragon/Baron/Herald respawn countdown."""

    def test_init_no_active_timers(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        assert t.active_count() == 0

    def test_start_dragon_timer(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("dragon", killed_at=600.0)
        assert t.active_count() == 1

    def test_respawn_time_dragon(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("dragon", killed_at=600.0)
        info = t.get_timer("dragon")
        assert info["respawn_at"] == 600.0 + 300.0  # 5 min respawn

    def test_respawn_time_baron(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("baron", killed_at=1500.0)
        info = t.get_timer("baron")
        assert info["respawn_at"] == 1500.0 + 360.0  # 6 min respawn

    def test_respawn_time_herald(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("herald", killed_at=480.0)
        info = t.get_timer("herald")
        assert info["respawn_at"] == 480.0 + 360.0  # 6 min respawn

    def test_time_remaining(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("dragon", killed_at=600.0)
        remaining = t.time_remaining("dragon", current_time=700.0)
        assert remaining == 200.0  # 900 - 700

    def test_time_remaining_expired(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("dragon", killed_at=600.0)
        remaining = t.time_remaining("dragon", current_time=1000.0)
        assert remaining <= 0.0

    def test_clear_timer(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("dragon", killed_at=600.0)
        t.clear_timer("dragon")
        assert t.active_count() == 0

    def test_get_all_timers(self, timer_mod):
        t = timer_mod.ObjectiveTimer()
        t.start_timer("dragon", killed_at=600.0)
        t.start_timer("baron", killed_at=1500.0)
        all_t = t.get_all_timers()
        assert len(all_t) == 2

    def test_evolution_callback(self, timer_mod):
        events = []
        t = timer_mod.ObjectiveTimer()
        t.evolution_callback = lambda e: events.append(e)
        t.start_timer("dragon", killed_at=600.0)
        assert len(events) >= 1


# ===================================================================
# M372: TeamCompEvaluator — 10 tests
# ===================================================================
class TestTeamCompEvaluator:
    """Team composition strength analysis."""

    def test_evaluate_returns_dict(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(
            ally_champions=["Garen", "Lux", "Jinx", "Leona", "Lee Sin"],
            enemy_champions=["Zed", "Ahri", "Vayne", "Thresh", "Elise"],
        )
        assert isinstance(result, dict)

    def test_evaluate_has_scores(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(
            ally_champions=["A", "B", "C", "D", "E"],
            enemy_champions=["F", "G", "H", "I", "J"],
        )
        assert "ally_score" in result
        assert "enemy_score" in result

    def test_evaluate_teamfight_rating(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(
            ally_champions=["Malphite", "Orianna", "MissFortune", "Leona", "Amumu"],
            enemy_champions=["Fiora", "Zed", "Vayne", "Pyke", "Nidalee"],
        )
        assert "teamfight_rating" in result

    def test_evaluate_empty_teams_safe(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(ally_champions=[], enemy_champions=[])
        assert result["ally_score"] == 0.0

    def test_evaluate_single_champion(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(
            ally_champions=["Garen"],
            enemy_champions=["Darius"],
        )
        assert isinstance(result["ally_score"], float)

    def test_register_champion_data(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        ev.register_champion("TestChamp", {"role": "fighter", "teamfight": 7})
        result = ev.evaluate(
            ally_champions=["TestChamp"],
            enemy_champions=["Unknown"],
        )
        assert result["ally_score"] > 0.0

    def test_advantage_field(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(
            ally_champions=["A", "B", "C", "D", "E"],
            enemy_champions=["F", "G", "H", "I", "J"],
        )
        assert "advantage" in result

    def test_get_recommendation(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(
            ally_champions=["Garen", "Lux", "Jinx", "Leona", "Lee Sin"],
            enemy_champions=["Zed", "Ahri", "Vayne", "Thresh", "Elise"],
        )
        assert "recommendation" in result

    def test_evaluate_preserves_names(self, comp_mod):
        ev = comp_mod.TeamCompEvaluator()
        result = ev.evaluate(
            ally_champions=["Garen"],
            enemy_champions=["Darius"],
        )
        assert result["ally_champions"] == ["Garen"]

    def test_evolution_callback(self, comp_mod):
        events = []
        ev = comp_mod.TeamCompEvaluator()
        ev.evolution_callback = lambda e: events.append(e)
        ev.evaluate(ally_champions=["A"], enemy_champions=["B"])
        assert len(events) >= 1


# ===================================================================
# M373: DangerZoneDetector — 10 tests
# ===================================================================
class TestDangerZoneDetector:
    """Vision/enemy position-based safe zone calculation."""

    def test_assess_empty_enemies(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        result = d.assess(my_position=(5000, 5000), enemies=[], wards=[])
        assert result["danger_level"] == 0.0

    def test_assess_close_enemy(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        result = d.assess(
            my_position=(5000, 5000),
            enemies=[{"position": (5100, 5100), "champion": "Zed"}],
            wards=[],
        )
        assert result["danger_level"] > 0.0

    def test_assess_far_enemy(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        result = d.assess(
            my_position=(1000, 1000),
            enemies=[{"position": (14000, 14000), "champion": "Ashe"}],
            wards=[],
        )
        assert result["danger_level"] < 2.0

    def test_wards_reduce_danger(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        no_ward = d.assess(
            my_position=(5000, 5000),
            enemies=[{"position": (6000, 6000), "champion": "Zed"}],
            wards=[],
        )
        with_ward = d.assess(
            my_position=(5000, 5000),
            enemies=[{"position": (6000, 6000), "champion": "Zed"}],
            wards=[{"position": (5500, 5500)}],
        )
        assert with_ward["danger_level"] <= no_ward["danger_level"]

    def test_multiple_enemies_increase_danger(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        one = d.assess(
            my_position=(5000, 5000),
            enemies=[{"position": (6000, 6000), "champion": "Zed"}],
            wards=[],
        )
        three = d.assess(
            my_position=(5000, 5000),
            enemies=[
                {"position": (6000, 6000), "champion": "Zed"},
                {"position": (5500, 6000), "champion": "Ahri"},
                {"position": (6000, 5500), "champion": "Lee Sin"},
            ],
            wards=[],
        )
        assert three["danger_level"] > one["danger_level"]

    def test_result_has_safe_direction(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        result = d.assess(
            my_position=(5000, 5000),
            enemies=[{"position": (6000, 6000), "champion": "Zed"}],
            wards=[],
        )
        assert "safe_direction" in result

    def test_is_safe_true(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        result = d.assess(
            my_position=(1000, 1000),
            enemies=[],
            wards=[{"position": (1500, 1500)}],
        )
        assert result["is_safe"] is True

    def test_is_safe_false(self, danger_mod):
        d = danger_mod.DangerZoneDetector()
        result = d.assess(
            my_position=(5000, 5000),
            enemies=[
                {"position": (5100, 5100), "champion": "Zed"},
                {"position": (4900, 4900), "champion": "Talon"},
            ],
            wards=[],
        )
        assert result["is_safe"] is False

    def test_danger_threshold_configurable(self, danger_mod):
        d = danger_mod.DangerZoneDetector(danger_threshold=3.0)
        assert d.danger_threshold == 3.0

    def test_evolution_callback(self, danger_mod):
        events = []
        d = danger_mod.DangerZoneDetector()
        d.evolution_callback = lambda e: events.append(e)
        d.assess(
            my_position=(5000, 5000),
            enemies=[{"position": (6000, 6000), "champion": "Zed"}],
            wards=[],
        )
        assert len(events) >= 1


# ===================================================================
# M374: PostGameAnalyzer — 10 tests
# ===================================================================
class TestPostGameAnalyzer:
    """Post-game review and key moment annotation."""

    def test_analyze_returns_dict(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        result = a.analyze(game_data={
            "outcome": "win",
            "duration": 1800,
            "events": [],
            "stats": {"kills": 10, "deaths": 3, "assists": 15},
        })
        assert isinstance(result, dict)

    def test_analyze_outcome_win(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        result = a.analyze(game_data={
            "outcome": "win",
            "duration": 1800,
            "events": [],
            "stats": {"kills": 10, "deaths": 3, "assists": 15},
        })
        assert result["outcome"] == "win"

    def test_analyze_key_moments_empty_events(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        result = a.analyze(game_data={
            "outcome": "loss",
            "duration": 2400,
            "events": [],
            "stats": {"kills": 2, "deaths": 8, "assists": 5},
        })
        assert result["key_moments"] == []

    def test_analyze_identifies_key_kills(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        result = a.analyze(game_data={
            "outcome": "win",
            "duration": 1800,
            "events": [
                {"type": "multikill", "time": 900, "count": 3},
                {"type": "baron_kill", "time": 1500},
            ],
            "stats": {"kills": 15, "deaths": 5, "assists": 20},
        })
        assert len(result["key_moments"]) >= 1

    def test_analyze_improvements(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        result = a.analyze(game_data={
            "outcome": "loss",
            "duration": 2400,
            "events": [],
            "stats": {"kills": 2, "deaths": 10, "assists": 3},
        })
        assert "improvements" in result
        assert len(result["improvements"]) >= 1

    def test_analyze_performance_score(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        result = a.analyze(game_data={
            "outcome": "win",
            "duration": 1800,
            "events": [],
            "stats": {"kills": 15, "deaths": 2, "assists": 20},
        })
        assert "performance_score" in result
        assert 0.0 <= result["performance_score"] <= 10.0

    def test_analyze_empty_stats_safe(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        result = a.analyze(game_data={
            "outcome": "loss",
            "duration": 1800,
            "events": [],
            "stats": {},
        })
        assert result["performance_score"] >= 0.0

    def test_analyze_stores_history(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        a.analyze(game_data={"outcome": "win", "duration": 1800, "events": [], "stats": {}})
        a.analyze(game_data={"outcome": "loss", "duration": 2400, "events": [], "stats": {}})
        assert len(a.get_history()) == 2

    def test_trend_analysis(self, postgame_mod):
        a = postgame_mod.PostGameAnalyzer()
        for i in range(5):
            a.analyze(game_data={
                "outcome": "win" if i % 2 == 0 else "loss",
                "duration": 1800,
                "events": [],
                "stats": {"kills": 5 + i, "deaths": 3, "assists": 10},
            })
        trend = a.get_trend()
        assert "win_rate" in trend

    def test_evolution_callback(self, postgame_mod):
        events = []
        a = postgame_mod.PostGameAnalyzer()
        a.evolution_callback = lambda e: events.append(e)
        a.analyze(game_data={"outcome": "win", "duration": 1800, "events": [], "stats": {}})
        assert len(events) >= 1


# ===================================================================
# M375: LoLAgentOrchestrator — 10 tests
# ===================================================================
class TestLoLAgentOrchestrator:
    """Master orchestrator integrating all M346-M374 modules."""

    def test_init(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        assert o is not None

    def test_register_module(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        o.register_module("test_module", {"type": "mock"})
        assert "test_module" in o.list_modules()

    def test_unregister_module(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        o.register_module("test_module", {"type": "mock"})
        o.unregister_module("test_module")
        assert "test_module" not in o.list_modules()

    def test_get_status(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        status = o.get_status()
        assert "state" in status
        assert "modules" in status

    def test_process_tick_empty(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        result = o.process_tick({})
        assert isinstance(result, dict)

    def test_process_tick_with_game_data(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        result = o.process_tick({
            "game_time": 600,
            "gold_diff": 1000,
            "all_players": [],
        })
        assert "decisions" in result or "action" in result

    def test_start_session(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        o.start_session()
        assert o.get_status()["state"] == "active"

    def test_end_session(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        o.start_session()
        o.end_session()
        assert o.get_status()["state"] == "idle"

    def test_get_session_summary(self, orch_mod):
        o = orch_mod.LoLAgentOrchestrator()
        o.start_session()
        o.process_tick({"game_time": 100})
        summary = o.get_session_summary()
        assert "tick_count" in summary

    def test_evolution_callback(self, orch_mod):
        events = []
        o = orch_mod.LoLAgentOrchestrator()
        o.evolution_callback = lambda e: events.append(e)
        o.start_session()
        assert len(events) >= 1

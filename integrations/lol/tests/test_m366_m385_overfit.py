"""
Overfit Validation Tests for M366-M385.

15 independent integration tests to verify implementations are not
overfitting to unit tests. These test cross-module interactions and
edge cases not covered in unit tests.
"""

import importlib.util
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_LOL_SRC = os.path.join(_ROOT, "lol", "src", "lol_agent")
_CLI_SRC = os.path.join(_ROOT, "..", "agentos", "cli")


def _load(name: str, filepath: str):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===================================================================
# Cross-module integration tests
# ===================================================================

class TestOverfitValidation:

    def test_session_to_orchestrator_lifecycle(self):
        """GameSessionManager → LoLAgentOrchestrator start/end."""
        sm = _load("gsm_ov", os.path.join(_LOL_SRC, "game_session_manager.py"))
        om = _load("orch_ov", os.path.join(_LOL_SRC, "lol_agent_orchestrator.py"))
        session = sm.GameSessionManager()
        orch = om.LoLAgentOrchestrator()
        session.transition("loading")
        orch.start_session()
        session.transition("running")
        orch.process_tick({"game_time": 100})
        session.transition("ended")
        orch.end_session()
        assert session.state == "ended"
        assert orch.get_status()["state"] == "idle"

    def test_poller_to_decision_engine(self):
        """RealTimePoller feeds DecisionEngine."""
        pm = _load("rtp_ov", os.path.join(_LOL_SRC, "real_time_poller.py"))
        dm = _load("de_ov", os.path.join(_LOL_SRC, "decision_engine.py"))
        decisions = []
        engine = dm.DecisionEngine()
        poller = pm.RealTimePoller()
        poller.register_handler("allgamedata", lambda d: decisions.append(
            engine.decide({"game_time": d.get("gameData", {}).get("gameTime", 0)})
        ))
        poller.simulate_poll("allgamedata", {"gameData": {"gameTime": 600}})
        assert len(decisions) == 1
        assert "action" in decisions[0]

    def test_decision_to_feedback_loop(self):
        """DecisionEngine advice → FeedbackRecorder tracking."""
        dm = _load("de_ov2", os.path.join(_LOL_SRC, "decision_engine.py"))
        fm = _load("fr_ov", os.path.join(_LOL_SRC, "feedback_recorder.py"))
        engine = dm.DecisionEngine()
        recorder = fm.FeedbackRecorder()
        advice = engine.decide({"game_time": 500, "gold_diff": 2000})
        recorder.record(advice=advice["action"], action="different_action", game_time=500.0)
        recorder.record(advice=advice["action"], action=advice["action"], game_time=510.0)
        assert recorder.match_rate() == 0.5

    def test_scout_to_danger_zone(self):
        """PregameScoutEngine threats → DangerZoneDetector."""
        sm = _load("pse_ov", os.path.join(_LOL_SRC, "pregame_scout_engine.py"))
        dz = _load("dzd_ov", os.path.join(_LOL_SRC, "danger_zone_detector.py"))
        scout = sm.PregameScoutEngine()
        report = scout.scout([{
            "summoner_name": "Threat",
            "champion": "Zed",
            "history": [{"kda": 10.0, "cs_per_min": 8.0, "win": True}] * 5,
        }])
        detector = dz.DangerZoneDetector()
        result = detector.assess(
            my_position=(5000, 5000),
            enemies=[{"position": (5500, 5500), "champion": report[0]["champion"]}],
            wards=[],
        )
        assert result["danger_level"] > 0

    def test_objective_timer_expiry(self):
        """ObjectiveTimer multiple timers + expiry check."""
        tm = _load("ot_ov", os.path.join(_LOL_SRC, "objective_timer.py"))
        timer = tm.ObjectiveTimer()
        timer.start_timer("dragon", killed_at=300.0)
        timer.start_timer("baron", killed_at=1200.0)
        # Dragon: respawn at 600, Baron: respawn at 1560
        assert timer.time_remaining("dragon", current_time=500.0) == 100.0
        assert timer.time_remaining("dragon", current_time=700.0) <= 0.0
        assert timer.time_remaining("baron", current_time=1400.0) == 160.0

    def test_team_comp_to_strategy(self):
        """TeamCompEvaluator feeds into strategy context."""
        cm = _load("tce_ov", os.path.join(_LOL_SRC, "team_comp_evaluator.py"))
        dm = _load("de_ov3", os.path.join(_LOL_SRC, "decision_engine.py"))
        evaluator = cm.TeamCompEvaluator()
        comp = evaluator.evaluate(
            ally_champions=["A", "B", "C", "D", "E"],
            enemy_champions=["F", "G", "H", "I", "J"],
        )
        engine = dm.DecisionEngine()
        decision = engine.decide({
            "game_time": 1200,
            "gold_diff": 1000,
            "teamfight_rating": comp.get("teamfight_rating", 5.0),
        })
        assert "action" in decision

    def test_postgame_multiple_games_trend(self):
        """PostGameAnalyzer trend over 5+ games."""
        pm = _load("pga_ov", os.path.join(_LOL_SRC, "post_game_analyzer.py"))
        analyzer = pm.PostGameAnalyzer()
        for i in range(7):
            analyzer.analyze(game_data={
                "outcome": "win" if i % 3 != 0 else "loss",
                "duration": 1800 + i * 60,
                "events": [],
                "stats": {"kills": 5 + i, "deaths": 3, "assists": 10},
            })
        trend = analyzer.get_trend()
        assert "win_rate" in trend
        assert 0.0 <= trend["win_rate"] <= 1.0

    def test_config_loader_merge_and_validate(self):
        """ConfigLoader merge + validate pipeline."""
        cm = _load("cl_ov", os.path.join(_CLI_SRC, "config_loader.py"))
        loader = cm.ConfigLoader()
        base = {"game": "lol", "interval": 1.0}
        override = {"interval": 0.5, "debug": True}
        merged = loader.merge(base, override)
        assert loader.validate(merged, required=["game", "interval"]) is True
        assert merged["interval"] == 0.5
        assert merged["debug"] is True

    def test_launcher_to_status_reporter(self):
        """GameLauncher + StatusReporter integration."""
        gl = _load("gl_ov", os.path.join(_CLI_SRC, "game_launcher.py"))
        sr = _load("sr_ov", os.path.join(_CLI_SRC, "status_reporter.py"))
        launcher = gl.GameLauncher()
        reporter = sr.StatusReporter()
        launcher.register_game("lol", {"entry": "lol_main"})
        launcher.launch("lol")
        status = launcher.get_game_status("lol")
        text = reporter.format_status(status)
        assert isinstance(text, str)

    def test_plugin_loader_discover_and_filter(self):
        """PluginLoader discover + enable/disable flow."""
        pl = _load("pl_ov", os.path.join(_CLI_SRC, "plugin_loader.py"))
        loader = pl.PluginLoader()
        manifest = {"plugins": [
            {"name": "lol", "game": "lol"},
            {"name": "dota2", "game": "dota2"},
            {"name": "mahjong", "game": "mahjong"},
        ]}
        loader.discover_from_manifest(manifest)
        loader.disable("dota2")
        enabled = loader.list_enabled()
        assert "lol" in enabled
        assert "dota2" not in enabled
        assert "mahjong" in enabled

    def test_credential_store_and_mask(self):
        """CredentialManager store + mask cycle."""
        cm = _load("cm_ov", os.path.join(_CLI_SRC, "credential_manager.py"))
        mgr = cm.CredentialManager()
        mgr.store("riot_key", "RGAPI-abc123def456")
        masked = mgr.masked("riot_key")
        assert "abc123def456" not in masked
        assert mgr.retrieve("riot_key") == "RGAPI-abc123def456"

    def test_session_recorder_full_lifecycle(self):
        """SessionRecorder start→events→stop→export."""
        sr = _load("srec_ov", os.path.join(_CLI_SRC, "session_recorder.py"))
        recorder = sr.SessionRecorder()
        recorder.start()
        for i in range(5):
            recorder.record_event({"type": "tick", "time": i * 100, "data": {"gold": 1000 + i * 50}})
        recorder.stop()
        exported = recorder.export_json()
        data = json.loads(exported)
        assert len(data["events"]) == 5

    def test_upgrade_checker_version_comparison(self):
        """UpgradeChecker various version strings."""
        uc = _load("uc_ov", os.path.join(_CLI_SRC, "upgrade_checker.py"))
        checker = uc.UpgradeChecker(current_version="1.5.3")
        assert checker.needs_update("1.5.4") is True
        assert checker.needs_update("1.5.3") is False
        assert checker.needs_update("1.5.2") is False
        assert checker.needs_update("2.0.0") is True

    def test_health_monitor_multiple_checks(self):
        """HealthMonitor repeated checks build history."""
        hm = _load("hm_ov", os.path.join(_CLI_SRC, "health_monitor.py"))
        monitor = hm.HealthMonitor()
        for _ in range(3):
            monitor.check()
        hist = monitor.get_history()
        assert len(hist) == 3
        assert all("overall" in h for h in hist)

    def test_orchestrator_full_game_simulation(self):
        """LoLAgentOrchestrator processes 10 ticks."""
        om = _load("orch_ov2", os.path.join(_LOL_SRC, "lol_agent_orchestrator.py"))
        orch = om.LoLAgentOrchestrator()
        orch.start_session()
        for t in range(10):
            orch.process_tick({"game_time": t * 120, "gold_diff": t * 200})
        summary = orch.get_session_summary()
        assert summary["tick_count"] == 10
        orch.end_session()

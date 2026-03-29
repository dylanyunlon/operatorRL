"""
Overfit Validation Tests for M346-M365.

20 independent tests that verify the implementation isn't
overfitting to the unit test suite. These test cross-module
integration, edge cases, and real-world usage patterns.

Tests written as TDD Step 5 — independent subagent verification.
"""

import importlib.util
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_LOL_SRC = os.path.join(_ROOT, "integrations", "lol", "src", "lol_agent")
_GOV_DIR = os.path.join(_ROOT, "agentos", "governance")


def _load(name, filepath):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# =====================================================================
# Cross-module integration: LoL pipeline
# =====================================================================
class TestLoLPipelineIntegration:
    """LiveClientConnector → LiveGameState → OpponentHistoryMerger → WinProbPredictor → LoLStrategyAdvisor"""

    def test_live_state_to_strategy(self):
        lgs_mod = _load("lgs_ov", os.path.join(_LOL_SRC, "live_game_state.py"))
        adv_mod = _load("adv_ov", os.path.join(_LOL_SRC, "lol_strategy_advisor.py"))
        lgs = lgs_mod.LiveGameState()
        lgs.update({
            "activePlayer": {"summonerName": "Me", "level": 12,
                             "currentGold": 8000.0, "championStats": {"maxHealth": 2000}},
            "allPlayers": [
                {"summonerName": "Me", "championName": "Jinx", "team": "ORDER",
                 "level": 12, "scores": {"kills": 8, "deaths": 3, "assists": 5, "creepScore": 180}},
                {"summonerName": "Foe", "championName": "Zed", "team": "CHAOS",
                 "level": 11, "scores": {"kills": 4, "deaths": 5, "assists": 2, "creepScore": 150}},
            ],
            "events": {"Events": []},
            "gameData": {"gameMode": "CLASSIC", "gameTime": 1200.0, "mapNumber": 11},
        })
        adv = adv_mod.LoLStrategyAdvisor()
        advice = adv.advise({
            "game_time": lgs.game_time,
            "gold_diff": lgs.compute_gold_advantage("ORDER"),
            "kill_diff": 4,
            "dragon_count": 1,
            "tower_diff": 2,
        })
        assert isinstance(advice, dict)

    def test_opponent_merge_to_threat_to_prediction(self):
        merger_mod = _load("mrg_ov", os.path.join(_LOL_SRC, "opponent_history_merger.py"))
        wp_mod = _load("wp_ov", os.path.join(_LOL_SRC, "win_probability_predictor.py"))
        ohm = merger_mod.OpponentHistoryMerger()
        live = {"summonerName": "E1", "championName": "Yasuo", "team": "CHAOS",
                "level": 10, "scores": {"kills": 7, "deaths": 2, "assists": 3, "creepScore": 160}}
        hist = [{"gameId": i, "champion": "Yasuo", "stats": {"kills": 8, "deaths": 3, "assists": 4}}
                for i in range(5)]
        merged = ohm.merge(live, hist)
        threat = ohm.compute_threat_score(merged)
        wpp = wp_mod.WinProbabilityPredictor()
        prob = wpp.predict({"gold_diff": -1000, "kill_diff": -3, "tower_diff": -1,
                            "dragon_diff": 0, "game_time": 900.0})
        assert 0.0 <= threat <= 1.0
        assert 0.0 <= prob <= 1.0

    def test_champion_analyzer_to_item_advisor(self):
        cpa_mod = _load("cpa_ov", os.path.join(_LOL_SRC, "champion_performance_analyzer.py"))
        iba_mod = _load("iba_ov", os.path.join(_LOL_SRC, "item_build_advisor.py"))
        cpa = cpa_mod.ChampionPerformanceAnalyzer()
        report = cpa.analyze({"kills": 10, "deaths": 1, "assists": 8, "creepScore": 200,
                               "damageDealt": 30000, "wardsPlaced": 10, "game_time_seconds": 1500})
        iba = iba_mod.ItemBuildAdvisor()
        items = iba.recommend({"champion": "Jinx", "role": "ADC", "current_items": [],
                               "gold": 3500, "opponents": []})
        assert report["kda"] > 0
        assert isinstance(items, list)

    def test_voice_engine_dequeue_order(self):
        vne_mod = _load("vne_ov", os.path.join(_LOL_SRC, "voice_narration_engine.py"))
        vne = vne_mod.VoiceNarrationEngine()
        vne.enqueue("low", priority=5)
        vne.enqueue("critical", priority=0)
        vne.enqueue("medium", priority=3)
        first = vne.dequeue()
        assert "critical" in first.lower()
        second = vne.dequeue()
        assert "medium" in second.lower()

    def test_evolution_loop_full_cycle(self):
        elo_mod = _load("elo_ov", os.path.join(_LOL_SRC, "lol_evolution_loop.py"))
        loop = elo_mod.LoLEvolutionLoop(evolve_threshold=2)
        loop.record_episode([{"g": 1}], [{"a": "f"}], reward=0.7)
        loop.record_episode([{"g": 2}], [{"a": "g"}], reward=0.9)
        assert loop.should_evolve()
        loop.advance_generation()
        spans = loop.export_training_data()
        assert len(spans) >= 1
        loop.reset()
        assert loop.episode_count() == 0


# =====================================================================
# Cross-module integration: Governance pipeline
# =====================================================================
class TestGovernancePipelineIntegration:
    """GameRegistry → DeploymentManager → TelemetryCollector → EvolutionOrchestrator"""

    def test_registry_to_deployment(self):
        reg_mod = _load("reg_ov", os.path.join(_GOV_DIR, "game_registry.py"))
        dep_mod = _load("dep_ov", os.path.join(_GOV_DIR, "deployment_manager.py"))
        reg = reg_mod.GameRegistry()
        reg.register("lol", config={"type": "moba"})
        cfg = reg.get_config("lol")
        dm = dep_mod.DeploymentManager()
        result = dm.deploy(game="lol", version="v1.0", config=cfg)
        assert result["status"] in ("deployed", "running")

    def test_deployment_to_telemetry(self):
        dep_mod = _load("dep2_ov", os.path.join(_GOV_DIR, "deployment_manager.py"))
        tel_mod = _load("tel_ov", os.path.join(_GOV_DIR, "telemetry_collector.py"))
        dm = dep_mod.DeploymentManager()
        dm.deploy(game="lol", version="v1.0", config={})
        health = dm.health_check("lol")
        tc = tel_mod.TelemetryCollector()
        tc.record("lol", metric="health", value=1.0 if health["healthy"] else 0.0)
        assert tc.get_latest("lol", "health") == 1.0

    def test_orchestrator_with_real_loops(self):
        orch_mod = _load("orch_ov", os.path.join(_GOV_DIR, "evolution_orchestrator.py"))
        elo_mod = _load("elo2_ov", os.path.join(_LOL_SRC, "lol_evolution_loop.py"))
        loop = elo_mod.LoLEvolutionLoop(evolve_threshold=1)
        loop.record_episode([{}], [{}], reward=0.8)
        eo = orch_mod.EvolutionOrchestrator()
        eo.register_loop("lol", loop)
        eo.run_cycle()
        assert loop.generation >= 1

    def test_pipeline_run_with_real_data(self):
        pipe_mod = _load("pipe_ov", os.path.join(_GOV_DIR, "data_pipeline.py"))
        dp = pipe_mod.DataPipeline()
        dp.add_stage("filter_valid", fn=lambda d: [x for x in d if x.get("reward") is not None])
        dp.add_stage("normalize", fn=lambda d: [
            {**x, "reward": max(-1, min(1, x["reward"]))} for x in d
        ])
        raw = [
            {"state": "s1", "action": "a1", "reward": 0.5},
            {"state": "s2", "action": "a2", "reward": None},
            {"state": "s3", "action": "a3", "reward": 5.0},
        ]
        result = dp.run(raw)
        assert len(result) == 2
        assert result[1]["reward"] == 1.0

    def test_notification_broadcast_integration(self):
        notif_mod = _load("notif_ov", os.path.join(_GOV_DIR, "notification_service.py"))
        ns = notif_mod.NotificationService()
        received = []
        ns.subscribe("evolution_complete", callback=lambda msg: received.append(msg))
        ns.broadcast("evolution_complete", message="LoL model v2.0 deployed")
        assert len(received) == 1

    def test_ab_test_full_flow(self):
        ab_mod = _load("ab_ov", os.path.join(_GOV_DIR, "ab_test_controller.py"))
        ab = ab_mod.ABTestController()
        ab.create_experiment(name="strat_test", variants=["aggro", "passive"],
                            traffic_split=[0.5, 0.5])
        for _ in range(30):
            ab.record_outcome("strat_test", variant="aggro", metric_value=0.7)
            ab.record_outcome("strat_test", variant="passive", metric_value=0.4)
        results = ab.get_results("strat_test")
        assert results["aggro"]["mean"] > results["passive"]["mean"]

    def test_policy_enforcer_blocks_excessive_rate(self):
        pol_mod = _load("pol_ov", os.path.join(_GOV_DIR, "policy_enforcer.py"))
        pe = pol_mod.PolicyEnforcer()
        pe.add_rule(name="max_actions_per_second", limit=10)
        ok = pe.check(action={"type": "move", "rate": 5})
        blocked = pe.check(action={"type": "move", "rate": 20})
        assert ok["allowed"] is True
        assert blocked["allowed"] is False

    def test_model_versioner_rollback_chain(self):
        mv_mod = _load("mv_ov", os.path.join(_GOV_DIR, "model_versioner.py"))
        mv = mv_mod.ModelVersioner()
        mv.save("m1", version="v1", weights={"w": [1]})
        mv.save("m1", version="v2", weights={"w": [2]})
        mv.save("m1", version="v3", weights={"w": [3]})
        mv.rollback("m1")  # back to v2
        latest = mv.load_latest("m1")
        assert latest["w"] == [2]

    def test_dashboard_multi_game_snapshot(self):
        db_mod = _load("db_ov", os.path.join(_GOV_DIR, "cross_game_dashboard.py"))
        db = db_mod.CrossGameDashboard()
        for game in ["lol", "dota2", "mahjong"]:
            db.register_panel(game, metrics=["wr", "fitness"])
            db.update_metric(game, "wr", 0.5)
            db.update_metric(game, "fitness", 0.6)
        snap = db.get_global_snapshot()
        assert len(snap) == 3
        for g in snap:
            assert snap[g]["wr"] == 0.5

    def test_seraphine_history_parse_round_trip(self):
        shc_mod = _load("shc_ov", os.path.join(_LOL_SRC, "seraphine_history_client.py"))
        shc = shc_mod.SeraphineHistoryClient()
        url = shc.build_match_history_url(puuid="test-puuid-123", count=10)
        assert "test-puuid-123" in url
        parsed = shc.parse_match_list({"games": [
            {"gameId": i, "champion": f"Champ{i}", "stats": {"kills": i, "deaths": 1, "assists": i * 2}}
            for i in range(10)
        ]})
        assert len(parsed) == 10
        assert parsed[5]["gameId"] == 5

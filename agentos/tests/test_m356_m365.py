"""
TDD Tests for M356-M365: Unified AgentOS Governance + Cross-game Deployment.

100 tests (10 per module), designed for ~50% initial failure rate.
Tests written BEFORE implementation per TDD methodology.

Reference projects (拿来主义):
  - DI-star: policy_factory.py game registry pattern
  - PARL: agent_factory.py deployment lifecycle
  - operatorRL: AgentOS governance patterns, evolution_callback, _EVOLUTION_KEY
"""

import importlib.util
import json
import os
import sys
import time

import pytest

# ---------------------------------------------------------------------------
# Helper: load module from file path
# ---------------------------------------------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_GOV_DIR = os.path.join(_ROOT, "governance")


def _load(name: str, filepath: str):
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def game_registry_mod():
    return _load("game_registry", os.path.join(_GOV_DIR, "game_registry.py"))


@pytest.fixture(scope="module")
def deploy_mod():
    return _load("deployment_manager", os.path.join(_GOV_DIR, "deployment_manager.py"))


@pytest.fixture(scope="module")
def ab_test_mod():
    return _load("ab_test_controller", os.path.join(_GOV_DIR, "ab_test_controller.py"))


@pytest.fixture(scope="module")
def telemetry_mod():
    return _load("telemetry_collector", os.path.join(_GOV_DIR, "telemetry_collector.py"))


@pytest.fixture(scope="module")
def policy_mod():
    return _load("policy_enforcer", os.path.join(_GOV_DIR, "policy_enforcer.py"))


@pytest.fixture(scope="module")
def versioner_mod():
    return _load("model_versioner", os.path.join(_GOV_DIR, "model_versioner.py"))


@pytest.fixture(scope="module")
def dashboard_mod():
    return _load("cross_game_dashboard", os.path.join(_GOV_DIR, "cross_game_dashboard.py"))


@pytest.fixture(scope="module")
def orchestrator_mod():
    return _load("evolution_orchestrator", os.path.join(_GOV_DIR, "evolution_orchestrator.py"))


@pytest.fixture(scope="module")
def pipeline_mod():
    return _load("data_pipeline", os.path.join(_GOV_DIR, "data_pipeline.py"))


@pytest.fixture(scope="module")
def notif_mod():
    return _load("notification_service", os.path.join(_GOV_DIR, "notification_service.py"))


# =====================================================================
# M356 — GameRegistry (10 tests)
# Reference: DI-star policy_factory game registration
# =====================================================================
class TestGameRegistry:
    def test_class_exists(self, game_registry_mod):
        assert hasattr(game_registry_mod, "GameRegistry")

    def test_has_evolution_key(self, game_registry_mod):
        assert hasattr(game_registry_mod, "_EVOLUTION_KEY")
        assert "game_registry" in game_registry_mod._EVOLUTION_KEY

    def test_instantiation(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        assert reg is not None

    def test_register_game(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        reg.register("league_of_legends", config={"type": "moba", "api": "live_client"})
        assert reg.is_registered("league_of_legends")

    def test_register_multiple_games(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        reg.register("lol", config={"type": "moba"})
        reg.register("dota2", config={"type": "moba"})
        reg.register("mahjong", config={"type": "tabletop"})
        assert reg.count() == 3

    def test_get_game_config(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        reg.register("lol", config={"type": "moba", "api": "live_client"})
        cfg = reg.get_config("lol")
        assert cfg["type"] == "moba"

    def test_unregister(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        reg.register("temp_game", config={"type": "test"})
        reg.unregister("temp_game")
        assert not reg.is_registered("temp_game")

    def test_list_games(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        reg.register("lol", config={})
        reg.register("dota2", config={})
        games = reg.list_games()
        assert "lol" in games
        assert "dota2" in games

    def test_get_unregistered_raises(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        with pytest.raises(KeyError):
            reg.get_config("nonexistent")

    def test_evolution_callback(self, game_registry_mod):
        reg = game_registry_mod.GameRegistry()
        events = []
        reg.evolution_callback = lambda e: events.append(e)
        reg.register("lol", config={"type": "moba"})
        assert len(events) >= 1


# =====================================================================
# M357 — DeploymentManager (10 tests)
# Reference: PARL agent deployment lifecycle
# =====================================================================
class TestDeploymentManager:
    def test_class_exists(self, deploy_mod):
        assert hasattr(deploy_mod, "DeploymentManager")

    def test_has_evolution_key(self, deploy_mod):
        assert hasattr(deploy_mod, "_EVOLUTION_KEY")
        assert "deployment" in deploy_mod._EVOLUTION_KEY

    def test_instantiation(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        assert dm is not None

    def test_deploy(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        result = dm.deploy(game="lol", version="v1.0", config={"mode": "assistant"})
        assert result["status"] in ("deployed", "running")

    def test_health_check(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        dm.deploy(game="lol", version="v1.0", config={})
        health = dm.health_check("lol")
        assert health["healthy"] is True

    def test_rollback(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        dm.deploy(game="lol", version="v1.0", config={})
        dm.deploy(game="lol", version="v2.0", config={})
        dm.rollback("lol")
        status = dm.get_status("lol")
        assert status["version"] == "v1.0"

    def test_stop(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        dm.deploy(game="lol", version="v1.0", config={})
        dm.stop("lol")
        status = dm.get_status("lol")
        assert status["status"] == "stopped"

    def test_list_deployments(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        dm.deploy(game="lol", version="v1.0", config={})
        dm.deploy(game="dota2", version="v1.0", config={})
        deps = dm.list_deployments()
        assert len(deps) == 2

    def test_deploy_nonexistent_stop(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        with pytest.raises(KeyError):
            dm.stop("nonexistent")

    def test_evolution_callback(self, deploy_mod):
        dm = deploy_mod.DeploymentManager()
        events = []
        dm.evolution_callback = lambda e: events.append(e)
        dm.deploy(game="test", version="v1", config={})
        assert len(events) >= 1


# =====================================================================
# M358 — ABTestController (10 tests)
# =====================================================================
class TestABTestController:
    def test_class_exists(self, ab_test_mod):
        assert hasattr(ab_test_mod, "ABTestController")

    def test_has_evolution_key(self, ab_test_mod):
        assert hasattr(ab_test_mod, "_EVOLUTION_KEY")
        assert "ab_test" in ab_test_mod._EVOLUTION_KEY

    def test_instantiation(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        assert ab is not None

    def test_create_experiment(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        exp_id = ab.create_experiment(
            name="strategy_v1_vs_v2",
            variants=["strategy_v1", "strategy_v2"],
            traffic_split=[0.5, 0.5],
        )
        assert exp_id is not None

    def test_assign_variant(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        ab.create_experiment(
            name="test_exp",
            variants=["A", "B"],
            traffic_split=[0.5, 0.5],
        )
        variant = ab.assign_variant("test_exp", user_id="user_123")
        assert variant in ("A", "B")

    def test_record_outcome(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        ab.create_experiment(name="exp1", variants=["A", "B"], traffic_split=[0.5, 0.5])
        ab.record_outcome("exp1", variant="A", metric_value=0.8)
        ab.record_outcome("exp1", variant="B", metric_value=0.6)
        results = ab.get_results("exp1")
        assert "A" in results
        assert "B" in results

    def test_statistical_significance(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        ab.create_experiment(name="sig_test", variants=["A", "B"], traffic_split=[0.5, 0.5])
        for _ in range(50):
            ab.record_outcome("sig_test", variant="A", metric_value=0.9)
            ab.record_outcome("sig_test", variant="B", metric_value=0.3)
        sig = ab.is_significant("sig_test", p_threshold=0.05)
        assert sig is True

    def test_not_significant_small_sample(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        ab.create_experiment(name="small_exp", variants=["A", "B"], traffic_split=[0.5, 0.5])
        ab.record_outcome("small_exp", variant="A", metric_value=0.5)
        ab.record_outcome("small_exp", variant="B", metric_value=0.5)
        sig = ab.is_significant("small_exp", p_threshold=0.05)
        assert sig is False

    def test_list_experiments(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        ab.create_experiment(name="exp_a", variants=["X", "Y"], traffic_split=[0.5, 0.5])
        ab.create_experiment(name="exp_b", variants=["X", "Y"], traffic_split=[0.5, 0.5])
        exps = ab.list_experiments()
        assert len(exps) >= 2

    def test_evolution_callback(self, ab_test_mod):
        ab = ab_test_mod.ABTestController()
        events = []
        ab.evolution_callback = lambda e: events.append(e)
        ab.create_experiment(name="cb_exp", variants=["A", "B"], traffic_split=[0.5, 0.5])
        assert len(events) >= 1


# =====================================================================
# M359 — TelemetryCollector (10 tests)
# =====================================================================
class TestTelemetryCollector:
    def test_class_exists(self, telemetry_mod):
        assert hasattr(telemetry_mod, "TelemetryCollector")

    def test_has_evolution_key(self, telemetry_mod):
        assert hasattr(telemetry_mod, "_EVOLUTION_KEY")
        assert "telemetry" in telemetry_mod._EVOLUTION_KEY

    def test_instantiation(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        assert tc is not None

    def test_record_metric(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        tc.record("lol", metric="win_rate", value=0.55)
        metrics = tc.get_metrics("lol")
        assert len(metrics) >= 1

    def test_record_multiple_games(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        tc.record("lol", metric="win_rate", value=0.55)
        tc.record("dota2", metric="win_rate", value=0.60)
        tc.record("mahjong", metric="win_rate", value=0.45)
        games = tc.list_games_with_metrics()
        assert len(games) == 3

    def test_get_latest(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        tc.record("lol", metric="fps", value=60.0)
        tc.record("lol", metric="fps", value=55.0)
        latest = tc.get_latest("lol", "fps")
        assert latest == 55.0

    def test_get_average(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        tc.record("lol", metric="latency", value=30.0)
        tc.record("lol", metric="latency", value=50.0)
        avg = tc.get_average("lol", "latency")
        assert abs(avg - 40.0) < 0.01

    def test_clear_game(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        tc.record("lol", metric="test", value=1.0)
        tc.clear("lol")
        assert len(tc.get_metrics("lol")) == 0

    def test_empty_metrics(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        metrics = tc.get_metrics("nonexistent")
        assert metrics == [] or metrics == {}

    def test_evolution_callback(self, telemetry_mod):
        tc = telemetry_mod.TelemetryCollector()
        events = []
        tc.evolution_callback = lambda e: events.append(e)
        tc.record("lol", metric="score", value=100.0)
        assert len(events) >= 1


# =====================================================================
# M360 — PolicyEnforcer (10 tests)
# =====================================================================
class TestPolicyEnforcer:
    def test_class_exists(self, policy_mod):
        assert hasattr(policy_mod, "PolicyEnforcer")

    def test_has_evolution_key(self, policy_mod):
        assert hasattr(policy_mod, "_EVOLUTION_KEY")
        assert "policy_enforcer" in policy_mod._EVOLUTION_KEY

    def test_instantiation(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        assert pe is not None

    def test_add_rule(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        pe.add_rule(name="max_actions_per_second", limit=10)
        assert pe.rule_count() >= 1

    def test_check_pass(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        pe.add_rule(name="max_actions_per_second", limit=10)
        result = pe.check(action={"type": "move", "rate": 5})
        assert result["allowed"] is True

    def test_check_fail(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        pe.add_rule(name="max_actions_per_second", limit=10)
        result = pe.check(action={"type": "move", "rate": 15})
        assert result["allowed"] is False

    def test_fairness_constraint(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        pe.add_rule(name="fairness", limit=1.0)
        result = pe.check(action={"type": "exploit", "advantage_score": 0.5})
        assert result["allowed"] is True

    def test_remove_rule(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        pe.add_rule(name="temp_rule", limit=5)
        pe.remove_rule("temp_rule")
        assert pe.rule_count() == 0

    def test_list_rules(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        pe.add_rule(name="rule_a", limit=10)
        pe.add_rule(name="rule_b", limit=20)
        rules = pe.list_rules()
        assert len(rules) == 2

    def test_evolution_callback(self, policy_mod):
        pe = policy_mod.PolicyEnforcer()
        events = []
        pe.evolution_callback = lambda e: events.append(e)
        pe.add_rule(name="test_rule", limit=5)
        pe.check(action={"type": "test", "rate": 3})
        assert len(events) >= 1


# =====================================================================
# M361 — ModelVersioner (10 tests)
# =====================================================================
class TestModelVersioner:
    def test_class_exists(self, versioner_mod):
        assert hasattr(versioner_mod, "ModelVersioner")

    def test_has_evolution_key(self, versioner_mod):
        assert hasattr(versioner_mod, "_EVOLUTION_KEY")
        assert "model_versioner" in versioner_mod._EVOLUTION_KEY

    def test_instantiation(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        assert mv is not None

    def test_save_version(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        mv.save("lol_strategy", version="v1.0", weights={"layer1": [0.1, 0.2, 0.3]})
        assert mv.version_count("lol_strategy") >= 1

    def test_load_version(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        mv.save("test_model", version="v1.0", weights={"w": [1.0, 2.0]})
        loaded = mv.load("test_model", version="v1.0")
        assert loaded["w"] == [1.0, 2.0]

    def test_load_latest(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        mv.save("model_a", version="v1.0", weights={"w": [1.0]})
        mv.save("model_a", version="v2.0", weights={"w": [2.0]})
        latest = mv.load_latest("model_a")
        assert latest["w"] == [2.0]

    def test_rollback(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        mv.save("model_b", version="v1.0", weights={"w": [1.0]})
        mv.save("model_b", version="v2.0", weights={"w": [2.0]})
        mv.rollback("model_b")
        latest = mv.load_latest("model_b")
        assert latest["w"] == [1.0]

    def test_list_versions(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        mv.save("model_c", version="v1.0", weights={})
        mv.save("model_c", version="v2.0", weights={})
        mv.save("model_c", version="v3.0", weights={})
        versions = mv.list_versions("model_c")
        assert len(versions) == 3

    def test_diff(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        mv.save("model_d", version="v1", weights={"a": [1.0, 2.0]})
        mv.save("model_d", version="v2", weights={"a": [1.0, 3.0]})
        diff = mv.diff("model_d", "v1", "v2")
        assert diff is not None
        assert len(diff) > 0

    def test_evolution_callback(self, versioner_mod):
        mv = versioner_mod.ModelVersioner()
        events = []
        mv.evolution_callback = lambda e: events.append(e)
        mv.save("cb_model", version="v1", weights={"x": [1.0]})
        assert len(events) >= 1


# =====================================================================
# M362 — CrossGameDashboard (10 tests)
# =====================================================================
class TestCrossGameDashboard:
    def test_class_exists(self, dashboard_mod):
        assert hasattr(dashboard_mod, "CrossGameDashboard")

    def test_has_evolution_key(self, dashboard_mod):
        assert hasattr(dashboard_mod, "_EVOLUTION_KEY")
        assert "dashboard" in dashboard_mod._EVOLUTION_KEY

    def test_instantiation(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        assert db is not None

    def test_register_game_panel(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        db.register_panel("lol", metrics=["win_rate", "kda", "cs_per_min"])
        assert db.panel_count() >= 1

    def test_update_metric(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        db.register_panel("lol", metrics=["win_rate"])
        db.update_metric("lol", "win_rate", 0.55)
        snapshot = db.get_snapshot("lol")
        assert snapshot["win_rate"] == 0.55

    def test_get_global_snapshot(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        db.register_panel("lol", metrics=["wr"])
        db.register_panel("dota2", metrics=["wr"])
        db.update_metric("lol", "wr", 0.55)
        db.update_metric("dota2", "wr", 0.60)
        snapshot = db.get_global_snapshot()
        assert "lol" in snapshot
        assert "dota2" in snapshot

    def test_remove_panel(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        db.register_panel("temp", metrics=["x"])
        db.remove_panel("temp")
        assert db.panel_count() == 0

    def test_export_report(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        db.register_panel("lol", metrics=["wr"])
        db.update_metric("lol", "wr", 0.55)
        report = db.export_report()
        assert isinstance(report, (dict, str))

    def test_empty_dashboard(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        snapshot = db.get_global_snapshot()
        assert snapshot == {} or isinstance(snapshot, dict)

    def test_evolution_callback(self, dashboard_mod):
        db = dashboard_mod.CrossGameDashboard()
        events = []
        db.evolution_callback = lambda e: events.append(e)
        db.register_panel("lol", metrics=["wr"])
        db.update_metric("lol", "wr", 0.55)
        assert len(events) >= 1


# =====================================================================
# M363 — EvolutionOrchestrator (10 tests)
# =====================================================================
class TestEvolutionOrchestrator:
    def test_class_exists(self, orchestrator_mod):
        assert hasattr(orchestrator_mod, "EvolutionOrchestrator")

    def test_has_evolution_key(self, orchestrator_mod):
        assert hasattr(orchestrator_mod, "_EVOLUTION_KEY")
        assert "evolution_orchestrator" in orchestrator_mod._EVOLUTION_KEY

    def test_instantiation(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator()
        assert eo is not None

    def test_register_loop(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator()

        class MockLoop:
            def compute_fitness(self): return 0.5
            def should_evolve(self): return True
            def advance_generation(self): pass
            def export_training_data(self): return []
            def reset(self): pass

        eo.register_loop("lol", MockLoop())
        assert eo.loop_count() >= 1

    def test_schedule_evolution(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator()

        class MockLoop:
            def __init__(self):
                self.gen = 0
            def compute_fitness(self): return 0.6
            def should_evolve(self): return True
            def advance_generation(self):
                self.gen += 1
            def export_training_data(self): return [{"span": "data"}]
            def reset(self): pass

        loop = MockLoop()
        eo.register_loop("lol", loop)
        eo.run_cycle()
        assert loop.gen >= 1

    def test_resource_allocation(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator(total_budget=100)

        class MockLoop:
            def compute_fitness(self): return 0.5
            def should_evolve(self): return True
            def advance_generation(self): pass
            def export_training_data(self): return []
            def reset(self): pass

        eo.register_loop("lol", MockLoop())
        eo.register_loop("dota2", MockLoop())
        alloc = eo.allocate_resources()
        assert sum(alloc.values()) <= 100

    def test_unregister_loop(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator()

        class MockLoop:
            def compute_fitness(self): return 0.0
            def should_evolve(self): return False
            def advance_generation(self): pass
            def export_training_data(self): return []
            def reset(self): pass

        eo.register_loop("temp", MockLoop())
        eo.unregister_loop("temp")
        assert eo.loop_count() == 0

    def test_get_fitness_report(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator()

        class MockLoop:
            def compute_fitness(self): return 0.7
            def should_evolve(self): return False
            def advance_generation(self): pass
            def export_training_data(self): return []
            def reset(self): pass

        eo.register_loop("lol", MockLoop())
        report = eo.get_fitness_report()
        assert "lol" in report
        assert report["lol"] == 0.7

    def test_empty_run_cycle(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator()
        # Should not crash with no loops
        eo.run_cycle()

    def test_evolution_callback(self, orchestrator_mod):
        eo = orchestrator_mod.EvolutionOrchestrator()
        events = []
        eo.evolution_callback = lambda e: events.append(e)

        class MockLoop:
            def compute_fitness(self): return 0.8
            def should_evolve(self): return True
            def advance_generation(self): pass
            def export_training_data(self): return [{"x": 1}]
            def reset(self): pass

        eo.register_loop("lol", MockLoop())
        eo.run_cycle()
        assert len(events) >= 1


# =====================================================================
# M364 — DataPipeline (10 tests)
# =====================================================================
class TestDataPipeline:
    def test_class_exists(self, pipeline_mod):
        assert hasattr(pipeline_mod, "DataPipeline")

    def test_has_evolution_key(self, pipeline_mod):
        assert hasattr(pipeline_mod, "_EVOLUTION_KEY")
        assert "data_pipeline" in pipeline_mod._EVOLUTION_KEY

    def test_instantiation(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        assert dp is not None

    def test_add_stage(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        dp.add_stage("clean", fn=lambda data: [d for d in data if d.get("valid")])
        assert dp.stage_count() >= 1

    def test_run_pipeline(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        dp.add_stage("passthrough", fn=lambda data: data)
        raw = [{"value": 1}, {"value": 2}]
        result = dp.run(raw)
        assert len(result) == 2

    def test_multi_stage(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        dp.add_stage("filter", fn=lambda data: [d for d in data if d.get("value", 0) > 1])
        dp.add_stage("transform", fn=lambda data: [{"v": d["value"] * 2} for d in data])
        raw = [{"value": 1}, {"value": 2}, {"value": 3}]
        result = dp.run(raw)
        assert len(result) == 2
        assert result[0]["v"] == 4

    def test_empty_pipeline(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        result = dp.run([{"x": 1}])
        assert result == [{"x": 1}]

    def test_empty_data(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        dp.add_stage("noop", fn=lambda data: data)
        result = dp.run([])
        assert result == []

    def test_to_training_spans(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        dp.add_stage("to_span", fn=lambda data: [
            {"state": d.get("state"), "action": d.get("action"), "reward": d.get("reward", 0)}
            for d in data
        ])
        raw = [{"state": {"hp": 100}, "action": "farm", "reward": 0.5}]
        spans = dp.run(raw)
        assert spans[0]["reward"] == 0.5

    def test_evolution_callback(self, pipeline_mod):
        dp = pipeline_mod.DataPipeline()
        events = []
        dp.evolution_callback = lambda e: events.append(e)
        dp.add_stage("noop", fn=lambda d: d)
        dp.run([{"x": 1}])
        assert len(events) >= 1


# =====================================================================
# M365 — NotificationService (10 tests)
# =====================================================================
class TestNotificationService:
    def test_class_exists(self, notif_mod):
        assert hasattr(notif_mod, "NotificationService")

    def test_has_evolution_key(self, notif_mod):
        assert hasattr(notif_mod, "_EVOLUTION_KEY")
        assert "notification" in notif_mod._EVOLUTION_KEY

    def test_instantiation(self, notif_mod):
        ns = notif_mod.NotificationService()
        assert ns is not None

    def test_send_notification(self, notif_mod):
        ns = notif_mod.NotificationService()
        result = ns.send(channel="log", message="Evolution complete for LoL v2.0")
        assert result["sent"] is True

    def test_subscribe(self, notif_mod):
        ns = notif_mod.NotificationService()
        ns.subscribe("evolution_events", callback=lambda msg: None)
        assert ns.subscriber_count("evolution_events") >= 1

    def test_broadcast(self, notif_mod):
        ns = notif_mod.NotificationService()
        received = []
        ns.subscribe("alerts", callback=lambda msg: received.append(msg))
        ns.broadcast("alerts", message="Model degradation detected!")
        assert len(received) == 1
        assert "degradation" in received[0]

    def test_multiple_subscribers(self, notif_mod):
        ns = notif_mod.NotificationService()
        r1, r2 = [], []
        ns.subscribe("ch1", callback=lambda msg: r1.append(msg))
        ns.subscribe("ch1", callback=lambda msg: r2.append(msg))
        ns.broadcast("ch1", message="test")
        assert len(r1) == 1
        assert len(r2) == 1

    def test_unsubscribe(self, notif_mod):
        ns = notif_mod.NotificationService()
        cb = lambda msg: None
        ns.subscribe("ch2", callback=cb)
        ns.unsubscribe("ch2", callback=cb)
        assert ns.subscriber_count("ch2") == 0

    def test_history(self, notif_mod):
        ns = notif_mod.NotificationService()
        ns.send(channel="log", message="msg1")
        ns.send(channel="log", message="msg2")
        history = ns.get_history("log")
        assert len(history) >= 2

    def test_evolution_callback(self, notif_mod):
        ns = notif_mod.NotificationService()
        events = []
        ns.evolution_callback = lambda e: events.append(e)
        ns.send(channel="log", message="test event")
        assert len(events) >= 1

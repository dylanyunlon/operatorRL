"""
TDD Tests for M726-M745: Intel Training Feedback Loop.
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "integrations",
                                "lol-history", "src"))
import pytest

class TestIntelPredictionEvaluator:
    def _make(self):
        from lol_history.intel_prediction_evaluator import IntelPredictionEvaluator
        return IntelPredictionEvaluator()

    def test_record_and_evaluate(self):
        e = self._make()
        e.record_prediction("p1", "scout", True, 0.8)
        r = e.record_outcome("p1", True)
        assert r["correct"] is True
        assert r["accuracy"] == 1.0

    def test_incorrect_prediction(self):
        e = self._make()
        e.record_prediction("p1", "scout", True, 0.9)
        r = e.record_outcome("p1", False)
        assert r["correct"] is False

    def test_numeric_prediction(self):
        e = self._make()
        e.record_prediction("p1", "matchup", 0.65, 0.7)
        r = e.record_outcome("p1", 0.60)
        assert r["status"] == "ok"
        assert r["accuracy"] > 0.9

    def test_evaluate_module(self):
        e = self._make()
        for i in range(10):
            e.record_prediction(f"p{i}", "scout", True, 0.8)
            e.record_outcome(f"p{i}", i < 7)
        r = e.evaluate_module("scout")
        assert r["evaluated"] == 10

    def test_evaluate_all(self):
        e = self._make()
        e.record_prediction("p1", "scout", True, 0.8)
        e.record_outcome("p1", True)
        e.record_prediction("p2", "matchup", 0.6, 0.7)
        e.record_outcome("p2", 0.55)
        r = e.evaluate_all()
        assert r["modules_evaluated"] == 2

    def test_calibration_error(self):
        e = self._make()
        for i in range(20):
            e.record_prediction(f"p{i}", "scout", True, 0.9)
            e.record_outcome(f"p{i}", i < 10)
        r = e.get_calibration_error("scout")
        assert r["status"] == "ok"
        assert "ece" in r

    def test_outcome_not_found(self):
        e = self._make()
        r = e.record_outcome("nonexistent", True)
        assert r["status"] == "error"

    def test_stats(self):
        e = self._make()
        s = e.get_stats()
        assert "predictions_recorded" in s

class TestIntelFeatureVectorBuilder:
    def _make(self):
        from lol_history.intel_feature_vector_builder import IntelFeatureVectorBuilder
        return IntelFeatureVectorBuilder(vector_dim=10)

    def test_register_and_build(self):
        b = self._make()
        b.register_encoder("test", lambda d: [1.0, 2.0, 3.0], ["a", "b", "c"])
        r = b.build_vector({"x": 1})
        assert r["status"] == "ok"
        assert len(r["vector"]) == 10
        assert r["vector"][:3] == [1.0, 2.0, 3.0]

    def test_padding(self):
        b = self._make()
        b.register_encoder("small", lambda d: [1.0])
        r = b.build_vector({})
        assert len(r["vector"]) == 10

    def test_batch(self):
        b = self._make()
        b.register_encoder("test", lambda d: [d.get("v", 0)])
        r = b.build_batch([{"v": 1}, {"v": 2}, {"v": 3}])
        assert r["count"] == 3

    def test_schema(self):
        b = self._make()
        b.register_encoder("group1", lambda d: [0], ["feat1"])
        r = b.get_feature_schema()
        assert "group1" in r["schema"]

class TestIntelRewardSignalGenerator:
    def _make(self):
        from lol_history.intel_reward_signal_generator import IntelRewardSignalGenerator
        return IntelRewardSignalGenerator()

    def test_correct_reward(self):
        g = self._make()
        r = g.generate_reward("scout", True, True, 0.9)
        assert r["reward"] > 0
        assert r["correct"] is True

    def test_incorrect_penalty(self):
        g = self._make()
        r = g.generate_reward("scout", True, False, 0.8)
        assert r["reward"] < 0

    def test_module_weights(self):
        g = self._make()
        g.set_module_weight("scout", 2.0)
        r = g.generate_reward("scout", True, True, 0.5)
        assert r["reward"] == 1.0  # 1.0 * 0.5 * 2.0

    def test_aggregate_reward(self):
        g = self._make()
        g.generate_reward("scout", True, True, 0.8)
        g.generate_reward("scout", True, False, 0.6)
        r = g.get_aggregate_reward()
        assert "overall_avg" in r

class TestDraftTrainingDataGenerator:
    def _make(self):
        from lol_history.draft_training_data_generator import DraftTrainingDataGenerator
        return DraftTrainingDataGenerator()

    def test_ingest_and_generate(self):
        g = self._make()
        g.ingest_draft_result(["A", "B"], ["X"], ["C", "D"], ["Y"], True, "gold")
        r = g.generate_samples()
        assert r["count"] == 1
        assert r["samples"][0]["label"] is True

    def test_stratify(self):
        g = self._make()
        g.ingest_draft_result(["A"], [], ["B"], [], True, "gold")
        g.ingest_draft_result(["C"], [], ["D"], [], False, "diamond")
        r = g.stratify_by_elo("gold")
        assert r["total"] == 1
        assert r["win_rate"] == 1.0

class TestIntelAccuracyTracker:
    def _make(self):
        from lol_history.intel_accuracy_tracker import IntelAccuracyTracker
        return IntelAccuracyTracker()

    def test_record_and_trend(self):
        t = self._make()
        for i in range(20):
            t.record("scout", i < 15, 0.8)
        r = t.get_trend("scout")
        assert r["precision"] == 0.75

    def test_degradation_detection(self):
        t = self._make()
        for i in range(20):
            t.record("scout", True, 0.8)
        for i in range(20):
            t.record("scout", False, 0.8)
        r = t.detect_degradation()
        assert len(r["degraded_modules"]) >= 1

    def test_module_report(self):
        t = self._make()
        t.record("scout", True, 0.9)
        r = t.get_module_report("scout")
        assert r["precision"] == 1.0

class TestIntelDataVersionManager:
    def _make(self):
        from lol_history.intel_data_version_manager import IntelDataVersionManager
        return IntelDataVersionManager()

    def test_snapshot_and_get(self):
        m = self._make()
        r = m.create_snapshot({"key": "val"}, "test_v1")
        vid = r["version_id"]
        r2 = m.get_version(vid)
        assert r2["data"]["key"] == "val"

    def test_rollback(self):
        m = self._make()
        r1 = m.create_snapshot({"a": 1}, "v1")
        r2 = m.create_snapshot({"b": 2}, "v2")
        m.rollback(r1["version_id"])
        r = m.get_version()
        assert r["data"]["a"] == 1

    def test_diff(self):
        m = self._make()
        r1 = m.create_snapshot({"a": 1, "b": 2}, "v1")
        r2 = m.create_snapshot({"a": 1, "c": 3}, "v2")
        r = m.diff_versions(r1["version_id"], r2["version_id"])
        assert "b" in r["removed"]
        assert "c" in r["added"]

    def test_list_versions(self):
        m = self._make()
        m.create_snapshot({"x": 1})
        r = m.list_versions()
        assert len(r["versions"]) == 1

class TestIntelModelFinetunePipeline:
    def _make(self):
        from lol_history.intel_model_finetune_pipeline import IntelModelFinetunePipeline
        return IntelModelFinetunePipeline(learning_rate=0.1, max_gradient=0.5)

    def test_finetune_step(self):
        p = self._make()
        p.set_params({"w1": 0.5, "w2": 0.3})
        p.add_feedback("w1", 0.2, 0.8)
        r = p.run_finetune_step()
        assert r["params_updated"] >= 1
        params = p.get_params()
        assert params["params"]["w1"] != 0.5

    def test_gradient_clipping(self):
        p = self._make()
        p.set_params({"w1": 0.5})
        p.add_feedback("w1", 10.0, 0.5)  # huge gradient
        p.run_finetune_step()
        params = p.get_params()
        # Should be clipped to max_gradient * lr
        assert abs(params["params"]["w1"] - 0.5) <= 0.1 * 0.5 + 0.001

    def test_no_feedback(self):
        p = self._make()
        r = p.run_finetune_step()
        assert r["updates"] == 0

class TestIntelABTestFramework:
    def _make(self):
        from lol_history.intel_ab_test_framework import IntelABTestFramework
        return IntelABTestFramework()

    def test_create_experiment(self):
        f = self._make()
        r = f.create_experiment("test", ["A", "B"])
        assert r["status"] == "ok"

    def test_assign_variant(self):
        f = self._make()
        f.create_experiment("test", ["A", "B"])
        r = f.assign_variant("test")
        assert r["variant"] in ["A", "B"]

    def test_record_and_check(self):
        f = self._make()
        f.create_experiment("test", ["A", "B"])
        for _ in range(50):
            f.record_result("test", "A", True)
            f.record_result("test", "B", False)
        r = f.check_significance("test")
        assert r["significant"] is True
        assert r["leader"] == "A"

    def test_not_enough_samples(self):
        f = self._make()
        f.create_experiment("test", ["A", "B"])
        f.record_result("test", "A", True)
        r = f.check_significance("test")
        assert r["significant"] is False

class TestIntelTrainingLoopOrchestrator:
    def _make(self):
        from lol_history.intel_training_loop_orchestrator import IntelTrainingLoopOrchestrator
        return IntelTrainingLoopOrchestrator()

    def test_register_init_process(self):
        o = self._make()
        o.register("dummy", type("D", (), {"get_stats": lambda self: {"ok": True}})())
        o.initialize()
        r = o.process()
        assert r["status"] == "ok"
        assert r["cycle"] == 1

    def test_error_isolation(self):
        class Bad:
            def get_stats(self): raise RuntimeError("boom")
        o = self._make()
        o.register("bad", Bad())
        o.register("good", type("G", (), {"get_stats": lambda self: {}})())
        o.initialize()
        r = o.process()
        assert r["results"]["good"]["status"] == "ok"
        assert r["results"]["bad"]["status"] == "error"

    def test_shutdown(self):
        o = self._make()
        o.initialize()
        r = o.shutdown()
        assert r["state"] == "shutdown"

    def test_report(self):
        o = self._make()
        o.register("m", type("M", (), {"get_stats": lambda self: {}})())
        o.initialize()
        o.process()
        r = o.get_report()
        assert r["process_count"] == 1

    def test_evolution_callback(self):
        o = self._make()
        events = []
        o.evolution_callback = lambda e: events.append(e)
        o.initialize()
        assert len(events) == 1

class TestIntelQualityFeedbackLoop:
    def _make(self):
        from lol_history.intel_quality_feedback_loop import IntelQualityFeedbackLoop
        return IntelQualityFeedbackLoop()

    def test_ingest_and_adjust(self):
        l = self._make()
        l.ingest_evaluation("scout", 0.3, 0.8)  # overconfident
        r = l.get_adjustments()
        assert r["adjustments"]["scout"]["confidence_threshold_delta"] < 0

    def test_apply_adjustments(self):
        l = self._make()
        l.ingest_evaluation("scout", 0.8, 0.5)
        config = {"scout": {"confidence_threshold": 0.5, "weight": 1.0}}
        r = l.apply_adjustments(config)
        assert r["modules_adjusted"] >= 1

class TestOnlineIntelModelUpdater:
    def _make(self):
        from lol_history.online_intel_model_updater import OnlineIntelModelUpdater
        return OnlineIntelModelUpdater(max_drift=0.1, lr=0.05)

    def test_update_weight(self):
        u = self._make()
        u.set_initial_weights({"w1": 0.5})
        r = u.update_weight("w1", 1.0)
        assert r["new"] > 0.5

    def test_drift_constraint(self):
        u = self._make()
        u.set_initial_weights({"w1": 0.5})
        for _ in range(100):
            u.update_weight("w1", 1.0)
        w = u.get_weights()
        assert abs(w["weights"]["w1"] - 0.5) <= 0.1 + 0.001

class TestOpponentAdaptationModelTrainer:
    def _make(self):
        from lol_history.opponent_adaptation_model_trainer import OpponentAdaptationModelTrainer
        return OpponentAdaptationModelTrainer()

    def test_register_and_process(self):
        t = self._make()
        t.register("mod", type("M", (), {"get_stats": lambda self: {}})())
        t.initialize()
        r = t.process()
        assert r["status"] == "ok"

class TestIntelPipelineFaultHardener:
    def _make(self):
        from lol_history.intel_pipeline_fault_hardener import IntelPipelineFaultHardener
        return IntelPipelineFaultHardener()

    def test_fault_isolation(self):
        class Bad:
            def get_stats(self): raise RuntimeError("fault")
        h = self._make()
        h.register("bad", Bad())
        h.initialize()
        r = h.process()
        assert r["results"]["bad"]["status"] == "error"
        assert h.get_stats()["errors"]["bad"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

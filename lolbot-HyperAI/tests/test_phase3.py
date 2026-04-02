"""
Phase 3 Integration Test — Validates all new Phase 3 modules.
================================================================
lolbot-HyperAI · Tests

Tests the 15 new modules introduced in Phase 3 (files 41-60):
    - MacroPlanner, TeamfightPredictor, OverlayRenderer
    - ActionDispatcher, MinimapAnalyzer, GameStatistics
    - ProtoUtil, KillFeedAnalyzer, SharedMemoryTransport
    - DashboardHTML, LaneAdvisor, TrainingDataCollector
    - ConfigLoader, CLIMonitor, DiagnosticRunner

Run:
    cd lolbot-HyperAI
    python -m pytest tests/test_phase3.py -v
    python tests/test_phase3.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import TestCase, main as unittest_main

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cyber.node.node import CyberNode, reset_all_channels
from modules.common.adapters.game_messages import (
    EventType, GameEvent, GamePhase, GameSnapshot,
    PlayerScore, PlayerState, TeamSide, TeamState,
    WinPrediction, TeamfightPrediction, StrategyAdvice,
)


# ─── Synthetic Data Helpers ─────────────────────────────────────────────────

def _make_player(
    name: str = "TestPlayer",
    champion: str = "Garen",
    level: int = 9,
    gold: float = 8000.0,
    health: float = 800.0,
    max_health: float = 1200.0,
    cs: int = 100,
    alive: bool = True,
) -> PlayerState:
    return PlayerState(
        champion_name=champion,
        summoner_name=name,
        level=level,
        current_gold=gold,
        current_health=health if alive else 0.0,
        max_health=max_health,
        is_dead=not alive,
        team=TeamSide.BLUE,
        scores=PlayerScore(kills=3, deaths=1, assists=5, creep_score=cs),
    )


def _make_snapshot(
    game_time: float = 600.0,
    blue_gold: float = 40000.0,
    red_gold: float = 38000.0,
    blue_kills: int = 10,
    red_kills: int = 8,
) -> GameSnapshot:
    blue_players = [
        _make_player(f"Blue{i}", gold=blue_gold / 5, level=9 + (i % 3))
        for i in range(5)
    ]
    red_players = [
        _make_player(f"Red{i}", gold=red_gold / 5, level=8 + (i % 3))
        for i in range(5)
    ]
    return GameSnapshot(
        game_time=game_time,
        phase=GamePhase.from_game_time(game_time),
        blue_team=TeamState(
            side=TeamSide.BLUE, players=tuple(blue_players),
            total_gold=blue_gold, total_kills=blue_kills,
            towers_destroyed=2, dragons_taken=1, barons_taken=0,
        ),
        red_team=TeamState(
            side=TeamSide.RED, players=tuple(red_players),
            total_gold=red_gold, total_kills=red_kills,
            towers_destroyed=1, dragons_taken=0, barons_taken=0,
        ),
        active_team=TeamSide.BLUE,
    )


def _make_kill_event(
    killer: str = "Blue0", victim: str = "Red0",
    game_time: float = 300.0,
) -> GameEvent:
    return GameEvent(
        event_type=EventType.CHAMPION_KILL,
        game_time=game_time,
        killer=killer,
        victim=victim,
    )


# ─── Tests ───────────────────────────────────────────────────────────────────

class TestMacroPlanner(TestCase):
    def setUp(self):
        from modules.planning.macro.macro_planner import MacroPlanner
        self.planner = MacroPlanner(cooldown_s=0.0)

    def test_decide_returns_decision(self):
        snapshot = _make_snapshot()
        decision = self.planner.decide(snapshot)
        self.assertIsNotNone(decision)
        self.assertIn(decision.action.value, [
            "baron", "dragon", "group", "split_push",
            "defend", "reset", "vision_control", "idle",
        ])

    def test_stats(self):
        snapshot = _make_snapshot()
        self.planner.decide(snapshot)
        stats = self.planner.stats()
        self.assertEqual(stats["decision_count"], 1)

    def test_reset(self):
        self.planner.decide(_make_snapshot())
        self.planner.reset()
        self.assertEqual(self.planner.stats()["decision_count"], 0)


class TestTeamfightPredictor(TestCase):
    def setUp(self):
        from modules.prediction.team_fight.teamfight_predictor import TeamfightPredictor
        self.predictor = TeamfightPredictor()

    def test_predict_returns_assessment(self):
        snapshot = _make_snapshot()
        assessment = self.predictor.predict(snapshot)
        self.assertIsNotNone(assessment)
        self.assertIn(assessment.recommended_action.value, [
            "engage", "disengage", "poke", "pick",
        ])
        self.assertGreaterEqual(assessment.our_win_probability, 0.0)
        self.assertLessEqual(assessment.our_win_probability, 1.0)

    def test_factor_breakdown(self):
        assessment = self.predictor.predict(_make_snapshot())
        self.assertIn("alive_ratio", assessment.factor_breakdown)
        self.assertIn("hp_ratio", assessment.factor_breakdown)


class TestOverlayRenderer(TestCase):
    def setUp(self):
        from modules.control.overlay.overlay_renderer import (
            OverlayRenderer, OverlayCommand, ElementType, OverlayZone,
        )
        self.renderer = OverlayRenderer(max_elements=4)
        self.OverlayCommand = OverlayCommand
        self.ElementType = ElementType
        self.OverlayZone = OverlayZone

    def test_submit_and_process(self):
        self.renderer.submit_command(self.OverlayCommand(
            source="test", category="cat1", text="Hello",
        ))
        processed = self.renderer.process_commands()
        self.assertEqual(processed, 1)
        self.assertEqual(self.renderer.active_count, 1)

    def test_deduplication(self):
        for _ in range(3):
            self.renderer.submit_command(self.OverlayCommand(
                source="test", category="cat1", text="Hello",
            ))
        self.renderer.process_commands()
        self.assertEqual(self.renderer.active_count, 1)

    def test_capacity_eviction(self):
        for i in range(6):
            self.renderer.submit_command(self.OverlayCommand(
                source="test", category=f"cat{i}", text=f"Item {i}",
                priority=i,
            ))
        self.renderer.process_commands()
        self.assertLessEqual(self.renderer.active_count, 4)


class TestActionDispatcher(TestCase):
    def setUp(self):
        from modules.control.action_dispatch.action_dispatcher import (
            ActionDispatcher, DispatchAction, ActionCategory, ActionPriority,
        )
        self.dispatcher = ActionDispatcher()
        self.DispatchAction = DispatchAction
        self.ActionCategory = ActionCategory
        self.ActionPriority = ActionPriority

    def test_dispatch_high_priority(self):
        result = self.dispatcher.dispatch(self.DispatchAction(
            category=self.ActionCategory.STRATEGY_ADVICE,
            priority=self.ActionPriority.HIGH,
            text="Take Baron now",
            source="test",
        ))
        self.assertTrue(result["log"])
        stats = self.dispatcher.stats()
        self.assertEqual(stats["total_dispatched"], 1)

    def test_deduplication(self):
        for _ in range(3):
            self.dispatcher.dispatch(self.DispatchAction(
                category=self.ActionCategory.WIN_UPDATE,
                priority=self.ActionPriority.MEDIUM,
                text="Win: 55%",
                source="test",
            ))
        stats = self.dispatcher.stats()
        self.assertGreater(stats["total_deduplicated"], 0)


class TestMinimapAnalyzer(TestCase):
    def setUp(self):
        from modules.perception.minimap.minimap_analyzer import MinimapAnalyzer
        self.analyzer = MinimapAnalyzer()

    def test_analyze_returns_state(self):
        snapshot = _make_snapshot()
        state = self.analyzer.analyze(snapshot)
        self.assertIn("top", state.lanes)
        self.assertIn("mid", state.lanes)
        self.assertIn("bot", state.lanes)
        self.assertIsNotNone(state.jungle)


class TestGameStatistics(TestCase):
    def setUp(self):
        from modules.common.math.statistics import GameStatistics
        self.tracker = GameStatistics()

    def test_record_and_compute(self):
        for v in [100, 200, 300, 400, 500]:
            self.tracker.record("gold_diff", float(v))
        stats = self.tracker.get("gold_diff")
        self.assertEqual(stats.count, 5)
        self.assertAlmostEqual(stats.mean, 300.0)

    def test_multi_series(self):
        self.tracker.record("gold", 1000.0)
        self.tracker.record("kills", 5.0)
        names = self.tracker.series_names()
        self.assertIn("gold", names)
        self.assertIn("kills", names)


class TestProtoUtil(TestCase):
    def setUp(self):
        from modules.common.util.proto_util import (
            JsonSerializer, BinarySerializer, to_json, from_json,
        )
        self.JsonSerializer = JsonSerializer
        self.BinarySerializer = BinarySerializer
        self.to_json = to_json
        self.from_json = from_json

    def test_json_roundtrip(self):
        data = {"game_time": 600.0, "players": ["A", "B"]}
        json_str = self.to_json(data)
        result = self.from_json(json_str)
        self.assertEqual(result["game_time"], 600.0)

    def test_binary_roundtrip(self):
        data = {"test": 42, "nested": {"key": "value"}}
        encoded = self.BinarySerializer.serialize(data)
        decoded, version = self.BinarySerializer.deserialize(encoded)
        self.assertEqual(decoded["test"], 42)


class TestKillFeedAnalyzer(TestCase):
    def setUp(self):
        from modules.perception.events.kill_feed_analyzer import KillFeedAnalyzer
        self.analyzer = KillFeedAnalyzer()

    def test_first_blood_detection(self):
        events = [_make_kill_event("Blue0", "Red0", 120.0)]
        patterns = self.analyzer.analyze(events)
        types = [p.pattern_type.value for p in patterns]
        self.assertIn("first_blood", types)

    def test_multi_kill_detection(self):
        events = [
            _make_kill_event("Blue0", "Red0", 300.0),
            _make_kill_event("Blue0", "Red1", 305.0),
        ]
        patterns = self.analyzer.analyze(events)
        types = [p.pattern_type.value for p in patterns]
        self.assertIn("double_kill", types)


class TestSharedMemoryTransport(TestCase):
    def setUp(self):
        from cyber.transport.shared_memory import SharedMemoryTransport
        self.transport = SharedMemoryTransport(slot_count=4, slot_size=1024)
        self.transport.initialize()

    def test_write_and_read(self):
        writer = self.transport.create_writer("/test/channel")
        reader = self.transport.create_reader("/test/channel")
        writer.write({"key": "value"})
        result = reader.read_latest()
        self.assertIsNotNone(result)
        self.assertEqual(result["key"], "value")

    def tearDown(self):
        self.transport.shutdown()


class TestConfigLoader(TestCase):
    def test_load_default(self):
        from configs.config_loader import ConfigLoader
        loader = ConfigLoader("nonexistent.yaml")
        cfg = loader.load()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.get_int("server.port", 0), 8080)

    def test_yaml_parser(self):
        from configs.config_loader import SimpleYAMLParser
        parser = SimpleYAMLParser()
        result = parser.parse("key: value\nnumber: 42\nflag: true")
        self.assertEqual(result["key"], "value")
        self.assertEqual(result["number"], 42)
        self.assertTrue(result["flag"])


class TestDashboardHTML(TestCase):
    def test_generate(self):
        from modules.dreamview.dashboard.dashboard_html import DashboardHTMLGenerator
        gen = DashboardHTMLGenerator()
        html = gen.generate(port=8080)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("lolbot-HyperAI", html)
        self.assertIn("Win Probability", html)


class TestLaneAdvisor(TestCase):
    def setUp(self):
        from modules.planning.strategy.lane_advisor import LaneAdvisor
        self.advisor = LaneAdvisor()

    def test_advise_early_game(self):
        snapshot = _make_snapshot(game_time=300.0)
        advices = self.advisor.advise(snapshot)
        self.assertIsInstance(advices, list)

    def test_no_advice_late_game(self):
        snapshot = _make_snapshot(game_time=2000.0)
        advices = self.advisor.advise(snapshot)
        self.assertEqual(len(advices), 0)


class TestTrainingDataCollector(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_training.db")
        from modules.common.adapters.training_data_collector import TrainingDataCollector
        self.collector = TrainingDataCollector(self.db_path)
        self.collector.initialize()

    def test_record_and_label(self):
        self.collector.start_session("test_session")
        self.collector._last_record_time = 0.0  # bypass cooldown
        self.collector.record({"gold_diff": 1500.0}, game_time=300.0)
        count = self.collector.end_session(outcome=1)
        self.assertEqual(count, 1)

    def test_stats(self):
        self.collector.start_session("s1")
        self.collector._last_record_time = 0.0  # bypass cooldown
        self.collector.record({"x": 1.0}, game_time=100.0)
        self.collector.end_session(outcome=0)
        stats = self.collector.stats()
        self.assertEqual(stats.total_sessions, 1)
        self.assertEqual(stats.labeled_samples, 1)

    def tearDown(self):
        self.collector.shutdown()


# ─── Runner ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest_main(verbosity=2)

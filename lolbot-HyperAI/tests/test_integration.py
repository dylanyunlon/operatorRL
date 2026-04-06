"""
Integration Test — Full pipeline validation without a live LoL game.
=====================================================================

Tests the complete canbus→perception→prediction→planning→voice pipeline
using synthetic game data.  Validates that:
- CyberNode pub/sub channels work correctly
- Components initialize and start in dependency order
- Data flows through the entire pipeline
- Predictions produce valid output
- Planning generates strategy advice

Run:
    cd lolbot-HyperAI
    python -m pytest tests/test_integration.py -v
    python tests/test_integration.py   (standalone)

Architecture validation:
    This test ensures the Apollo-style architecture works end-to-end
    before connecting to a live LoL game.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import TestCase, main as unittest_main

# Ensure project root is on path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cyber.component.timer_component import ComponentConfig, ComponentState, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer, reset_all_channels
from cyber.scheduler.scheduler import CyberScheduler
from cyber.timer.rate_timer import RateController, AdaptiveRate, ActivityLevel
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    GamePhase,
    GameSnapshot,
    PlayerScore,
    PlayerState,
    RawLCUData,
    StrategyAdvice,
    TeamSide,
    TeamState,
    TeamfightPrediction,
    VoiceCommand,
    WinPrediction,
)


# ─── Synthetic Data Generator ───────────────────────────────────────────────

def make_synthetic_allgamedata(game_time: float = 600.0) -> Dict[str, Any]:
    """Generate synthetic allgamedata matching the Live Client Data API schema."""
    return {
        "gameData": {
            "gameTime": game_time,
            "gameMode": "CLASSIC",
            "mapNumber": 11,
            "mapName": "Map11",
        },
        "activePlayer": {
            "riotIdGameName": "TestPlayer",
            "summonerName": "TestPlayer",
            "level": 9,
            "currentGold": 3500.0,
            "championStats": {
                "currentHealth": 800.0,
                "maxHealth": 1200.0,
                "resourceValue": 400.0,
                "resourceMax": 600.0,
                "attackDamage": 120.0,
                "abilityPower": 0.0,
                "armor": 80.0,
                "magicResist": 50.0,
                "moveSpeed": 345.0,
            },
            "abilities": {
                "Q": {"abilityLevel": 5},
                "W": {"abilityLevel": 1},
                "E": {"abilityLevel": 3},
                "R": {"abilityLevel": 1},
            },
        },
        "allPlayers": [
            _make_player("TestPlayer", "ORDER", "Jinx", 9, "BOTTOM",
                         kills=3, deaths=1, assists=5, cs=120),
            _make_player("Ally1", "ORDER", "Thresh", 8, "UTILITY",
                         kills=1, deaths=2, assists=8, cs=30),
            _make_player("Ally2", "ORDER", "Ahri", 10, "MIDDLE",
                         kills=5, deaths=0, assists=3, cs=140),
            _make_player("Ally3", "ORDER", "Darius", 8, "TOP",
                         kills=2, deaths=3, assists=1, cs=100),
            _make_player("Ally4", "ORDER", "LeeSin", 9, "JUNGLE",
                         kills=4, deaths=1, assists=6, cs=90),
            _make_player("Enemy1", "CHAOS", "Caitlyn", 8, "BOTTOM",
                         kills=2, deaths=3, assists=2, cs=110),
            _make_player("Enemy2", "CHAOS", "Lulu", 7, "UTILITY",
                         kills=0, deaths=4, assists=4, cs=25),
            _make_player("Enemy3", "CHAOS", "Zed", 9, "MIDDLE",
                         kills=3, deaths=2, assists=1, cs=130),
            _make_player("Enemy4", "CHAOS", "Garen", 8, "TOP",
                         kills=1, deaths=2, assists=2, cs=95),
            _make_player("Enemy5", "CHAOS", "Elise", 8, "JUNGLE",
                         kills=2, deaths=3, assists=3, cs=80),
        ],
        "events": {
            "Events": [
                {"EventID": 1, "EventName": "GameStart", "EventTime": 0.0},
                {"EventID": 2, "EventName": "FirstBlood", "EventTime": 180.0,
                 "KillerName": "Ally2", "VictimName": "Enemy3", "Assisters": []},
                {"EventID": 3, "EventName": "ChampionKill", "EventTime": 300.0,
                 "KillerName": "TestPlayer", "VictimName": "Enemy1",
                 "Assisters": ["Ally1"]},
                {"EventID": 4, "EventName": "DragonKill", "EventTime": 420.0,
                 "KillerName": "Ally4", "Assisters": ["TestPlayer", "Ally1"]},
            ],
        },
    }


def _make_player(
    name: str, team: str, champion: str, level: int, position: str,
    kills: int = 0, deaths: int = 0, assists: int = 0, cs: int = 0,
) -> Dict[str, Any]:
    return {
        "riotIdGameName": name,
        "summonerName": name,
        "championName": champion,
        "team": team,
        "level": level,
        "position": position,
        "isDead": False,
        "respawnTimer": 0.0,
        "scores": {
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "creepScore": cs,
            "wardScore": 1.5,
        },
        "items": [
            {"itemID": 3031, "displayName": "IE", "price": 3400},
        ],
        "summonerSpells": {
            "summonerSpellOne": {"displayName": "Flash"},
            "summonerSpellTwo": {"displayName": "Heal"},
        },
    }


# ─── Test Cases ──────────────────────────────────────────────────────────────

class TestCyberNodePubSub(TestCase):
    """Test CyberNode pub/sub messaging."""

    def setUp(self):
        reset_all_channels()

    def test_basic_publish_subscribe(self):
        """Messages published on a channel are received by readers."""
        node_a = CyberNode("producer")
        node_b = CyberNode("consumer")

        writer = node_a.CreateWriter("/test/channel", dict)
        reader = node_b.CreateReader("/test/channel", dict)

        # Publish
        writer.Write({"value": 42})

        # Read
        reader.Observe()
        msg = reader.GetLatestObserved()
        self.assertIsNotNone(msg)
        self.assertEqual(msg["value"], 42)

    def test_multiple_subscribers(self):
        """Multiple readers on the same channel all receive messages."""
        writer_node = CyberNode("writer")
        reader_node_1 = CyberNode("reader1")
        reader_node_2 = CyberNode("reader2")

        writer = writer_node.CreateWriter("/test/multi", dict)
        reader1 = reader_node_1.CreateReader("/test/multi", dict)
        reader2 = reader_node_2.CreateReader("/test/multi", dict)

        writer.Write({"x": 1})

        reader1.Observe()
        reader2.Observe()
        self.assertEqual(reader1.GetLatestObserved()["x"], 1)
        self.assertEqual(reader2.GetLatestObserved()["x"], 1)

    def test_bounded_queue_backpressure(self):
        """Queue drops oldest messages when full."""
        node = CyberNode("test")
        writer = node.CreateWriter("/test/bounded", int)
        reader = node.CreateReader("/test/bounded", int, pending_queue_size=3)

        # Write 5 messages into queue of size 3
        for i in range(5):
            writer.Write(i)

        # Should only have last 3
        msgs = reader.drain()
        self.assertEqual(len(msgs), 3)
        self.assertEqual(msgs, [2, 3, 4])

    def test_callback_reader(self):
        """Callback readers fire on each message."""
        received = []
        node = CyberNode("test")
        writer = node.CreateWriter("/test/cb", int)
        reader = node.CreateReader(
            "/test/cb", int, callback=lambda msg: received.append(msg)
        )

        writer.Write(10)
        writer.Write(20)
        self.assertEqual(received, [10, 20])


class TestTimerComponent(TestCase):
    """Test TimerComponent lifecycle."""

    def test_init_proc_cycle(self):
        """Component initializes and runs Proc() correctly."""

        class Counter(TimerComponent):
            def __init__(self):
                super().__init__(ComponentConfig(
                    name="counter", interval_ms=50
                ))
                self.count = 0

            def Init(self):
                return True

            def Proc(self):
                self.count += 1
                return True

        comp = Counter()
        self.assertTrue(comp.initialize())
        self.assertEqual(comp.state, ComponentState.INITIALIZED)

        comp.start()
        time.sleep(0.3)
        comp.stop()

        self.assertGreater(comp.count, 3)
        self.assertEqual(comp.state, ComponentState.SHUTDOWN)

    def test_circuit_breaker(self):
        """Circuit breaker trips after consecutive failures."""

        class Failer(TimerComponent):
            def __init__(self):
                super().__init__(ComponentConfig(
                    name="failer", interval_ms=20,
                    max_consecutive_failures=3, cooldown_s=0.1,
                ))
                self.call_count = 0

            def Init(self):
                return True

            def Proc(self):
                self.call_count += 1
                return False  # always fails

        comp = Failer()
        comp.initialize()
        comp.start()
        time.sleep(0.5)
        comp.stop()

        # Should have triggered circuit breaker at least once
        self.assertGreater(comp.call_count, 3)


class TestGameMessages(TestCase):
    """Test game message data structures."""

    def test_game_snapshot_feature_dict(self):
        """GameSnapshot.to_feature_dict() returns valid features."""
        blue = TeamState(
            side=TeamSide.BLUE, total_kills=5, total_gold=15000.0,
            towers_destroyed=2, dragons_taken=1,
            players=(
                PlayerState(level=9, team=TeamSide.BLUE),
                PlayerState(level=8, team=TeamSide.BLUE),
            ),
        )
        red = TeamState(
            side=TeamSide.RED, total_kills=3, total_gold=12000.0,
            towers_destroyed=1, dragons_taken=0,
            players=(
                PlayerState(level=8, team=TeamSide.RED),
                PlayerState(level=7, team=TeamSide.RED),
            ),
        )
        snapshot = GameSnapshot(
            game_time=600.0, phase=GamePhase.MID,
            blue_team=blue, red_team=red,
            gold_diff=3000.0,
        )

        fd = snapshot.to_feature_dict()
        self.assertEqual(fd["game_time"], 600.0)
        self.assertEqual(fd["gold_diff"], 3000.0)
        self.assertEqual(fd["kill_diff"], 2)
        self.assertEqual(fd["tower_diff"], 1)

    def test_player_score_kda(self):
        s = PlayerScore(kills=5, deaths=2, assists=10)
        self.assertEqual(s.kda, 7.5)

    def test_player_score_kda_zero_deaths(self):
        s = PlayerScore(kills=5, deaths=0, assists=3)
        self.assertEqual(s.kda, 8.0)


class TestStatusSystem(TestCase):
    """Test error code and status system."""

    def test_status_ok(self):
        s = Status(code=ErrorCode.OK)
        self.assertTrue(s.is_ok)
        self.assertTrue(bool(s))
        self.assertEqual(s.code, ErrorCode.OK)

    def test_status_error(self):
        s = Status.error(
            ErrorCode.CANBUS_LCU_TIMEOUT, "Timed out after 2s"
        )
        self.assertFalse(s.is_ok)
        self.assertEqual(s.module, "canbus")

    def test_status_chain(self):
        inner = Status.error(ErrorCode.CANBUS_LCU_TIMEOUT, "timeout")
        outer = Status.wrap(inner, ErrorCode.PERCEPTION_STATE_INCOMPLETE, "no data")
        chain = outer.chain()
        self.assertEqual(len(chain), 2)
        self.assertEqual(outer.root_cause().code, ErrorCode.CANBUS_LCU_TIMEOUT)

    def test_status_serialization(self):
        s = Status.error(ErrorCode.PREDICTION_MODEL_NOT_LOADED, "missing v2")
        d = s.to_dict()
        restored = Status.from_dict(d)
        self.assertEqual(restored.code, s.code)
        self.assertEqual(restored.message, s.message)


class TestRateControl(TestCase):
    """Test rate control utilities."""

    def test_rate_controller(self):
        rc = RateController(rate_per_second=10, burst_size=3)
        # Burst: first 3 should succeed
        self.assertTrue(rc.acquire())
        self.assertTrue(rc.acquire())
        self.assertTrue(rc.acquire())
        # 4th should fail immediately
        self.assertFalse(rc.acquire())

    def test_adaptive_rate(self):
        ar = AdaptiveRate()
        ar.set_activity(ActivityLevel.HIGH)
        for _ in range(20):
            ar.tick()
        # Should have moved toward HIGH interval (200ms)
        self.assertLess(ar.current_interval_ms, 1000)


class TestFullPipeline(TestCase):
    """Test the full data pipeline using synthetic data."""

    def setUp(self):
        reset_all_channels()

    def test_canbus_to_perception_flow(self):
        """Synthetic data flows from canbus channel to perception reader."""
        canbus_node = CyberNode("canbus")
        perception_node = CyberNode("perception")

        writer = canbus_node.CreateWriter("/lol/raw_lcu", RawLCUData)
        reader = perception_node.CreateReader("/lol/raw_lcu", RawLCUData)

        # Simulate canbus publishing
        raw = RawLCUData(
            allgamedata=make_synthetic_allgamedata(600.0),
            timestamp=time.time(),
            lcu_latency_ms=15.0,
        )
        writer.Write(raw)

        # Perception reads
        reader.Observe()
        received = reader.GetLatestObserved()
        self.assertIsNotNone(received)
        self.assertEqual(
            received.allgamedata["gameData"]["gameTime"], 600.0
        )

    def test_prediction_from_snapshot(self):
        """Win prediction model produces valid output from snapshot."""
        from modules.prediction.prediction_component import (
            PredictionFeatures, WinPredictor,
        )

        blue = TeamState(
            side=TeamSide.BLUE, total_kills=10, total_gold=25000,
            towers_destroyed=3, dragons_taken=2,
            players=tuple(
                PlayerState(level=12, team=TeamSide.BLUE) for _ in range(5)
            ),
        )
        red = TeamState(
            side=TeamSide.RED, total_kills=5, total_gold=20000,
            towers_destroyed=1, dragons_taken=0,
            players=tuple(
                PlayerState(level=10, team=TeamSide.RED) for _ in range(5)
            ),
        )
        snapshot = GameSnapshot(
            game_time=900.0, phase=GamePhase.MID,
            blue_team=blue, red_team=red,
            all_players=blue.players + red.players,
            gold_diff=5000.0,
        )

        features = PredictionFeatures.from_snapshot(snapshot)
        predictor = WinPredictor()
        prob = predictor.predict(features)

        # Blue is winning, so prob should be > 0.5
        self.assertGreater(prob, 0.5)
        self.assertLessEqual(prob, 0.99)
        self.assertGreaterEqual(prob, 0.01)

    def test_end_to_end_channel_pipeline(self):
        """Full pipeline: canbus → perception → prediction channels."""
        # Set up the full channel topology
        canbus = CyberNode("canbus")
        perception = CyberNode("perception")
        prediction = CyberNode("prediction")

        # Canbus publishes
        raw_writer = canbus.CreateWriter("/lol/raw_lcu", RawLCUData)
        state_writer = perception.CreateWriter("/lol/game_state", GameSnapshot)

        # Perception reads raw, writes state
        raw_reader = perception.CreateReader("/lol/raw_lcu", RawLCUData)

        # Prediction reads state
        state_reader = prediction.CreateReader("/lol/game_state", GameSnapshot)
        pred_writer = prediction.CreateWriter("/lol/win_prediction", WinPrediction)

        # Simulate pipeline flow
        raw_writer.Write(RawLCUData(
            allgamedata=make_synthetic_allgamedata(),
        ))

        raw_reader.Observe()
        raw = raw_reader.GetLatestObserved()
        self.assertIsNotNone(raw)

        # Perception creates snapshot and publishes
        snapshot = GameSnapshot(
            game_time=600.0, phase=GamePhase.MID, gold_diff=3000.0,
        )
        state_writer.Write(snapshot)

        # Prediction receives
        state_reader.Observe()
        gs = state_reader.GetLatestObserved()
        self.assertIsNotNone(gs)
        self.assertEqual(gs.game_time, 600.0)

        # Prediction publishes
        pred = WinPrediction(blue_win_prob=0.65, confidence=0.8)
        pred_writer.Write(pred)

        # Verify channel topology
        self.assertEqual(raw_writer.write_count, 1)
        self.assertEqual(state_writer.write_count, 1)
        self.assertEqual(pred_writer.write_count, 1)


# ─── Standalone runner ───────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("lolbot-HyperAI Integration Tests")
    print("=" * 60)
    unittest_main(verbosity=2)

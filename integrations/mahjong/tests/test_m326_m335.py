"""
TDD Tests for M326-M335: Mortal + Akagi Mahjong AI Deep Integration.

100 tests (10 per module), designed for ~50% initial failure rate.
Tests written BEFORE implementation per TDD methodology.

Reference projects (拿来主义):
  - Mortal: engine.py react_batch / reward_calculator.py calc_delta_pt
  - Akagi: mitm_abc.py ClientWebSocketABC / majsoul.py ClientWebSocket
  - operatorRL ABCs: GameBridgeABC, EvolutionLoopABC, StrategyAdvisorABC
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
_MAHJONG_SRC = os.path.join(_ROOT, "mahjong", "src", "mahjong_agent")
_MODULES_DIR = os.path.join(_ROOT, "..", "modules")
_EXTENSIONS_DIR = os.path.join(_ROOT, "..", "extensions")


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
def mortal_bridge_mod():
    return _load("mortal_bridge", os.path.join(_MAHJONG_SRC, "mortal_bridge.py"))


@pytest.fixture(scope="module")
def akagi_mitm_mod():
    return _load("akagi_mitm_adapter", os.path.join(_MAHJONG_SRC, "akagi_mitm_adapter.py"))


@pytest.fixture(scope="module")
def tile_encoder_mod():
    return _load("tile_encoder", os.path.join(_MAHJONG_SRC, "tile_encoder.py"))


@pytest.fixture(scope="module")
def shanten_mod():
    return _load("shanten_calculator", os.path.join(_MAHJONG_SRC, "shanten_calculator.py"))


@pytest.fixture(scope="module")
def discard_mod():
    return _load("discard_advisor", os.path.join(_MAHJONG_SRC, "discard_advisor.py"))


@pytest.fixture(scope="module")
def opponent_mod():
    return _load("opponent_model", os.path.join(_MAHJONG_SRC, "opponent_model.py"))


@pytest.fixture(scope="module")
def score_mod():
    return _load("score_calculator", os.path.join(_MAHJONG_SRC, "score_calculator.py"))


@pytest.fixture(scope="module")
def replay_mod():
    return _load("replay_converter", os.path.join(_MAHJONG_SRC, "replay_converter.py"))


@pytest.fixture(scope="module")
def evo_loop_mod():
    return _load("mahjong_evolution_loop", os.path.join(_MAHJONG_SRC, "mahjong_evolution_loop.py"))


@pytest.fixture(scope="module")
def strategy_mod():
    return _load("mahjong_strategy_advisor", os.path.join(_MAHJONG_SRC, "mahjong_strategy_advisor.py"))


# =====================================================================
# M326 — MortalBridge (10 tests)
# =====================================================================
class TestMortalBridge:
    def test_class_exists(self, mortal_bridge_mod):
        assert hasattr(mortal_bridge_mod, "MortalBridge")

    def test_has_evolution_key(self, mortal_bridge_mod):
        assert hasattr(mortal_bridge_mod, "_EVOLUTION_KEY")
        assert "mortal_bridge" in mortal_bridge_mod._EVOLUTION_KEY

    def test_instantiation_default(self, mortal_bridge_mod):
        mb = mortal_bridge_mod.MortalBridge()
        assert mb is not None

    def test_connect_disconnect(self, mortal_bridge_mod):
        mb = mortal_bridge_mod.MortalBridge()
        mb.connect()
        assert mb.connected is True
        mb.disconnect()
        assert mb.connected is False

    def test_game_name_property(self, mortal_bridge_mod):
        mb = mortal_bridge_mod.MortalBridge()
        assert mb.game_name == "mahjong"

    def test_get_game_state_returns_dict(self, mortal_bridge_mod):
        mb = mortal_bridge_mod.MortalBridge()
        mb.connect()
        state = mb.get_game_state()
        assert isinstance(state, dict)
        mb.disconnect()

    def test_send_action_returns_bool(self, mortal_bridge_mod):
        mb = mortal_bridge_mod.MortalBridge()
        mb.connect()
        result = mb.send_action({"type": "dahai", "pai": "1m"})
        assert isinstance(result, bool)
        mb.disconnect()

    def test_react_batch_stub(self, mortal_bridge_mod):
        """Mirrors Mortal engine.react_batch interface."""
        mb = mortal_bridge_mod.MortalBridge()
        obs = [[0.0] * 34]
        masks = [[True] * 34]
        actions, q_values = mb.react(obs[0], masks[0])
        assert isinstance(actions, int)
        assert isinstance(q_values, list)

    def test_fire_evolution(self, mortal_bridge_mod):
        mb = mortal_bridge_mod.MortalBridge()
        fired = []
        mb.evolution_callback = lambda key, data: fired.append((key, data))
        mb._fire_evolution({"test": 1})
        assert len(fired) == 1
        assert fired[0][0] == mortal_bridge_mod._EVOLUTION_KEY

    def test_mjai_protocol_format(self, mortal_bridge_mod):
        """Output should be mjai-compatible dict."""
        mb = mortal_bridge_mod.MortalBridge()
        msg = mb.format_mjai_action("dahai", actor=0, pai="5s", tsumogiri=False)
        assert msg["type"] == "dahai"
        assert msg["actor"] == 0
        assert msg["pai"] == "5s"


# =====================================================================
# M327 — AkagiMitmAdapter (10 tests)
# =====================================================================
class TestAkagiMitmAdapter:
    def test_class_exists(self, akagi_mitm_mod):
        assert hasattr(akagi_mitm_mod, "AkagiMitmAdapter")

    def test_has_evolution_key(self, akagi_mitm_mod):
        assert "akagi_mitm" in akagi_mitm_mod._EVOLUTION_KEY

    def test_instantiation(self, akagi_mitm_mod):
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        assert adapter is not None

    def test_parse_raw_message_empty(self, akagi_mitm_mod):
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        result = adapter.parse_raw_message(b"")
        assert result is None

    def test_parse_raw_message_json(self, akagi_mitm_mod):
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        raw = json.dumps({"id": 1, "type": "ActionDealTile", "data": {"tile": "1m"}}).encode()
        result = adapter.parse_raw_message(raw)
        assert result is not None
        assert "type" in result

    def test_to_mjai_format(self, akagi_mitm_mod):
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        liqi_msg = {"type": "ActionDealTile", "data": {"tile": "1m"}}
        mjai = adapter.to_mjai(liqi_msg)
        assert isinstance(mjai, dict)
        assert "type" in mjai

    def test_flow_tracking_start_end(self, akagi_mitm_mod):
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        adapter.on_flow_start("flow_001")
        assert "flow_001" in adapter.active_flows
        adapter.on_flow_end("flow_001")
        assert "flow_001" not in adapter.active_flows

    def test_message_queue(self, akagi_mitm_mod):
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        adapter.enqueue_mjai({"type": "tsumo", "actor": 0, "pai": "3p"})
        msg = adapter.dequeue_mjai()
        assert msg["type"] == "tsumo"

    def test_fire_evolution(self, akagi_mitm_mod):
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        fired = []
        adapter.evolution_callback = lambda k, d: fired.append(k)
        adapter._fire_evolution({"x": 1})
        assert len(fired) == 1

    def test_bridge_lock_safety(self, akagi_mitm_mod):
        """Mirrors Akagi majsoul.py bridge_lock pattern."""
        adapter = akagi_mitm_mod.AkagiMitmAdapter()
        assert hasattr(adapter, "bridge_lock")


# =====================================================================
# M328 — TileEncoder (10 tests)
# =====================================================================
class TestTileEncoder:
    def test_class_exists(self, tile_encoder_mod):
        assert hasattr(tile_encoder_mod, "TileEncoder")

    def test_has_evolution_key(self, tile_encoder_mod):
        assert "tile_encoder" in tile_encoder_mod._EVOLUTION_KEY

    def test_encode_single_tile(self, tile_encoder_mod):
        enc = tile_encoder_mod.TileEncoder()
        vec = enc.encode_tile("1m")
        assert isinstance(vec, list)
        assert len(vec) == 34  # 34 tile types in riichi mahjong

    def test_encode_hand(self, tile_encoder_mod):
        enc = tile_encoder_mod.TileEncoder()
        hand = ["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "1z", "1z", "1z", "2z"]
        vec = enc.encode_hand(hand)
        assert isinstance(vec, list)
        assert len(vec) == 34

    def test_encode_hand_empty(self, tile_encoder_mod):
        enc = tile_encoder_mod.TileEncoder()
        vec = enc.encode_hand([])
        assert sum(vec) == 0

    def test_tile_id_mapping(self, tile_encoder_mod):
        enc = tile_encoder_mod.TileEncoder()
        assert enc.tile_to_id("1m") == 0
        assert enc.tile_to_id("9m") == 8
        assert enc.tile_to_id("1p") == 9
        assert enc.tile_to_id("1s") == 18
        assert enc.tile_to_id("1z") == 27

    def test_id_to_tile_roundtrip(self, tile_encoder_mod):
        enc = tile_encoder_mod.TileEncoder()
        for tile in ["1m", "5p", "9s", "7z"]:
            tid = enc.tile_to_id(tile)
            assert enc.id_to_tile(tid) == tile

    def test_encode_136_format(self, tile_encoder_mod):
        """136-tile encoding: 4 copies × 34 types."""
        enc = tile_encoder_mod.TileEncoder()
        tile_136 = enc.encode_tile_136(0)  # first copy of 1m
        assert tile_136 == 0
        tile_136 = enc.encode_tile_136(4)  # first copy of 2m
        assert tile_136 == 1

    def test_decode_136_to_34(self, tile_encoder_mod):
        enc = tile_encoder_mod.TileEncoder()
        assert enc.tile136_to_tile34(0) == 0
        assert enc.tile136_to_tile34(3) == 0
        assert enc.tile136_to_tile34(4) == 1
        assert enc.tile136_to_tile34(135) == 33

    def test_fire_evolution(self, tile_encoder_mod):
        enc = tile_encoder_mod.TileEncoder()
        fired = []
        enc.evolution_callback = lambda k, d: fired.append(k)
        enc._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M329 — ShantenCalculator (10 tests)
# =====================================================================
class TestShantenCalculator:
    def test_class_exists(self, shanten_mod):
        assert hasattr(shanten_mod, "ShantenCalculator")

    def test_has_evolution_key(self, shanten_mod):
        assert "shanten" in shanten_mod._EVOLUTION_KEY

    def test_tenpai_hand(self, shanten_mod):
        """A hand needing 1 tile to win → shanten = 0 (tenpai)."""
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [0] * 34
        # 1m2m3m 4p5p6p 7s8s9s 1z1z1z — needs 2z for pair
        for i in [0, 1, 2, 12, 13, 14, 24, 25, 26, 27, 27, 27]:
            hand_34[i] += 1
        hand_34[28] = 1  # 13 tiles, tenpai for 2z
        shanten = calc.calculate(hand_34)
        assert shanten == 0

    def test_complete_hand(self, shanten_mod):
        """A complete 14-tile hand → shanten = -1."""
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [0] * 34
        # 1m2m3m 4p5p6p 7s8s9s 1z1z1z 2z2z
        for i in [0, 1, 2, 12, 13, 14, 24, 25, 26]:
            hand_34[i] = 1
        hand_34[27] = 3
        hand_34[28] = 2
        shanten = calc.calculate(hand_34)
        assert shanten == -1

    def test_iishanten(self, shanten_mod):
        """A hand needing 2 tiles → shanten = 1."""
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [0] * 34
        # scattered hand
        hand_34[0] = 1; hand_34[2] = 1; hand_34[4] = 1
        hand_34[9] = 1; hand_34[11] = 1; hand_34[13] = 1
        hand_34[18] = 1; hand_34[20] = 1; hand_34[22] = 1
        hand_34[27] = 2; hand_34[28] = 1; hand_34[29] = 1
        hand_34[30] = 1
        shanten = calc.calculate(hand_34)
        assert shanten >= 1

    def test_effective_tiles(self, shanten_mod):
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [0] * 34
        # Near-tenpai hand
        hand_34[0] = 1; hand_34[1] = 1; hand_34[2] = 1
        hand_34[9] = 1; hand_34[10] = 1; hand_34[11] = 1
        hand_34[18] = 1; hand_34[19] = 1; hand_34[20] = 1
        hand_34[27] = 3; hand_34[28] = 1
        effective = calc.get_effective_tiles(hand_34)
        assert isinstance(effective, list)
        assert len(effective) > 0

    def test_empty_hand(self, shanten_mod):
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [0] * 34
        shanten = calc.calculate(hand_34)
        assert shanten >= 6  # very far from complete

    def test_seven_pairs_shanten(self, shanten_mod):
        """Seven pairs (chiitoitsu) path."""
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [0] * 34
        # 6 pairs + 1 single = shanten 0 for chiitoitsu
        for i in range(6):
            hand_34[i] = 2
        hand_34[6] = 1
        shanten = calc.calculate_seven_pairs(hand_34)
        assert shanten == 0

    def test_kokushi_shanten(self, shanten_mod):
        """Kokushi musou (thirteen orphans) path."""
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [0] * 34
        terminals = [0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33]
        for t in terminals:
            hand_34[t] = 1
        shanten = calc.calculate_kokushi(hand_34)
        assert shanten == 0  # has all 13 unique terminals

    def test_fire_evolution(self, shanten_mod):
        calc = shanten_mod.ShantenCalculator()
        fired = []
        calc.evolution_callback = lambda k, d: fired.append(k)
        calc._fire_evolution({})
        assert len(fired) == 1

    def test_calculate_returns_int(self, shanten_mod):
        calc = shanten_mod.ShantenCalculator()
        hand_34 = [1] * 13 + [0] * 21
        result = calc.calculate(hand_34)
        assert isinstance(result, int)


# =====================================================================
# M330 — DiscardAdvisor (10 tests)
# =====================================================================
class TestDiscardAdvisor:
    def test_class_exists(self, discard_mod):
        assert hasattr(discard_mod, "DiscardAdvisor")

    def test_has_evolution_key(self, discard_mod):
        assert "discard_advisor" in discard_mod._EVOLUTION_KEY

    def test_advise_returns_dict(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        hand = ["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "1z", "1z", "1z", "3z"]
        result = advisor.advise_discard(hand)
        assert isinstance(result, dict)
        assert "tile" in result
        assert "reason" in result

    def test_danger_evaluation(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        # Tile danger against a riichi player
        danger = advisor.evaluate_danger("1z", discards=["2z", "3z"], riichi=True)
        assert isinstance(danger, float)
        assert 0.0 <= danger <= 1.0

    def test_safe_tile_identification(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        # Tiles already discarded by opponent are safer
        danger_discarded = advisor.evaluate_danger("5m", discards=["5m", "5m", "5m"], riichi=False)
        danger_unseen = advisor.evaluate_danger("5m", discards=[], riichi=False)
        assert danger_discarded <= danger_unseen

    def test_efficiency_score(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        hand_34 = [0] * 34
        hand_34[0] = 1; hand_34[1] = 1; hand_34[2] = 1
        hand_34[9] = 1; hand_34[10] = 1; hand_34[11] = 1
        hand_34[18] = 1; hand_34[19] = 1; hand_34[20] = 1
        hand_34[27] = 3; hand_34[30] = 1
        score = advisor.tile_efficiency(hand_34, discard_idx=30)
        assert isinstance(score, float)

    def test_advise_empty_hand(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        result = advisor.advise_discard([])
        assert result["tile"] is None

    def test_multiple_candidates(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        hand = ["1m", "9m", "1p", "9p", "1s", "9s", "1z", "2z", "3z", "4z", "5z", "6z", "7z"]
        candidates = advisor.get_discard_candidates(hand, top_k=3)
        assert isinstance(candidates, list)
        assert len(candidates) <= 3

    def test_fire_evolution(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        fired = []
        advisor.evolution_callback = lambda k, d: fired.append(k)
        advisor._fire_evolution({})
        assert len(fired) == 1

    def test_strategy_mode(self, discard_mod):
        advisor = discard_mod.DiscardAdvisor()
        # attack vs defense mode
        advisor.set_strategy("attack")
        assert advisor.strategy == "attack"
        advisor.set_strategy("defense")
        assert advisor.strategy == "defense"


# =====================================================================
# M331 — OpponentModel (10 tests)
# =====================================================================
class TestOpponentModel:
    def test_class_exists(self, opponent_mod):
        assert hasattr(opponent_mod, "OpponentModel")

    def test_has_evolution_key(self, opponent_mod):
        assert "opponent_model" in opponent_mod._EVOLUTION_KEY

    def test_instantiation(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        assert model is not None

    def test_record_discard(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        model.record_discard(player=1, tile="3m", turn=5)
        assert len(model.get_discards(player=1)) == 1

    def test_infer_meld(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        model.record_meld(player=2, meld_type="chi", tiles=["4s", "5s", "6s"])
        melds = model.get_melds(player=2)
        assert len(melds) == 1
        assert melds[0]["type"] == "chi"

    def test_tenpai_prediction(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        # After many discards, predict tenpai
        for i, t in enumerate(["1z", "2z", "3z", "4z", "5z", "6z", "7z", "1m", "9m", "1p"]):
            model.record_discard(player=1, tile=t, turn=i)
        prob = model.predict_tenpai(player=1)
        assert isinstance(prob, float)
        assert 0.0 <= prob <= 1.0

    def test_waiting_tiles_prediction(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        for i, t in enumerate(["1z", "2z", "3z", "4z", "5z", "6z", "7z"]):
            model.record_discard(player=1, tile=t, turn=i)
        waits = model.predict_waiting_tiles(player=1)
        assert isinstance(waits, list)

    def test_threat_level(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        level = model.assess_threat(player=1)
        assert isinstance(level, str)
        assert level in ("unknown", "low", "medium", "high", "critical")

    def test_reset(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        model.record_discard(player=1, tile="1m", turn=0)
        model.reset()
        assert len(model.get_discards(player=1)) == 0

    def test_fire_evolution(self, opponent_mod):
        model = opponent_mod.OpponentModel(num_players=4)
        fired = []
        model.evolution_callback = lambda k, d: fired.append(k)
        model._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M332 — ScoreCalculator (10 tests)
# =====================================================================
class TestScoreCalculator:
    def test_class_exists(self, score_mod):
        assert hasattr(score_mod, "ScoreCalculator")

    def test_has_evolution_key(self, score_mod):
        assert "score_calculator" in score_mod._EVOLUTION_KEY

    def test_basic_han_fu(self, score_mod):
        calc = score_mod.ScoreCalculator()
        # 1 han 30 fu = 1000 points (non-dealer)
        points = calc.compute_points(han=1, fu=30, is_dealer=False)
        assert points == 1000

    def test_dealer_bonus(self, score_mod):
        calc = score_mod.ScoreCalculator()
        points_nondealer = calc.compute_points(han=3, fu=30, is_dealer=False)
        points_dealer = calc.compute_points(han=3, fu=30, is_dealer=True)
        assert points_dealer > points_nondealer

    def test_mangan(self, score_mod):
        calc = score_mod.ScoreCalculator()
        # 5 han = mangan = 8000 (non-dealer)
        points = calc.compute_points(han=5, fu=30, is_dealer=False)
        assert points == 8000

    def test_haneman(self, score_mod):
        calc = score_mod.ScoreCalculator()
        # 6-7 han = haneman = 12000 (non-dealer)
        points = calc.compute_points(han=6, fu=30, is_dealer=False)
        assert points == 12000

    def test_baiman(self, score_mod):
        calc = score_mod.ScoreCalculator()
        # 8-10 han = baiman = 16000
        points = calc.compute_points(han=8, fu=30, is_dealer=False)
        assert points == 16000

    def test_sanbaiman(self, score_mod):
        calc = score_mod.ScoreCalculator()
        # 11-12 han = sanbaiman = 24000
        points = calc.compute_points(han=11, fu=30, is_dealer=False)
        assert points == 24000

    def test_yakuman(self, score_mod):
        calc = score_mod.ScoreCalculator()
        # 13+ han = yakuman = 32000
        points = calc.compute_points(han=13, fu=30, is_dealer=False)
        assert points == 32000

    def test_fu_calculation(self, score_mod):
        calc = score_mod.ScoreCalculator()
        fu = calc.compute_fu(
            win_type="tsumo",
            melds=[],
            pair_tile="1z",
            wait_type="shanpon"
        )
        assert isinstance(fu, int)
        assert fu >= 20

    def test_fire_evolution(self, score_mod):
        calc = score_mod.ScoreCalculator()
        fired = []
        calc.evolution_callback = lambda k, d: fired.append(k)
        calc._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M333 — ReplayConverter (10 tests)
# =====================================================================
class TestReplayConverter:
    def test_class_exists(self, replay_mod):
        assert hasattr(replay_mod, "ReplayConverter")

    def test_has_evolution_key(self, replay_mod):
        assert "replay_converter" in replay_mod._EVOLUTION_KEY

    def test_convert_tenhou_xml(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        xml_data = '<mjloggm ver="2.3"><GO type="169"/><INIT seed="0,0,0,0,0,0" ten="250,250,250,250" oya="0" hai0="1,2,3" hai1="4,5,6" hai2="7,8,9" hai3="10,11,12"/></mjloggm>'
        spans = conv.convert_tenhou(xml_data)
        assert isinstance(spans, list)

    def test_convert_majsoul_json(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        record = {
            "head": {"config": {"mode": {"mode": 2}}},
            "data": [
                {"name": "RecordNewRound", "data": {"tiles0": ["1m", "2m", "3m"]}}
            ]
        }
        spans = conv.convert_majsoul(json.dumps(record))
        assert isinstance(spans, list)

    def test_to_training_span_format(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        span = conv.build_training_span(
            states=[{"hand": [0]*34}],
            actions=[0],
            reward=1.0
        )
        assert "states" in span
        assert "actions" in span
        assert "reward" in span

    def test_empty_replay(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        spans = conv.convert_tenhou("")
        assert spans == []

    def test_batch_convert(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        records = [
            {"format": "majsoul", "data": json.dumps({"head": {}, "data": []})},
            {"format": "majsoul", "data": json.dumps({"head": {}, "data": []})},
        ]
        all_spans = conv.batch_convert(records)
        assert isinstance(all_spans, list)

    def test_span_fields_complete(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        span = conv.build_training_span(
            states=[{"hand": [1]*34}],
            actions=[5],
            reward=0.5
        )
        assert isinstance(span["reward"], float)
        assert len(span["states"]) == 1
        assert len(span["actions"]) == 1

    def test_reward_clipping(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        span = conv.build_training_span(
            states=[{}], actions=[0], reward=999.0
        )
        assert -10.0 <= span["reward"] <= 10.0

    def test_fire_evolution(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        fired = []
        conv.evolution_callback = lambda k, d: fired.append(k)
        conv._fire_evolution({})
        assert len(fired) == 1

    def test_format_detection(self, replay_mod):
        conv = replay_mod.ReplayConverter()
        assert conv.detect_format('<mjloggm') == "tenhou"
        assert conv.detect_format('{"head":') == "majsoul"
        assert conv.detect_format("unknown") == "unknown"


# =====================================================================
# M334 — MahjongEvolutionLoop (10 tests)
# =====================================================================
class TestMahjongEvolutionLoop:
    def test_class_exists(self, evo_loop_mod):
        assert hasattr(evo_loop_mod, "MahjongEvolutionLoop")

    def test_has_evolution_key(self, evo_loop_mod):
        assert "mahjong_evolution" in evo_loop_mod._EVOLUTION_KEY

    def test_record_episode(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop()
        loop.record_episode(
            states=[{"hand": [0]*34}],
            actions=[1, 2, 3],
            reward=1.0
        )
        assert loop.episode_count == 1

    def test_compute_fitness(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop()
        loop.record_episode([{}], [0], 0.8)
        loop.record_episode([{}], [0], 0.6)
        fitness = loop.compute_fitness()
        assert isinstance(fitness, float)
        assert 0.0 <= fitness <= 1.0

    def test_should_evolve(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop(min_episodes=2)
        loop.record_episode([{}], [0], 0.5)
        assert loop.should_evolve() is False
        loop.record_episode([{}], [0], 0.5)
        assert loop.should_evolve() is True

    def test_advance_generation(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop(min_episodes=1)
        loop.record_episode([{}], [0], 0.5)
        gen_before = loop.generation
        loop.advance_generation()
        assert loop.generation == gen_before + 1

    def test_export_training_data(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop()
        loop.record_episode([{"h": 1}], [0], 1.0)
        data = loop.export_training_data()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_reset(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop()
        loop.record_episode([{}], [0], 0.5)
        loop.reset()
        assert loop.episode_count == 0

    def test_generation_tracking(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop(min_episodes=1)
        loop.record_episode([{}], [0], 0.5)
        loop.advance_generation()
        loop.record_episode([{}], [0], 0.7)
        loop.advance_generation()
        assert loop.generation == 2

    def test_fire_evolution(self, evo_loop_mod):
        loop = evo_loop_mod.MahjongEvolutionLoop()
        fired = []
        loop.evolution_callback = lambda k, d: fired.append(k)
        loop._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M335 — MahjongStrategyAdvisor (10 tests)
# =====================================================================
class TestMahjongStrategyAdvisor:
    def test_class_exists(self, strategy_mod):
        assert hasattr(strategy_mod, "MahjongStrategyAdvisor")

    def test_has_evolution_key(self, strategy_mod):
        assert "mahjong_strategy" in strategy_mod._EVOLUTION_KEY

    def test_game_name(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        assert advisor.game_name == "mahjong"

    def test_advise_returns_dict(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        state = {
            "hand": ["1m", "2m", "3m", "5p", "6p", "7p", "8s", "9s", "1z", "1z", "1z", "3z", "4z"],
            "turn": 5,
            "round_wind": "east",
        }
        result = advisor.advise(state)
        assert isinstance(result, dict)
        assert "action" in result

    def test_evaluate_action(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        score = advisor.evaluate_action(
            action={"type": "dahai", "pai": "3z"},
            outcome={"result": "safe"}
        )
        assert isinstance(score, float)

    def test_get_confidence(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        conf = advisor.get_confidence()
        assert 0.0 <= conf <= 1.0

    def test_advise_empty_state(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        result = advisor.advise({})
        assert "action" in result

    def test_confidence_increases_with_data(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        c1 = advisor.get_confidence()
        advisor.evaluate_action({"type": "dahai"}, {"result": "safe"})
        advisor.evaluate_action({"type": "dahai"}, {"result": "safe"})
        advisor.evaluate_action({"type": "dahai"}, {"result": "safe"})
        c2 = advisor.get_confidence()
        assert c2 >= c1

    def test_reasoning_field(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        state = {"hand": ["1m"], "turn": 1}
        result = advisor.advise(state)
        assert "reasoning" in result

    def test_fire_evolution(self, strategy_mod):
        advisor = strategy_mod.MahjongStrategyAdvisor()
        fired = []
        advisor.evolution_callback = lambda k, d: fired.append(k)
        advisor._fire_evolution({})
        assert len(fired) == 1

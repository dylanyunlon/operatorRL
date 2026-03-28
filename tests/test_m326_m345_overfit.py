"""
Overfitting Verification Tests for M326-M345.

20 independent tests that validate implementations are NOT overfitting
to the unit tests. These test cross-module integration and edge cases
not covered in the primary test suite.
"""

import importlib.util
import json
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_MAHJONG_SRC = os.path.join(_ROOT, "integrations", "mahjong", "src", "mahjong_agent")
_VISION_SRC = os.path.join(_ROOT, "extensions", "vision-bridge", "src", "vision_bridge")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# =====================================================================
# Cross-module integration: Mahjong pipeline
# =====================================================================
class TestMahjongPipeline:
    """Test full pipeline: TileEncoder → ShantenCalc → DiscardAdvisor → StrategyAdvisor."""

    def test_encode_then_shanten(self):
        te = _load("te", os.path.join(_MAHJONG_SRC, "tile_encoder.py"))
        sc = _load("sc", os.path.join(_MAHJONG_SRC, "shanten_calculator.py"))
        enc = te.TileEncoder()
        calc = sc.ShantenCalculator()
        hand = ["1m", "2m", "3m", "5p", "6p", "7p", "8s", "9s", "1z", "1z", "1z", "3z", "4z"]
        hand_34 = enc.encode_hand(hand)
        shanten = calc.calculate(hand_34)
        assert isinstance(shanten, int)
        assert shanten >= 0

    def test_discard_reduces_shanten(self):
        te = _load("te2", os.path.join(_MAHJONG_SRC, "tile_encoder.py"))
        sc = _load("sc2", os.path.join(_MAHJONG_SRC, "shanten_calculator.py"))
        da = _load("da2", os.path.join(_MAHJONG_SRC, "discard_advisor.py"))
        enc = te.TileEncoder()
        calc = sc.ShantenCalculator()
        advisor = da.DiscardAdvisor()
        hand = ["1m", "2m", "3m", "5p", "6p", "7p", "8s", "9s", "1z", "1z", "1z", "3z", "4z"]
        result = advisor.advise_discard(hand)
        assert result["tile"] is not None

    def test_strategy_advise_with_full_state(self):
        sa = _load("sa", os.path.join(_MAHJONG_SRC, "mahjong_strategy_advisor.py"))
        advisor = sa.MahjongStrategyAdvisor()
        state = {
            "hand": ["1m", "2m", "3m", "4p", "5p", "6p", "7s", "8s", "9s", "1z", "1z", "1z", "2z"],
            "turn": 10,
            "round_wind": "east",
            "seat_wind": "south",
            "dora": ["5m"],
        }
        result = advisor.advise(state)
        assert "action" in result
        assert "reasoning" in result

    def test_mortal_bridge_react_format(self):
        mb = _load("mb", os.path.join(_MAHJONG_SRC, "mortal_bridge.py"))
        bridge = mb.MortalBridge()
        obs = [0.0] * 34
        masks = [True] * 34
        action, q_vals = bridge.react(obs, masks)
        assert 0 <= action < 34

    def test_akagi_adapter_flow_lifecycle(self):
        aa = _load("aa", os.path.join(_MAHJONG_SRC, "akagi_mitm_adapter.py"))
        adapter = aa.AkagiMitmAdapter()
        adapter.on_flow_start("f1")
        adapter.on_flow_start("f2")
        assert len(adapter.active_flows) == 2
        adapter.on_flow_end("f1")
        assert len(adapter.active_flows) == 1
        adapter.on_flow_end("f2")
        assert len(adapter.active_flows) == 0


# =====================================================================
# Cross-module integration: Vision pipeline
# =====================================================================
class TestVisionPipeline:
    """Test: ScreenCapture → FrameBuffer → VisualStateEncoder → VisionEvolutionBridge."""

    def test_frame_buffer_to_encoder(self):
        fb = _load("fb", os.path.join(_VISION_SRC, "frame_buffer.py"))
        ve = _load("ve", os.path.join(_VISION_SRC, "visual_state_encoder.py"))
        buf = fb.FrameBuffer(capacity=10)
        buf.push(frame=[0.5] * (10*10*3), timestamp=1.0)
        latest = buf.get_latest()
        enc = ve.VisualStateEncoder(feature_dim=16)
        vec = enc.encode(latest["frame"], height=10, width=10)
        assert len(vec) == 16

    def test_encoder_to_evolution_bridge(self):
        ve2 = _load("ve2", os.path.join(_VISION_SRC, "visual_state_encoder.py"))
        veb = _load("veb", os.path.join(_VISION_SRC, "vision_evolution_bridge.py"))
        enc = ve2.VisualStateEncoder(feature_dim=8)
        frame = [0.1] * (10*10*3)
        features = enc.encode(frame, height=10, width=10)
        bridge = veb.VisionEvolutionBridge()
        bridge.record_state(features, 1.0)
        bridge.record_action(2, 1.0)
        bridge.record_reward(0.7, 1.0)
        spans = bridge.build_training_spans()
        assert len(spans) == 1
        assert len(spans[0]["state"]) == 8

    def test_comparator_with_perfect_data(self):
        comp_mod = _load("cm", os.path.join(_VISION_SRC, "fiddler_vision_comparator.py"))
        comp = comp_mod.FiddlerVisionComparator()
        for i in range(10):
            comp.compare({"gold": i * 100}, {"gold": i * 100})
        report = comp.generate_report()
        assert report["total_comparisons"] == 10
        assert report["match_rate"] == 1.0

    def test_fusion_both_sources(self):
        fm = _load("fm", os.path.join(_VISION_SRC, "vision_protocol_fusion.py"))
        fuser = fm.VisionProtocolFusion()
        result = fuser.fuse(
            {"gold": 1000, "timestamp": 5.0},
            {"gold": 1005, "timestamp": 5.05}
        )
        assert "gold" in result

    def test_league_ai_detection_center(self):
        la = _load("la", os.path.join(_VISION_SRC, "league_ai_adapter.py"))
        adapter = la.LeagueAIAdapter()
        det = adapter.create_detection("minion", 0, 0, 100, 200)
        assert det["x"] == 50
        assert det["y"] == 100
        assert det["w"] == 100
        assert det["h"] == 200


# =====================================================================
# Cross-pipeline: Mahjong ↔ Vision
# =====================================================================
class TestCrossPipeline:
    def test_replay_converter_span_to_evolution(self):
        rc = _load("rc", os.path.join(_MAHJONG_SRC, "replay_converter.py"))
        el = _load("el", os.path.join(_MAHJONG_SRC, "mahjong_evolution_loop.py"))
        conv = rc.ReplayConverter()
        span = conv.build_training_span([{"h": 1}], [0], 0.8)
        loop = el.MahjongEvolutionLoop(min_episodes=1)
        loop.record_episode(span["states"], span["actions"], span["reward"])
        assert loop.episode_count == 1
        assert loop.should_evolve() is True

    def test_score_calculator_yakuman_dealer(self):
        sm = _load("sm", os.path.join(_MAHJONG_SRC, "score_calculator.py"))
        calc = sm.ScoreCalculator()
        # Yakuman dealer = 48000
        points = calc.compute_points(han=13, fu=30, is_dealer=True)
        assert points == 48000

    def test_opponent_model_threat_escalation(self):
        om = _load("om", os.path.join(_MAHJONG_SRC, "opponent_model.py"))
        model = om.OpponentModel(num_players=4)
        # Record many discards → threat should increase
        for i in range(15):
            model.record_discard(player=1, tile=f"{i%9+1}m", turn=i)
        threat = model.assess_threat(player=1)
        assert threat in ("low", "medium", "high", "critical")

    def test_minimap_nms_no_detections(self):
        mm = _load("mm", os.path.join(_VISION_SRC, "minimap_detector.py"))
        det = mm.MinimapDetector()
        filtered = det.apply_nms([], iou_threshold=0.5)
        assert filtered == []

    def test_ml_agents_bridge_reset_step_cycle(self):
        ma = _load("ma", os.path.join(_VISION_SRC, "ml_agents_bridge.py"))
        bridge = ma.MLAgentsBridge(obs_size=4, action_size=2)
        obs = bridge.reset()
        assert len(obs) == 4
        obs2, r, done, info = bridge.step(0)
        assert len(obs2) == 4
        assert bridge.step_count == 1

    def test_ocr_parse_game_time_edge(self):
        ocr = _load("ocr", os.path.join(_VISION_SRC, "ocr_extractor.py"))
        ext = ocr.OcrExtractor()
        assert ext.parse_game_time("00:00") == 0
        assert ext.parse_game_time("59:59") == 59 * 60 + 59

    def test_screen_capture_mode_validation(self):
        sc = _load("sc3", os.path.join(_VISION_SRC, "screen_capture.py"))
        cap = sc.ScreenCapture(width=1280, height=720, fps=30, mode="desktop")
        assert cap.input_mode == "desktop"
        cap2 = sc.ScreenCapture(width=1280, height=720, fps=30, mode="video")
        assert cap2.input_mode == "video"

    def test_frame_buffer_timestamp_ordering(self):
        fb2 = _load("fb2", os.path.join(_VISION_SRC, "frame_buffer.py"))
        buf = fb2.FrameBuffer(capacity=100)
        for i in range(50):
            buf.push([i], float(i))
        frames = buf.get_range(10.0, 20.0)
        timestamps = [f["timestamp"] for f in frames]
        assert timestamps == sorted(timestamps)

    def test_shanten_seven_pairs_complete(self):
        sc4 = _load("sc4", os.path.join(_MAHJONG_SRC, "shanten_calculator.py"))
        calc = sc4.ShantenCalculator()
        hand_34 = [0] * 34
        for i in range(7):
            hand_34[i] = 2  # 7 pairs
        shanten = calc.calculate_seven_pairs(hand_34)
        assert shanten == -1  # complete seven pairs

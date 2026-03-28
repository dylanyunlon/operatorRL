"""
TDD Tests for M336-M345: ml-agents + LeagueAI Vision System Integration.

100 tests (10 per module), designed for ~50% initial failure rate.
Tests written BEFORE implementation per TDD methodology.

Reference projects (拿来主义):
  - LeagueAI: LeagueAI_helper.py input_output + yolov3_detector.py
  - ml-agents: a2c_optimizer.py / dqn_trainer.py patterns
  - operatorRL: fiddler-bridge, evolution_callback pattern
"""

import importlib.util
import os
import sys
import time

import pytest

# ---------------------------------------------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_VISION_SRC = os.path.join(_ROOT, "vision-bridge", "src", "vision_bridge")


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
def screen_capture_mod():
    return _load("screen_capture", os.path.join(_VISION_SRC, "screen_capture.py"))


@pytest.fixture(scope="module")
def minimap_mod():
    return _load("minimap_detector", os.path.join(_VISION_SRC, "minimap_detector.py"))


@pytest.fixture(scope="module")
def ocr_mod():
    return _load("ocr_extractor", os.path.join(_VISION_SRC, "ocr_extractor.py"))


@pytest.fixture(scope="module")
def fusion_mod():
    return _load("vision_protocol_fusion", os.path.join(_VISION_SRC, "vision_protocol_fusion.py"))


@pytest.fixture(scope="module")
def frame_buffer_mod():
    return _load("frame_buffer", os.path.join(_VISION_SRC, "frame_buffer.py"))


@pytest.fixture(scope="module")
def ml_agents_mod():
    return _load("ml_agents_bridge", os.path.join(_VISION_SRC, "ml_agents_bridge.py"))


@pytest.fixture(scope="module")
def visual_encoder_mod():
    return _load("visual_state_encoder", os.path.join(_VISION_SRC, "visual_state_encoder.py"))


@pytest.fixture(scope="module")
def vision_evo_mod():
    return _load("vision_evolution_bridge", os.path.join(_VISION_SRC, "vision_evolution_bridge.py"))


@pytest.fixture(scope="module")
def league_ai_mod():
    return _load("league_ai_adapter", os.path.join(_VISION_SRC, "league_ai_adapter.py"))


@pytest.fixture(scope="module")
def comparator_mod():
    return _load("fiddler_vision_comparator", os.path.join(_VISION_SRC, "fiddler_vision_comparator.py"))


# =====================================================================
# M336 — ScreenCapture (10 tests)
# =====================================================================
class TestScreenCapture:
    def test_class_exists(self, screen_capture_mod):
        assert hasattr(screen_capture_mod, "ScreenCapture")

    def test_has_evolution_key(self, screen_capture_mod):
        assert "screen_capture" in screen_capture_mod._EVOLUTION_KEY

    def test_instantiation(self, screen_capture_mod):
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14)
        assert sc.width == 1920
        assert sc.height == 1080
        assert sc.target_fps == 14

    def test_frame_interval(self, screen_capture_mod):
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14)
        assert abs(sc.frame_interval - 1.0 / 14) < 0.001

    def test_capture_stub_returns_ndarray_shape(self, screen_capture_mod):
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14)
        frame = sc.capture_frame_stub()
        assert isinstance(frame, list) or hasattr(frame, "shape")
        if hasattr(frame, "shape"):
            assert frame.shape == (1080, 1920, 3)

    def test_set_region(self, screen_capture_mod):
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14)
        sc.set_capture_region(top=100, left=200, width=800, height=600)
        assert sc.region == {"top": 100, "left": 200, "width": 800, "height": 600}

    def test_resize_output(self, screen_capture_mod):
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14)
        sc.set_output_size(640, 480)
        assert sc.output_width == 640
        assert sc.output_height == 480

    def test_input_mode_desktop(self, screen_capture_mod):
        """Mirrors LeagueAI input_output 'desktop' mode."""
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14, mode="desktop")
        assert sc.input_mode == "desktop"

    def test_input_mode_video(self, screen_capture_mod):
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14, mode="video")
        assert sc.input_mode == "video"

    def test_fire_evolution(self, screen_capture_mod):
        sc = screen_capture_mod.ScreenCapture(width=1920, height=1080, fps=14)
        fired = []
        sc.evolution_callback = lambda k, d: fired.append(k)
        sc._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M337 — MinimapDetector (10 tests)
# =====================================================================
class TestMinimapDetector:
    def test_class_exists(self, minimap_mod):
        assert hasattr(minimap_mod, "MinimapDetector")

    def test_has_evolution_key(self, minimap_mod):
        assert "minimap_detector" in minimap_mod._EVOLUTION_KEY

    def test_instantiation(self, minimap_mod):
        det = minimap_mod.MinimapDetector()
        assert det is not None

    def test_detect_empty_frame(self, minimap_mod):
        det = minimap_mod.MinimapDetector()
        # Black frame → no detections
        frame = [[0]*3 for _ in range(100*100)]
        results = det.detect(frame, frame_width=100, frame_height=100)
        assert isinstance(results, list)

    def test_detection_fields(self, minimap_mod):
        det = minimap_mod.MinimapDetector()
        d = det.create_detection("champion", 10, 20, 30, 40)
        assert d["class"] == "champion"
        assert d["x_min"] == 10
        assert d["y_min"] == 20
        assert d["x_max"] == 30
        assert d["y_max"] == 40

    def test_minimap_region_default(self, minimap_mod):
        det = minimap_mod.MinimapDetector()
        region = det.get_minimap_region(1920, 1080)
        assert "x" in region and "y" in region
        assert "width" in region and "height" in region

    def test_champion_icons(self, minimap_mod):
        det = minimap_mod.MinimapDetector()
        assert hasattr(det, "champion_classes")
        assert isinstance(det.champion_classes, list)

    def test_confidence_threshold(self, minimap_mod):
        det = minimap_mod.MinimapDetector(confidence_threshold=0.7)
        assert det.confidence_threshold == 0.7

    def test_nms_filter(self, minimap_mod):
        det = minimap_mod.MinimapDetector()
        boxes = [
            {"x_min": 0, "y_min": 0, "x_max": 10, "y_max": 10, "score": 0.9},
            {"x_min": 1, "y_min": 1, "x_max": 11, "y_max": 11, "score": 0.8},
        ]
        filtered = det.apply_nms(boxes, iou_threshold=0.5)
        assert len(filtered) == 1  # overlapping → keep highest

    def test_fire_evolution(self, minimap_mod):
        det = minimap_mod.MinimapDetector()
        fired = []
        det.evolution_callback = lambda k, d: fired.append(k)
        det._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M338 — OcrExtractor (10 tests)
# =====================================================================
class TestOcrExtractor:
    def test_class_exists(self, ocr_mod):
        assert hasattr(ocr_mod, "OcrExtractor")

    def test_has_evolution_key(self, ocr_mod):
        assert "ocr_extractor" in ocr_mod._EVOLUTION_KEY

    def test_instantiation(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        assert ocr is not None

    def test_extract_number(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        result = ocr.extract_number("12345 gold")
        assert result == 12345

    def test_extract_number_none(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        result = ocr.extract_number("")
        assert result is None

    def test_extract_health_bar(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        hp = ocr.parse_health_text("500/1000")
        assert hp == {"current": 500, "max": 1000}

    def test_extract_cooldown(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        cd = ocr.parse_cooldown_text("12.5s")
        assert abs(cd - 12.5) < 0.01

    def test_extract_game_time(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        seconds = ocr.parse_game_time("25:30")
        assert seconds == 25 * 60 + 30

    def test_roi_regions(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        regions = ocr.get_default_roi_regions(1920, 1080)
        assert "gold" in regions
        assert "health" in regions
        assert "game_time" in regions

    def test_fire_evolution(self, ocr_mod):
        ocr = ocr_mod.OcrExtractor()
        fired = []
        ocr.evolution_callback = lambda k, d: fired.append(k)
        ocr._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M339 — VisionProtocolFusion (10 tests)
# =====================================================================
class TestVisionProtocolFusion:
    def test_class_exists(self, fusion_mod):
        assert hasattr(fusion_mod, "VisionProtocolFusion")

    def test_has_evolution_key(self, fusion_mod):
        assert "vision_protocol_fusion" in fusion_mod._EVOLUTION_KEY

    def test_instantiation(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        assert f is not None

    def test_fuse_matching_data(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        protocol_data = {"gold": 5000, "health": 800, "timestamp": 100.0}
        vision_data = {"gold": 5000, "health": 795, "timestamp": 100.1}
        fused = f.fuse(protocol_data, vision_data)
        assert isinstance(fused, dict)
        assert "gold" in fused
        assert "confidence" in fused

    def test_fuse_protocol_only(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        fused = f.fuse({"gold": 5000}, None)
        assert fused["gold"] == 5000
        assert fused["source"] == "protocol"

    def test_fuse_vision_only(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        fused = f.fuse(None, {"gold": 4900})
        assert fused["gold"] == 4900
        assert fused["source"] == "vision"

    def test_conflict_resolution(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        # Large discrepancy → flag conflict
        protocol_data = {"gold": 5000, "timestamp": 100.0}
        vision_data = {"gold": 3000, "timestamp": 100.0}
        fused = f.fuse(protocol_data, vision_data)
        assert fused.get("conflict") is True

    def test_timestamp_alignment(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        aligned = f.align_timestamps(
            protocol_ts=100.0, vision_ts=100.5, max_drift=1.0
        )
        assert aligned is True
        aligned2 = f.align_timestamps(
            protocol_ts=100.0, vision_ts=102.0, max_drift=1.0
        )
        assert aligned2 is False

    def test_priority_setting(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion(priority="protocol")
        assert f.priority == "protocol"
        f2 = fusion_mod.VisionProtocolFusion(priority="vision")
        assert f2.priority == "vision"

    def test_empty_inputs(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        fused = f.fuse(None, None)
        assert fused == {}

    def test_fire_evolution(self, fusion_mod):
        f = fusion_mod.VisionProtocolFusion()
        fired = []
        f.evolution_callback = lambda k, d: fired.append(k)
        f._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M340 — FrameBuffer (10 tests)
# =====================================================================
class TestFrameBuffer:
    def test_class_exists(self, frame_buffer_mod):
        assert hasattr(frame_buffer_mod, "FrameBuffer")

    def test_has_evolution_key(self, frame_buffer_mod):
        assert "frame_buffer" in frame_buffer_mod._EVOLUTION_KEY

    def test_instantiation(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=10)
        assert buf.capacity == 10
        assert len(buf) == 0

    def test_push_and_len(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=5)
        buf.push(frame=[0]*100, timestamp=1.0)
        buf.push(frame=[1]*100, timestamp=2.0)
        assert len(buf) == 2

    def test_capacity_overflow(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=3)
        for i in range(5):
            buf.push(frame=[i]*10, timestamp=float(i))
        assert len(buf) == 3
        # oldest frames should be evicted
        oldest = buf.get_oldest()
        assert oldest["timestamp"] == 2.0

    def test_get_by_timestamp(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=10)
        buf.push(frame=[0]*10, timestamp=1.0)
        buf.push(frame=[1]*10, timestamp=2.0)
        buf.push(frame=[2]*10, timestamp=3.0)
        result = buf.get_nearest(timestamp=2.1)
        assert result["timestamp"] == 2.0

    def test_get_range(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=20)
        for i in range(10):
            buf.push(frame=[i], timestamp=float(i))
        frames = buf.get_range(start=3.0, end=6.0)
        assert len(frames) == 4  # timestamps 3, 4, 5, 6

    def test_clear(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=10)
        buf.push(frame=[0], timestamp=1.0)
        buf.clear()
        assert len(buf) == 0

    def test_latest(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=10)
        buf.push(frame=[0], timestamp=1.0)
        buf.push(frame=[1], timestamp=2.0)
        latest = buf.get_latest()
        assert latest["timestamp"] == 2.0

    def test_empty_get_returns_none(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=10)
        assert buf.get_latest() is None

    def test_fire_evolution(self, frame_buffer_mod):
        buf = frame_buffer_mod.FrameBuffer(capacity=10)
        fired = []
        buf.evolution_callback = lambda k, d: fired.append(k)
        buf._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M341 — MLAgentsBridge (10 tests)
# =====================================================================
class TestMLAgentsBridge:
    def test_class_exists(self, ml_agents_mod):
        assert hasattr(ml_agents_mod, "MLAgentsBridge")

    def test_has_evolution_key(self, ml_agents_mod):
        assert "ml_agents_bridge" in ml_agents_mod._EVOLUTION_KEY

    def test_instantiation(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge()
        assert bridge is not None

    def test_step_returns_tuple(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge()
        obs, reward, done, info = bridge.step(action=0)
        assert isinstance(obs, list)
        assert isinstance(reward, float)
        assert isinstance(done, bool)
        assert isinstance(info, dict)

    def test_reset(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge()
        obs = bridge.reset()
        assert isinstance(obs, list)

    def test_observation_space(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge(obs_size=84)
        assert bridge.observation_size == 84

    def test_action_space(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge(action_size=5)
        assert bridge.action_size == 5

    def test_batch_step(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge()
        results = bridge.batch_step(actions=[0, 1, 2])
        assert isinstance(results, list)
        assert len(results) == 3

    def test_episode_tracking(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge()
        bridge.reset()
        bridge.step(0)
        bridge.step(1)
        assert bridge.step_count == 2

    def test_fire_evolution(self, ml_agents_mod):
        bridge = ml_agents_mod.MLAgentsBridge()
        fired = []
        bridge.evolution_callback = lambda k, d: fired.append(k)
        bridge._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M342 — VisualStateEncoder (10 tests)
# =====================================================================
class TestVisualStateEncoder:
    def test_class_exists(self, visual_encoder_mod):
        assert hasattr(visual_encoder_mod, "VisualStateEncoder")

    def test_has_evolution_key(self, visual_encoder_mod):
        assert "visual_state_encoder" in visual_encoder_mod._EVOLUTION_KEY

    def test_instantiation(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=128)
        assert enc.feature_dim == 128

    def test_encode_frame(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=64)
        # Simulate a small frame as flat list (H*W*3)
        frame = [0.0] * (84 * 84 * 3)
        vec = enc.encode(frame, height=84, width=84)
        assert isinstance(vec, list)
        assert len(vec) == 64

    def test_encode_batch(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=64)
        frames = [[0.0] * (84 * 84 * 3)] * 4
        vecs = enc.encode_batch(frames, height=84, width=84)
        assert len(vecs) == 4
        assert len(vecs[0]) == 64

    def test_pooling_method(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=64, pooling="avg")
        assert enc.pooling == "avg"

    def test_normalize_output(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=32, normalize=True)
        frame = [1.0] * (10 * 10 * 3)
        vec = enc.encode(frame, height=10, width=10)
        # normalized → L2 norm ≈ 1.0
        norm = sum(v**2 for v in vec) ** 0.5
        assert abs(norm - 1.0) < 0.01

    def test_grayscale_mode(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=32, channels=1)
        assert enc.channels == 1

    def test_empty_frame(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=32)
        vec = enc.encode([], height=0, width=0)
        assert len(vec) == 32  # zero-padded

    def test_fire_evolution(self, visual_encoder_mod):
        enc = visual_encoder_mod.VisualStateEncoder(feature_dim=32)
        fired = []
        enc.evolution_callback = lambda k, d: fired.append(k)
        enc._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M343 — VisionEvolutionBridge (10 tests)
# =====================================================================
class TestVisionEvolutionBridge:
    def test_class_exists(self, vision_evo_mod):
        assert hasattr(vision_evo_mod, "VisionEvolutionBridge")

    def test_has_evolution_key(self, vision_evo_mod):
        assert "vision_evolution_bridge" in vision_evo_mod._EVOLUTION_KEY

    def test_instantiation(self, vision_evo_mod):
        bridge = vision_evo_mod.VisionEvolutionBridge()
        assert bridge is not None

    def test_record_visual_state(self, vision_evo_mod):
        bridge = vision_evo_mod.VisionEvolutionBridge()
        bridge.record_state(features=[0.1]*64, timestamp=1.0)
        assert bridge.state_count == 1

    def test_record_action(self, vision_evo_mod):
        bridge = vision_evo_mod.VisionEvolutionBridge()
        bridge.record_action(action=3, timestamp=1.0)
        assert bridge.action_count == 1

    def test_record_reward(self, vision_evo_mod):
        bridge = vision_evo_mod.VisionEvolutionBridge()
        bridge.record_reward(reward=0.5, timestamp=1.0)
        assert bridge.reward_count == 1

    def test_build_training_spans(self, vision_evo_mod):
        bridge = vision_evo_mod.VisionEvolutionBridge()
        bridge.record_state([0.1]*4, 1.0)
        bridge.record_action(1, 1.0)
        bridge.record_reward(0.5, 1.0)
        spans = bridge.build_training_spans()
        assert isinstance(spans, list)
        assert len(spans) == 1

    def test_incomplete_triplet_truncation(self, vision_evo_mod):
        """Mirrors FiddlerEvolutionBridge: incomplete triplets → min(len) truncation."""
        bridge = vision_evo_mod.VisionEvolutionBridge()
        bridge.record_state([0.1]*4, 1.0)
        bridge.record_state([0.2]*4, 2.0)
        bridge.record_action(1, 1.0)
        # 2 states, 1 action, 0 rewards → min = 0
        spans = bridge.build_training_spans()
        assert len(spans) == 0

    def test_reset(self, vision_evo_mod):
        bridge = vision_evo_mod.VisionEvolutionBridge()
        bridge.record_state([0.1]*4, 1.0)
        bridge.reset()
        assert bridge.state_count == 0

    def test_fire_evolution(self, vision_evo_mod):
        bridge = vision_evo_mod.VisionEvolutionBridge()
        fired = []
        bridge.evolution_callback = lambda k, d: fired.append(k)
        bridge._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M344 — LeagueAIAdapter (10 tests)
# =====================================================================
class TestLeagueAIAdapter:
    def test_class_exists(self, league_ai_mod):
        assert hasattr(league_ai_mod, "LeagueAIAdapter")

    def test_has_evolution_key(self, league_ai_mod):
        assert "league_ai_adapter" in league_ai_mod._EVOLUTION_KEY

    def test_instantiation(self, league_ai_mod):
        adapter = league_ai_mod.LeagueAIAdapter()
        assert adapter is not None

    def test_detection_format(self, league_ai_mod):
        """Mirrors LeagueAI detection class format."""
        adapter = league_ai_mod.LeagueAIAdapter()
        det = adapter.create_detection(
            object_class="champion", x_min=10, y_min=20, x_max=50, y_max=60
        )
        assert det["object_class"] == "champion"
        assert det["w"] == 40
        assert det["h"] == 40
        assert det["x"] == 30  # center x
        assert det["y"] == 40  # center y

    def test_class_names(self, league_ai_mod):
        adapter = league_ai_mod.LeagueAIAdapter()
        names = adapter.get_class_names()
        assert isinstance(names, list)
        assert len(names) > 0

    def test_resolution_config(self, league_ai_mod):
        adapter = league_ai_mod.LeagueAIAdapter(resolution=416)
        assert adapter.resolution == 416

    def test_threshold_config(self, league_ai_mod):
        adapter = league_ai_mod.LeagueAIAdapter(threshold=0.6)
        assert adapter.threshold == 0.6

    def test_detect_stub(self, league_ai_mod):
        adapter = league_ai_mod.LeagueAIAdapter()
        frame = [0] * (416 * 416 * 3)
        detections = adapter.detect(frame, width=416, height=416)
        assert isinstance(detections, list)

    def test_nms_confidence(self, league_ai_mod):
        adapter = league_ai_mod.LeagueAIAdapter(nms_confidence=0.4)
        assert adapter.nms_confidence == 0.4

    def test_fire_evolution(self, league_ai_mod):
        adapter = league_ai_mod.LeagueAIAdapter()
        fired = []
        adapter.evolution_callback = lambda k, d: fired.append(k)
        adapter._fire_evolution({})
        assert len(fired) == 1


# =====================================================================
# M345 — FiddlerVisionComparator (10 tests)
# =====================================================================
class TestFiddlerVisionComparator:
    def test_class_exists(self, comparator_mod):
        assert hasattr(comparator_mod, "FiddlerVisionComparator")

    def test_has_evolution_key(self, comparator_mod):
        assert "fiddler_vision_comparator" in comparator_mod._EVOLUTION_KEY

    def test_instantiation(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator()
        assert comp is not None

    def test_compare_matching(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator()
        result = comp.compare(
            fiddler_data={"gold": 5000, "kills": 3},
            vision_data={"gold": 5000, "kills": 3}
        )
        assert result["match"] is True
        assert result["accuracy"] == 1.0

    def test_compare_mismatch(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator()
        result = comp.compare(
            fiddler_data={"gold": 5000},
            vision_data={"gold": 3000}
        )
        assert result["match"] is False
        assert result["accuracy"] < 1.0

    def test_tolerance_config(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator(tolerance=0.1)
        # 10% tolerance: 5000 vs 5400 → within tolerance
        result = comp.compare(
            fiddler_data={"gold": 5000},
            vision_data={"gold": 5400}
        )
        assert result["match"] is True

    def test_report_generation(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator()
        comp.compare({"gold": 100}, {"gold": 100})
        comp.compare({"gold": 200}, {"gold": 190})
        report = comp.generate_report()
        assert "total_comparisons" in report
        assert report["total_comparisons"] == 2

    def test_field_by_field_comparison(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator()
        result = comp.compare(
            fiddler_data={"gold": 5000, "kills": 3, "deaths": 1},
            vision_data={"gold": 5000, "kills": 2, "deaths": 1}
        )
        assert "field_results" in result
        assert result["field_results"]["gold"]["match"] is True
        assert result["field_results"]["kills"]["match"] is False

    def test_empty_inputs(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator()
        result = comp.compare({}, {})
        assert result["match"] is True
        assert result["accuracy"] == 1.0

    def test_fire_evolution(self, comparator_mod):
        comp = comparator_mod.FiddlerVisionComparator()
        fired = []
        comp.evolution_callback = lambda k, d: fired.append(k)
        comp._fire_evolution({})
        assert len(fired) == 1

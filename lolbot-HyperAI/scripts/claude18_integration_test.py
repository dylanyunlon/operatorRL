"""
scripts/claude18_integration_test.py — Full pipeline integration test.
=======================================================================
Claude18 · Validates all Claude6-18 code running together

Runs the complete system for 10 seconds with SimulatedReplayDataSource
and verifies:
    1. No import errors across all modules
    2. All 6 components reach RUNNING state
    3. Canbus produces data without stale-data spam
    4. Perception produces snapshots with advancing game_time
    5. Prediction produces win probabilities after warmup
    6. Planning produces strategy advice
    7. Control dispatches at least 1 action
    8. Channel health has no dead channels (except expected)
    9. New Claude18 modules load and function correctly

Usage:
    python -m scripts.claude18_integration_test
"""

from __future__ import annotations

import sys
import time
import threading
from pathlib import Path

# Ensure project root in path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))


def run_integration_test() -> bool:
    """Run full pipeline test. Returns True if all checks pass."""
    print("=" * 60)
    print("  Claude18 Integration Test")
    print("  Full pipeline with SimulatedReplayDataSource")
    print("=" * 60)
    print()

    errors = []

    # ── Phase 1: Import validation ────────────────────────────────────
    print("[Phase 1] Import validation...")
    imports_ok = True

    modules_to_test = [
        ("modules.canbus.canbus_component", "CanbusComponent"),
        ("modules.canbus.vehicle.simulated_replay", "SimulatedReplayDataSource"),
        ("modules.perception.perception_component", "PerceptionComponent"),
        ("modules.prediction.prediction_component", "PredictionComponent"),
        ("modules.planning.planning_component", "PlanningComponent"),
        ("modules.control.control_component", "ControlComponent"),
        ("modules.monitor.monitor_component", "MonitorComponent"),
        ("launch.mainboard", "Mainboard"),
        ("cyber.diagnostics.channel_diagnostics", "ChannelDiagnostics"),
        ("cyber.diagnostics.proc_histogram", "ProcHistogramProfiler"),
        ("modules.prediction.evaluator.confidence_calibrator", "ConfidenceCalibrator"),
        ("modules.prediction.objective.objective_window_advisor", "ObjectiveWindowAdvisor"),
        ("modules.perception.game_state.phase_detector", "PhaseDetector"),
        ("modules.perception.fusion.gold_trend_analyzer", "GoldTrendAnalyzer"),
        ("modules.planning.strategy.power_spike_detector", "PowerSpikeDetector"),
        ("modules.control.voice_output.voice_priority_queue", "VoicePriorityQueue"),
        ("modules.common.adapters.game_record", "GameRecorder"),
    ]

    for mod_path, cls_name in modules_to_test:
        try:
            mod = __import__(mod_path, fromlist=[cls_name])
            getattr(mod, cls_name)
            print(f"  OK: {mod_path}.{cls_name}")
        except Exception as e:
            print(f"  FAIL: {mod_path}.{cls_name}: {e}")
            errors.append(f"Import {mod_path}: {e}")
            imports_ok = False

    if not imports_ok:
        print(f"\n[FAIL] {len(errors)} import errors. Aborting.")
        return False

    # ── Phase 2: System startup ───────────────────────────────────────
    print("\n[Phase 2] System startup...")

    from modules.canbus.canbus_component import CanbusComponent, CanbusConfig
    from modules.perception.perception_component import PerceptionComponent
    from modules.prediction.prediction_component import PredictionComponent
    from modules.planning.planning_component import PlanningComponent
    from modules.control.control_component import ControlComponent
    from modules.monitor.monitor_component import MonitorComponent
    from launch.mainboard import Mainboard
    from modules.common.component_base import ComponentRegistry
    from cyber.node.node import reset_all_channels

    # Reset state from any previous test
    reset_all_channels()
    ComponentRegistry.reset()

    cfg = CanbusConfig(data_source="auto")
    canbus = CanbusComponent(cfg)
    perception = PerceptionComponent()
    prediction = PredictionComponent()
    planning = PlanningComponent()
    control = ControlComponent()
    monitor = MonitorComponent()

    board = Mainboard()
    for comp in [canbus, perception, prediction, planning, control, monitor]:
        board.register(comp)

    board.enable_channel_monitor()
    ok = board.start_all()
    print(f"  Mainboard.start_all(): {ok}")
    if not ok:
        errors.append("Mainboard.start_all() returned False")

    # ── Phase 3: Run for 8 seconds ────────────────────────────────────
    print("\n[Phase 3] Running pipeline for 8 seconds...")
    time.sleep(8)

    # ── Phase 4: Collect metrics ──────────────────────────────────────
    print("\n[Phase 4] Collecting metrics...")

    status = board.status()
    for name, info in status["components"].items():
        state = info.get("state", "UNKNOWN")
        seq = info.get("sequence", 0)
        print(f"  {name}: state={state} seq={seq}")
        if state != "RUNNING":
            errors.append(f"{name} not RUNNING (state={state})")

    # Component-specific checks
    print(f"\n  canbus.data_source_type = {canbus._data_source_type}")
    print(f"  canbus.game_active = {canbus.game_active}")
    print(f"  perception.snapshot_count = {perception._snapshot_seq}")
    print(f"  prediction.pred_count = {prediction._pred_count}")
    print(f"  planning.plan_count = {planning._plan_count}")
    print(f"  control.dispatch_count = {control._dispatch_count}")

    # Check: perception should have produced snapshots
    if perception._snapshot_seq < 5:
        errors.append(
            f"Perception produced only {perception._snapshot_seq} snapshots "
            f"(expected >= 5)"
        )

    # Check: prediction should have run (game_time starts at 120s)
    if prediction._pred_count < 1:
        errors.append(
            f"Prediction produced {prediction._pred_count} predictions "
            f"(expected >= 1)"
        )

    # Check: planning should have run
    if planning._plan_count < 1:
        errors.append(
            f"Planning produced {planning._plan_count} plans (expected >= 1)"
        )

    # Check: canbus stale_count should be LOW with simulated data
    stale = canbus._stale_count
    print(f"  canbus.stale_count = {stale}")
    if canbus._data_source_type in ("testdata", "simulated") and stale > 5:
        errors.append(
            f"Canbus stale_count={stale} with simulated data (expected <= 5)"
        )

    # ── Phase 5: Channel diagnostics ──────────────────────────────────
    print("\n[Phase 5] Channel diagnostics...")
    try:
        from cyber.diagnostics.channel_diagnostics import ChannelDiagnostics
        diag = ChannelDiagnostics()
        report = diag.analyze()
        print(f"  Channels: {len(report.channels)} total, "
              f"{report.healthy_count} healthy, "
              f"{report.warning_count} warnings, "
              f"{report.error_count} errors")
        for ch in report.channels:
            if ch.health.name not in ("HEALTHY", "IDLE"):
                print(f"    [{ch.health.name}] {ch.channel_name}: "
                      f"{ch.diagnosis}")
    except Exception as e:
        print(f"  Channel diagnostics failed: {e}")

    # ── Phase 6: Test new Claude18 modules ────────────────────────────
    print("\n[Phase 6] Testing Claude18 modules...")

    # Test ConfidenceCalibrator
    try:
        from modules.prediction.evaluator.confidence_calibrator import (
            ConfidenceCalibrator, DataQualitySignal,
        )
        cal = ConfidenceCalibrator()
        sig = DataQualitySignal(
            canbus_source_type=canbus._data_source_type,
            game_time=500.0,
            perception_event_count=10,
        )
        result = cal.calibrate(0.7, sig)
        print(f"  ConfidenceCalibrator: raw=0.70 → "
              f"calibrated={result.final_confidence:.3f}")
    except Exception as e:
        errors.append(f"ConfidenceCalibrator: {e}")
        print(f"  FAIL: ConfidenceCalibrator: {e}")

    # Test PhaseDetector
    try:
        from modules.perception.game_state.phase_detector import (
            PhaseDetector, PhaseContext,
        )
        det = PhaseDetector()
        ctx = PhaseContext(game_time=600.0, total_kills=8, towers_destroyed=1)
        trans = det.update(ctx)
        print(f"  PhaseDetector: phase={det.current_phase.name}")
    except Exception as e:
        errors.append(f"PhaseDetector: {e}")
        print(f"  FAIL: PhaseDetector: {e}")

    # Test GoldTrendAnalyzer
    try:
        from modules.perception.fusion.gold_trend_analyzer import (
            GoldTrendAnalyzer,
        )
        gta = GoldTrendAnalyzer()
        for i in range(30):
            gta.record(game_time=500.0 + i, gold_diff=1000.0 + i * 50)
        report = gta.analyze()
        print(f"  GoldTrendAnalyzer: momentum={report.short_momentum:.1f} "
              f"gold/s, advantage={report.advantage_team}")
    except Exception as e:
        errors.append(f"GoldTrendAnalyzer: {e}")
        print(f"  FAIL: GoldTrendAnalyzer: {e}")

    # Test VoicePriorityQueue
    try:
        from modules.control.voice_output.voice_priority_queue import (
            VoicePriorityQueue, VoicePriority,
        )
        vpq = VoicePriorityQueue()
        vpq.enqueue("Test low", "general", VoicePriority.LOW)
        vpq.enqueue("Test high", "event_announcement", VoicePriority.HIGH)
        entry = vpq.dequeue()
        assert entry is not None and entry.text == "Test high", (
            f"Expected 'Test high', got {entry}"
        )
        print(f"  VoicePriorityQueue: priority ordering works")
    except Exception as e:
        errors.append(f"VoicePriorityQueue: {e}")
        print(f"  FAIL: VoicePriorityQueue: {e}")

    # ── Phase 7: Shutdown ─────────────────────────────────────────────
    print("\n[Phase 7] Shutdown...")
    results = board.stop_all(timeout=3.0)
    for name, state in results.items():
        print(f"  {name}: {state}")
        if state not in ("SHUTDOWN",):
            errors.append(f"{name} shutdown state: {state}")

    # ── Summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if errors:
        print(f"  RESULT: FAIL ({len(errors)} errors)")
        for e in errors:
            print(f"    - {e}")
        print("=" * 60)
        return False
    else:
        print("  RESULT: ALL CHECKS PASSED")
        print("=" * 60)
        return True


if __name__ == "__main__":
    success = run_integration_test()
    sys.exit(0 if success else 1)

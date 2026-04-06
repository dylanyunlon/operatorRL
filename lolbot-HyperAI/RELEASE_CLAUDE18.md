# RELEASE_CLAUDE18.md — SimulatedReplay + 10 Analysis Modules

> Claude18 · Based on Claude16 commit 24d20c0 (all Claude6-16 code preserved)
> Author: dylanyunlong <dylanyunlong@gmail.com>

---

## 0. Design Spec (Apollo Pattern Template)

查看 Apollo modules/canbus/vehicle/vehicle_factory.h 上现有 vehicle factory
的实现方式, 理解其模式, 特别是 数据源适配器 是如何通过工厂模式统一创建并
在运行时切换的。从 Apollo VehicleFactory::CreateVehicle() 这个好例子开始。
然后, 遵循该模式实现一个新的 SimulatedReplayDataSource, 让 canbus 可以
从静态 JSON 回放时自动递增 gameTime 和玩家状态, 并能 避免 stale 告警洪泛。
接着 ChannelDiagnostics 引入 per-channel 根因分析, 使 monitor 能够 精确
定位死通道和背压通道, 同时 ProcHistogramProfiler 优化 Proc() 延迟分布追踪。
随后 ConfidenceCalibrator 整合 多信号置信度校准, 令 prediction 支持
数据质量感知的置信度调节, 进而 ObjectiveWindowAdvisor 增强 目标窗口战略建议。
最终 PhaseDetector + GoldTrendAnalyzer + PowerSpikeDetector 完善 perception
和 planning 的态势感知能力, 确保 全链路分析模块 兼容 Apollo 的模块化设计
理念, 全面 系统性地 升级 管道分析质量 以达成 诊断驱动改进 的目标。

---

## 1. Problem Diagnosis

From 6-second diagnostic run on Claude16 code:

| Issue | Severity | Root Cause |
|-------|----------|------------|
| `canbus.stale_count = 60+` | HIGH | testdata loops same JSON, gameTime=1680.5 never changes |
| Stale WARNING floods (~10/sec) | HIGH | `_check_stale()` fires every 100ms after tick 50 |
| `control.dispatch_count = 1` | MEDIUM | Strategy advice dedup keys too similar |
| Channel health: dead channels | MEDIUM | Status channels have no consumers |
| No time progression | MEDIUM | ReplayDataSource loops identical frames |

## 2. Changes

| # | File | Type | Lines | Fix |
|---|------|------|-------|-----|
| 1 | `modules/canbus/vehicle/simulated_replay.py` | NEW | 342 | Time-advancing replay (stale fix) |
| 2 | `modules/canbus/vehicle/data_source_factory.py` | MOD | +42 | auto_detect() uses SimulatedReplay |
| 3 | `cyber/diagnostics/channel_diagnostics.py` | NEW | 257 | Per-channel health diagnosis |
| 4 | `cyber/diagnostics/proc_histogram.py` | NEW | 202 | Histogram latency profiler |
| 5 | `modules/prediction/evaluator/confidence_calibrator.py` | NEW | 185 | Multi-signal confidence |
| 6 | `modules/prediction/objective/objective_window_advisor.py` | NEW | 268 | Objective window planning |
| 7 | `modules/perception/game_state/phase_detector.py` | NEW | 230 | Tempo-aware phase detection |
| 8 | `modules/perception/fusion/gold_trend_analyzer.py` | NEW | 203 | Gold diff trend analysis |
| 9 | `modules/planning/strategy/power_spike_detector.py` | NEW | 261 | Champion power spike detection |
| 10 | `modules/control/voice_output/voice_priority_queue.py` | NEW | 218 | Priority voice queue |
| 11 | `modules/common/adapters/game_record.py` | NEW | 334 | Structured game recording |
| 12 | `scripts/claude18_integration_test.py` | NEW | 275 | Full pipeline integration test |
| | **Total** | | **+2817** | |

## 3. Test Results

```
8-second integration run with SimulatedReplayDataSource:
  All 6 components: RUNNING → SHUTDOWN (clean)
  canbus.stale_count = 0 (was 60+)
  perception.snapshot_count = 79
  prediction.pred_count = 16
  planning.plan_count = 16
  control.dispatch_count = 2
  17/17 module imports pass
  4/4 Claude18 unit tests pass
  RESULT: ALL CHECKS PASSED
```

## 4. Critique

### From User Perspective:
1. **SimulatedReplay auto-reset**: When gameTime hits 2400s, it resets to 120s. This could cause a perception event ID collision if the same synthetic EventIDs are re-injected. Mitigation: event IDs use 9000+ range (unlikely to collide with real LCU events starting from 0).
2. **Channel diagnostics warns on status channels**: `/lol/canbus_status` etc are WRITE_ONLY because MonitorComponent doesn't subscribe to them individually. This is expected but looks like a bug to users checking diagnostics.
3. **PowerSpikeDetector item IDs**: Hardcoded to a specific patch's item IDs. These change every LoL patch. In production, should load from a config file.

### From System Perspective:
1. **SimulatedReplay deep-copies**: `copy.deepcopy()` on every poll() (10Hz) adds ~0.3ms per tick. Acceptable for testdata mode but should be benchmarked if used for stress testing.
2. **ChannelDiagnostics accesses internal `_GLOBAL_CHANNELS`**: This couples diagnostics to node.py internals. A proper API on CyberNode would be cleaner but requires modifying existing code.
3. **New modules not yet wired into Proc() chains**: The 10 new analysis modules are importable and tested independently but NOT yet integrated into component Proc() loops. This is intentional — Claude19 should wire them in incrementally to avoid breaking the working pipeline.

## 5. For Claude19

The next Claude should:
1. Wire `PhaseDetector` into `PerceptionComponent.Proc()` to replace pure time-based phase classification
2. Wire `GoldTrendAnalyzer` into perception and pass momentum data to prediction
3. Wire `ObjectiveWindowAdvisor` into `PlanningComponent.Proc()` for objective-aware strategy
4. Wire `VoicePriorityQueue` into `ControlComponent` to replace the simple dedup_key cooldown
5. Wire `GameRecorder` into `MainLoop` session management for evolution
6. Wire `ConfidenceCalibrator` into `PredictionComponent.Proc()` 
7. Wire `PowerSpikeDetector` into `PlanningComponent.Proc()`
8. Continue with remaining 10 files from the original 20-file plan

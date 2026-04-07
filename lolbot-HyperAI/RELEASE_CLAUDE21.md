# RELEASE — Claude 21

## Design Specification

从 Apollo `canbus_component.cc` 的 `Init()/Proc()` 分离模式 这个好例子开始。
然后，遵循该模式修复 `PerceptionComponent` 中 `PhaseContext` 的错误字段名，
让 perception 可以 正确传递龙/男爵/水晶数据给 PhaseDetector，
并能 触发准确的 phase transition 事件。

接着 在 `PlanningComponent` 引入 正确的 `champion_name` 字段访问，
使 PowerSpikeDetector 能够 生成包含英雄名称的策略建议，
同时 消除每次 Proc() 循环的 AttributeError 告警。

随后 对 12 个 thin 模块 整合 V2 生产级扩展，
令 DataQualityGate 支持 自适应阈值 + 异常检测 + 断路器，
进而 MapAwareness 增强 区域压力分析 + 轮转检测 + 野区控制追踪。

最终 FogEstimator 完善 视野评分 + Gank 预测 + 迷雾威胁评估，
确保 CoordinateTransform 兼容 小地图投影 + 地标距离 + 英雄聚类，
全面 系统性地 升级 12 个模块质量 以达成 生产级数据管道的目标。

## Bug Fixes (2 critical runtime errors)

### Bug 1: PhaseDetector TypeError — 每100ms触发一次
- **文件**: `modules/perception/perception_component.py:352-359`
- **根因**: 调用 `PhaseContext()` 时传入了错误的字段名
  - `dragons_taken=0` → PhaseContext 字段名是 `dragons_killed`
  - `inhibitors_down=0` → PhaseContext 字段名是 `inhibitors_destroyed`
  - 还缺少 `barons_killed` 字段
- **修复**: 使用正确字段名 + 从 `final.blue_team` / `final.red_team` 的 TeamState 读取真实数据
- **影响**: PhaseDetector 现在能正确接收龙/男爵/水晶/塔的数据，phase transition 检测功能完全恢复

### Bug 2: PowerSpikeDetector AttributeError — 每500ms触发一次
- **文件**: `modules/planning/planning_component.py:498`
- **根因**: 访问 `spike.champion` 但 PowerSpike dataclass 的字段名是 `champion_name`
- **修复**: `spike.champion` → `spike.champion_name`
- **影响**: 英雄能力跃升提醒功能完全恢复，策略建议中正确显示英雄名称

## Module Expansions (12 files, +3294 lines, append-only)

| File | Lines Added | Key Features |
|------|-------------|--------------|
| `perception/fusion/data_quality_gate.py` | +457 | AdaptiveThreshold, AnomalyEvent, FieldValidationRule, CircuitBreaker |
| `localization/map_awareness.py` | +350 | ZonePressure, RotationEvent, MapState, jungle control scoring |
| `localization/fog_estimator.py` | +334 | FogZone, VisionScore, GankPrediction, role-based threat curves |
| `prediction/evaluator/confidence_calibrator.py` | +317 | IsotonicCalibrator, CalibrationBin, CalibrationReport, drift detection |
| `common/adapters/training_data_collector.py` | +266 | LabeledExample, outcome labeling, feature matrix export |
| `common/adapters/replay_messages.py` | +260 | ReplayFrameDiff, ReplayAnnotation, ReplaySessionMeta, seek index |
| `common/adapters/channel_registry.py` | +250 | ChannelDeclaration, ChannelHealth, topology validation |
| `perception/game_state/momentum_calculator.py` | +231 | MomentumSignal, MomentumSnapshot, streak detection, trend analysis |
| `transform/coordinate_transform.py` | +227 | MinimapPoint, GamePoint, landmarks, champion clustering |
| `dreamview/dashboard/live_data_pusher.py` | +222 | ClientSubscription, delta compression, backpressure handling |
| `common/util/proto_util.py` | +213 | SchemaField, MessageSchema, VersionedSerializer, safe_json_encode |
| `calibration/model_calibrator.py` | +167 | TemperatureConfig, temperature scaling, online recalibration |

## Handoff to Claude 22

以下模块已有 V2 扩展但可继续增强（建议方向：V3 集成层，接入 Proc() 循环）:
- `event_dedup_filter.py` (231行) — 可增加 per-channel dedup 策略配置
- `overlay_protocol.py` (133行) — 可增加 WebSocket 实际发送层
- `replay_driver.py` (185行) — 可增加 keyframe-based seek 实现
- `ab_test_manager.py` (322行) — 可增加 multi-armed bandit 自动毕业
- `commentary_template.py` (265行) — 可增加 中英双语模板
- `game_narrator.py` (431行) — 可增加 momentum-aware 叙事
- `voice_narrator.py` (356行) — 可增加 TTS 引擎对接
- `action_dispatcher.py` (527行) — 可增加 overlay + voice 联合调度

# lolbot-HyperAI Release — Claude#6 Batch (Integration Wiring + Control Layer + Data Pipeline)

> 20 production files, ~10,000 lines
> Author: dylanyunlong <dylanyunlong@gmail.com>
> Phase: 4 — Sub-module wiring, Control component, Training data, Dashboard backend

---

## 核心问题诊断

Phase 3 RELEASE_PHASE3.md 的 RISK-01 指出: **7 个子模块全部是孤岛**——
kill_feed_analyzer, minimap_analyzer, teamfight_predictor, macro_planner,
lane_advisor, action_dispatcher, overlay_renderer 均未接入父组件的 Proc() 循环。
本批次的首要任务是**修复这一架构断裂**。

此外缺失: ControlComponent (统一输出层)、ReplayRecorder (训练数据闭环)、
DashboardBackend (dreamview API 后端)、ChannelMonitor (通道健康监控)。

---

## File 01 — modules/perception/perception_component.py [PATCH]
### 感知组件接入 KillFeedAnalyzer + MinimapAnalyzer

查看 modules/perception/perception_component.py 上现有 Proc() 的实现方式,
理解其模式, 特别是 GameSnapshot 组装和 event 检测是如何在单个 Proc() 中顺序执行的。
可以从 _assemble_snapshot() → _detect_new_events() 的串联调用 这个好例子开始。
然后, 遵循该模式将 KillFeedAnalyzer.analyze() 和 MinimapAnalyzer.analyze()
接入 Proc() 尾部, 让 kill_feed 分析结果发布到 /lol/kill_feed 通道,
minimap 分析结果发布到 /lol/minimap_analysis 通道,
并能 被下游 prediction_component 和 planning_component 消费。
从头开始构建 wiring 代码, 除了代码库中已有的库之外, 不要使用其他库。

## File 02 — modules/prediction/prediction_component.py [PATCH]
### 预测组件接入 TeamfightPredictor

查看 modules/prediction/prediction_component.py 上现有 Proc() 的实现方式,
理解其模式, 特别是 WinPredictor 和内联 TeamfightAnalyzer 是如何在 Proc() 中被调用的。
可以从 _predict_teamfight() 的内联实现 这个好例子开始。
然后, 遵循该模式将 TeamfightPredictor.predict() 替换内联团战分析,
让 teamfight 预测结果同时发布到 /lol/teamfight_prediction 通道,
并能 提供 8 维特征向量而非简单的存活人数比。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 03 — modules/planning/planning_component.py [PATCH]
### 规划组件接入 MacroPlanner + LaneAdvisor

查看 modules/planning/planning_component.py 上现有 MacroDecisionEngine 内联实现方式,
理解其模式, 特别是 _early/_mid/_late_game_strategy 的阶段分发模式。
可以从 MacroDecisionEngine.decide() 的 phase switch 这个好例子开始。
然后, 遵循该模式将 MacroPlanner.decide() 和 LaneAdvisor.advise() 接入 Proc(),
让 宏观决策结果发布到 /lol/macro_decision 通道,
并能 与原有 strategy advice 合并后统一输出到 /lol/strategy。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 04 — modules/control/control_component.py [NEW]
### 控制层组件 — 统一输出调度 (Apollo control 对标)

查看 modules/canbus/canbus_component.py 上现有 TimerComponent 子类的实现方式,
理解其模式, 特别是 Init() 中 CyberNode 读写器创建和 Proc() 中数据读取发布的模式。
可以从 CanbusComponent.Init() 的 CreateReader/CreateWriter 模式 这个好例子开始。
然后, 遵循该模式实现一个新的 ControlComponent,
让 ActionDispatcher + OverlayRenderer + VoiceNarrator 可以 通过 /lol/strategy
通道消费策略建议, 并能 根据 ActionPriority 自动分发到 voice/overlay/log 三条通道。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 05 — modules/control/proto/control_messages.py [NEW]
### 控制层消息类型定义

查看 modules/common/adapters/game_messages.py 上现有 frozen dataclass 消息定义方式,
理解其模式, 特别是 WinPrediction / StrategyAdvice / VoiceCommand 的字段设计。
可以从 StrategyAdvice 的 rec_type + priority + confidence 三字段 这个好例子开始。
然后, 遵循该模式实现控制层专用消息类型:
ControlAction, OverlayUpdate, VoiceDirective, ControlStatus,
让 ControlComponent 可以 通过类型化消息与子模块通信。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 06 — modules/control/conf/control_config.py [NEW]
### 控制层配置

查看 conf/default_config.py 上现有 @dataclass 配置层级结构的实现方式,
理解其模式, 特别是 PerceptionConfig / PredictionConfig 的字段命名规范。
可以从 PlanningConfig 的 min_confidence / cooldown_seconds 这个好例子开始。
然后, 遵循该模式实现 ControlConfig,
让 voice / overlay / dispatch 各子系统的参数 可以 通过统一配置树管理。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 07 — cyber/transport/channel_monitor.py [NEW]
### 通道健康监控器

查看 canbus/transport.py 上现有 Transport 装饰器的实现方式,
理解其模式, 特别是 _channel_stats 字典和延迟追踪是如何收集诊断数据的。
可以从 Transport.diagnostics() 的 per-channel 统计输出 这个好例子开始。
然后, 遵循该模式实现一个新的 ChannelMonitor,
让 mainboard/scheduler 可以 实时检测通道死锁/饥饿/延迟异常,
并能 发布 /system/channel_health 告警消息。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 08 — modules/common/adapters/training_data_collector.py [REWRITE]
### 训练数据收集器 (游戏录像→特征矩阵)

查看 modules/common/adapters/training_data_collector.py 上现有骨架的实现方式,
理解其模式。可以从 canbus/transport.py 的 MessageRecorder JSONL 写入模式 这个好例子开始。
然后, 遵循该模式将骨架重写为生产级训练数据收集器,
让 每个 Proc() 周期的 snapshot + features + prediction 可以 被记录为训练样本,
并能 在游戏结束后导出为 CSV/JSONL 矩阵供离线模型训练使用。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 09 — modules/common/util/proto_util.py [REWRITE]
### 消息序列化工具

查看 modules/common/util/proto_util.py 上现有骨架的实现方式,
理解其模式。可以从 proto/lolbot_messages.py 的 to_dict/from_dict 这个好例子开始。
然后, 遵循该模式重写为生产级序列化层,
让 所有 frozen dataclass 消息 可以 在 dict ↔ bytes ↔ JSON 之间无损往返转换,
并能 自动处理 Enum / datetime / nested dataclass 等复杂类型。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 10 — modules/common/math/statistics.py [REWRITE]
### 在线统计工具 (EMA/Welford/分位数)

查看 modules/common/math/statistics.py 上现有骨架的实现方式,
理解其模式。可以从 cyber/component/timer_component.py 的 LatencyStats 滚动窗口 这个好例子开始。
然后, 遵循该模式重写为生产级在线统计库,
让 prediction/planning 各组件 可以 使用 EMA/Welford/P2 分位数估计等在线算法,
并能 在 O(1) 内存下维护无限数据流的统计摘要。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 11 — modules/dreamview/dashboard/dashboard_backend.py [NEW]
### Dreamview 仪表盘后端 API

查看 modules/dreamview/api/dreamview_api.py 上现有 HTTP 路由注册方式,
理解其模式, 特别是 /api/game_state 和 /api/prediction 的 GET handler。
可以从 DreamviewAPI._register_routes() 这个好例子开始。
然后, 遵循该模式实现一个新的 DashboardBackend,
让 dashboard_html.py 前端 可以 通过 WebSocket 实时推送接收所有通道数据,
并能 支持历史回放和通道订阅/取消订阅。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 12 — modules/perception/conf/perception_config.py [NEW]
### 感知层配置

查看 conf/default_config.py 上现有顶层配置的实现方式。
可以从 PerceptionConfig 的 poll_interval_ms 字段 这个好例子开始。
然后, 遵循该模式实现感知层子系统专用配置,
让 kill_feed_analyzer / minimap_analyzer / state_assembler 各组件 可以 独立配置阈值,
并能 通过 pipeline.yaml 覆盖默认值。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 13 — modules/prediction/conf/prediction_config.py [NEW]
### 预测层配置

同 File 12 模式, 为 prediction 子系统实现专用配置。

## File 14 — modules/planning/conf/planning_config.py [NEW]
### 规划层配置

同 File 12 模式, 为 planning 子系统实现专用配置。

## File 15 — modules/canbus/proto/canbus_messages.py [NEW]
### CAN Bus 层消息类型

查看 modules/common/adapters/game_messages.py 上现有 RawLCUData / RawFiddlerData 定义,
理解其模式。然后实现 canbus 层专用类型:
CanbusFrame, LCUEndpointData, FiddlerCaptureData, ConnectionStatus,
让 canbus_component 和 fiddler_mcp 可以 通过类型化消息通信。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

## File 16 — modules/perception/proto/perception_messages.py [NEW]
### 感知层消息类型

KillFeedResult, MinimapAnalysis, StateAssemblyResult 等结构化输出类型。

## File 17 — modules/prediction/proto/prediction_messages.py [NEW]
### 预测层消息类型

TeamfightResult, ObjectiveTimerState, WinProbabilityDetail 等结构化输出类型。

## File 18 — modules/planning/proto/planning_messages.py [NEW]
### 规划层消息类型

MacroDecision, LaneAdvice, ItemBuildSuggestion 等结构化输出类型。

## File 19 — launch/mainboard.py [PATCH]
### Mainboard 接入 ControlComponent + ChannelMonitor

查看 launch/mainboard.py 上现有组件注册和启动流程的实现方式。
然后将 ControlComponent 和 ChannelMonitor 接入启动序列。

## File 20 — scripts/diagnostic_runner.py [REWRITE]
### 诊断运行器 (从 stub 升级为生产级)

查看 scripts/diagnostic_runner.py 上现有 stub 的实现方式。
可以从 logs/diagnostic.json 的输出格式 这个好例子开始。
然后重写为可独立运行的诊断套件, 检测 import 完整性 / 通道连通性 /
Proc() 健康度 / 配置校验。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

---

## 给下一位 Claude#7 的交接说明

完成本批次后, 项目的核心管线 canbus→perception→prediction→planning→control
全部连通。下一位需要:

1. **File 21-25**: 测试套件 — 为 File 01-04 的 Proc() wiring 编写集成测试
2. **File 26-30**: evolution 层升级 — 将 fitness_evaluator 接入训练数据收集器
3. **File 31-35**: dreamview 前端 — 将 dashboard_html.py 升级为 React SPA
4. **File 36-40**: replay 系统 — 录制回放完整闭环测试

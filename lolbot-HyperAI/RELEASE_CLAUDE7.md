# lolbot-HyperAI Release — Claude#7 Batch (Pipeline Integration & Runtime Hardening)

> 20 production files, ~10,000 lines target
> Focus: Wire orphan channels, integrate sub-modules into Proc() loops,
>        add runtime diagnostics, replay testing infrastructure
> Author: Claude#7 · dylanyunlong <dylanyunlong@gmail.com>

---

## Diagnosis: Gaps Found in Current Codebase

Channel `/lol/events` published by perception but **zero subscribers**.
Channels `/lol/*_status` published by every component but **zero subscribers**.
Sub-modules (`kill_feed_analyzer`, `minimap_analyzer`, `objective_timer`,
`teamfight_predictor`, `item_build_advisor`, `macro_planner`, `lane_advisor`,
`action_dispatcher`, `overlay_renderer`) exist as standalone classes but
**are not wired into their parent component Proc() loops**.
No `/lol/objective_timers` or `/lol/kill_feed` channels exist.
No system-wide health aggregation subscribes to status channels.
`DreamviewAPI` websocket server exists but no live data push loop.

---

## File 01 — modules/perception/events/event_stream_processor.py

查看 `modules/perception/perception_component.py` 上现有 `_detect_new_events()`
的实现方式, 理解其模式, 特别是事件去重 (seen_event_ids) 和增量检测是如何与
GameSnapshot 发布分离的。可以从 `modules/perception/events/kill_feed_analyzer.py`
的 multi-kill / spree 检测逻辑这个好例子开始。然后, 遵循该模式实现一个新的
`EventStreamProcessor`, 让 PerceptionComponent.Proc() 可以将原始事件流委托给
专门处理器, 分发到 `/lol/kill_feed` 和 `/lol/objective_events` 两个子频道,
并能通过滑动窗口识别团战簇 (5秒内3+击杀 = 团战进行中)。从头开始构建, 除了
代码库中已有的库之外, 不要使用其他库。

## File 02 — modules/prediction/objective/objective_tracker.py

查看 `modules/prediction/objective/objective_timer.py` 上现有目标重生计时器
的实现方式, 理解其模式, 特别是 spawn_time 常量表和 event-driven 计时启动
是如何与 UI 显示分离的。可以从 `modules/common/adapters/game_messages.py` 的
`ObjectiveType` 枚举这个好例子开始。然后, 遵循该模式实现一个新的
`ObjectiveTracker` 组件, 订阅 `/lol/events` 频道的目标击杀事件并自动启动
重生倒计时, 发布 `/lol/objective_timers` 供 planning 和 voice 消费,
并能在距离重生60秒和30秒时发布 VoiceCommand 提醒。

## File 03 — modules/perception/fusion/sensor_fusion.py

查看 `modules/perception/perception_component.py` 上现有的单源 LCU 数据处理,
理解其模式, 特别是 raw_lcu_reader → assemble_snapshot 的单向管线。可以从
Apollo `modules/perception/multi_sensor_fusion/` 多传感器融合架构这个好例子
开始。然后, 遵循该模式实现一个新的 `SensorFusion`, 让 PerceptionComponent 可以
同时融合 LCU + Fiddler + Replay 三个数据源的快照, 使用时间戳对齐和
优先级合并策略, 并能在单源降级时自动回退到可用源而不中断 Proc() 循环。

## File 04 — modules/planning/strategy/teamfight_caller.py

查看 `modules/prediction/team_fight/teamfight_predictor.py` 上现有团战胜率
预测的实现方式, 理解其模式, 特别是 engage/disengage/hold 三元决策是如何
基于 alive_diff 和 level_diff 生成的。可以从 `dota2bot-OpenHyperAI/bots/`
的 mode_team 决策树这个好例子开始。然后, 遵循该模式实现一个新的
`TeamfightCaller`, 让 PlanningComponent 可以将 TeamfightPrediction 转化为
带置信度和紧急度的 VoiceCommand, 根据上下文 (baron pit / dragon pit /
lane) 细化建议文本, 并能施加冷却防止同一建议重复播报。

## File 05 — modules/control/dispatch/control_component.py

查看 `modules/control/voice_output/voice_narrator.py` 和
`modules/control/action_dispatch/action_dispatcher.py` 上现有的输出路由模式,
理解其模式, 特别是 priority queue 和 rate-limiting 是如何与 TTS 后端分离的。
可以从 Apollo `modules/control/control_component.cc` 的统一控制组件这个好例子
开始。然后, 遵循该模式实现一个新的 `ControlComponent` (TimerComponent),
作为控制层唯一 Proc() 入口, 聚合 voice_narrator + action_dispatcher +
overlay_renderer, 订阅 `/lol/strategy_advice` + `/lol/voice_command` +
`/lol/objective_timers`, 统一分发到语音/叠加/日志三条输出管道。

## File 06 — cyber/diagnostics/channel_monitor.py

查看 `cyber/node/node.py` 上现有的 _Channel 发布统计 (_write_count),
理解其模式, 特别是全局 _GLOBAL_CHANNELS 注册表是如何跟踪活跃频道的。
可以从 `runtime/health_monitor.py` 的组件级健康检查这个好例子开始。
然后, 遵循该模式实现一个新的 `ChannelMonitor`, 让 scheduler 或
dreamview 可以实时查看每个频道的发布速率 (msg/s)、最后消息时间戳、
subscriber 数量和 backpressure 丢弃次数, 并能检测 "死频道" (>5s 无消息)
和 "雪崩频道" (>100 msg/s 超限)。

## File 07 — runtime/session_manager.py

查看 `launch/main_loop.py` 上现有的 SessionState 状态机 (IDLE → PRE_GAME →
IN_GAME → POST_GAME → EVOLVING), 理解其模式, 特别是 _check_state_transitions
和 _on_game_start / _on_game_end 回调是如何与 Proc() 循环分离的。可以从
`launch/mainboard.py` 的 component lifecycle 管理这个好例子开始。然后,
遵循该模式实现一个新的 `SessionManager` (TimerComponent), 将会话生命周期
管理从 main_loop 中提取为独立组件, 发布 `/lol/session_state` 频道,
让其他组件可以按需响应游戏阶段变化而不耦合到 main_loop。

## File 08 — modules/dreamview/dashboard/live_data_pusher.py

查看 `modules/dreamview/api/dreamview_api.py` 上现有的 WebSocket server 框架,
理解其模式, 特别是 CreateReader 订阅和 snapshot 缓存是如何为 HTTP 端点
服务的。可以从 Apollo DreamView 的前端实时数据推送这个好例子开始。然后,
遵循该模式实现一个新的 `LiveDataPusher` (TimerComponent, 5Hz), 汇聚
game_state / win_prediction / teamfight_prediction / strategy_advice /
objective_timers 五个频道的最新数据, 序列化为 JSON, 通过 WebSocket
广播给所有连接的 dashboard 客户端。

## File 09 — modules/common/adapters/channel_registry.py

查看 `canbus/channel_message.py` 上现有的 CH_* 频道常量定义和
`modules/common/adapters/game_messages.py` 的消息类型定义, 理解其模式,
特别是频道名和消息 schema 是如何通过常量和 frozen dataclass 保证一致性的。
可以从 Apollo `modules/common_msgs/` proto 注册表这个好例子开始。然后,
遵循该模式实现一个新的 `ChannelRegistry`, 将所有频道定义集中管理,
提供 `get_channel(name) → ChannelDef(name, msg_type, rate_hz, description)`
查询, 并能在注册时自动验证无重复、无循环依赖。

## File 10 — scripts/integration_test_runner.py

查看 `tests/test_integration.py` 上现有的手动 CyberNode 测试, 理解其模式,
特别是 Writer.Write → Reader.GetLatestObserved 的发布-消费验证模式。可以从
`scripts/replay_simulator.py` 的 JSONL 回放驱动测试这个好例子开始。然后,
遵循该模式实现一个新的 `IntegrationTestRunner`, 让开发者可以
一键启动 mainboard (dry-run), 注入 mock RawLCUData 序列, 验证
perception→prediction→planning→voice 全链路消息流通, 并能输出每个
频道的延迟统计和丢包率报告。

## File 11 — modules/perception/game_state/momentum_calculator.py

查看 `modules/common/adapters/game_messages.py` 上现有的 `to_feature_dict()`
特征提取, 理解其模式。可以从 `modules/prediction/prediction_component.py` 的
PredictionFeatures.from_snapshot 趋势特征计算这个好例子开始。然后, 实现
`MomentumCalculator`, 基于最近 60 秒的击杀/目标/塔事件计算 [-1, +1] 动量
评分, 用指数衰减加权, 发布到 GameSnapshot 的 momentum_score 字段。

## File 12 — modules/common/filters/event_dedup_filter.py

查看 `modules/perception/perception_component.py` 上现有的 `_seen_event_ids`
集合去重, 理解其模式。可以从 `canbus/transport.py` 的 rate limiter 和
dead letter queue 这个好例子开始。然后, 实现 `EventDedupFilter`,
通用事件去重器, 支持 event_id 去重 + content hash 去重 + TTL 过期清理,
作为 perception 和所有 event subscriber 的前置过滤器。

## File 13 — modules/planning/strategy/back_timing_advisor.py

查看 `modules/planning/strategy/lane_advisor.py` 上现有的对线建议,
理解其模式, 特别是 game phase 过滤和 confidence 评分。可以从
LeagueAI 的 wave management 分析这个好例子开始。然后, 实现
`BackTimingAdvisor`, 根据当前金币、对面位置、wave 状态计算最优
回城时机, 考虑关键装备断点 (BF Sword 1300g, Lost Chapter 1300g),
发布 VoiceCommand "Good time to back for [item]"。

## File 14 — modules/prediction/team_fight/cooldown_tracker.py

查看 `modules/prediction/team_fight/teamfight_predictor.py` 上现有的
团战评分因子, 理解其模式。可以从 `modules/common/adapters/game_messages.py`
的 PlayerAbilities 数据结构这个好例子开始。然后, 实现 `CooldownTracker`,
通过事件流推断关键技能冷却状态 (Flash 300s, TP 360s, Ultimate 各异),
提供 `is_flash_up(player)`, `ult_ready_estimate(player)` 接口供
teamfight_predictor 消费。

## File 15 — modules/common/adapters/replay_messages.py

查看 `modules/common/adapters/game_messages.py` 上现有的消息类型体系,
理解其模式。可以从 `scripts/replay_simulator.py` 的 JSONL 读写这个好
例子开始。然后, 实现 `ReplayMessages`, 定义 ReplayFrame / ReplayMetadata /
ReplayIndex 数据类, 作为录制和回放系统的序列化协议, 支持时间切片索引
和频道过滤查询。

## File 16 — cyber/diagnostics/proc_profiler.py

查看 `cyber/component/timer_component.py` 上现有的 LatencyStats 采集,
理解其模式, 特别是 _run_loop 中的 elapsed_ms 测量和 circuit breaker。
可以从 Apollo `cyber/tools/cyber_monitor` 这个好例子开始。然后, 实现
`ProcProfiler`, 为每个 TimerComponent 的 Proc() 调用记录详细 profile
(wall time, CPU time, GC pause, 内存增量), 生成火焰图数据格式输出,
支持运行时开关 (默认关闭, 按需开启单个组件)。

## File 17 — runtime/config_hot_reload.py

查看 `conf/default_config.py` 上现有的 LolBotConfig 加载, 理解其模式。
可以从 `cyber/scheduler/scheduler.py` 的 hot_reload() 机制这个好例子开始。
然后, 实现 `ConfigHotReload`, 监听配置文件 mtime 变化, 解析 diff,
通过 `/lol/config_update` 频道广播变更, 让各组件动态调整参数
(如 prediction interval、voice cooldown) 而无需重启。

## File 18 — modules/control/overlay/overlay_protocol.py

查看 `modules/control/overlay/overlay_renderer.py` 上现有的 overlay element
管理, 理解其模式。可以从 `modules/dreamview/dashboard/dashboard_html.py`
的 HTML 模板生成这个好例子开始。然后, 实现 `OverlayProtocol`, 定义
OverlayElement / OverlayCommand / OverlayLayout 消息类型, 作为
planning → overlay_renderer 的类型安全通信协议, 支持 text / bar / timer /
icon 四种 element 类型。

## File 19 — scripts/log_analyzer.py

查看 `scripts/diagnostic_runner.py` 上现有的系统诊断, 理解其模式。可以从
`logs/` 目录下 JSONL 日志文件格式这个好例子开始。然后, 实现 `LogAnalyzer`,
读取所有 `logs/*.jsonl`, 解析时间线, 生成: 每组件 Proc() 延迟分布、
频道消息速率时间线、error 聚合 (top-10 by frequency)、session 时间线
(idle→game→post_game transitions), 输出 Markdown 报告。

## File 20 — scripts/smoke_test.py

查看 `tests/test_integration.py` 的手动节点测试和 `launch/mainboard.py --dry-run`
的初始化验证, 理解其模式。然后, 实现 `SmokeTest`, 一键端到端验证:
1) mainboard dry-run 初始化全部通过
2) 注入 3 帧 mock 数据, 验证 game_state 频道有输出
3) 验证 win_prediction 频道有输出
4) 验证 strategy_advice 频道有输出
5) 验证 voice_command 频道有输出
6) 输出 PASS/FAIL 和时序图

---

## Claude#8 接续任务

Claude#8 应继续实现:
- File 21-30: evolution 层集成 (将 evolution/ 下的 fitness_evaluator / 
  generation_manager / strategy_mutator 接入 SessionManager 的 post_game 回调)
- File 31-40: M 系列任务整合 (将 M1046-M1065 的 network_capture_engine、
  strategy_engine、evolution_controller 适配为 TimerComponent 子模块)

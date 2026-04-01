# lolbot-HyperAI Release Plan — Phase 2 (Files 21-40)
# ====================================================
#
# 前置: Phase 1 已完成 20 个核心文件 (8,398 行), 搭建了 Apollo 风格的
# canbus→perception→prediction→planning→voice 管线, 18/18 测试通过,
# dry-run 模式验证全部 5 个组件按依赖序初始化成功。
#
# Phase 2 目标: 补全管线中的功能缺口, 实现 ABC 接口落地, 接入 M 系列
# 已有模块的数据与逻辑, 使系统从 "骨架能跑" 升级为 "功能可用"。


## File 21 — modules/planning/item_build/item_build_advisor.py
# ItemBuildAdvisor — 上下文感知出装推荐引擎
#
# 查看 integrations/lol/src/lol_agent/item_build_advisor.py 上现有
# 多因子出装评分器的实现方式, 理解其模式, 特别是 item_pool 静态数据
# 和 score_item 评分函数 是如何与游戏状态查询分离的。
# 可以从 leagueoflegends-optimizer/notebooks/models_2025.ipynb 中
# 基于真实对局数据的出装路径分析 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ItemBuildAdvisor (Apollo planning 子模块),
# 让 PlanningComponent 可以 在每次 Proc() 中根据当前金币/对手阵容/游戏阶段
# 生成出装建议, 并能 通过 /lol/strategy_advice 频道将推荐结果推送给控制层。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 22 — modules/planning/macro/macro_planner.py
# MacroPlanner — 宏观决策引擎 (分推/团战/目标控制)
#
# 查看 dota2bot-OpenHyperAI/bots/ 上现有 mode_push / mode_team 决策树
# 的实现方式, 理解其模式, 特别是 mode 优先级评分和 desire 权重系统
# 是如何与实际指令执行分离的。
# 可以从 M1016 ObjectiveControlAnalyzer 的目标控制时机分析 这个好例子开始。
# 然后, 遵循该模式实现一个新的 MacroPlanner,
# 让 PlanningComponent 可以 基于当前阵亡时间/目标刷新/视野控制
# 输出 split/group/baron/dragon/defend 宏观指令,
# 并能 根据我方与敌方阵亡数差值动态调整指令紧迫度。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 23 — modules/prediction/objective/objective_timer.py
# ObjectiveTimer — 目标重生计时器 (龙/男爵/峡谷先锋)
#
# 查看 integrations/lol/src/lol_agent/objective_timer.py 上现有
# 龙/男爵计时器的实现方式, 理解其模式, 特别是 spawn_time 常量表
# 和 event-driven 计时启动 是如何与 UI 显示分离的。
# 可以从 modules/objective_tracker_abc.py 的统一接口定义 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ObjectiveTimer (实现 ObjectiveTrackerABC),
# 让 PredictionComponent 可以 监听 /lol/events 频道的目标击杀事件
# 并自动启动重生倒计时, 并能 在距离重生 60 秒时触发 VoiceCommand 提醒。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 24 — modules/prediction/team_fight/teamfight_predictor.py
# TeamfightPredictor — 团战预测模型 (独立模型文件)
#
# 查看 modules/prediction/prediction_component.py 上现有
# TeamfightAnalyzer 内联实现的方式, 理解其模式, 特别是存活人数
# 和阶段乘数 是如何与最终概率输出分离的。
# 可以从 M1017 TeamfightDetector 的基于事件窗口的团战检测 这个好例子开始。
# 然后, 遵循该模式实现一个新的 TeamfightPredictor (独立模型文件),
# 让 PredictionComponent 可以 使用更丰富的特征 (技能冷却/物品栏/召唤师技能)
# 预测团战胜率, 并能 输出 engage/disengage/poke 三种推荐行动及对应理由。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 25 — modules/control/overlay/overlay_renderer.py
# OverlayRenderer — 游戏内 HUD 叠加层渲染器
#
# 查看 integrations/lol/src/lol_agent/overlay_renderer.py 上现有
# 文字/矩形/标记覆盖元素管理器的实现方式, 理解其模式, 特别是
# TTL 自动过期和 max_elements 限制 是如何与渲染后端分离的。
# 可以从 LeagueAI/LeagueAI_helper.py 的 overlay 绘制模式 这个好例子开始。
# 然后, 遵循该模式实现一个新的 OverlayRenderer (Apollo control 子模块),
# 让 ControlComponent 可以 在屏幕上显示胜率/策略建议/目标倒计时,
# 并能 通过 /lol/overlay_commands 频道接收其他模块的显示请求。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 26 — modules/control/action_dispatch/action_dispatcher.py
# ActionDispatcher — 动作分发器 (语音+叠加层+日志统一出口)
#
# 查看 modules/control/voice_output/voice_narrator.py 上现有
# 优先级队列和去重机制的实现方式, 理解其模式, 特别是 _QueueItem
# 优先级排序和 is_expired 过期检测 是如何与 TTS 后端分离的。
# 可以从 integrations/lol/src/lol_agent/decision_engine.py 的
# 决策→动作分发模式 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ActionDispatcher,
# 让 PlanningComponent 的策略建议 可以 同时分发到语音/叠加层/日志三条通道,
# 并能 根据动作类型和紧迫度自动选择最合适的输出通道。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 27 — modules/perception/minimap/minimap_analyzer.py
# MinimapAnalyzer — 小地图状态分析器
#
# 查看 integrations/lol/src/lol_agent/minimap_annotator.py 上现有
# 小地图标注器的实现方式, 理解其模式, 特别是坐标归一化
# 和区域分类 (jungle/lane/river) 是如何与图像处理分离的。
# 可以从 M1019 DeathHeatmapGenerator 的地图坐标→区域映射 这个好例子开始。
# 然后, 遵循该模式实现一个新的 MinimapAnalyzer,
# 让 PerceptionComponent 可以 基于玩家位置数据判断阵线状态和视野盲区,
# 并能 输出 lane_pressure / jungle_control / ward_coverage 指标到 /lol/minimap_state。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 28 — modules/common/filters/kalman_filter.py
# KalmanFilter — 一维卡尔曼滤波器 (用于胜率/金币差平滑)
#
# 查看 cyber/timer/rate_timer.py 上现有 EMA (指数移动平均) 平滑器
# 的实现方式, 理解其模式, 特别是 alpha 参数和 tick() 递推
# 是如何与业务逻辑分离的。
# 可以从 Apollo modules/prediction/evaluator/ 的轨迹预测滤波 这个好例子开始。
# 然后, 遵循该模式实现一个新的 KalmanFilter (一维标量版),
# 让 PredictionComponent 可以 用更稳健的滤波替代简单 EMA 平滑胜率曲线,
# 并能 同时估计过程噪声和观测噪声以自动调节平滑强度。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 29 — modules/common/math/statistics.py
# GameStatistics — 游戏统计工具 (滑动窗口聚合)
#
# 查看 cyber/component/timer_component.py 上现有 LatencyStats 的实现方式,
# 理解其模式, 特别是 deque(maxlen=N) 滑动窗口和 p95/p99 百分位数计算
# 是如何与业务指标分离的。
# 可以从 M1015 GoldDiffTrendTracker 的金币差趋势追踪 这个好例子开始。
# 然后, 遵循该模式实现一个新的 GameStatistics 工具集,
# 让所有模块 可以 复用标准化的滑动窗口 min/max/mean/p95/trend 统计,
# 并能 支持多维时间序列 (金币/击杀/经验) 的并行追踪。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 30 — modules/common/util/proto_util.py
# ProtoUtil — 消息序列化/反序列化工具
#
# 查看 modules/common/adapters/game_messages.py 上现有
# frozen dataclass 消息定义的方式, 理解其模式, 特别是
# to_feature_dict() 和 @dataclass(frozen=True) 是如何与传输层分离的。
# 可以从 modules/common/status/error_code.py 的 to_dict/from_dict 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ProtoUtil,
# 让所有消息类型 可以 统一序列化为 JSON/msgpack/bytes 格式,
# 并能 支持版本化 schema 以兼容消息格式升级。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 31 — modules/common/adapters/abc_impl.py
# ABC 实现适配器 — 将 lolbot-HyperAI 组件适配到 operatorRL ABC 接口
#
# 查看 modules/game_bridge_abc.py + modules/strategy_advisor_abc.py 上现有
# 跨游戏抽象接口的实现方式, 理解其模式, 特别是 @abstractmethod
# 和 game_name property 是如何与游戏特定逻辑分离的。
# 可以从 integrations/lol/src/lol_agent/lol_strategy_advisor.py 的
# LoL 特定策略适配 这个好例子开始。
# 然后, 遵循该模式实现 LoLGameBridge / LoLStrategyAdvisor / LoLObjectiveTracker,
# 让 operatorRL 的统一接口 可以 通过 ABC 实现直接调用 lolbot-HyperAI 的组件,
# 并能 作为 modules/ 下 8 个 ABC 的 LoL 具体实现注册到系统。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 32 — modules/dreamview/dashboard/dashboard_html.py
# DashboardHTML — Dreamview 仪表盘 HTML 生成器
#
# 查看 modules/dreamview/api/dreamview_api.py 上现有 REST API
# 的实现方式, 理解其模式, 特别是 DreamviewState 共享状态
# 和 SSE 日志流 是如何与 HTTP 路由分离的。
# 可以从 Apollo modules/dreamview/frontend/ 的实时可视化界面 这个好例子开始。
# 然后, 遵循该模式实现一个新的 DashboardHTML (纯 Python 生成的单文件 HTML),
# 让用户 可以 在浏览器中实时查看胜率曲线/策略建议/组件健康状态,
# 并能 通过 SSE 自动刷新而无需手动轮询。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 33 — cyber/transport/shared_memory.py
# SharedMemoryTransport — 零拷贝大状态传输
#
# 查看 cyber/node/node.py 上现有 _Channel 发布/订阅的实现方式,
# 理解其模式, 特别是 _Subscriber.queue (deque) 消息缓冲
# 和 fan-out 分发 是如何与消息内容分离的。
# 可以从 Apollo cyber/transport/shm/ 的共享内存传输 这个好例子开始。
# 然后, 遵循该模式实现一个新的 SharedMemoryTransport,
# 让大体积消息 (如完整 GameSnapshot 含 10 名玩家数据) 可以
# 通过引用传递而非深拷贝来降低 GC 压力,
# 并能 支持 reader 端的 copy-on-read 语义保证线程安全。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 34 — modules/perception/events/kill_feed_analyzer.py
# KillFeedAnalyzer — 击杀流分析器 (连杀/多杀/一血检测)
#
# 查看 modules/perception/events/event_detector.py 上现有
# 团战检测的实现方式, 理解其模式, 特别是时间窗口滑动
# 和参与者集合聚合 是如何与事件类型判定分离的。
# 可以从 M1017 TeamfightDetector 的多杀/连杀检测逻辑 这个好例子开始。
# 然后, 遵循该模式实现一个新的 KillFeedAnalyzer,
# 让 PerceptionComponent 可以 检测连杀 (double/triple/quadra/penta)、
# 连续击杀 (killing spree)、和关键击杀 (shutdown),
# 并能 为每个检测到的模式生成带置信度和参与者列表的 DetectedPattern。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 35 — scripts/replay_simulator.py
# ReplaySimulator — 录像回放模拟器 (离线测试工具)
#
# 查看 tests/test_integration.py 上现有 make_synthetic_allgamedata()
# 合成数据生成器的实现方式, 理解其模式, 特别是 JSON 结构
# 和 _make_player 工厂函数 是如何与测试断言分离的。
# 可以从 M1008 MatchTimelineDeserializer 的时间线事件解析 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ReplaySimulator,
# 让开发者 可以 从保存的 allgamedata JSON 文件按时间序列回放游戏,
# 并能 以可配置的速度 (1x/2x/4x) 驱动整条管线运行。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 36 — tools/cli_monitor.py
# CLIMonitor — 终端实时监控工具
#
# 查看 modules/dreamview/api/dreamview_api.py 上现有
# DreamviewState.get_all() 状态快照的实现方式, 理解其模式,
# 特别是 JSON API 端点和状态聚合 是如何与前端展示分离的。
# 可以从 launch/mainboard.py 的 CLI argparse 模式 这个好例子开始。
# 然后, 遵循该模式实现一个新的 CLIMonitor,
# 让运维人员 可以 在终端中用 curses/ANSI 实时显示胜率/策略/组件状态,
# 并能 通过 HTTP 轮询 Dreamview API 获取数据而无需进入主进程。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 37 — modules/planning/strategy/lane_advisor.py
# LaneAdvisor — 对线阶段策略顾问
#
# 查看 integrations/lol/src/lol_agent/lane_matchup_predictor.py 上现有
# 对线胜率预测器的实现方式, 理解其模式, 特别是 champion_matchup
# 静态数据和 predict_lane_outcome 推理函数 是如何与游戏状态分离的。
# 可以从 M1013 LaneMatchupStatEngine 的对线统计引擎 这个好例子开始。
# 然后, 遵循该模式实现一个新的 LaneAdvisor,
# 让 PlanningComponent 可以 在早期阶段 (0-14 min) 给出具体的换血/补刀/回城建议,
# 并能 基于英雄 matchup 数据库调整建议的积极/保守程度。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 38 — modules/common/adapters/training_data_collector.py
# TrainingDataCollector — 训练数据收集管线
#
# 查看 modules/prediction/win_probability/win_predictor.py 上现有
# FeatureStore (SQLite) 的实现方式, 理解其模式, 特别是
# record() 写入和 export_training_data() 导出 是如何与模型训练分离的。
# 可以从 integrations/lol/src/lol_agent/training_data_sqlite.py 的
# 对局数据 SQLite 存储 这个好例子开始。
# 然后, 遵循该模式实现一个新的 TrainingDataCollector,
# 让系统 可以 在每局游戏中自动收集 (时间戳, 特征向量, 最终胜负) 三元组,
# 并能 在游戏结束后批量更新所有记录的 outcome 标签。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 39 — configs/pipeline.yaml + configs/config_loader.py
# ConfigLoader — YAML 配置加载器
#
# 查看 launch/mainboard.py 上现有 argparse CLI 参数的实现方式,
# 理解其模式, 特别是 CanbusConfig / LogConfig 等 dataclass 配置
# 和命令行参数 是如何与组件实例化分离的。
# 可以从 M866-M885/configs/ 目录下的 YAML 配置文件 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ConfigLoader + pipeline.yaml,
# 让运维人员 可以 通过修改 YAML 文件调整所有组件参数而无需改代码,
# 并能 支持环境变量覆盖和配置热重载。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。


## File 40 — tests/test_phase2.py
# Phase2 集成测试
#
# 查看 tests/test_integration.py 上现有 18 个测试用例的实现方式,
# 理解其模式, 特别是 reset_all_channels() 隔离和
# make_synthetic_allgamedata() 合成数据 是如何与断言逻辑分离的。
# 可以从 integrations/lol/tests/test_m346_m355.py 的 pytest 模式 这个好例子开始。
# 然后, 遵循该模式实现 Phase 2 的集成测试,
# 让 CI 可以 验证 File 21-39 的全部新功能 (出装/宏观/计时器/叠加层/滤波器),
# 并能 覆盖 ABC 实现适配器、配置加载、录像回放等边界场景。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

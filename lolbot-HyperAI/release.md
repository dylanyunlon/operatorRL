# lolbot-HyperAI Release — Claude#2 Batch (CAN Bus → Evolution)

> 16 production modules, 8,767 lines
> Author: dylanyunlong <dylanyunlong@gmail.com>

---

## 1. CAN Bus 数据总线层 (`canbus/`)

### 1.1 channel_message.py — 类型化消息定义与黑板总线

查看 `modules/amb`（Agent Message Bus）上现有 **发布/订阅消息传递** 的实现方式，理解其模式，特别是消息类型定义和频道路由是如何分离的。可以从 `M1046-M1065/capture/network_capture_engine.py` 中 `InterceptedRequest` 数据类 这个好例子开始。然后，遵循该模式实现一个新的 **ChannelMessage + MessageBus 黑板系统**，让 所有组件（感知/预测/规划/输出/演化）可以 通过35+命名频道发布和订阅类型化不可变消息，并能 在不直接import彼此的情况下完成全部数据交换（Apollo CAN bus解耦模式）。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `ChannelMessage` frozen dataclass: channel / timestamp_ms / sequence_id / payload / source_module / schema_version / priority / ttl_ms
- `MessageFactory`: 自动递增sequence_id，深拷贝payload防止别名
- `MessageBus`: latest-message-per-channel黑板语义、同步回调、asyncio.Event异步等待、历史环形缓冲区
- 35+频道常量（CH_WIN_PROBABILITY, CH_LIVE_GAME_STATE, CH_STRATEGY_RECOMMENDATION 等）
- `register_channel_schema()` / `validate_message()` schema注册与运行时校验

### 1.2 transport.py — 传输层（录制/回放/限速/诊断）

查看 `modules/amb` 上现有 **消息路由和传输** 的实现方式，理解其模式，特别是消息投递和错误处理是如何分离的。可以从 Apollo `cyber/transport` 和 `cyber/record/RecordWriter` 这个好例子开始。然后，遵循该模式实现一个新的 **Transport 装饰器层**，让 MessageBus 可以 增加令牌桶限速、JSONL录制（类rosbag）、回放、死信队列和延迟追踪，并能 在不修改底层MessageBus的情况下添加这些横切关注点。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `Transport`: 包装MessageBus的装饰器，publish()加限速+录制+延迟追踪
- `MessageRecorder`: JSONL写入 + gzip压缩，每100条flush
- `MessageReplayer`: 支持实时/快进/频道过滤/时间切片回放
- `DeadLetter` 队列：subscriber回调异常时捕获，不让总线崩溃
- `_RateLimit`: 令牌桶算法，默认100 msg/s/channel

---

## 2. 感知层 (`perception/`)

### 2.1 network_listener.py — 网络数据捕获（传感器驱动）

查看 `M1046-M1065/capture/network_capture_engine.py` 上现有 **Fiddler/LCU数据捕获** 的实现方式，理解其模式，特别是数据源检测和端点分类是如何分离的。可以从 `NetworkCaptureEngine.detect_mode()` 和 `EndpointCategory` 枚举 这个好例子开始。然后，遵循该模式实现一个新的 **NetworkListener组件**，让 CAN bus 可以 接收来自LCU lockfile自动检测、Live Client API (127.0.0.1:2999)、Fiddler代理或回放文件的数据，并能 以50ms Proc()周期按配置间隔轮询各端点、分类解析、发布到对应频道。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `LCUConnection`: 自动扫描lockfile路径 + wmic进程回退 + SSL自签证书处理 + Basic Auth
- `LiveClientAPI`: 游戏进行中127.0.0.1:2999的5个端点轮询
- `CaptureMode` 枚举: LCU_POLLING / LCU_WEBSOCKET / FIDDLER_PROXY / REPLAY_FILE / MOCK
- `EndpointCategory` + regex分类: GAMEFLOW / CHAMP_SELECT / LIVE_CLIENT_DATA / MATCH_HISTORY / SUMMONER
- 变化检测：hash去重避免重复发布、已处理event_id集合

### 2.2 game_state_parser.py — 游戏状态归一化融合

查看 `M1046-M1065/core/game_state_tracker.py` 上现有 **GamePhase状态机和GameContext** 的实现方式，理解其模式，特别是原始API数据和归一化状态是如何分离的。可以从 `GamePhase` 枚举和 `GameContext.to_dict()` 这个好例子开始。然后，遵循该模式实现一个新的 **GameStateParser融合组件**，让 下游模块（预测/规划/输出）可以 消费单一归一化的GameState而不需要理解Riot API原始schema，并能 以2Hz稳定速率发布融合状态、自动计算团队差值和动量分数。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `GamePhase` 枚举: NONE → LOBBY → CHAMP_SELECT → EARLY_LANING → LANING → MID_GAME → LATE_GAME → POST_GAME
- `PlayerState` / `TeamState` / `ObjectiveState` / `GameState` 四层数据模型
- `from_gameflow_and_time()`: 同时使用gameflow字符串和游戏时间判定阶段
- `momentum_score()`: 基于最近60秒击杀/目标事件的[-1,+1]动量评分
- 订阅5个原始频道 → 发布1个归一化CH_LIVE_GAME_STATE

---

## 3. 预测层 (`prediction/`)

### 3.1 feature_pipeline.py — ML特征提取管道

查看 `M1046-M1065/analysis/trend_analyzer.py` 上现有 **SessionSnapshot和指标计算** 的实现方式，理解其模式，特别是原始数据和衍生指标是如何分离的。可以从 `SessionSnapshot` 数据类和趋势计算 这个好例子开始。然后，遵循该模式实现一个新的 **FeaturePipeline特征提取器**，让 预测模型 可以 消费32维归一化特征向量（7类：金币/节奏/阵容/视野/结构/动量/时间），并能 提供可解释性（top_features排序、category_summary、feature_trend历史）。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `FeatureSpec` 冻结数据类: name / category / description / min_val / max_val
- 32个`FEATURE_SPECS`注册表，覆盖gold_economy(8) / tempo(6) / composition(5) / structural(6) / momentum(4) / time(3)
- `FeatureVector`: as_list() / as_dict() / top_features(n) / category_summary()
- `_normalize()` → [-1,1], `_normalize_01()` → [0,1]
- 英雄缩放/伤害类型字典用于阵容特征计算

### 3.2 win_probability_engine.py — 实时胜率预测引擎

查看 `M1046-M1065/strategy/strategy_engine.py` 上现有 **Recommendation生成和置信度评估** 的实现方式，理解其模式，特别是模型预测和可解释性输出是如何分离的。可以从 `Recommendation` 数据类和置信度字段 这个好例子开始。然后，遵循该模式实现一个新的 **WinProbabilityEngine预测引擎**，让 规划层和语音输出 可以 消费校准过的胜率预测（含趋势、关键因素、what-if场景分析），并能 通过演化控制器调整模型权重实现跨代自优化。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `LogisticModel`: 32个手工标定权重（gold_diff_norm=1.8最强）, sigmoid预测, feature_contributions可解释性
- `what_if_analysis()`: 7种假设场景（get_dragon, get_baron, team_wipe等）
- `TrendTracker`: 短期(30s)/长期(120s) EMA, rising/falling/stable检测
- `ConfidenceEstimator`: coverage × time_factor × stability加权
- 演化API: get/set_model_weights(), adjust_weight(), get_calibration_data()

---

## 4. 规划层 (`planning/`)

### 4.1 strategy_planner.py — 战术策略推荐引擎

查看 `M1046-M1065/strategy/strategy_engine.py` 上现有 **RecommendationType和优先级系统** 的实现方式，理解其模式，特别是推荐生成和冷却控制是如何分离的。可以从 `Recommendation` 数据类和 `Priority` 枚举 这个好例子开始。然后，遵循该模式实现一个新的 **StrategyPlanner多Advisor系统**，让 语音播报层 可以 接收带优先级、冷却控制、过期时间的战术建议（15种类型），并能 结合what-if分析为落后局面生成翻盘路径建议。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- 15个`RecType` + 4级`Priority`(LOW/MEDIUM/HIGH/CRITICAL)
- `CooldownManager`: 每类型独立冷却，CRITICAL绕过冷却
- `ObjectiveAdvisor`: 龙魂预警（敌方3龙→CRITICAL）、男爵机会检测（2人阵亡+己方0阵亡）
- `TempoAdvisor`: 动量转移告警、翻盘路径（调用what-if找最佳场景）
- `LaneAdvisor`: CS落后警告、回城时机建议
- 每tick最多发布2条，置信度阈值过滤

---

## 5. 输出层 (`output/`)

### 5.1 voice_announcer.py — 语音播报引擎

查看 `M1046-M1065/integration/voice_output_engine.py` 上现有 **TTSBackend和语音队列** 的实现方式，理解其模式，特别是TTS后端抽象和播报控制是如何分离的。可以从 `VoiceOutputEngine` 和 `TTSBackend` 枚举 这个好例子开始。然后，遵循该模式实现一个新的 **VoiceAnnouncer优先级播报系统**，让 玩家 可以 听到按优先级排序、去重、有冷却控制的语音战术建议，并能 自动检测最佳TTS后端（pyttsx3/Windows SAPI/macOS say/espeak）并在后台线程异步播报。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `TTSEngine`: 4种后端自动检测，后台线程worker，非阻塞speak()
- `VoiceAnnouncer`: 优先级排序队列（CRITICAL优先）、30秒去重窗口、相位感知
- 定期胜率播报（60秒间隔），仅在游戏进行中
- mute/unmute控制，force_announce绕过队列
- 最小播报间隔5秒（可演化调整）

---

## 6. 演化层 (`evolution/`)

### 6.1 fitness_evaluator.py — 适应度评估器

查看 `M1046-M1065/core/evolution_controller.py` 上现有 **EvolutionProposal和日志分析** 的实现方式，理解其模式，特别是指标收集和评分逻辑是如何分离的。可以从 `EvolutionProposal` 数据类和 `confidence` 字段 这个好例子开始。然后，遵循该模式实现一个新的 **FitnessEvaluator多维评分系统**，让 演化控制器 可以 获得量化的[0,1]适应度分数来决定commit还是rollback一代，并能 从CAN bus历史或录制文件离线评估。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- 5个适应度维度: prediction(0.30权重) / recommendation(0.25) / health(0.20) / coverage(0.15) / engagement(0.10)
- `PredictionAccuracyMetrics`: Brier score + ECE(Expected Calibration Error) + 趋势准确率
- `RecommendationMetrics`: 类型覆盖率 / 阶段覆盖率 / 时机评分 / 每分钟推荐量（最优1.5/min）
- `SystemHealthMetrics`: 错误率 / 可用性 / P95延迟
- `_update_calibration()`: 用实际游戏胜负回溯更新预测校准桶

### 6.2 generation_manager.py — 代际生命周期管理器

查看 `M1046-M1065/core/evolution_controller.py` 上现有 **程序A→A'切换逻辑** 的实现方式，理解其模式，特别是参数快照和回滚控制是如何分离的。可以从 `EvolutionProposal.parameters` 字典和 `applied` 标志 这个好例子开始。然后，遵循该模式实现一个新的 **GenerationManager完整生命周期控制器**，让 演化循环 可以 checkpoint所有可调参数 → 应用变异 → 评估 → commit/rollback，并能 在磁盘上持久化每代快照（JSON + symlink "current"）、自动剪枝到50代。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `GenerationSnapshot`: prediction_weights / prediction_bias / recommendation_cooldowns / thresholds / fitness_scores 全部可调参数
- `MutationProposal`: category / target_param / old_value / new_value / rationale / confidence
- 生命周期: init() → apply_mutations() → evaluate → commit()/rollback()
- 磁盘结构: `data/generations/gen_XXXX_hash/snapshot.json`, symlink "current"
- `should_commit()`: 保守策略，新代适应度必须超过旧代+threshold才提交

### 6.3 strategy_mutator.py — 策略变异器（LLM修复酶）

查看 `M1046-M1065/core/evolution_controller.py` 上现有 **日志分析→修改建议** 的实现方式，理解其模式，特别是问题诊断和修改提案是如何分离的。可以从 plan.md §二 中「LLM（修复酶）→ 看日志，建议修改」这个好例子开始。然后，遵循该模式实现一个新的 **StrategyMutator多策略变异器**，让 演化循环 可以 从适应度分析自动生成针对性变异提案（而非随机搜索），并能 使用4种变异策略（梯度/校准/冷却调优/探索）逐步改进系统。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `FitnessDiagnosis`: 找到最弱维度 + 具体问题列表 + 建议策略
- `GRADIENT`: 正则化极端权重（|w|>1.5时回缩0.1）
- `CALIBRATION`: 修正prediction_bias + 调整min_recommendation_confidence
- `COOLDOWN_TUNE`: 播报过少→降低冷却，过多→增加冷却+间隔
- `EXPLORATION`: 高斯扰动(σ=0.1)随机1-2个权重，15%概率触发
- 每代最多5个变异提案，避免同时改太多

---

## 7. 集成层 (`integration/`)

### 7.1 riot_api_client.py — Riot Games API客户端

查看 `M1046-M1065/history/match_data_crawler.py` 上现有 **Riot API调用和数据缓存** 的实现方式，理解其模式，特别是API调用和数据转换是如何分离的。可以从 `HistoricalMatchCrawler` 和 `HistoricalDataCache` 这个好例子开始。然后，遵循该模式实现一个新的 **RiotAPIClient完整API封装**，让 特征管道和演化控制器 可以 获取召唤师信息、对战历史、英雄数据和实时观战数据，并能 自动处理Riot的频率限制（20/1s, 100/2min）、区域路由、指数退避重试和LRU缓存。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `Region`(16个) + `Cluster`(4个) 枚举及映射
- `RateLimiter`: 双窗口令牌桶（短1s + 长2min）
- `LRUCache`: OrderedDict实现, TTL=30min, max_size=500
- `fetch_training_data()`: 批量获取ranked match并转换为简化训练格式
- Data Dragon CDN: 英雄数据/装备数据（无需API key）

### 7.2 agent_os_connector.py — Agent OS治理内核桥接

查看 `src/agent_os/stateless.py` 上现有 **StatelessKernel策略执行** 的实现方式，理解其模式，特别是策略检查和执行是如何分离的。可以从 `src/agent_os/integrations/agent_lightning/reward.py` 中 `PolicyReward.__call__()` 这个好例子开始。然后，遵循该模式实现一个新的 **AgentOSConnector桥接层**，让 lolbot-HyperAI 可以 接入operatorRL治理内核的策略执行和奖励信号，并能 在agent_os不可用时优雅降级为ungoverned模式（内置速率限制和权重幅度限制）。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `GovernanceMode`: UNGOVERNED / GOVERNED / DRY_RUN三种模式
- `check_policy()`: 变异频率限制(10/hour) / 权重变化幅度限制(delta<1.0) / API调用频率限制
- `RewardSignal`: value[-1,+1] / category / source / details
- `report_game_outcome()` / `report_prediction_quality()` → 累积奖励
- `_try_import_kernel()`: 自动检测agent_os可用性并切换模式

---

## 8. 配置/协议/启动 (`conf/`, `proto/`, `launch/`)

### 8.1 default_config.py — 系统配置中心

查看 `M1046-M1065/config/config_manager.py` 上现有 **配置加载和默认值** 的实现方式，理解其模式，特别是配置定义和运行时覆盖是如何分离的。可以从 `ConfigManager` 这个好例子开始。然后，遵循该模式实现一个新的 **LolBotConfig分层配置系统**，让 所有模块 可以 从单一配置对象获取参数，并能 通过环境变量(LOLBOT_前缀)、JSON文件、代际快照三层覆盖。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- 8个`@dataclass`配置段: transport / perception / prediction / planning / output / evolution / integration / system
- 覆盖优先级: runtime > generation > config file > env > defaults
- `_apply_env_overrides()`: 类型感知转换（bool/int/float/str）
- `get_config()` / `set_config()` 全局单例
- RIOT_API_KEY特殊处理从环境变量

### 8.2 lolbot_messages.py — 消息协议定义

查看 Apollo `modules/common_msgs/*.proto` 上现有 **protobuf消息schema** 的实现方式，理解其模式，特别是字段定义和运行时验证是如何分离的。可以从 `canbus/channel_message.py` 中 `register_channel_schema()` 这个好例子开始。然后，遵循该模式实现一个新的 **完整协议注册表**，让 演化兼容性检查 可以 在运行时验证变异后的模块是否仍产生合法消息，并能 作为全系统的消息文档（publisher/subscribers/字段类型/范围约束）。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `FieldSpec` 冻结数据类: name / field_type / required / description / min_value / max_value
- `MessageSchema`: validate() → 类型检查 + 范围检查 + 必填检查
- 13个频道schema完整注册（perception 6个 + prediction 1个 + planning 1个 + output 1个 + evolution 2个 + system 2个）
- `schema_summary()`: 全部频道的文档化摘要

### 8.3 main_loop.py — Apollo风格主循环入口

查看 `M1046-M1065/orchestrator.py` 上现有 **系统编排和启动序列** 的实现方式，理解其模式，特别是组件初始化和主循环是如何分离的。可以从 `Orchestrator` 类的启动序列注释 这个好例子开始。然后，遵循该模式实现一个新的 **MainLoop Apollo风格while-true入口**，让 整个lolbot-HyperAI系统 可以 以10Hz(100ms)周期按 Perception→Prediction→Planning→Output 顺序执行所有组件的Proc()，并能 管理session状态机（IDLE→PRE_GAME→IN_GAME→POST_GAME→EVOLVING）在游戏结束后自动触发演化。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

**实现要点**：
- `_init_components()`: 创建并连线全部组件（bus→transport→listener→parser→features→engine→planner→announcer→evaluator→manager→mutator→connector）
- `_tick()`: Perception → Prediction → Planning → Output 顺序执行
- `SessionState` 状态机: IDLE ↔ PRE_GAME → IN_GAME → POST_GAME → IDLE
- `_evolve()`: evaluate fitness → diagnose → propose mutations → check policy → apply → commit
- `_apply_generation()`: 将代际快照参数注入到活跃组件
- SIGINT/SIGTERM优雅关闭 + 录制压缩 + 最终统计输出

---

## 与 Claude#1 的关系

| 本批次目录 | Claude#1 目录 | 关系 |
|---|---|---|
| `canbus/` | `modules/canbus/` | **互补**：我的是消息定义和总线，他的是LCU轮询组件 |
| `perception/` | `modules/perception/` | **互补**：我的是融合归一化，他的是事件检测 |
| `prediction/` | `modules/prediction/` | **互补**：我的是特征+引擎，他的是组件封装 |
| `planning/` | `modules/planning/` | **互补**：我的是多Advisor系统，他的是组件封装 |
| `output/` | `modules/control/` | **互补**：我的是播报逻辑，他的是TTS封装 |
| `evolution/` | — | **独有**：演化三件套（评估/代管/变异） |
| `integration/` | — | **独有**：Riot API + Agent OS桥接 |
| `conf/` + `proto/` | `modules/common/` | **互补**：我的是配置+协议，他的是类型+状态码 |
| `launch/main_loop.py` | `launch/mainboard.py` | **互补**：两个入口点，可选择使用 |

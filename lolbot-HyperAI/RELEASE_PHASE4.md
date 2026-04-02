# lolbot-HyperAI Release Plan — Phase 4 (Files 58-77)
# ====================================================
#
# 前置: Phase 1 已完成 20 个核心文件 (8,398 行), 搭建了 Apollo 风格的
# canbus→perception→prediction→planning→voice 管线, 18/18 测试通过。
# Phase 2 已描述 20 个功能补全文件 (RELEASE.md), 部分已实现为骨架。
# Phase 3 已完成 17 个生产文件 (7,701 行新代码), 覆盖宏观规划、
# 团战预测、配置加载、诊断系统、共享内存传输等核心基础设施。
#
# Phase 4 目标: 补齐 Apollo 架构对标中缺失的基础设施层,
# 新增 CyberRT 录制/回放、RPC 服务、运行时统计、系统监控、
# 叙事解说、地图感知、坐标变换、回放驱动、模型标定、
# A/B 测试、通道注册表、DAG 启动器、配置加载器、
# 插眼追踪、选秀分析、技能冷却追踪共 20 个生产文件。
#
# Phase 4 总计: 20 个生产文件, 7,423 行新代码, 20/20 编译通过,
# 全部 import 解析正确, 遵循 TimerComponent Init()/Proc() 模式。


## File 58 — cyber/record/record_writer.py (674 lines)
# RecordWriter — Apollo 风格消息录制持久化
#
# 查看 Apollo cyber/record/record_writer.h 上现有 WriteChannel()
# 的实现方式, 理解其模式, 特别是 header + JSONL body + index
# 文件结构 是如何与运行时消息总线分离的。
# 可以从 Apollo cyber/record/record_base.h 的 chunk 布局
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 RecordWriter,
# 让 CyberNode 消息可以 被录制到 .cyberrecord + .cyberindex 文件对,
# 并能 通过后台线程缓冲写入、文件轮转、gzip 压缩实现高效持久化。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: RecordHeader (二进制序列化) + IndexEntry (O(1) 时间戳寻址)
# + RecordWriter (线程安全缓冲 + 后台 flush + 频道过滤 + 轮转)。
# 位置: cyber/record/record_writer.py


## File 59 — cyber/record/record_reader.py (542 lines)
# RecordReader — 从 .cyberrecord 回放消息
#
# 查看 cyber/record/record_writer.py 上现有 RecordWriter 写入格式
# 的实现方式, 理解其模式, 特别是 header 解析、index 寻址
# 是如何与 JSONL 消息体分离的。
# 可以从 Apollo cyber/record/record_reader.h 的 ReadMessage()
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 RecordReader,
# 让录制文件可以 按时间片段、频道过滤回放,
# 并能 通过 index 文件实现 O(1) 时间戳定位和任意位置 seek。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: RecordReader (header 解析 + index 加载 + 迭代器回放
# + 时间片过滤 + 频道过滤 + gzip 透明解压)。
# 位置: cyber/record/record_reader.py


## File 60 — cyber/service/service.py (370 lines)
# CyberService — Apollo 风格 RPC 请求-响应模式
#
# 查看 Apollo cyber/service/service.h 上现有 Service<Request, Response>
# 的实现方式, 理解其模式, 特别是 请求路由、超时控制、
# 回调注册 是如何与底层传输分离的。
# 可以从 CyberNode 的 pub/sub 通道
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 CyberService,
# 让模块间可以 通过 request()/handle() 进行同步 RPC 调用,
# 并能 通过 Future 模式支持异步调用和超时控制。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: ServiceRequest/ServiceResponse (消息封装) + CyberService
# (路由注册 + Future 超时 + 并发处理 + 统计计数)。
# 位置: cyber/service/service.py


## File 61 — cyber/statistics/statistics.py (289 lines)
# Statistics — Apollo 风格运行时性能统计
#
# 查看 Apollo cyber/statistics/statistics.h 上现有 Statistics
# 的实现方式, 理解其模式, 特别是 滑动窗口、百分位计算
# 是如何与组件主循环分离的。
# 可以从 TimerComponent 内置的 LatencyStats
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 Statistics 子系统,
# 让各组件可以 注册命名计数器/直方图/Gauge,
# 并能 通过全局注册表聚合并导出 Prometheus 格式指标。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: Counter + Histogram + Gauge + StatisticsRegistry
# (线程安全注册 + 滑动窗口 + 百分位 + Prometheus 导出)。
# 位置: cyber/statistics/statistics.py


## File 62 — modules/monitor/monitor_component.py (350 lines)
# MonitorComponent — 系统健康守护 TimerComponent
#
# 查看 Apollo modules/monitor/monitor.cc 上现有 Monitor
# 的实现方式, 理解其模式, 特别是 健康检查调度、
# 阈值告警 是如何与具体资源监控分离的。
# 可以从 TimerComponent 的 Init()/Proc() 模式
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 MonitorComponent,
# 让系统可以 周期性检测所有注册组件的健康状态,
# 并能 通过发布 /lol/monitor 频道触发告警和自动恢复。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: HealthCheck (检查项抽象) + MonitorComponent
# (TimerComponent 继承 + 组件心跳 + 阈值告警 + 恢复动作)。
# 位置: modules/monitor/monitor_component.py


## File 63 — modules/monitor/resource_tracker.py (214 lines)
# ResourceTracker — CPU/内存/线程/GC 监控
#
# 查看 modules/monitor/monitor_component.py 上现有 MonitorComponent
# 的实现方式, 理解其模式, 特别是 健康检查接口
# 是如何与具体资源采集分离的。
# 可以从 Python psutil 风格的 os 模块资源采集
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ResourceTracker,
# 让 MonitorComponent 可以 定期采集 CPU/内存/线程/GC 指标,
# 并能 通过阈值判断触发资源告警。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: ResourceSnapshot (数据类) + ResourceTracker
# (stdlib os/threading/gc 采集 + 滑动窗口 + 阈值告警)。
# 位置: modules/monitor/resource_tracker.py


## File 64 — modules/storytelling/game_narrator.py (402 lines)
# GameNarrator — 事件→叙事生成引擎
#
# 查看 Apollo modules/storytelling/storytelling_component.cc 上现有
# 叙事生成的实现方式, 理解其模式, 特别是 事件优先级排序、
# 冷却去重 是如何与模板渲染分离的。
# 可以从 modules/common/adapters/game_messages.py 的 EventType 枚举
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 GameNarrator,
# 让各类游戏事件可以 被转换为自然语言解说段落,
# 并能 通过语气适配和去重机制避免重复播报。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: NarrationSegment (输出数据类) + GameNarrator
# (事件路由 + 优先级队列 + 冷却去重 + 上下文语气适配)。
# 位置: modules/storytelling/game_narrator.py


## File 65 — modules/storytelling/commentary_template.py (265 lines)
# CommentaryTemplate — 模板化解说引擎
#
# 查看 modules/storytelling/game_narrator.py 上现有 GameNarrator
# 的实现方式, 理解其模式, 特别是 事件类型到文本模板的映射
# 是如何与变量插值分离的。
# 可以从 Python string.Template 的变量替换模式
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 CommentaryTemplate 引擎,
# 让每种事件类型可以 有多个可选模板 (避免重复),
# 并能 通过条件分支和变量绑定生成多样化解说文本。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: TemplateSlot (变量绑定) + CommentaryTemplate
# (模板注册 + 随机选取 + 条件过滤 + 变量插值 + 历史去重)。
# 位置: modules/storytelling/commentary_template.py


## File 66 — modules/localization/map_awareness.py (317 lines)
# MapAwareness — 玩家区域追踪 + 野区象限感知
#
# 查看 Apollo modules/localization/localization_component.cc 上现有
# 定位组件的实现方式, 理解其模式, 特别是 坐标到区域的映射
# 是如何与导航决策分离的。
# 可以从 LoL 地图的三路 + 四个野区象限 + 河道区域划分
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 MapAwareness 模块,
# 让系统可以 追踪每个玩家当前所在的地图区域,
# 并能 通过区域变化事件通知下游模块。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: MapZone (区域定义) + PlayerPosition (追踪状态)
# + MapAwareness (区域分类 + 玩家追踪 + 区域变化检测)。
# 位置: modules/localization/map_awareness.py


## File 67 — modules/localization/fog_estimator.py (278 lines)
# FogEstimator — 战争迷雾估计
#
# 查看 modules/perception/ward_tracker/ward_tracker.py 上现有
# 插眼追踪的实现方式, 理解其模式, 特别是 视野半径和区域覆盖
# 是如何与地图格子分离的。
# 可以从 MapAwareness 的区域划分
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 FogEstimator,
# 让系统可以 从已知的眼位 + 友方英雄位置估计视野覆盖率,
# 并能 输出各区域的 "可见" / "可能可见" / "未知" 三级状态。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: FogCell (格子状态) + FogEstimator
# (视野源聚合 + 格子化覆盖计算 + 区域级汇总)。
# 位置: modules/localization/fog_estimator.py


## File 68 — modules/transform/coordinate_transform.py (253 lines)
# CoordinateTransform — 小地图↔游戏坐标变换
#
# 查看 Apollo modules/transform/transform_component.cc 上现有
# 坐标变换的实现方式, 理解其模式, 特别是 旋转/平移/缩放矩阵
# 是如何与传感器标定参数分离的。
# 可以从 LoL 小地图像素坐标与游戏世界坐标的映射关系
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 CoordinateTransform,
# 让系统可以 在小地图像素坐标和游戏世界坐标之间双向转换,
# 并能 通过标定参数支持不同分辨率和 HUD 配置。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: MapCalibration (标定参数) + CoordinateTransform
# (仿射变换 + 双向转换 + 区域查询 + 距离计算)。
# 位置: modules/transform/coordinate_transform.py


## File 69 — modules/drivers/replay_driver.py (185 lines)
# ReplayDriver — 从 .cyberrecord 回放数据驱动
#
# 查看 cyber/record/record_reader.py 上现有 RecordReader 回放
# 的实现方式, 理解其模式, 特别是 时间戳迭代和频道过滤
# 是如何与实际数据消费分离的。
# 可以从 TimerComponent 的 Proc() 驱动循环
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ReplayDriver,
# 让录制文件可以 作为虚拟数据源驱动整个管线 (替代实时 LCU),
# 并能 通过倍速/暂停/跳转控制回放速率。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: ReplayDriver (TimerComponent 继承 + RecordReader 消费
# + 速率控制 + 频道注入 + 状态管理)。
# 位置: modules/drivers/replay_driver.py


## File 70 — modules/calibration/model_calibrator.py (318 lines)
# ModelCalibrator — 预测模型标定 + Platt 缩放
#
# 查看 Apollo modules/calibration/calibration_component.cc 上现有
# 传感器标定的实现方式, 理解其模式, 特别是 ground truth 收集
# 和误差修正 是如何与在线推理分离的。
# 可以从 modules/prediction/win_probability/ 的概率输出
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ModelCalibrator,
# 让预测模块输出的概率可以 通过 Platt 缩放进行事后标定,
# 并能 在线收集预测-结果对用于持续校准。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: CalibrationSample (样本对) + PlattScaler (逻辑回归拟合)
# + ModelCalibrator (在线收集 + 定期重训 + 标定参数持久化)。
# 位置: modules/calibration/model_calibrator.py


## File 71 — modules/calibration/ab_test_manager.py (322 lines)
# ABTestManager — 进化代际 A/B 测试
#
# 查看 evolution/generation_manager.py 上现有 代际管理
# 的实现方式, 理解其模式, 特别是 策略变异和适应度评估
# 是如何与实际对战分离的。
# 可以从 统计学 A/B 测试的 样本量计算 + 置信区间
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ABTestManager,
# 让进化系统可以 对不同代际策略进行受控对比实验,
# 并能 通过统计显著性检验决定是否推广新策略。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: ExperimentConfig (实验参数) + ABTestResult (统计结果)
# + ABTestManager (流量分配 + 样本收集 + z-test/t-test + 自动决策)。
# 位置: modules/calibration/ab_test_manager.py


## File 72 — modules/common/proto/channel_registry.py (303 lines)
# ChannelRegistry — 集中式频道名 + 模式注册表
#
# 查看 cyber/node/node.py 上现有 CyberNode pub/sub 频道
# 的实现方式, 理解其模式, 特别是 频道名称字符串
# 是如何与消息类型绑定分离的。
# 可以从 Apollo cyber/proto/topology_change.proto 的频道发现
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ChannelRegistry,
# 让所有频道名可以 在一个集中注册表中管理 (防止拼写错误),
# 并能 提供类型安全的频道引用和运行时拓扑查询。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: ChannelDef (频道定义) + ChannelRegistry
# (注册/查询 + 类型校验 + 拓扑图 + 依赖分析)。
# 位置: modules/common/proto/channel_registry.py


## File 73 — launch/dag_launcher.py (253 lines)
# DAGLauncher — DAG 依赖图启动器
#
# 查看 launch/main_loop.py 上现有 MainLoop 顺序启动
# 的实现方式, 理解其模式, 特别是 组件初始化顺序
# 是如何与实际 Proc() 调度分离的。
# 可以从 Apollo cyber/mainboard/module_controller.cc 的 DAG 加载
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 DAGLauncher,
# 让组件可以 声明依赖关系并按拓扑排序启动,
# 并能 通过并行初始化和分层启动加速系统冷启动。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: DAGNode (依赖声明) + DAGLauncher
# (拓扑排序 + 环检测 + 并行初始化 + 分层启动/关闭)。
# 位置: launch/dag_launcher.py


## File 74 — launch/component_config.py (263 lines)
# ComponentConfigLoader — YAML→ComponentConfig 加载器
#
# 查看 cyber/component/timer_component.py 上现有 ComponentConfig
# 数据类的实现方式, 理解其模式, 特别是 name/interval_ms/enable_latency_stats
# 是如何与运行时配置分离的。
# 可以从 Apollo cyber/conf/cyber.pb.conf 的配置文件格式
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 ComponentConfigLoader,
# 让系统可以 从 YAML/JSON 文件加载完整的组件配置,
# 并能 通过环境变量覆盖和配置合并支持多环境部署。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: ComponentConfigLoader (YAML/JSON 加载 + 环境变量覆盖
# + 配置验证 + 默认值合并 + 热重载)。
# 位置: launch/component_config.py


## File 75 — modules/perception/ward_tracker/ward_tracker.py (558 lines)
# WardTracker — 实时插眼生命周期和视野覆盖追踪
#
# 查看 Apollo modules/perception/lidar/segmentation 上现有
# 目标生命周期追踪的实现方式, 理解其模式, 特别是
# 目标创建/更新/过期 是如何与检测输入分离的。
# 可以从 TimerComponent 的 Init()/Proc() 驱动模式
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 WardTracker,
# 让系统可以 追踪每个眼位的类型/位置/持续时间/过期时间,
# 并能 聚合计算各区域的视野覆盖评分。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: WardInstance (生命周期状态) + WardMapSnapshot (快照输出)
# + WardTracker (TimerComponent + 事件消费 + 过期淘汰 + 视野评分)。
# 位置: modules/perception/ward_tracker/ward_tracker.py


## File 76 — modules/prediction/draft/draft_analyzer.py (638 lines)
# DraftAnalyzer — 英雄选秀胜率分析
#
# 查看 modules/prediction/prediction_component.py 上现有
# 预测组件的实现方式, 理解其模式, 特别是 概率输出
# 是如何与特征工程分离的。
# 可以从 TimerComponent 的 Init()/Proc() 驱动模式
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 DraftAnalyzer,
# 让系统可以 基于英雄对位胜率矩阵分析选秀阶段的胜率,
# 并能 推荐最优 ban/pick 策略。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: ChampionStats (英雄数据) + MatchupMatrix (对位矩阵)
# + DraftAnalyzer (TimerComponent + 组合评估 + ban/pick 推荐)。
# 位置: modules/prediction/draft/draft_analyzer.py


## File 77 — modules/control/cooldown_tracker/cooldown_tracker.py (629 lines)
# CooldownTracker — 召唤师技能 + 大招冷却追踪
#
# 查看 modules/common/adapters/game_messages.py 上现有
# PlayerAbilities/PlayerState 的实现方式, 理解其模式, 特别是
# spell_d/spell_f 和 r_level 是如何与冷却时间计算分离的。
# 可以从 TimerComponent 的 Init()/Proc() 驱动模式
# 这个好例子开始。
# 然后, 遵循该模式实现一个新的 CooldownTracker,
# 让系统可以 追踪所有玩家的召唤师技能和大招冷却状态,
# 并能 预估敌方技能可用窗口支持团战决策。
# 从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。
#
# 实现: SpellCooldown (单技能状态) + PlayerCooldowns (全技能汇总)
# + CooldownTracker (TimerComponent + 事件驱动 + 冷却缩减计算)。
# 位置: modules/control/cooldown_tracker/cooldown_tracker.py


# ─── Phase 4 总结 ──────────────────────────────────────────────────────
#
# Phase 4 新增文件列表:
#   58. cyber/record/record_writer.py          (674 lines)
#   59. cyber/record/record_reader.py          (542 lines)
#   60. cyber/service/service.py               (370 lines)
#   61. cyber/statistics/statistics.py         (289 lines)
#   62. modules/monitor/monitor_component.py   (350 lines)
#   63. modules/monitor/resource_tracker.py    (214 lines)
#   64. modules/storytelling/game_narrator.py  (402 lines)
#   65. modules/storytelling/commentary_template.py (265 lines)
#   66. modules/localization/map_awareness.py  (317 lines)
#   67. modules/localization/fog_estimator.py  (278 lines)
#   68. modules/transform/coordinate_transform.py (253 lines)
#   69. modules/drivers/replay_driver.py       (185 lines)
#   70. modules/calibration/model_calibrator.py (318 lines)
#   71. modules/calibration/ab_test_manager.py (322 lines)
#   72. modules/common/proto/channel_registry.py (303 lines)
#   73. launch/dag_launcher.py                 (253 lines)
#   74. launch/component_config.py             (263 lines)
#   75. modules/perception/ward_tracker/ward_tracker.py (558 lines)
#   76. modules/prediction/draft/draft_analyzer.py (638 lines)
#   77. modules/control/cooldown_tracker/cooldown_tracker.py (629 lines)
#
# 总行数: 7,423 行
# 编译检查: 20/20 通过
# Import 解析: 20/20 通过
# Apollo 对标覆盖: cyber/record, cyber/service, cyber/statistics,
#   modules/monitor, modules/storytelling, modules/localization,
#   modules/transform, modules/drivers, modules/calibration,
#   modules/common/proto, launch (DAG+config),
#   perception/ward_tracker, prediction/draft, control/cooldown_tracker

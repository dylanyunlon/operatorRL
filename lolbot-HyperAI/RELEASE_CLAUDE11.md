# RELEASE_CLAUDE11.md — 结构对标审查 + 20模块生产级重构

> Claude11 · 基于 Apollo canbus_component.cc / Seraphine connector.py 实际代码对比
> Author: dylanyunlong <dylanyunlong@gmail.com>

---

## 0. 真实对比发现 (diff audit)

### 0.1 Apollo `canbus_component.cc` 的 Init()/Proc() 模式

从 `ApolloAuto/apollo/modules/canbus/canbus_component.cc` 上现有 **CanbusComponent** 的实现方式，理解其模式，特别是 **Init() 配置加载与 Proc() 数据发布** 是如何分离的。

Apollo 的 Proc() 只有 3 行：
```cpp
bool CanbusComponent::Proc() {
    PublishChassis();
    if (FLAGS_enable_chassis_detail_pub) { PublishChassisDetail(); }
    return true;
}
```

Init() 负责：加载 protobuf 配置 → 创建 vehicle_object (factory) → 启动 CAN 收发线程 → 订阅 control_command reader。

**关键设计哲学**：Proc() 极度精简，所有状态管理、重连逻辑、错误恢复都在 Init() 或独立组件中处理，Proc() 本身 **永远不应该失败**。

### 0.2 我们的 `canbus_component.py` 的问题

| 维度 | Apollo | 我们 (当前) | 差距 |
|---|---|---|---|
| `Proc()` 行数 | 3行 | 70+行 (含重连/backoff/stale检测) | Proc()塞了太多非数据流逻辑 |
| 配置管理 | `conf/*.pb.txt` + proto | 空的 `conf/` 目录 + 硬编码常量 | 没有外部配置文件 |
| DAG 编排 | `dag/*.dag` 文件声明组件依赖 | `dag_launcher.py` (代码式) | 缺少声明式DAG |
| 测试数据 | `testdata/` 目录 | 无 | 无法离线测试 |
| vehicle 适配层 | `vehicle/` factory模式适配不同车型 | 写死LCU一种数据源 | 缺少数据源工厂 |
| 消息定义 | `proto/*.proto` → 自动生成 | Python dataclass (无schema约束) | 无schema验证 |

### 0.3 Seraphine `connector.py` 的模式

从 `Seraphine/app/lol/connector.py` 上现有 **LCU连接器** 的实现方式，理解其模式，特别是 **retry装饰器与PastRequest日志** 是如何与HTTP session分离的。

Seraphine 的核心模式：
1. `@needLcu` 装饰器 — 统一检查连接状态，失败时抛异常而非返回None
2. `@retry(count=3, backoff=0.5)` — 装饰器式重试，不污染业务逻辑
3. `PastRequest` 环形缓冲 — 所有请求自动记录，用于调试回放
4. `SGPConnector` / `LCUConnector` 双路径 — 国服/国际服透明切换

**我们的 `lcu_connector.py` 的问题**：
- 重试逻辑内联在 `_http_get` 中（不像 Seraphine 的装饰器分离）
- 无 PastRequest 日志（调试困难）
- `health_check()` 在调用端做，而不是装饰器自动触发
- 无连接池（每次请求新建SSL握手）

### 0.4 M系列任务文件夹的根本问题

13个M系列文件夹共约475个.py文件，全部是 **独立孤岛**：
- 每个文件夹自成一体，与 `lolbot-HyperAI/modules/` 无import关系
- 没有接入 canbus MessageBus
- 没有实现 `Init()/Proc()` 组件接口
- 没有 DAG 声明
- 大量代码重复（每个文件夹都重写了 LCU 连接、JSON 解析等）

---

## 1. 重构规范（20个文件）

以下每个文件遵循模板：

> 查看 **A** 上现有 **B** 的实现方式，理解其模式，特别是代码和接口是如何分离的。
> 从 **C** 这个好例子开始。然后，遵循该模式实现一个新的 **D**，让 **E** 可以 **F**，并能 **G**。

---

### 1.1 `modules/canbus/conf/canbus_conf.py` — 配置中心 (新建)

查看 Apollo `modules/canbus/conf/canbus_conf.pb.txt` 上现有 **protobuf文本格式配置** 的实现方式，理解其模式，特别是配置加载和默认值是如何分离的。从 Apollo 的 `GetProtoConfig(&canbus_conf_)` 这个好例子开始。然后，遵循该模式实现一个新的 `CanbusConf` dataclass + YAML加载器，让 canbus_component 可以 从 `conf/canbus.yaml` 读取所有参数，并能 在运行时热更新而不重启进程。接着引入 schema 校验，使错误配置能够在启动时被发现，同时优化默认值继承。

**位置**: `lolbot-HyperAI/modules/canbus/conf/canbus_conf.py`

### 1.2 `modules/canbus/canbus_component.py` — Proc()精简重构 (修改)

查看 Apollo `canbus_component.cc` 上现有 `Proc()` 的实现方式（仅3行：PublishChassis + PublishChassisDetail），理解其模式，特别是 **Proc()只做数据发布，所有连接/重试/状态管理都在Init()或独立组件中**。从 Apollo 的 `Proc(){PublishChassis(); return true;}` 这个好例子开始。然后，遵循该模式重构我们的 `Proc()`，将backoff/stale检测/game_active检测全部抽出到独立的 `ConnectionManager` 类中，让 Proc() 可以 只包含 `_poll_and_publish()` + `_publish_status()` 两步，并能 保证 Proc() **永远返回true**（Apollo范式）。

**位置**: `lolbot-HyperAI/modules/canbus/canbus_component.py` (修改)

### 1.3 `modules/canbus/connection_manager.py` — 连接状态机 (新建)

查看 Seraphine `connector.py` 上现有 **ConnectionState + 自动重连** 的实现方式，理解其模式，特别是状态转换和backoff是如何与业务逻辑分离的。从 Seraphine 的 `ConnectorState` 状态机这个好例子开始。然后，遵循该模式实现一个新的 `ConnectionManager`，让 canbus_component.Proc() 可以 直接调用 `manager.ensure_connected()` 而不关心重连细节，并能 通过事件回调通知上层状态变化。

**位置**: `lolbot-HyperAI/modules/canbus/connection_manager.py`

### 1.4 `modules/canbus/dag/canbus.dag` — 声明式DAG编排 (新建)

查看 Apollo `modules/canbus/dag/canbus.dag` 上现有 **DAG声明式组件编排** 的实现方式，理解其模式，特别是组件依赖和通道绑定是如何与代码分离的。从 Apollo 的 `module_config { ... components { ... timer_component_config { ... } } }` 这个好例子开始。然后，遵循该模式实现一个YAML格式的DAG文件，让 DAG launcher 可以 解析声明文件来实例化和连接组件，并能 在不修改代码的情况下调整组件拓扑。

**位置**: `lolbot-HyperAI/modules/canbus/dag/canbus.dag`

### 1.5 `modules/canbus/testdata/sample_allgamedata.json` — 测试数据 (新建)

查看 Apollo `modules/canbus/testdata/` 上现有 **离线测试数据** 的实现方式，理解其模式，特别是真实数据快照是如何用于单元测试的。从 Apollo testdata 目录这个好例子开始。然后，提供完整的 allgamedata JSON 快照，让单元测试可以不连接真实LoL客户端运行，并能覆盖各种游戏阶段（早期/中期/晚期/团战/结束）。

**位置**: `lolbot-HyperAI/modules/canbus/testdata/sample_allgamedata.json`

### 1.6 `modules/canbus/vehicle/data_source_factory.py` — 数据源工厂 (新建)

查看 Apollo `modules/canbus/vehicle/vehicle_factory.h` 上现有 **车辆工厂模式** 的实现方式，理解其模式，特别是不同车型适配器是如何通过工厂统一创建的。从 Apollo 的 `VehicleFactory::CreateVehicle(vehicle_parameter)` 这个好例子开始。然后，遵循该模式实现一个 `DataSourceFactory`，让 canbus 可以 根据配置创建 LCU/Fiddler/Replay/Mock 不同数据源适配器，并能 在运行时切换数据源（如从实时切到回放）。

**位置**: `lolbot-HyperAI/modules/canbus/vehicle/data_source_factory.py`

### 1.7 `modules/common/decorators/retry.py` — 重试装饰器 (新建)

查看 Seraphine `connector.py` 上现有 **@retry装饰器** 的实现方式，理解其模式，特别是重试逻辑和业务代码是如何通过装饰器分离的。从 Seraphine 的 `@retry(count=3, backoff=0.5)` 这个好例子开始。然后，遵循该模式实现一个通用的 `@retry` 装饰器，让所有HTTP调用可以 统一添加重试/backoff/jitter，并能 按异常类型决定是否重试。接着引入断路器模式，使持续失败的调用能够快速失败而不浪费资源。

**位置**: `lolbot-HyperAI/modules/common/decorators/retry.py`

### 1.8 `modules/common/decorators/need_connection.py` — 连接前置检查 (新建)

查看 Seraphine `connector.py` 上现有 **@needLcu装饰器** 的实现方式，理解其模式，特别是连接前置条件检查和业务逻辑是如何分离的。从 Seraphine 的 `@needLcu` 装饰器这个好例子开始。然后，遵循该模式实现一个 `@need_connection` 装饰器，让所有需要LCU连接的方法可以 自动检查连接状态，并能 在未连接时触发自动重连或抛出清晰的异常。

**位置**: `lolbot-HyperAI/modules/common/decorators/need_connection.py`

### 1.9 `modules/common/request_log.py` — PastRequest日志 (新建)

查看 Seraphine `connector.py` 上现有 **PastRequest环形缓冲** 的实现方式，理解其模式，特别是请求日志和HTTP执行是如何分离的。从 Seraphine 的 PastRequest 模式这个好例子开始。然后，遵循该模式实现一个 `RequestLog` 环形缓冲，让所有HTTP请求可以 自动记录 URL/状态码/耗时/响应摘要，并能 导出为JSONL用于离线调试和回放。

**位置**: `lolbot-HyperAI/modules/common/request_log.py`

### 1.10 `modules/perception/perception_component.py` — Proc()重构 (修改)

查看 Apollo `modules/perception/` 上现有 **感知组件** 的实现方式，理解其模式，特别是传感器数据融合和特征提取是如何在Proc()中流水线执行的。从 Apollo perception 的 Reader订阅 + Proc()数据融合这个好例子开始。然后，重构我们的 perception Proc()，将游戏状态解析、事件检测、小地图分析拆分为独立的子处理器（SubProcessor），让 Proc() 可以 按配置启用/禁用子处理器，并能 并行执行无依赖的子处理器。

**位置**: `lolbot-HyperAI/modules/perception/perception_component.py` (修改)

### 1.11 `modules/perception/fusion/game_state_assembler.py` — 数据融合 (新建)

查看 Apollo `modules/perception/fusion/` 上现有 **多传感器融合** 的实现方式，理解其模式，特别是不同数据源的时间对齐和冲突解决。从 Apollo sensor fusion pipeline 这个好例子开始。然后，遵循该模式实现一个 `GameStateAssembler`，让 perception 可以 将 LCU Live Client 数据、Fiddler 网络数据、WebSocket 事件流融合为统一的游戏状态快照，并能 处理数据延迟不一致（LCU 100ms vs Fiddler 500ms）。

**位置**: `lolbot-HyperAI/modules/perception/fusion/game_state_assembler.py`

### 1.12 `modules/prediction/prediction_component.py` — Proc()重构 (修改)

查看 Apollo `modules/prediction/prediction_component.cc` 上现有 **预测组件 Proc()** 的实现方式，理解其模式，特别是多模型推理和结果融合是如何在Proc()中编排的。从 Apollo prediction 的 ContainerManager + Evaluator + Predictor 三级流水线这个好例子开始。然后，重构我们的 prediction Proc()，让特征提取、win_probability计算、团战预测、资源争夺预测可以 作为独立Evaluator注册到PredictionManager中，并能 按游戏阶段动态切换激活的预测模型。

**位置**: `lolbot-HyperAI/modules/prediction/prediction_component.py` (修改)

### 1.13 `modules/prediction/evaluator/evaluator_manager.py` — 评估器管理 (新建)

查看 Apollo `modules/prediction/evaluator/` 上现有 **EvaluatorManager** 的实现方式，理解其模式，特别是不同评估器是如何通过Manager统一调度的。从 Apollo 的 EvaluatorManager::Run() 这个好例子开始。然后，遵循该模式实现我们的 `EvaluatorManager`，让 prediction 可以 注册多个评估器（WinProbability、TeamFight、Objective、Draft），并能 根据游戏阶段和可用数据选择性激活。

**位置**: `lolbot-HyperAI/modules/prediction/evaluator/evaluator_manager.py`

### 1.14 `modules/planning/planning_component.py` — Proc()重构 (修改)

查看 Apollo `modules/planning/planning_component.cc` 上现有 **规划组件** 的实现方式，理解其模式，特别是场景调度和规划器选择是如何在Proc()中执行的。从 Apollo planning 的 ScenarioManager + Task pipeline 这个好例子开始。然后，重构我们的 planning Proc()，将战略建议生成拆分为场景化的策略器（LanePhasePlanner、TeamfightPlanner、ObjectivePlanner），让 Proc() 可以 按当前游戏场景分发到对应策略器，并能 合并多策略器的建议并排优先级。

**位置**: `lolbot-HyperAI/modules/planning/planning_component.py` (修改)

### 1.15 `modules/control/control_component.py` — 扩充输出控制 (修改)

查看 Apollo `modules/control/control_component.cc` 上现有 **控制组件** 的实现方式，理解其模式，特别是控制指令生成和安全检查是如何分离的。从 Apollo control 的 ControllerAgent + 安全卫士模式 这个好例子开始。然后，扩充我们的 control_component，将语音输出、Overlay绘制、日志记录统一为 `OutputChannel` 抽象，让不同输出方式可以 通过注册机制添加，并能 按优先级和冷却时间调度输出。

**位置**: `lolbot-HyperAI/modules/control/control_component.py` (修改)

### 1.16 `modules/monitor/monitor_component.py` — 系统监控 (修改)

查看 Apollo `modules/monitor/` 上现有 **系统监控组件** 的实现方式，理解其模式，特别是硬件状态、模块健康、系统资源是如何统一监控的。从 Apollo monitor 的 RecurrentRunner + FunctionalSafetyMonitor 这个好例子开始。然后，扩充我们的 monitor_component，增加每个组件的 Proc() 耗时追踪、消息延迟监控、内存使用告警，让运维可以 通过 `/lol/monitor_status` 频道实时获取系统健康，并能 在组件异常时自动降级。

**位置**: `lolbot-HyperAI/modules/monitor/monitor_component.py` (修改)

### 1.17 `launch/dag_launcher.py` — 声明式DAG加载 (修改)

查看 Apollo `cyber/launch/` 上现有 **launch文件解析和组件启动** 的实现方式，理解其模式，特别是DAG文件和组件实例化是如何分离的。从 Apollo 的 `cyber_launch start xxx.launch` 这个好例子开始。然后，重构 dag_launcher 使其能解析 YAML 格式的 DAG 文件（而不仅是Python代码式），让组件拓扑可以 声明式配置，并能 自动解析依赖顺序。

**位置**: `lolbot-HyperAI/launch/dag_launcher.py` (修改)

### 1.18 `launch/main_loop.py` — 主循环精简 (修改)

查看 Apollo `CanbusComponent::Proc()` 的极简风格（3行），理解其模式。从 Apollo main loop 只做 `tick → sleep → repeat` 这个好例子开始。然后，精简 main_loop，将组件初始化移交 dag_launcher，让 main_loop 可以 只负责 while-true 调度和信号处理，并能 支持动态调整tick频率。

**位置**: `lolbot-HyperAI/launch/main_loop.py` (修改)

### 1.19 `scripts/diagnostic_runner.py` — 诊断工具 (修改)

查看 Apollo `modules/canbus/tools/` 上现有 **CAN总线调试工具** 的实现方式。从 Apollo canbus tools 目录这个好例子开始。然后，扩充 diagnostic_runner，增加对每个组件的 Init()/Proc() 单独测试、消息流断点调试、性能火焰图生成，让开发者可以 不启动完整系统就测试单个组件。

**位置**: `lolbot-HyperAI/scripts/diagnostic_runner.py` (修改)

### 1.20 `modules/common/component_base.py` — 组件基类统一 (新建)

查看 Apollo `cyber/component/timer_component.h` 上现有 **TimerComponent基类** 的实现方式，理解其模式，特别是 Init()/Proc()/Shutdown() 生命周期是如何强制约束的。从 Apollo TimerComponent 这个好例子开始。然后，遵循该模式实现一个 `ComponentBase` 抽象基类，让所有 *_component.py 可以 继承统一的生命周期接口，并能 自动注册到组件注册表、自动采集 Proc() 性能指标。

**位置**: `lolbot-HyperAI/modules/common/component_base.py`

---

## 2. 给下一位 Claude12 的指引

Claude12 需要基于以上20个文件继续实现另外20个：

- 2.1-2.5: M系列任务整合 — 将M786-M1065的475个孤岛文件逐步迁入 `modules/` 结构
- 2.6-2.10: 测试体系 — 每个 *_component.py 对应一个 *_component_test.py，使用 testdata/ 离线数据
- 2.11-2.15: dreamview 可视化仪表盘 — 实时展示系统状态、消息流、性能指标
- 2.16-2.20: evolution 演化层改进 — 接入组件级fitness，而非仅session级

---

## 3. 文件清单 (Claude11 产出)

| # | 文件路径 | 操作 | 行数 |
|---|---|---|---|
| 1 | modules/canbus/conf/canbus_conf.py | 新建 | ~500 |
| 2 | modules/canbus/canbus_component.py | 修改 | ~530→500 |
| 3 | modules/canbus/connection_manager.py | 新建 | ~500 |
| 4 | modules/canbus/dag/canbus.dag | 新建 | ~50 |
| 5 | modules/canbus/testdata/sample_allgamedata.json | 新建 | ~200 |
| 6 | modules/canbus/vehicle/data_source_factory.py | 新建 | ~500 |
| 7 | modules/common/decorators/retry.py | 新建 | ~500 |
| 8 | modules/common/decorators/need_connection.py | 新建 | ~500 |
| 9 | modules/common/request_log.py | 新建 | ~500 |
| 10 | modules/perception/perception_component.py | 修改 | ~516→500 |
| 11 | modules/perception/fusion/game_state_assembler.py | 新建 | ~500 |
| 12 | modules/prediction/prediction_component.py | 修改 | ~583→500 |
| 13 | modules/prediction/evaluator/evaluator_manager.py | 新建 | ~500 |
| 14 | modules/planning/planning_component.py | 修改 | ~449→500 |
| 15 | modules/control/control_component.py | 修改 | ~267→500 |
| 16 | modules/monitor/monitor_component.py | 修改 | 扩充 |
| 17 | launch/dag_launcher.py | 修改 | ~253→500 |
| 18 | launch/main_loop.py | 修改 | 精简 |
| 19 | scripts/diagnostic_runner.py | 修改 | 扩充 |
| 20 | modules/common/component_base.py | 新建 | ~500 |

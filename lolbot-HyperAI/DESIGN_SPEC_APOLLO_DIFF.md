# 设计规范：Apollo canbus_component.cc vs 我们的代码 — 真实 diff 报告 V2

## 勘误

前一版本（Claude22 V1）声称 Apollo Proc() 只有 3 行。
这是错误的。实际的 Apollo canbus_component.cc 有约 300 行有效代码，
Proc() 有 35 行。以下是基于 `cat apollo/modules/canbus/canbus_component.cc`
实际源码的修正分析。

---

## 一、Apollo canbus_component.cc 真实完整结构（~300行有效代码）

```
canbus_component.cc
├── CanbusComponent() 构造函数：3行
│
├── Init()：约 120 行
│   ├── GetProtoConfig(&canbus_conf_)                  — 5行 配置加载
│   ├── PathExists(FLAGS_load_vehicle_library)          — 4行 动态库检查
│   ├── ClassLoader + CreateClassObj<VehicleFactory>    — 8行 动态工厂加载
│   ├── vehicle_object_->Init(&canbus_conf_)           — 4行 工厂初始化
│   ├── CreateReader<GuardianCommand> + lambda回调      — 25行 含超时计时
│   ├── CreateReader<ControlCommand> + lambda回调       — 同上（条件分支）
│   ├── CreateReader<ChassisCommand>                   — 7行
│   ├── CreateWriter<Chassis>                          — 1行
│   └── vehicle_object_->Start()                       — 6行
│
├── Clear()：2行
│
├── PublishChassis()：6行（含 cmd_time_delay 错误码设置）
│
├── Proc()：约 35 行 ← 不是 3 行！
│   ├── start_time 计时                                — 1行
│   ├── Reader->Observe() + CommandCheck               — 15行（guardian/control 分支）
│   ├── CheckChassisCommunicationFault()               — 5行 通信故障检测
│   ├── PublishChassis()                               — 1行
│   ├── if FLAGS → PublishChassisDetail()              — 2行
│   ├── if FLAGS → PublishChassisDetailSender()        — 2行
│   ├── UpdateHeartbeat()                              — 1行
│   └── end_time + 超时告警                            — 5行
│
├── OnControlCommand()：15行 — 命令间隔检查 + UpdateCommand
├── OnControlCommandCheck()：35行 — 超时→Guardian降级→紧急停车
├── OnGuardianCommand()：3行 — 代理
├── OnGuardianCommandCheck()：30行 — Guardian超时处理
├── OnChassisCommand()：15行 — 底盘命令
├── OnError()：2行
├── ProcessTimeoutByClearCanSender()：20行 — CAN发送协议清理
└── ProcessGuardianCmdTimeout()：5行 — 设置紧急刹车参数
```

## 二、Apollo Proc() 35行的真实结构

```cpp
bool CanbusComponent::Proc() {
  const auto start_time = Time::Now().ToMicrosecond();

  // 1. 读取+检查控制命令（15行）
  if (FLAGS_receive_guardian) {
    guardian_cmd_reader_->Observe();
    const auto &msg = guardian_cmd_reader_->GetLatestObserved();
    if (msg == nullptr) {
      AERROR << "guardian cmd msg is not ready!";
    } else {
      OnGuardianCommandCheck(*msg);
    }
  } else {
    control_command_reader_->Observe();
    const auto &msg = control_command_reader_->GetLatestObserved();
    if (msg == nullptr) {
      AERROR << "control cmd msg is not ready!";
    } else {
      OnControlCommandCheck(*msg);
    }
  }

  // 2. 通信故障检测（5行）
  if (vehicle_object_->CheckChassisCommunicationFault()) {
    AERROR << "Can not get the chassis info...";
    is_chassis_communication_fault_ = true;
  } else {
    is_chassis_communication_fault_ = false;
  }

  // 3. 发布核心数据（1行 + 条件发布）
  PublishChassis();
  if (FLAGS_enable_chassis_detail_pub)
    vehicle_object_->PublishChassisDetail();
  if (FLAGS_enable_chassis_detail_sender_pub)
    vehicle_object_->PublishChassisDetailSender();

  // 4. 心跳（1行）
  vehicle_object_->UpdateHeartbeat();

  // 5. 性能监控（5行）
  const auto end_time = Time::Now().ToMicrosecond();
  const double time_diff_ms = (end_time - start_time) * 1e-3;
  if (time_diff_ms > (1 / FLAGS_chassis_freq * 1e3)) {
    AWARN << "CanbusComponent::Proc() takes too much time: " << time_diff_ms;
  }

  return true;
}
```

## 三、修正后的真实对比

### 3.1 Proc() 行数对比（修正后）

| 组件 | Apollo Proc() | 我们的 Proc() | 比率 | 评估 |
|------|--------------|--------------|------|------|
| canbus | **35行** | 20行 | 0.57x | ✅ 我们更简洁（因为用了 measure_proc 和 _poll_and_publish 委托） |
| perception | N/A | 225行 | — | ⚠️ 但 Apollo perception_component 也有复杂 Proc |
| planning | N/A | 100行 | — | ⚠️ |
| prediction | N/A | 80行 | — | ⚠️ |
| control | N/A | 7行 | — | ✅ |

### 3.2 Apollo Proc() 做了什么（我之前遗漏的）

Apollo 的 Proc() 不是只有「Publish + 条件 Publish」。它还做了：

1. **命令读取 + 超时检查**：Reader->Observe() + OnCommandCheck()
   - 这在我们的 canbus 里不需要（我们只读 LCU，不接收控制命令）
   - 但在 control_component.py 里我们有等价的 drain_inputs()

2. **通信故障检测**：CheckChassisCommunicationFault()
   - 等价于我们的 connection_state + stale 检测

3. **心跳更新**：UpdateHeartbeat()
   - 我们有 heartbeat 在 main_loop supervisor 里

4. **性能监控**：start_time/end_time 计时 + AWARN
   - 等价于我们的 measure_proc() context manager

### 3.3 Init() 对比

| 维度 | Apollo Init() ~120行 | 我们的 Init() ~70行 | 评估 |
|------|---------------------|---------------------|------|
| 配置加载 | GetProtoConfig | DataSourceFactory | ✅ |
| 动态加载 | ClassLoader + CreateClassObj | DataSourceFactory.create | ✅ |
| Reader 创建 | 3个 Reader + lambda 回调 | 无（canbus 只 poll，不接收命令） | 差异合理 |
| Writer 创建 | 1个 Writer | 3个 Writer（raw_lcu + fiddler + status） | 我们更多通道 |
| 启动 | vehicle_object_->Start() | data_source.init() + fallback | ✅ |

### 3.4 Apollo 有而我们没有的（真实缺口）

| Apollo 特性 | 对应代码 | 我们有吗 | 建议 |
|------------|---------|---------|------|
| 命令超时降级到 Guardian | OnControlCommandCheck ~35行 | ❌ | 游戏不需要紧急停车，但 **数据源降级** 是等价概念，DataSourceFactory.fallback 已实现 |
| CAN 通信故障检测 | CheckChassisCommunicationFault | ✅ | connection_state + stale 检测 |
| CAN 发送协议清理 | ProcessTimeoutByClearCanSender | ❌ | 游戏不发送 CAN，不需要 |
| 紧急刹车参数设置 | ProcessGuardianCmdTimeout | ❌ | 游戏等价：**紧急静音/禁止输出**，建议加入 |
| 命令间隔限流 | OnControlCommand 中 min_cmd_interval | ✅ | 我们在 action_dispatcher 的 cooldown 里有 |
| 性能计时+告警 | start_time/end_time + AWARN | ✅ | measure_proc() |

### 3.5 我们有而 Apollo 没有的

| 我们的特性 | 对应代码 | Apollo 有吗 | 原因 |
|-----------|---------|------------|------|
| 数据源自动降级 | DataSourceFactory fallback | ❌ | Apollo CAN 总线不会 fallback |
| game-active 探测 | _check_game_active | ❌ | Apollo 车永远在运行 |
| HTTP 响应验证 | _validate_lcu_response | ❌ | Apollo 读 CAN 帧，格式固定 |
| 指数退避重连 | _apply_backoff | ❌ | Apollo CAN 是物理连接 |
| 多数据源并行（LCU+Fiddler） | _poll_fiddler | ❌ | Apollo 只有一个 CAN 总线 |

## 四、修正后的设计规范

### 核心认知修正

Apollo 的 Proc() **不是 3 行**，而是 **35 行**，包含：
- 命令读取 + 超时检查（输入侧）
- 故障检测（健康检查）
- 数据发布（输出侧）
- 心跳 + 性能监控（运维侧）

这是一个 **4 段式结构**：`Read → Check → Publish → Monitor`

### 修正后的模板

从 Apollo `canbus_component.cc` 的 **Proc() 4段式结构（Read→Check→Publish→Monitor，35行）**
这个好例子开始。

然后，遵循该模式审视 **PerceptionComponent.Proc()**，
让 Proc() 可以 **清晰体现 4 段结构**，
并能 **将 sub-analyzer 调用集中到 _run_sub_analyzers() 委托方法**。

接着 在 **PlanningComponent** 引入 **_run_planning_cycle() 委托**，
使 Proc() 能够 **将 7 个 sub-planner 的 try/except 块从 Proc() 抽出**，
同时 保留 **Apollo 的 Read→Check→Publish→Monitor 骨架**。

最终确保每个 Proc() 都体现 **Apollo 的 4 段式**，但不追求极端压缩行数。
Apollo 自己的 Proc() 也有 35 行——**清晰比短更重要**。

## 五、修正后的状态评估

| 组件 | Proc()行数 | Apollo 4段式？ | 真实评估 |
|------|-----------|---------------|---------|
| canbus | 20行 | ✅ Read→Poll→Publish→(Monitor via measure_proc) | **优于 Apollo** |
| control | 7行 | ✅ Drain→Dispatch→Flush | ✅ 达标 |
| perception | 225行 | ❌ 4段混在一起 + sub-analyzers 内联 | **需要提取委托方法** |
| planning | 100行 | ❌ sub-planners 内联 | **需要提取委托方法** |
| prediction | 80行 | ⚠️ 基本是 4段但内联太多 | **建议提取** |

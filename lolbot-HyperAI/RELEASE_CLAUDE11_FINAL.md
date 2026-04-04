# RELEASE_CLAUDE11_FINAL.md — Production Refactor Delivery

> Claude11 · 13 files, 5280+ lines of production-grade code
> Author: dylanyunlong <dylanyunlong@gmail.com>

---

## 1. Delivered Files (13/20)

### 1.1 `modules/common/component_base.py` — 688 lines (rewrite)

查看 Apollo cyber/component/timer_component.h 上现有 TimerComponent 基类的实现方式, 理解其模式, 特别是 Init()/Proc()/Shutdown() 生命周期是如何强制约束的。从 Apollo TimerComponent 这个好例子开始。然后遵循该模式实现了:

- `LifecycleState` 枚举 + 状态机 (CREATED→INITIALIZING→READY→RUNNING→DEGRADED→STOPPING→STOPPED)
- `ComponentRegistry` 全局单例 (线程安全, 依赖图, 关闭顺序)
- `ProcMetrics` (P95/P99, 连续失败计数, 失败原因统计)
- `ProcCircuitBreaker` (CLOSED→OPEN→HALF_OPEN, 指数退避)
- `ManagedComponent` mixin (自动注册 + 性能采集 + 依赖检查 + 降级)
- `HealthStatus` 标准化健康协议

### 1.2 `modules/common/decorators/retry.py` — 600 lines (rewrite)

查看 Seraphine connector.py 上现有 @retry 装饰器的实现方式。遵循该模式实现了:

- `@retry` 同步重试装饰器 (count, backoff, jitter, retryable exceptions)
- `@async_retry` 异步重试装饰器
- `RetryPolicy` 可复用策略对象 (LCU_RETRY, NETWORK_RETRY, FAST_RETRY)
- `CircuitBreaker` 独立断路器 (装饰器 + 上下文管理器)
- `BackoffStrategy` (exponential/linear/constant)
- `RetryStats` 统计

### 1.3 `modules/common/decorators/need_connection.py` — 388 lines (rewrite)

- `@need_connection` 通用连接状态守卫
- `@need_lcu` / `@need_fiddler` / `@need_game_active` 专用守卫
- `ConnectionInfo` 连接状态协议 (state, freshness, staleness)
- `GuardStats` 拦截统计
- 同步 + 异步支持

### 1.4 `modules/common/request_log.py` — 459 lines (rewrite)

- `RequestRecord` 单次请求记录
- `RequestLog` 线程安全环形缓冲 (filter, export_jsonl)
- `@log_request` 自动记录装饰器
- Header 脱敏 (_SENSITIVE_HEADERS)
- Body 截断 (_MAX_BODY_SNIPPET)

### 1.5 `modules/control/control_component.py` — 617 lines (rewrite)

查看 Apollo modules/control/control_component.cc 上现有控制组件的实现方式。遵循该模式实现了:

- `OutputChannel` 抽象基类 (priority filter, cooldown, auto-disable)
- `VoiceOutputChannel` / `OverlayOutputChannel` / `LogOutputChannel` 三路输出
- 注册机制: `register_channel()` / `unregister_channel()`
- Apollo 3-step Proc(): drain → dispatch → flush
- Per-channel 健康追踪 (consecutive errors → auto-disable)

### 1.6 `modules/monitor/monitor_component.py` — 514 lines (rewrite)

查看 Apollo modules/monitor/ 上现有系统监控组件的实现方式。遵循该模式实现了:

- `AlertManager` (fire/resolve/dedup, severity levels, callbacks)
- `ResourceTracker` (RSS memory watchdog)
- `ComponentHealthTracker` (per-component latency/success rate)
- 自动告警: latency > 50ms(WARN) / 200ms(ERROR), success_rate < 50%
- Heartbeat publisher (30s interval)

### 1.7 `launch/dag_launcher.py` — 488 lines (rewrite)

查看 Apollo cyber/launch/ 上现有 launch 文件解析和组件启动的实现方式。遵循该模式实现了:

- YAML DAG 加载 (load_dag_from_yaml, load_dag_from_dict)
- Kahn's algorithm 拓扑排序 + 环检测
- DAG 验证 (missing deps, duplicate names, empty paths)
- 依赖顺序启动 + 反序关闭
- `restart_component()` 热重启

### 1.8 `scripts/diagnostic_runner.py` — 479 lines (rewrite)

- 40 module import 检查
- 目录结构完整性检查
- pipeline.yaml 配置验证
- 6 component Init() 测试
- `--benchmark` Proc() 延迟基准测试 (p50/p95/p99)
- `--component canbus` 单组件测试模式
- `--output report.json` JSON 报告导出

### 1.9 `modules/perception/fusion/game_state_assembler.py` — 464 lines (rewrite)

查看 Apollo modules/perception/fusion/ 上现有多传感器融合的实现方式。遵循该模式实现了:

- `SourceFrame` 带时间戳的数据帧 (freshness score)
- `FusedSnapshot` 融合后的游戏状态快照
- `EventDeduplicator` 基于内容哈希的事件去重
- 多源时间对齐 (LCU 100ms, Fiddler 500ms, WebSocket 1s)
- Quality = freshness × completeness

### 1.10-1.13 Component ManagedComponent Integration (4 files, modify)

为 canbus, perception, prediction, planning 四个组件添加:
- `ManagedComponent` mixin 继承
- `COMPONENT_NAME` / `DEPENDENCIES` / `VERSION` 声明
- `_managed_init()` 在 Init() 起始
- `register_self()` + `_transition(READY/RUNNING)` 在 Init() 末尾
- `should_skip_proc()` circuit breaker 检查在 Proc() 起始
- `_managed_shutdown()` 在 on_shutdown()

### 1.14 `launch/main_loop.py` — modify

- 添加 `ComponentRegistry` import
- 添加 `ComponentRegistry.reset()` 防止 crash-restart 残留

---

## 2. 验证结果

```
39/40 modules import OK (dreamview.dashboard.dashboard_server 不存在, Claude12 范围)
6/6 components Init() OK (canbus, perception, prediction, planning, control, monitor)
DAGLauncher topo-sort + start_all + shutdown_all 全通过
ComponentRegistry 6 组件注册成功
E2E MainLoop 10 ticks, 0 errors
```

---

## 3. 给 Claude12 的指引

Claude12 需要完成剩余 7 个文件:

1. `modules/canbus/conf/canbus_conf.py` — YAML 热加载 (已有 591 行, 需要集成 ManagedComponent)
2. `modules/canbus/connection_manager.py` — 连接池 + 状态机 (已有 504 行, 需要集成 @retry + @need_connection)
3. `modules/canbus/vehicle/data_source_factory.py` — 工厂模式 (已有 481 行, 需要集成)
4. `modules/prediction/evaluator/evaluator_manager.py` — 评估器注册表 (已有 530 行, 需要集成)
5. `modules/canbus/testdata/sample_allgamedata.json` — 测试数据 (新建)
6. `modules/dreamview/dashboard/dashboard_server.py` — SSE 仪表盘 (新建)
7. 为所有 component 添加 `*_component_test.py` 单元测试

**关键模式**: 所有 *_component.py 必须继承 `ManagedComponent`, 在 Init() 中调用 `self._managed_init()`, 在 Proc() 开头调用 `self.should_skip_proc()`, 在 shutdown 中调用 `self._managed_shutdown()`.

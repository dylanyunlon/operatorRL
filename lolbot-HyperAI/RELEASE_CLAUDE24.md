# RELEASE_CLAUDE24.md — Pipeline Entry Point + Flow Diagnostics

> Author: dylanyunlong <dylanyunlong@gmail.com>
> Base: commit e213de8c (Claude22-v3)
> Files: 4 new, 3 modified, 0 deleted. Zero lines of existing logic removed.

---

## Design Spec (Apollo Diff)

查看 Apollo `cyber/mainboard/mainboard.cc` 上现有 `main()` 入口的实现方式,
理解其模式, 特别是 **ModuleArgument 参数解析** 和 **ModuleController 组件
生命周期** 是如何分离的。从 Apollo mainboard.cc 的
`ParseArgument → Init → WaitForShutdown → Clear` 这个好例子开始。

然后, 遵循该模式实现一个新的 `run.py`, 让用户可以通过 `--mock` / `--replay`
/ `--dag` / `--log-level` 等 CLI 参数控制启动行为, 并能一条命令启动整个
pipeline。

接着在 `launch/pipeline_diagnostics.py` 中引入 `PipelineDiagnostics` (Apollo
`cyber_monitor` 等价), 使运维人员能够实时看到消息在
canbus→perception→prediction→planning 之间的流动频率和延迟, 同时优化为
采样式追踪避免性能开销。

随后在 `launch/mainboard.py` 中整合 `enable_pipeline_diagnostics()`, 令
Mainboard 支持在 `start_all()` / `stop_all()` 生命周期内自动管理诊断线程,
进而增强运行时可观测性。

最终在 `launch/main_loop.py` 中完善诊断集成 (startup/shutdown/health_check/
stats), 确保诊断数据兼容 structured_logger 和 dreamview dashboard, 全面系
统性升级运维体验以达成 **毫秒级流水线可见性** 的目标。

---

## New Files (4)

### 1. `run.py` — CLI Entry Point (Apollo `mainboard.cc::main()` 等价)

位置: `lolbot-HyperAI/run.py`

- `_parse_args()` → Apollo `ModuleArgument::ParseArgument()`
- `_configure_logging()` → Apollo glog `FLAGS` setup in `Init()`
- `_apply_overrides()` → env vars for mock/replay/no-voice/no-evolution
- `_dry_run()` → validate DAG + config without starting components
- `_run_main_loop()` → Apollo `controller.Init(); WaitForShutdown()`
- `--profile` → cProfile dump to `logs/profile.prof`

### 2. `launch/pipeline_diagnostics.py` — Flow Diagnostics (Apollo `cyber_monitor`)

位置: `lolbot-HyperAI/launch/pipeline_diagnostics.py`

- `ChannelStats` — per-channel frequency/latency/stale detection
- `FlowTrace` — sampled E2E latency trace (canbus→voice)
- `PipelineDiagnostics` — aggregator with auto-report thread
- `format_report()` → ASCII table of channel flow stats
- `check_anomalies()` → stale channel + high latency detection

### 3. `conf/dag/lolbot_full.yaml` — Full Pipeline DAG

位置: `lolbot-HyperAI/conf/dag/lolbot_full.yaml`

Apollo .dag protobuf 的 YAML 等价:
canbus(100ms) → perception(100ms) → prediction(500ms) → planning(500ms) → control(200ms) + monitor(2000ms)

### 4. `conf/dag/lolbot_minimal.yaml` — Debug DAG

位置: `lolbot-HyperAI/conf/dag/lolbot_minimal.yaml`

仅 canbus + perception, mock 模式, 用于快速连通性测试。

### 5. `scripts/launch_pipeline.sh` — Shell Wrapper

位置: `lolbot-HyperAI/scripts/launch_pipeline.sh`

`--diag` / `--debug` / `--quiet` / `--dry` 简写 → 转发给 run.py。

---

## Modified Files (3)

### `launch/mainboard.py` (+52 lines, 0 removed)

在 `component_summary()` 之后追加:
- `enable_pipeline_diagnostics()` — 创建 PipelineDiagnostics 实例, 注册所有 pipeline 通道
- `pipeline_diagnostics` property — 组件可访问诊断实例
- `diagnostics_snapshot()` — 返回可序列化快照

**用户视角审查**: 新方法全部可选调用, 不影响已有 `start_all()` / `stop_all()` 流程。
**系统视角审查**: `_pipeline_diag` 使用 `getattr` 安全访问, ImportError 时降级为 None。

### `launch/main_loop.py` (+25 lines, 0 removed)

4 处精确追加:
1. `run()` 中 `start_all()` 前: 检查 `LOLBOT_DIAGNOSTICS` env → `enable_pipeline_diagnostics()`
2. `run()` 中 `start_all()` 后: `diag.start_auto_report()`
3. `_shutdown()` 中: `diag.stop_auto_report()` (在 `stop_all()` 前)
4. `_run_health_check()` 中: `diag.check_anomalies()` 打印 stale channel 警告
5. `stats()` 中: 添加 `pipeline_diagnostics` 字段

**用户视角审查**: 诊断默认不启用, 仅当 `LOLBOT_DIAGNOSTICS=1` (由 `run.py --diagnostics` 设置) 时激活。对不使用 `--diagnostics` 的用户, 行为与 Claude22 完全一致。
**系统视角审查**: 诊断线程是 daemon 线程, 主进程退出自动终止。`check_anomalies()` 在 `_run_health_check()` 的 `try/except` 保护下运行。

---

## Diff Verification

```
# 验证 mainboard.py 只增不删:
diff <(git show HEAD:lolbot-HyperAI/launch/mainboard.py) lolbot-HyperAI/launch/mainboard.py

# 验证 main_loop.py 只增不删:
diff <(git show HEAD:lolbot-HyperAI/launch/main_loop.py) lolbot-HyperAI/launch/main_loop.py
```

---

## For Claude25

在此基础上继续开发:
1. 在 `canbus_component.py` 的 `Proc()` 中调用 `diag.record_message("/lol/raw_lcu")` 接入真实数据
2. 同理在 perception/prediction/planning 组件中接入
3. 实现 `FlowTrace` 的跨组件传播 (通过 ChannelMessage.metadata)
4. 增加 replay 模式的 `ReplayDriver` 组件 (读 JSONL → 发布到 canbus 通道)
5. 增加 `scripts/analyze_logs.py` 日志分析工具

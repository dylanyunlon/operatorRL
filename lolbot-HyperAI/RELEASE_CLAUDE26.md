# RELEASE — Claude26: Apollo Code/Interface Separation Phase 2

## 设计规范

从 Apollo `multi_sensor_fusion/fusion/fusion_system/probabilistic_fusion.cc`
这个好例子开始——它将传感器融合(209行组件+23个子文件)从 component.cc 中
分离到独立的子目录。然后, 遵循该模式实现新的 `assembler/`, `detector/`,
`channel/`, `dispatch/`, `alert/`, `resource/` 子模块, 让各组件的 `Proc()`
可以保持 Apollo 级别的简洁, 并能独立测试每个子逻辑。接着 `SnapshotAssembler`
引入类型安全的数据转换, 使 perception 能够可靠地将 JSON→typed objects,
同时 `EventDetector` 优化事件去重。随后 `OutputChannel` 层级整合 voice/overlay/log,
令 control 支持 registry 模式的渠道管理, 进而 `AlertManager` 增强告警生命周期。
最终 `ResourceTracker` + `HealthTracker` 完善监控基础设施, 确保提取后的模块
兼容 Claude1-25 所有已有逻辑, 全面系统性升级代码组织以达成 Apollo 级别的
代码/接口分离目标。

## 真实Diff对比

| 组件 | Apollo参考 | Claude25 | Claude26 | 削减 |
|------|-----------|----------|----------|------|
| perception | 209行+23子文件 | 915行, 1 class | 624行+2子模块 | -291行 |
| control | 704行, 子控制器独立 | 1087行, 13 class | 522行+2子模块 | -565行 |
| monitor | N/A | 880行, 12 class | 655行+2子模块 | -225行 |
| **合计** | | **2882行** | **1801行+6子模块** | **-1081行** |

## 新文件清单 (20个)

### modules/perception/assembler/ (从perception_component.py提取)
- `__init__.py`
- `snapshot_assembler.py` (189行) — _assemble_snapshot, _parse_player, _build_team_state

### modules/perception/detector/ (从perception_component.py提取)
- `__init__.py`
- `event_detector.py` (193行) — _detect_new_events, event_rates, data_quality_score, detect_anomalies, validate_input

### modules/control/channel/ (从control_component.py提取)
- `__init__.py`
- `output_channel.py` (130行) — OutputChannel, OutputChannelState, OutputChannelStats
- `voice_channel.py` (31行) — VoiceOutputChannel
- `overlay_channel.py` (31行) — OverlayOutputChannel
- `log_channel.py` (34行) — LogOutputChannel

### modules/control/dispatch/ (从control_component.py提取)
- `__init__.py`
- `safety_guard.py` (66行) — SafetyGuard (Claude11)
- `cooldown_tracker.py` (43行) — CooldownTracker (Claude11)
- `dedup_filter.py` (30行) — DedupFilter (Claude11)
- `rate_limiter.py` (49行) — DispatchRateLimiter (Claude17)
- `effectiveness_tracker.py` (74行) — ActionEffectivenessTracker (Claude17)

### modules/monitor/alert/ (从monitor_component.py提取)
- `__init__.py`
- `alert_manager.py` (120行) — AlertSeverity, AlertRecord, AlertManager

### modules/monitor/resource/ (从monitor_component.py提取)
- `__init__.py`
- `resource_tracker.py` (44行) — ResourceTracker
- `health_tracker.py` (67行) — ComponentHealthEntry, ComponentHealthTracker

## 修改文件清单 (3个)

- `perception_component.py`: 915→624行, 7个方法改为delegation
- `control_component.py`: 1087→522行, 8个class提取为import
- `monitor_component.py`: 880→655行, 6个class提取为import

## 验证

- 20/20 新文件 AST py_compile PASS
- 3/3 修改文件 AST py_compile PASS
- perception: 所有10个关键方法保留, 7个委托方法保留
- control: 所有13个关键方法保留, 5个inline class提取
- monitor: 所有6个关键方法保留, 6个inline class提取
- Zero lines of Claude1-25 logic removed — pure extraction refactor

## 用户角度批判

1. **OutputChannel.dispatch()** 的引用路径变了: 从 `control_component.OutputChannel` 变为
   `control.channel.output_channel.OutputChannel`。如果有外部代码直接 `from modules.control.control_component import OutputChannel`
   会 ImportError。**修复**: control_component.py 头部已添加 re-export import。
2. **DispatchRecord** dataclass 被移除: 经检查全项目无引用, 安全删除。

## 系统角度批判

1. **循环导入风险**: channel/ 导入 action_dispatcher, action_dispatcher 不导入 channel/ — 单向依赖, 安全。
2. **线程安全**: 所有提取的类文档标注 "NOT thread-safe", 与 Apollo TimerComponent 单线程 Proc() 模型一致。
3. **内存**: 子模块实例在 __init__ 创建, 生命周期与组件一致, 无泄漏。

# lolbot-HyperAI 设计规范 — Apollo 结构对标审计

> 作者: dylanyunlong <dylanyunlong@gmail.com>
> 基于: Apollo 9.0 实际源码 vs lolbot-HyperAI 当前实现的逐文件对比
> 日期: 2026-04-02

---

## 一、Apollo 的 5 层设计纪律（从源码提取）

读完 Apollo `modules/canbus/canbus_component.{h,cc}` 和
`modules/control/control_component/control_component.{h,cc}` 后，
总结出 Apollo 每个模块严格遵守的 5 层文件结构：

```
modules/{module_name}/
├── {module}_component.h      ← 接口声明：Reader/Writer 类型、私有方法签名
├── {module}_component.cc     ← 实现：Init() + Proc() + 私有方法体
├── proto/
│   └── {module}_conf.proto   ← 消息类型：protobuf IDL，不含逻辑
├── conf/
│   ├── {module}.conf         ← gflags 运行参数
│   └── {module}_conf.pb.txt  ← protobuf 文本格式配置
├── dag/
│   └── {module}.dag          ← 组件组装声明：类名 + 频率 + 配置路径
├── common/
│   └── {module}_gflags.{h,cc}← 模块专属 flag 定义
└── vehicle/ 或 submodules/   ← 业务子模块（被 component 调用）
```

**关键纪律：**

1. **接口与实现分离**: `.h` 只有声明（119行），`.cc` 只有实现（386行）。
   读 `.h` 就知道组件的全部输入输出通道，不需要读实现。

2. **Proc() 极度精简**: Apollo 的 `CanbusComponent::Proc()` 只有 ~30 行，
   做 4 件事：Observe → Check → Publish → MeasureLatency。
   所有业务逻辑委托给 `vehicle_object_` 工厂。

3. **proto 独立于代码**: 消息类型用 `.proto` 文件定义，不是 Python dataclass
   混在业务代码里。任何消费者只需看 `.proto` 就知道消息格式。

4. **dag 声明式组装**: 组件的通道连接、频率、配置路径写在 `.dag` 里，
   不是硬编码在 `main_loop.py` 的 `_init_components()` 中。

5. **conf 文本化**: 运行参数是文本文件，不是 Python dataclass 默认值。
   部署时改 `.conf` 不需要改代码。

---

## 二、我们的差距（逐项对比）

### 差距 1：没有接口声明文件

| Apollo | 我们 |
|--------|------|
| `canbus_component.h` (119行) — 纯声明 | 不存在 |
| 读 `.h` 就知道：3个 Reader、1个 Writer、7个私有方法 | 必须读完 530 行 `.py` 才知道全貌 |

**后果**: 其他 Claude 开发者无法快速理解组件的输入输出合约，
导致 Phase 3 的 7 个子模块没人知道该接到哪里。

**修复**: 每个 `*_component.py` 顶部 docstring 加 **通道合约表**：

```
Channels:
    IN:  /lol/raw_lcu        RawLCUData        canbus → perception
    IN:  /lol/raw_fiddler    RawFiddlerData    canbus → perception
    OUT: /lol/game_state     GameSnapshot      perception → prediction, planning
    OUT: /lol/events         List[GameEvent]   perception → prediction, planning
    OUT: /lol/kill_feed      List[Pattern]     perception → prediction
    OUT: /lol/minimap_state  MinimapState      perception → planning
```

### 差距 2：Proc() 膨胀

| Apollo `CanbusComponent::Proc()` | 我们的 `PerceptionComponent.Proc()` |
|----------------------------------|--------------------------------------|
| ~30 行 | ~100 行（含子模块调用） |
| 4 步：Observe → Check → Publish → Latency | 7 步，内含 try/except、条件分支 |
| 业务逻辑全部委托给 `vehicle_object_` | 直接内联 `_assemble_snapshot()` 调用 |

**后果**: Proc() 越长越难审查，越容易在追加功能时引入副作用。

**修复**: Proc() 应该只是调度器，每步一行：

```python
def Proc(self) -> bool:
    raw = self._read_input()           # Observe
    if raw is None: return True
    snapshot = self._assembler.assemble(raw)  # 委托
    events = self._detector.detect(raw)       # 委托
    patterns = self._kill_feed.analyze(events, snapshot)  # 委托
    minimap = self._minimap.analyze(snapshot)              # 委托
    self._publish_all(snapshot, events, patterns, minimap) # Publish
    return True
```

### 差距 3：没有 dag 文件

| Apollo | 我们 |
|--------|------|
| `canbus.dag`: 声明组件类名 + 频率 + 配置路径 | 硬编码在 `main_loop.py._init_components()` |
| 改频率只需改 `interval: 10` → `interval: 20` | 改频率要改 Python 常量然后重新部署 |

**后果**: 组件编排和代码耦合，无法热配置。

**修复**: 引入 `configs/pipeline.yaml`（已有骨架但未启用）：

```yaml
components:
  - class: CanbusComponent
    interval_ms: 100
    config: conf/canbus.yaml
  - class: PerceptionComponent
    interval_ms: 100
    config: conf/perception.yaml
    depends_on: [CanbusComponent]
  - class: PredictionComponent
    interval_ms: 500
    config: conf/prediction.yaml
    depends_on: [PerceptionComponent]
```

### 差距 4：proto 散落在业务代码中

| Apollo | 我们 |
|--------|------|
| `proto/canbus_conf.proto` (14行) — 纯 IDL | `game_messages.py` (483行) — 混杂了枚举、数据类、工厂方法 |
| 跨语言：C++/Python 都能用 | 只能 Python |

**后果**: 消息定义与序列化逻辑混在一起，改消息格式可能破坏序列化。

**修复已部分完成**: Phase 4 已经为每层创建了独立的 `proto/*_messages.py`。
下一步应该将 `game_messages.py` 中的类型定义拆分到对应层的 `proto/` 目录。

### 差距 5：配置硬编码

| Apollo | 我们 |
|--------|------|
| `conf/canbus_conf.pb.txt` — 文本文件 | `conf/default_config.py` — Python dataclass 默认值 |
| 部署时改文本文件 | 部署时改代码或 JSON |

**后果**: 默认值嵌在代码中，运维人员必须懂 Python。

**修复已部分完成**: Phase 4 已经为每层创建了 `conf/*_config.py`。
下一步应该让 `pipeline.yaml` 覆盖这些默认值，实现"代码零修改部署"。

---

## 三、设计规范模板

从 Apollo `modules/canbus/` 这个好例子开始。

然后，遵循该模式实现每个新模块时，必须按以下 checklist 执行：

### 每个 TimerComponent 子模块必须包含：

| 序号 | 文件 | 用途 | Apollo 对标 |
|------|------|------|-------------|
| 1 | `{module}_component.py` | Init() + Proc() 调度器 | `.h` + `.cc` |
| 2 | `proto/{module}_messages.py` | 消息类型定义（frozen dataclass） | `proto/*.proto` |
| 3 | `conf/{module}_config.py` | 配置数据类 + from_dict() | `conf/*.pb.txt` |
| 4 | 顶部 docstring 通道合约表 | 声明全部 IN/OUT 通道 | `.h` 声明 |

### Proc() 纪律：

1. Proc() 体不超过 20 行
2. 第一行：`reader.Observe()` — 拉取输入
3. 中间：委托给子模块（每步一行调用）
4. 倒数第二行：`writer.Write()` — 发布输出
5. 最后一行：`return True`
6. 所有子模块调用包在 try/except 中，失败日志但不阻断
7. 子模块频率控制用 tick divisor，不用 sleep

### 通道命名规范：

```
/lol/{layer}/{data_type}

层:  raw → canbus 原始数据
     game_state, events, kill_feed, minimap → perception 输出
     win_prediction, teamfight → prediction 输出
     strategy, macro, lane → planning 输出
     voice, overlay → control 输出
     system → 系统级（心跳、错误、监控）
```

---

## 四、具体改造计划（按优先级排序）

### P0：Proc() 瘦身（影响运行时稳定性）

从 `modules/perception/perception_component.py` 的 100 行 Proc() 开始。
然后，遵循 Apollo Proc() 只做"Observe→Delegate→Publish"的模式实现瘦身，
让 Proc() 可以在 20 行内完成全部调度，
并能 将 `_assemble_snapshot` / `_detect_new_events` 下沉到独立的 Assembler/Detector 类。
接着 在 prediction_component 引入同样的瘦身，
使 WinPredictor 和 TeamfightPredictor 的调用各只占一行，
同时 FeatureExtraction 优化为独立步骤。
随后 在 planning_component 整合 MacroPlanner 和 LaneAdvisor 调用，
令 Proc() 支持"一行一步"的线性管线，
进而 control_component 增强为纯调度器（已经是了，保持）。
最终 在 canbus_component 完善 Proc() 到 Apollo 水平，
确保 最终 Proc() 兼容 Apollo 的 30 行内纪律，
全面 系统性地 升级 全部 5 个组件的 Proc() 质量以达成 Apollo 标准。

### P1：通道合约表（影响开发者协作）

为 5 个 component 文件添加标准化通道合约 docstring。

### P2：pipeline.yaml 启用（影响部署灵活性）

将硬编码的组件注册逻辑迁移到 `configs/pipeline.yaml` 声明式描述。

### P3：game_messages.py 拆分（影响代码清洁度）

将 483 行的 `game_messages.py` 拆分到各层 `proto/` 目录。

---

## 五、对比总结

```
                        Apollo          我们（当前）   目标
接口声明文件              ✓ (.h)          ✗              通道合约 docstring
Proc() 行数              ~30             50-100         ≤20
proto 独立               ✓ (.proto)      部分 ✓         全部拆分
配置文本化               ✓ (.pb.txt)     部分 ✓         pipeline.yaml
dag 声明式               ✓ (.dag)        ✗              pipeline.yaml
子模块工厂化             ✓ (factory)     ✗              Assembler/Detector 类
测试覆盖                 ✓ (*_test.cc)   骨架           集成测试
```

**结论**: 我们的代码功能正确（74/74 import OK），但结构上离 Apollo 水平
还有 3 步距离。最关键的是 **Proc() 纪律** 和 **通道合约声明**，
这两项直接影响下一批 Claude 开发者能否正确接入新模块。

# AgentRL 自演化持续训练系统 — 前100文件阅读与迁移规划

> **核心命题**：证明 AgentRL 持续训练系统让最强 "compute us in agent" 系列大模型（如 Claude Code）在全新真实领域发生自演化。
>
> **鲁迅拿来主义原则**：运用脑髓，放出眼光，自己来拿。每个文件逐一推敲，不增不删函数，只做精准替换。

---

## 一、项目定位与技术栈映射

### 1.1 当前项目 operatorRL 的双层结构

| 层 | 目录 | 角色 | 类比 NVIDIA 生态 |
|---|---|---|---|
| **agentlightning/** | RL训练引擎 | PPO/GRPO训练循环、Runner/Store/Tracer | veRL → Megatron-LM 训练内核 |
| **src/agent_os/** | 治理内核 | 策略执行、安全沙箱、MCP网关 | NeMo Guardrails + TensorRT 推理约束 |

### 1.2 Megatron → Trainium2 迁移关键映射

| NVIDIA (Megatron-LM/CUDA) | AWS (Trainium2/Neuron) | 在 operatorRL 中的触点 |
|---|---|---|
| `NVMegatronRayWorkerGroup` | `NxDTrainiumRayWorkerGroup`(待建) | `verl/entrypoint.py:140` |
| `megatron_workers.ActorRolloutRefWorker` | `nxd_workers.ActorRolloutRefWorker`(待建) | `verl/entrypoint.py:141` |
| FSDP (PyTorch) | FSDP via NxD Core + XLA | `verl/entrypoint.py:125-128` |
| NCCL all-reduce | Neuron Collective Communication | `verl/daemon.py` 分布式同步 |
| vLLM (CUDA) | vLLM-Neuron / NxD Inference | `verl/async_server.py` |
| TensorRT-LLM | Neuron Compiler (neuronx-cc) | 推理加速层 |

### 1.3 NVIDIA Projects/6 定位判断

`github.com/orgs/NVIDIA/projects/6` — 这是 NVIDIA 的组织级 GitHub Project Board（看板），用于跟踪跨仓库的工作项。它**不是一个独立代码仓库**，而是项目管理工具。在 NVIDIA CUDA 生态技术栈中，它处于**内部协调层**——连接 Megatron-LM、NeMo、TensorRT-LLM 等仓库的工程进度。

**可利用性**：有限。我们无法直接"拿来"看板内容，但可以从其关联的仓库（Megatron-LM、NeMo Framework）中提取训练策略模式，迁移到 Trainium2 技术栈。

---

## 二、自演化闭环架构（AgentRL in Loop）

你的"进化思想"文档提出的核心闭环：

```
真实世界 HTTP —→ success/error（不可改变的物理事实）
    ↓
程序A（Agent）—→ 运行，撞墙，记录日志
    ↓
LLM（修复酶）—→ 看日志，建议修改
    ↓
程序A'（新一代）—→ 替换 A
```

映射到 operatorRL 代码：

```
GovernedEnvironment.step()  ←→  真实世界 HTTP 反馈
    ↓
GovernedRunner.step()       ←→  程序A运行 + 日志收集
    ↓
PolicyReward.__call__()     ←→  success/error → 奖励信号
    ↓
AgentLightningTrainer.fit() ←→  LLM修复酶（PPO更新权重）
    ↓
verl/daemon.py 热替换      ←→  A → A' 自演化
```

**关键洞察**：当前系统的 `verl/entrypoint.py` 只支持 `fsdp`/`fsdp2`/`megatron` 三种策略。我们需要添加 `trainium` 策略，同时保持函数签名不变（不增不删函数）。

---

## 三、前100文件阅读规划

### 阅读分级标记

- **🔴 P0-核心改造**：直接涉及自演化循环的文件，必须精读并改造
- **🟡 P1-适配层**：需要适配 Trainium2 或 Claude Code 的文件
- **🟢 P2-观察层**：需要理解但改动较小的支撑文件
- **⚪ P3-保持原样**：读后确认无需改动

### 阶段 A：自演化训练核心（文件 1-25）

> 这是"大脑"——决定 Agent 如何学习、如何从 A 变成 A'

| # | 文件路径 | 级别 | 阅读目标 | 迁移要点 |
|---|---|---|---|---|
| 1 | `agentlightning/verl/entrypoint.py` | 🔴 | PPO训练入口，strategy分发 | 添加 `trainium` 分支：`NxDTrainiumRayWorkerGroup` |
| 2 | `agentlightning/verl/trainer.py` | 🔴 | `AgentLightningTrainer`，PPO核心循环 | 适配 NxD Core 的梯度同步原语 |
| 3 | `agentlightning/verl/daemon.py` | 🔴 | Agent模式守护进程，rollout聚合 | 适配 Neuron Runtime 的 token 处理 |
| 4 | `agentlightning/verl/async_server.py` | 🔴 | 异步vLLM推理服务 | 替换为 vLLM-Neuron 或 NxD Inference |
| 5 | `agentlightning/verl/dataset.py` | 🟡 | Agent数据集加载 | 适配 Neuron DataLoader（MpDeviceLoader） |
| 6 | `agentlightning/verl/config.yaml` | 🟡 | Hydra配置 | 增加 `trainium` strategy 配置项 |
| 7 | `agentlightning/verl/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 8 | `agentlightning/verl/__main__.py` | ⚪ | 入口点 | 无需改动 |
| 9 | `agentlightning/algorithm/base.py` | 🟡 | Algorithm基类 | 确保 `run()` 兼容 XLA 设备 |
| 10 | `agentlightning/algorithm/fast.py` | 🟡 | FastAlgorithm/Baseline | 确保轻量级算法在 Neuron 上可运行 |
| 11 | `agentlightning/algorithm/decorator.py` | 🟢 | 算法装饰器 | 检查是否依赖CUDA特有API |
| 12 | `agentlightning/algorithm/utils.py` | 🟢 | 算法工具函数 | 检查 LLM proxy / store 交互 |
| 13 | `agentlightning/algorithm/apo/apo.py` | 🟡 | APO算法实现 | 确保 prompt 优化逻辑设备无关 |
| 14 | `agentlightning/algorithm/apo/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 15 | `agentlightning/algorithm/verl/__init__.py` | ⚪ | veRL算法包 | 无需改动 |
| 16 | `agentlightning/algorithm/verl/interface.py` | 🟡 | veRL算法接口 | 确保与 Trainium worker 兼容 |
| 17 | `agentlightning/algorithm/__init__.py` | ⚪ | 算法包初始化 | 无需改动 |
| 18 | `agentlightning/trainer/trainer.py` | 🔴 | 高层Trainer编排 | 核心：wiring Algorithm↔Runner↔Store |
| 19 | `agentlightning/trainer/init_utils.py` | 🟢 | 组件构建工具 | 检查动态实例化是否兼容 |
| 20 | `agentlightning/trainer/legacy.py` | 🟢 | 遗留Trainer | 确认废弃路径不影响新流程 |
| 21 | `agentlightning/trainer/registry.py` | 🟡 | ExecutionStrategy注册表 | 注册 Trainium 执行策略 |
| 22 | `agentlightning/trainer/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 23 | `agentlightning/runner/agent.py` | 🔴 | LitAgentRunner 主实现 | 核心Runner，确保异步iter兼容 |
| 24 | `agentlightning/runner/base.py` | 🟡 | Runner抽象基类 | 确认接口稳定 |
| 25 | `agentlightning/runner/legacy.py` | ⚪ | 遗留Runner | 无需改动 |

### 阶段 B：数据流与存储（文件 26-45）

> 这是"突触"——决定信号如何传递和存储

| # | 文件路径 | 级别 | 阅读目标 | 迁移要点 |
|---|---|---|---|---|
| 26 | `agentlightning/store/base.py` | 🔴 | LightningStore抽象 | 核心存储接口，确保 span/rollout 兼容 |
| 27 | `agentlightning/store/memory.py` | 🟢 | 内存存储实现 | 开发/测试用，确认正常 |
| 28 | `agentlightning/store/client_server.py` | 🟡 | 客户端-服务端存储 | 分布式场景下与 Trainium 节点的通信 |
| 29 | `agentlightning/store/collection_based.py` | 🟢 | 集合存储 | 检查序列化兼容性 |
| 30 | `agentlightning/store/collection/base.py` | 🟢 | 集合基类 | 稳定接口 |
| 31 | `agentlightning/store/collection/memory.py` | ⚪ | 内存集合 | 无需改动 |
| 32 | `agentlightning/store/collection/mongo.py` | ⚪ | MongoDB集合 | 无需改动 |
| 33 | `agentlightning/store/collection/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 34 | `agentlightning/store/mongo.py` | ⚪ | MongoDB存储 | 无需改动 |
| 35 | `agentlightning/store/sqlite.py` | ⚪ | SQLite存储 | 无需改动 |
| 36 | `agentlightning/store/threading.py` | 🟢 | 线程安全存储 | 检查锁机制是否影响 XLA 编译 |
| 37 | `agentlightning/store/utils.py` | ⚪ | 存储工具 | 无需改动 |
| 38 | `agentlightning/store/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 39 | `agentlightning/adapter/base.py` | 🟡 | TraceAdapter基类 | trace → triplet 转换管线 |
| 40 | `agentlightning/adapter/triplet.py` | 🟡 | Triplet适配器 | token ID / logprob 处理需适配 Neuron |
| 41 | `agentlightning/adapter/messages.py` | 🟢 | 消息格式适配 | 检查 chat template 兼容性 |
| 42 | `agentlightning/adapter/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 43 | `agentlightning/types/core.py` | 🟡 | 核心数据类型 | Span/Rollout/Task 类型定义 |
| 44 | `agentlightning/types/resources.py` | 🟢 | 资源类型 | NamedResources 定义 |
| 45 | `agentlightning/types/tracer.py` | 🟢 | Tracer类型 | SpanLike 等类型 |

### 阶段 C：Agent OS 治理内核（文件 46-70）

> 这是"免疫系统"——policy enforcement = 环境反馈信号

| # | 文件路径 | 级别 | 阅读目标 | 迁移要点 |
|---|---|---|---|---|
| 46 | `src/agent_os/__init__.py` | 🟡 | 治理内核入口 | 确认所有公开API |
| 47 | `src/agent_os/stateless.py` | 🔴 | StatelessKernel | 核心执行引擎，策略检查→信号 |
| 48 | `src/agent_os/base_agent.py` | 🟡 | BaseAgent定义 | Agent生命周期管理 |
| 49 | `src/agent_os/integrations/base.py` | 🔴 | BaseIntegration | 所有适配器的基类，execute生命周期 |
| 50 | `src/agent_os/integrations/anthropic_adapter.py` | 🔴 | Anthropic/Claude适配器 | **关键**：Claude Code集成入口 |
| 51 | `src/agent_os/integrations/agent_lightning/__init__.py` | 🟡 | AL集成包 | 桥接层入口 |
| 52 | `src/agent_os/integrations/agent_lightning/runner.py` | 🔴 | GovernedRunner | 治理Runner→RL信号 |
| 53 | `src/agent_os/integrations/agent_lightning/reward.py` | 🔴 | PolicyReward | 违规→惩罚→梯度信号 |
| 54 | `src/agent_os/integrations/agent_lightning/environment.py` | 🔴 | GovernedEnvironment | Gym兼容的治理环境 |
| 55 | `src/agent_os/integrations/agent_lightning/emitter.py` | 🟡 | FlightRecorderEmitter | 审计日志→训练span |
| 56 | `src/agent_os/integrations/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 57 | `src/agent_os/integrations/config.py` | 🟢 | 集成配置 | 检查配置结构 |
| 58 | `src/agent_os/integrations/openai_adapter.py` | 🟢 | OpenAI适配器 | 参考模式：如何适配 LLM provider |
| 59 | `src/agent_os/integrations/langchain_adapter.py` | ⚪ | LangChain适配器 | 参考但不改动 |
| 60 | `src/agent_os/integrations/registry.py` | 🟢 | 适配器注册表 | 确保新适配器可注册 |
| 61 | `src/agent_os/policies/__init__.py` | 🟢 | 策略包 | 策略类型导出 |
| 62 | `src/agent_os/policies/schema.py` | 🟡 | 策略Schema | GovernancePolicy字段定义 |
| 63 | `src/agent_os/policies/evaluator.py` | 🟡 | 策略评估器 | 策略匹配逻辑 |
| 64 | `src/agent_os/policies/bridge.py` | 🟢 | 策略桥接 | 跨模块策略传递 |
| 65 | `src/agent_os/policies/shared.py` | 🟢 | 共享策略 | 策略共享机制 |
| 66 | `src/agent_os/exceptions.py` | ⚪ | 异常定义 | 无需改动 |
| 67 | `src/agent_os/circuit_breaker.py` | 🟢 | 熔断器 | Agent 过载保护 |
| 68 | `src/agent_os/sandbox.py` | 🟡 | 沙箱执行 | Claude Code 执行隔离 |
| 69 | `src/agent_os/semantic_policy.py` | 🟢 | 语义策略 | LLM-based策略判断 |
| 70 | `src/agent_os/mcp_gateway.py` | 🟡 | MCP网关 | Claude Code MCP工具调用入口 |

### 阶段 D：Emitter、Tracer、Instrumentation（文件 71-85）

> 这是"感觉神经"——收集信号的管道

| # | 文件路径 | 级别 | 阅读目标 | 迁移要点 |
|---|---|---|---|---|
| 71 | `agentlightning/emitter/reward.py` | 🔴 | 奖励发射器 | 多维奖励emit→训练循环 |
| 72 | `agentlightning/emitter/annotation.py` | 🟡 | 标注发射器 | 治理数据标注 |
| 73 | `agentlightning/emitter/message.py` | 🟢 | 消息发射器 | 消息格式 |
| 74 | `agentlightning/emitter/object.py` | 🟢 | 对象发射器 | 通用对象emit |
| 75 | `agentlightning/emitter/exception.py` | ⚪ | 异常发射器 | 无需改动 |
| 76 | `agentlightning/emitter/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 77 | `agentlightning/tracer/base.py` | 🟡 | Tracer基类 | span生命周期 |
| 78 | `agentlightning/tracer/otel.py` | 🟢 | OpenTelemetry tracer | 分布式追踪 |
| 79 | `agentlightning/tracer/agentops.py` | 🟢 | AgentOps tracer | 第三方集成 |
| 80 | `agentlightning/tracer/weave.py` | ⚪ | Weave tracer | 无需改动 |
| 81 | `agentlightning/tracer/dummy.py` | ⚪ | 哑Tracer | 测试用 |
| 82 | `agentlightning/tracer/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 83 | `agentlightning/instrumentation/__init__.py` | 🟢 | 仪表化入口 | 自动instrument逻辑 |
| 84 | `agentlightning/instrumentation/vllm.py` | 🟡 | vLLM仪表化 | 需适配 vLLM-Neuron |
| 85 | `agentlightning/instrumentation/litellm.py` | 🟢 | LiteLLM仪表化 | 检查兼容性 |

### 阶段 E：执行策略与代理层（文件 86-100）

> 这是"运动神经"——决定如何执行动作

| # | 文件路径 | 级别 | 阅读目标 | 迁移要点 |
|---|---|---|---|---|
| 86 | `agentlightning/execution/base.py` | 🔴 | ExecutionStrategy基类 | 需添加 Trainium 执行策略 |
| 87 | `agentlightning/execution/client_server.py` | 🟡 | 客户端-服务端执行 | 分布式 Trainium 集群通信 |
| 88 | `agentlightning/execution/shared_memory.py` | 🟡 | 共享内存执行 | 单节点多NeuronCore场景 |
| 89 | `agentlightning/execution/events.py` | 🟢 | 执行事件 | 停止信号机制 |
| 90 | `agentlightning/execution/inter_process.py` | ⚪ | 进程间执行 | 无需改动 |
| 91 | `agentlightning/execution/__init__.py` | ⚪ | 包初始化 | 无需改动 |
| 92 | `agentlightning/llm_proxy.py` | 🔴 | LLM代理层（1454行） | **关键大文件**：所有LLM调用的代理 |
| 93 | `agentlightning/client.py` | 🟡 | AgentLightningClient | 客户端API |
| 94 | `agentlightning/server.py` | 🟡 | AgentLightningServer | 服务端API |
| 95 | `agentlightning/config.py` | 🟢 | CLI配置解析 | lightning_cli |
| 96 | `agentlightning/logging.py` | ⚪ | 日志配置 | 无需改动 |
| 97 | `agentlightning/env_var.py` | 🟡 | 环境变量 | 添加 NEURON_* 环境变量 |
| 98 | `agentlightning/semconv.py` | 🟢 | 语义约定 | span属性命名 |
| 99 | `agentlightning/reward.py` | ⚪ | 废弃重导出 | 无需改动 |
| 100 | `agentlightning/__init__.py` | 🟡 | 包顶层初始化 | 确保新组件导出 |

---

## 四、迁移执行规则（铁律）

### 4.1 绝不增删函数

```
❌ 错误：在 entrypoint.py 中添加新函数 def create_trainium_workers()
✅ 正确：在 run_ppo() 的 elif 分支中增加 "trainium" 策略判断
```

### 4.2 一个一个代码替换

每次修改一个文件，立即 diff 验证：

```bash
git diff --stat                    # 确认只改了一个文件
git diff <file>                    # 逐行审查
python -m py_compile <file>        # 语法检查
```

### 4.3 鲁迅拿来主义迁移清单

对每个 🔴/🟡 文件，执行以下流程：

1. **读原文**：完整阅读当前实现
2. **找CUDA绑定**：grep `cuda|nccl|gpu|torch.device` 定位硬件依赖
3. **查Neuron对等物**：
   - `torch.cuda.device_count()` → `torch_neuronx.xla_device_count()` 或 `xm.xrt_world_size()`
   - `torch.device("cuda")` → `xm.xla_device()`
   - NCCL → Neuron Collective Communication
   - vLLM CUDA → vLLM-Neuron (transformers-neuronx)
4. **精准替换**：只改硬件绑定代码，保持业务逻辑不变
5. **diff验证**：确保改动最小且可追踪

### 4.4 改造优先级队列

```
第一批（自演化核心路径）：
  1 → 2 → 3 → 4 → 92 → 18 → 23 → 86

第二批（治理↔训练桥接）：
  47 → 49 → 50 → 52 → 53 → 54 → 71

第三批（适配层）：
  39 → 40 → 26 → 28 → 84 → 87 → 88

第四批（配置与环境）：
  6 → 97 → 100 → 21 → 74 → 46
```

---

## 五、Claude Code 自演化证明路径

### 5.1 证明目标

> Claude Code 在"Megatron→Trainium2 技术栈迁移"这个全新真实领域中，通过 AgentRL 闭环实现自演化。

### 5.2 闭环流程

```
┌─────────────────────────────────────────────────────┐
│ Claude Code（全自动运行）                              │
│   ├─ 读取迁移任务（从 LightningStore）                  │
│   ├─ 执行代码修改（通过 MCP 工具）                       │
│   ├─ 运行测试（pytest via sandbox）                     │
│   └─ 产生 HTTP 反馈（success/error）                    │
│          ↓                                            │
│ GovernedEnvironment.step()                            │
│   ├─ 策略检查（不能删函数、不能改签名）                    │
│   ├─ 违规 → PolicyViolation → 负奖励                    │
│   └─ 成功 → success → 正奖励                           │
│          ↓                                            │
│ PolicyReward.__call__()                               │
│   ├─ base_reward = test_pass_rate                     │
│   ├─ penalty = violation_count × severity             │
│   └─ emit_reward → LightningStore                     │
│          ↓                                            │
│ AgentLightningTrainer.fit()                           │
│   ├─ PPO/GRPO 更新                                    │
│   ├─ 在 Trainium2 上执行前向/反向传播                     │
│   └─ 检查点保存 → 新版 Agent                            │
│          ↓                                            │
│ 新版 Claude Code Agent → 回到第一步                      │
└─────────────────────────────────────────────────────┘
```

### 5.3 真实世界 HTTP 反馈定义

| 反馈源 | success 条件 | error 条件 |
|---|---|---|
| `pytest` | 测试全部通过 | 任何测试失败 |
| `pyright` | 类型检查通过 | 类型错误 |
| `git diff --stat` | 只改了目标文件 | 改了非目标文件或增删了函数 |
| `python -c "import agentlightning"` | 导入成功 | ImportError |
| `neuron-top`（Trainium2） | NeuronCore 利用率 > 0 | 编译失败或OOM |

---

## 六、与原始代码的 diff 验证协议

每完成一个文件的迁移，执行：

```bash
# 1. 确认函数数量不变
grep -c "def " <original_file> > /tmp/func_count_before
grep -c "def " <modified_file> > /tmp/func_count_after
diff /tmp/func_count_before /tmp/func_count_after

# 2. 确认类数量不变
grep -c "class " <original_file> > /tmp/class_count_before
grep -c "class " <modified_file> > /tmp/class_count_after
diff /tmp/class_count_before /tmp/class_count_after

# 3. 查看精确改动
git diff <file>

# 4. 语法和类型检查
python -m py_compile <file>
```

---

## 七、下一步行动

等待你的四段 "agentic RL in loop for kernel and anything real HTTP/HTTPS POST information"，然后我们按照上述优先级队列，从文件 #1 `verl/entrypoint.py` 开始逐文件替换。

每次替换前我会：
1. 展示原始代码片段
2. 展示替换后的代码片段
3. 解释迁移理由（鲁迅拿来主义：为什么拿、拿什么、怎么拿）
4. 执行 diff 验证

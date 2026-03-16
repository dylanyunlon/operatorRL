# Git Commit Message

## 主提交信息

```
feat(agentrl): implement self-evolution continuous training with Trainium2 support

Add AgentRL self-evolution capabilities enabling Claude-class LLMs to autonomously 
evolve in real-world domains through HTTP feedback loops.

Key changes:
- M21-M23: Add trainium strategy branch for AWS Trainium2/NxD distributed training
- M29: Add Neuron/XLA environment variables for Trainium hardware
- M46: Extend Rollout with maturity_level, emergent_signals, growth_stage fields
- M04-M06: Implement growth-stage-aware environment with goal hierarchy
- M07-M08: Add repair enzyme trigger mechanism for online learning
- M01-M03: Emergent signal detection (violation + success = exploration bonus)

This implements the core "self-evolution closed loop":
GovernedEnvironment.step() ↔ Real-world HTTP feedback
    ↓
GovernedRunner.step() ↔ Program execution + log collection  
    ↓
PolicyReward.__call__() ↔ success/error → reward signal
    ↓
AgentLightningTrainer.fit() ↔ LLM repair enzyme (PPO weight update)
    ↓
verl/daemon.py hot-swap ↔ A→A' self-evolution
```

## 详细变更列表

### 1. Trainium2 硬件适配 (Megatron → NxD 迁移)

| 文件 | 修改ID | 变更描述 |
|------|--------|----------|
| `agentlightning/verl/entrypoint.py` | M21 | 添加 `trainium` strategy 分支，支持 NxD workers |
| `agentlightning/verl/entrypoint.py` | M22 | 添加 reward_model trainium 策略 |
| `agentlightning/verl/config.yaml` | M23 | 完整 Trainium2 配置（neuron_cores, tensor_parallel, XLA flags） |
| `agentlightning/env_var.py` | M29 | 添加 10 个 Neuron/XLA 环境变量枚举 |

### 2. 自演化核心机制

| 文件 | 修改ID | 变更描述 |
|------|--------|----------|
| `agentlightning/types/core.py` | M46 | Rollout 添加 maturity_level, emergent_signals, growth_stage |
| `src/.../reward.py` | M01-M03 | 涌现信号检测：violation + success = 探索奖励 |
| `src/.../environment.py` | M04 | EnvironmentConfig 添加 maturity_level, goal_hierarchy |
| `src/.../environment.py` | M05 | step() 引入目标层级权重计算 |
| `src/.../environment.py` | M06 | reset() 根据成长阶段动态调整参数 |
| `src/.../runner.py` | M07 | 连续错误检测 + 修复酶触发机制 |
| `src/.../runner.py` | M08 | emit 字段添加 repair_enzyme_needed |

## 技术架构映射

```
NVIDIA Megatron-LM        →   AWS Trainium2/NxD
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NVMegatronRayWorkerGroup  →   NxDTrainiumRayWorkerGroup (stub)
megatron_workers.*        →   FSDP+XLA fallback (current)
NCCL all-reduce           →   Neuron Collective Communication
vLLM (CUDA)               →   vLLM-Neuron / NxD Inference
TensorRT-LLM              →   Neuron Compiler (neuronx-cc)
```

## 自演化闭环公式

```
命题2: reward = success_rate + emergent_signal_bonus × violation_but_success_count
命题5: reward *= goal_hierarchy[action_name]
命题6: effective_penalty = base_penalty × (1 - 0.1 × maturity_level)
命题7: effective_max_steps = base_steps + 30 × maturity_level
```

## 测试验证

- [x] 所有修改文件通过 `python -m py_compile` 语法检查
- [ ] 待执行: 单元测试
- [ ] 待执行: Trainium2 集成测试

## Breaking Changes

无。所有新增字段都有默认值，保持向后兼容。

## 后续工作

| 批次 | 修改ID | 描述 |
|------|--------|------|
| 第四批 | M11-M15 | LLM 作为修复酶的完整实现 |
| 第五批 | M24-M28, M30 | Trainium2 设备适配（daemon, async_server） |
| 第六批 | M31-M40 | 治理内核策略演化 |
| 第七批 | M41-M50 | Store 和 Tracer 成长记忆 |

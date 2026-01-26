# Agent OS

**A Safety-First Kernel for Autonomous AI Agents**

Agent OS is a deterministic governance framework that provides POSIX-inspired primitives for AI agent systems. Unlike efficiency-focused alternatives (AIOS), Agent OS guarantees **0% policy violation** through kernel-level enforcement.

## Philosophy: Scale by Subtraction

- **Kernel, not SaaS**: We build Linux for Agents, not ServiceNow for Agents
- **CLI-first**: Engineers prefer `agentctl` over drag-and-drop
- **Safety over Speed**: Kernel panic on policy violation, not graceful degradation
- **POSIX-inspired**: Signals, VFS, Pipes - familiar primitives for systems engineers

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    USER SPACE (Untrusted)                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  LLM Generation  │  Agent Logic  │  Tool Execution  │   │
│  └─────────────────────────────────────────────────────┘   │
│                         ▲ Syscalls ▼                        │
├─────────────────────────────────────────────────────────────┤
│                   KERNEL SPACE (Trusted)                    │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────────────┐   │
│  │ Policy  │ │ Flight  │ │ Signal  │ │ Agent VFS       │   │
│  │ Engine  │ │Recorder │ │Dispatch │ │ /mem /state     │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Packages

| Layer | Package | Description |
|-------|---------|-------------|
| **L1: Primitives** | `primitives` | Failure types, base models |
| **L1: Primitives** | `cmvk` | Cross-model verification, drift detection |
| **L1: Primitives** | `caas` | Context-as-a-Service, RAG pipeline |
| **L1: Primitives** | `emk` | Episodic Memory Kernel |
| **L2: Infrastructure** | `iatp` | Inter-Agent Trust Protocol |
| **L2: Infrastructure** | `amb` | Agent Message Bus |
| **L2: Infrastructure** | `atr` | Agent Tool Registry |
| **L3: Framework** | `control-plane` | Governance kernel, signals, VFS |
| **L4: Intelligence** | `scak` | Self-Correcting Agent Kernel |
| **L4: Intelligence** | `mute-agent` | Reasoning/Execution decoupling |

## Installation

```bash
# Full stack
pip install agent-os[all]

# Individual packages
pip install agent-os[control-plane]
pip install agent-os[iatp]
pip install agent-os[scak]
```

## Quick Start

```python
from agent_os import KernelSpace, AgentSignal, AgentVFS

# Create kernel
kernel = KernelSpace()

# Create agent context (user space)
ctx = kernel.create_agent_context("agent-001")

# Agent operations go through syscalls
await ctx.write("/mem/working/task.txt", "Analyze data")
data = await ctx.read("/mem/working/task.txt")

# Signal handling
ctx.signals.signal(AgentSignal.SIGSTOP)  # Pause for inspection
ctx.signals.signal(AgentSignal.SIGCONT)  # Resume

# Policy violation = kernel panic (non-catchable)
# kernel.panic("Policy violation: unauthorized action")
```

## Key Features

### POSIX-Style Signals
```python
AgentSignal.SIGSTOP   # Pause execution (shadow mode)
AgentSignal.SIGKILL   # Immediate termination (non-maskable)
AgentSignal.SIGPOLICY # Policy violation (triggers SIGKILL)
```

### Agent Virtual File System
```
/mem/working/    # Ephemeral scratchpad
/mem/episodic/   # Experience logs
/mem/semantic/   # Facts (vector store mount)
/state/          # Checkpoints for rollback
/policy/         # Read-only policy files
```

### Typed IPC Pipes
```python
# Unix-style piping with policy enforcement
pipeline = (
    research_agent
    | PolicyCheckPipe(allowed_types=["ResearchResult"])
    | summary_agent
)
result = await pipeline.execute(query)
```

## Comparison with AIOS

| Aspect | AIOS | Agent OS |
|--------|------|----------|
| **Focus** | Efficiency (throughput) | Safety (0% violations) |
| **Failure Mode** | Graceful degradation | Kernel panic |
| **Memory Model** | Short/Long-term | VFS with mount points |
| **Signal Handling** | None | POSIX-style |
| **Crash Isolation** | Same process | Kernel/User space |

## Research

Agent OS is designed for both production use and academic research:

- **Target Venue**: ASPLOS 2026
- **Novel Contribution**: Safety-first kernel design for AI agents
- **Key Innovation**: eBPF-enforced policy at packet level (future)

## License

MIT License - See [LICENSE](LICENSE) for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

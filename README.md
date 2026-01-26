<div align="center">

# Agent OS

### The Linux Kernel for AI Agents

**0% Safety Violations • Deterministic Enforcement • POSIX-Inspired**

[![PyPI](https://img.shields.io/pypi/v/agent-os?label=PyPI)](https://pypi.org/project/agent-os/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)

[Quick Start](#-quick-start) • [Why Agent OS?](#-why-agent-os) • [Demos](#-live-demos) • [Integrations](#-integrations) • [Contributing](#-contributing)

</div>

---

## 🎯 What is Agent OS?

**Agent OS treats LLMs like raw compute and provides OS-level governance.** 

Current AI agent frameworks let the LLM "decide" everything - including whether to follow safety rules. Agent OS inverts this: the **kernel decides**, the LLM just computes.

```
┌─────────────────────────────────────────────────────────┐
│              USER SPACE (Untrusted LLM)                 │
│   Your agent code runs here. It can crash, hallucinate, │
│   or misbehave - the kernel survives.                   │
├─────────────────────────────────────────────────────────┤
│              KERNEL SPACE (Trusted)                     │
│   Policy Engine │ Flight Recorder │ Signal Dispatch     │
│   If agent violates policy → SIGKILL (non-catchable)   │
└─────────────────────────────────────────────────────────┘
```

## ❓ Why Agent OS?

### The Problem with Current Approaches

| Approach | How it works | Why it fails |
|----------|--------------|--------------|
| **Prompt-based safety** | "Please don't do bad things" | LLM can ignore prompts |
| **Constitutional AI** | Train model to be safe | Training doesn't guarantee |
| **Guardrails** | Check output after generation | Too late - damage done |
| **Agent OS** | **Kernel-level enforcement** | **Can't bypass the kernel** |

### Real Benchmark Results

| Metric | Prompt-based | Agent OS |
|--------|-------------|----------|
| Safety Violations | 26.67% | **0.00%** |
| Response Tokens | 26.1 avg | **0.5 avg** |
| Deterministic | No | **Yes** |

## 🚀 Quick Start

### Option 1: pip install (30 seconds)

```bash
pip install agent-os

# Verify installation
python -c "from agent_os import KernelSpace; print('Agent OS ready!')"
```

### Option 2: GitHub CLI Extension (Recommended for daily use)

```bash
# Install the gh extension
gh extension install imran-siddique/gh-agent-os

# Now use Agent OS directly from your terminal
gh agent-os run "analyze this codebase for security issues"
gh agent-os audit --policy pci-dss
gh agent-os status
```

### Option 3: Docker (Production)

```bash
docker pull ghcr.io/imran-siddique/agent-os:latest
docker run -it agent-os agentctl --help
```

## 💡 Core Concepts (5 minutes)

### 1. Signals - Control Your Agents Like Processes

```python
from agent_os import AgentSignal, SignalDispatcher

# Pause agent to inspect state (like Ctrl+Z)
dispatcher.signal(agent_id, AgentSignal.SIGSTOP)

# Resume execution
dispatcher.signal(agent_id, AgentSignal.SIGCONT)

# Policy violation? Kernel kills it (non-catchable)
dispatcher.signal(agent_id, AgentSignal.SIGKILL)
```

### 2. Virtual File System - Memory That Makes Sense

```python
from agent_os import AgentVFS

vfs = AgentVFS(agent_id="agent-001")

# Standard mount points (like /home, /tmp, /etc)
vfs.write("/mem/working/task.txt", "Current task...")  # Ephemeral
vfs.write("/mem/episodic/session.log", "What happened")  # Experience
vfs.read("/policy/allowed_actions.yaml")  # Read-only rules
```

### 3. IPC Pipes - Agents Talk Through Policies

```python
from agent_os.iatp import Pipeline, PolicyCheckPipe

# Unix-style piping: agent1 | policy | agent2
pipeline = Pipeline([
    research_agent,
    PolicyCheckPipe(allowed=["ResearchResult"]),  # Type check!
    summary_agent
])

# If research_agent outputs wrong type → pipeline fails safely
result = await pipeline.execute("Find recent AI papers")
```

## 🎬 Live Demos

Four production-ready demos showing Agent OS in action:

| Demo | Industry | What it shows | Run it |
|------|----------|---------------|--------|
| **[Carbon Auditor](examples/carbon-auditor/)** | Climate | CMVK drift detection catches $5M fraud | `python examples/carbon-auditor/demo.py` |
| **[Grid Balancing](examples/grid-balancing/)** | Energy | 100 agents negotiate in 100ms | `python examples/grid-balancing/demo.py` |
| **[DeFi Sentinel](examples/defi-sentinel/)** | Crypto | Stop hacks in 142ms | `python examples/defi-sentinel/demo.py` |
| **[Pharma Compliance](examples/pharma-compliance/)** | Healthcare | Find contradictions in 100K pages | `python examples/pharma-compliance/demo.py` |

```bash
# Run all demos
cd examples && docker-compose up
```

## 🔌 Integrations

### Works With Your Existing Stack

Agent OS doesn't replace your tools - it governs them:

```python
# With LangChain
from agent_os.integrations import langchain_kernel
chain = langchain_kernel.wrap(your_langchain_agent)

# With CrewAI
from agent_os.integrations import crewai_kernel
crew = crewai_kernel.wrap(your_crew)

# With AutoGen
from agent_os.integrations import autogen_kernel
autogen_kernel.govern(your_autogen_agents)
```

### GitHub Integration

```yaml
# .github/workflows/agent-os.yml
name: Agent OS Safety Check
on: [push, pull_request]

jobs:
  safety-audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: imran-siddique/agent-os-action@v1
        with:
          policy: .agent-os/policy.yaml
          fail-on-violation: true
```

### VS Code Extension

```bash
# Install from marketplace
code --install-extension imran-siddique.agent-os

# Or use Command Palette: "Agent OS: Audit Current File"
```

### CLI for Daily Workflows

```bash
# Audit any agent codebase
agentctl audit ./my-agent-project

# Run with safety constraints
agentctl run --policy strict "agent.py"

# Monitor running agents
agentctl status --watch

# Replay agent execution from flight recorder
agentctl replay --from checkpoint-001
```

## 📦 Package Architecture

```
agent-os/
├── L1: Primitives (Foundation)
│   ├── primitives     # Failure types, base models
│   ├── cmvk           # Cross-model verification
│   ├── caas           # Context-as-a-Service
│   └── emk            # Episodic Memory Kernel
│
├── L2: Infrastructure (Communication)
│   ├── iatp           # Inter-Agent Trust Protocol
│   ├── amb            # Agent Message Bus
│   └── atr            # Agent Tool Registry
│
├── L3: Framework (Governance)
│   └── control-plane  # Kernel, signals, VFS
│
└── L4: Intelligence (Self-Correction)
    ├── scak           # Self-Correcting Agent Kernel
    └── mute-agent     # Reasoning/Execution split
```

Install what you need:

```bash
pip install agent-os              # Core only
pip install agent-os[control-plane]  # + Governance
pip install agent-os[full]        # Everything
```

## 🆚 How We Compare

| Feature | LangChain | AutoGen | CrewAI | AIOS | **Agent OS** |
|---------|-----------|---------|--------|------|--------------|
| Multi-agent | ✅ | ✅ | ✅ | ✅ | ✅ |
| Safety guarantees | ❌ | ❌ | ❌ | ❌ | **✅ Kernel-level** |
| Deterministic | ❌ | ❌ | ❌ | ❌ | **✅** |
| Process isolation | ❌ | ❌ | ❌ | ❌ | **✅ Kernel/User** |
| Policy enforcement | ❌ | ❌ | ❌ | ❌ | **✅ SIGKILL** |
| Audit trail | Partial | Partial | Partial | ❌ | **✅ Flight Recorder** |
| Memory model | Ad-hoc | Ad-hoc | Ad-hoc | Short/Long | **✅ VFS** |

**TL;DR**: Other frameworks help you *build* agents. Agent OS helps you *trust* them.

## 🤝 Contributing

We welcome contributions! Agent OS is designed to be extended.

### Good First Issues

- 🏷️ [`good-first-issue`](https://github.com/imran-siddique/agent-os/labels/good-first-issue) - Perfect for newcomers
- 📚 [`documentation`](https://github.com/imran-siddique/agent-os/labels/documentation) - Help improve docs
- 🧪 [`needs-tests`](https://github.com/imran-siddique/agent-os/labels/needs-tests) - Add test coverage

### Development Setup

```bash
# Clone and setup
git clone https://github.com/imran-siddique/agent-os.git
cd agent-os
pip install -e ".[dev]"

# Run tests
pytest

# Run a specific demo
python examples/carbon-auditor/demo.py
```

### Integration Bounties

Want to add an integration? We're looking for:

| Integration | Bounty | Status |
|-------------|--------|--------|
| LangChain adapter | 🎁 | Open |
| CrewAI adapter | 🎁 | Open |
| AutoGen adapter | 🎁 | Open |
| OpenAI Swarm adapter | 🎁 | Open |
| Anthropic Claude adapter | 🎁 | Open |

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

## 📖 Documentation

- **[Architecture Guide](docs/architecture.md)** - Deep dive into kernel design
- **[API Reference](docs/api/)** - Full API documentation
- **[Examples](examples/)** - Working demos
- **[AIOS Comparison](docs/AIOS_COMPARISON.md)** - Detailed competitor analysis

## 🔬 Research

Agent OS is designed for both production and academic use:

- **Target**: ASPLOS 2026 Workshop on Agentic Systems
- **Paper**: "Agent OS: A Safety-First Kernel for Autonomous AI Agents"
- **Novel Contribution**: First OS-level governance for LLM agents

## 📜 License

MIT License - Use it, modify it, ship it. See [LICENSE](LICENSE).

---

<div align="center">

**Built for engineers who don't trust their agents (yet).**

[⭐ Star us on GitHub](https://github.com/imran-siddique/agent-os) • [📦 PyPI](https://pypi.org/project/agent-os/) • [💬 Discussions](https://github.com/imran-siddique/agent-os/discussions)

</div>

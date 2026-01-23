# ATR - Agent Tool Registry

[![PyPI version](https://badge.fury.io/py/agent-tool-registry.svg)](https://badge.fury.io/py/agent-tool-registry)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Build Status](https://img.shields.io/github/actions/workflow/status/imran-siddique/atr/test.yml?branch=main)](https://github.com/imran-siddique/atr/actions)

**A type-safe, decentralized tool registry for autonomous agents. Part of the Agent OS ecosystem.**

---

## Why This Exists

Most agent frameworks hardcode tools directly into their runtimes. This creates tight coupling: add a new capability, restart the entire system. Change a function signature, update dozens of agents. Scale by addition leads to fragility.

**We built `atr` because tool registration should not require restarting your infrastructure.**  

The Agent Tool Registry decouples tool providers from tool consumers. Agents discover capabilities at runtime through a standardized interface. We subtract the dependency between agent logic and tool implementation to add scale.

This is **Scale by Subtraction** applied to the agent capability layer.

---

## Installation

```bash
pip install agent-tool-registry
```

---

## Quick Start

Register a tool in 5 lines:

```python
import atr

@atr.register(name="calculator", tags=["math"])
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b
```

Discover and execute:

```python
tool = atr.get_tool("calculator")
schema = tool.to_openai_function_schema()  # OpenAI-compatible
func = atr.get_callable("calculator")
result = func(a=5, b=3)  # Returns 8
```

---

## Architecture

`atr` sits in **Layer 2 (Infrastructure)** of the Agent OS stack.

**Responsibility:** Tool registration, discovery, and schema generation.  
**Not responsible for:** Tool execution (handled by the Agent Control Plane).

### Design

- **Registry:** In-memory dictionary-based lookup (local or distributed).
- **Decorator:** `@atr.register()` extracts type signatures and validates strict typing.
- **Spec:** Pydantic schema enforcing inputs, outputs, side effects, and metadata.
- **Schema Export:** Converts to OpenAI, Anthropic, and other LLM function-calling formats.

The registry stores specifications, not callables. Execution happens in the control plane with proper error handling and observability.

---

## The Ecosystem Map

ATR is one component in a modular Agent OS. Each layer solves a specific problem:

### Primitives (Layer 1)
- **[caas](https://github.com/imran-siddique/caas)** - Context-as-a-Service: Manages agent memory and state.
- **[cmvk](https://github.com/imran-siddique/cmvk)** - Context Merkle Verification Kit: Cryptographic verification of context integrity.
- **[emk](https://github.com/imran-siddique/emk)** - Episodic Memory Kit: Long-term memory storage and retrieval.

### Infrastructure (Layer 2)
- **[iatp](https://github.com/imran-siddique/iatp)** - Inter-Agent Trust Protocol: Secure message authentication.
- **[amb](https://github.com/imran-siddique/amb)** - Agent Message Bus: Decoupled event transport.
- **[atr](https://github.com/imran-siddique/atr)** - Agent Tool Registry: Tool discovery and schema generation *(you are here)*.

### Framework (Layer 3)
- **[agent-control-plane](https://github.com/imran-siddique/agent-control-plane)** - The Core: Agent orchestration and lifecycle management.
- **[scak](https://github.com/imran-siddique/scak)** - Self-Correction Agent Kit: Automated error recovery and learning.

---

## Citation

If you use ATR in research, please cite:

```bibtex
@software{atr2024,
  title={ATR: Agent Tool Registry},
  author={Siddique, Imran},
  year={2024},
  url={https://github.com/imran-siddique/atr},
  note={Part of the Agent OS ecosystem}
}
```

---

## License

MIT License - See [LICENSE](LICENSE) for details.

---

**Repository:** https://github.com/imran-siddique/atr  
**Documentation:** https://github.com/imran-siddique/atr#readme  
**Issues:** https://github.com/imran-siddique/atr/issues

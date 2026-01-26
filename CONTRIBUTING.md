# Contributing to Agent OS

Thank you for your interest in contributing! Agent OS is designed to be extended by the community.

## 🚀 Quick Start (5 minutes)

```bash
# Clone and install
git clone https://github.com/imran-siddique/agent-os.git
cd agent-os
pip install -e ".[dev]"

# Run tests to make sure everything works
pytest tests/ -v

# Run a demo
python examples/carbon-auditor/demo.py
```

## 🏷️ Good First Issues

New to the project? Start here:

| Label | Description |
|-------|-------------|
| [`good-first-issue`](https://github.com/imran-siddique/agent-os/labels/good-first-issue) | Small, well-defined tasks |
| [`documentation`](https://github.com/imran-siddique/agent-os/labels/documentation) | Improve docs and examples |
| [`needs-tests`](https://github.com/imran-siddique/agent-os/labels/needs-tests) | Add test coverage |

## 🎁 Integration Bounties

We're actively looking for integration contributions:

| Integration | Description | Status |
|-------------|-------------|--------|
| **LangChain** | Wrap LangChain agents | 🟡 Starter code in `integrations/` |
| **CrewAI** | Wrap CrewAI crews | 🟡 Starter code in `integrations/` |
| **AutoGen** | Wrap Microsoft AutoGen | 🟡 Starter code in `integrations/` |
| **OpenAI Swarm** | Wrap OpenAI's Swarm | 🔴 Open |
| **LlamaIndex** | Wrap LlamaIndex agents | 🔴 Open |

See `src/agent_os/integrations/` for the adapter pattern.

## 📁 Project Structure

```
agent-os/
├── src/agent_os/        # Main package (re-exports everything)
│   ├── __init__.py      # Unified imports
│   ├── cli.py           # agentctl CLI
│   └── integrations/    # Framework adapters
├── packages/            # Individual kernel modules
│   ├── primitives/      # L1: Base types
│   ├── cmvk/            # L1: Verification
│   ├── iatp/            # L2: Trust protocol
│   ├── control-plane/   # L3: Kernel
│   └── scak/            # L4: Self-correction
├── examples/            # Working demos
│   ├── carbon-auditor/
│   ├── grid-balancing/
│   ├── defi-sentinel/
│   └── pharma-compliance/
├── docs/                # Documentation
├── tests/               # Integration tests
└── gh-extension/        # GitHub CLI extension
```

## 🧪 Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific layer
pytest tests/test_layer1_primitives.py -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run demos (integration test)
python examples/carbon-auditor/demo.py --scenario both
python examples/grid-balancing/demo.py --agents 10
python examples/defi-sentinel/demo.py --attack all
python examples/pharma-compliance/demo.py --reports 10
```

## 📝 Code Style

```bash
# Format (we use ruff)
ruff format .

# Lint
ruff check .

# Type check
mypy src/
```

## 🔀 Pull Request Process

1. **Fork** the repository
2. **Create branch**: `git checkout -b feature/my-feature`
3. **Make changes** (follow the design philosophy below)
4. **Test**: `pytest tests/ -v`
5. **Commit**: `git commit -m "feat: add my feature"`
6. **Push**: `git push origin feature/my-feature`
7. **Open PR** with description of changes

### Commit Message Convention

```
feat: add new feature
fix: fix a bug
docs: documentation only
test: add tests
refactor: code change that neither fixes a bug nor adds a feature
```

## 🎯 Design Philosophy

**"Scale by Subtraction"** - We value simplicity over features.

### We ✅ Want

- POSIX-inspired primitives (signals, VFS, pipes)
- CLI-first interfaces
- Safety guarantees (0% violation)
- Kernel/user space separation
- Minimal dependencies

### We ❌ Avoid

- Visual workflow editors
- CRM/ERP connectors  
- Low-code builders
- Feature bloat
- Vendor lock-in

## 📚 Layer Guidelines

| Layer | May Depend On | Focus |
|-------|---------------|-------|
| **L1: Primitives** | Nothing | Pure types, zero deps |
| **L2: Infrastructure** | L1 | Protocols, transport |
| **L3: Framework** | L1, L2 | Governance, kernel |
| **L4: Intelligence** | L1, L2, L3 | Self-correction |

## 💬 Getting Help

- **Questions?** Open a [Discussion](https://github.com/imran-siddique/agent-os/discussions)
- **Found a bug?** Open an [Issue](https://github.com/imran-siddique/agent-os/issues)
- **Want to chat?** See the README for community links

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

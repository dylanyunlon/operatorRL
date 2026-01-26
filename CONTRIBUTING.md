# Contributing to Agent OS

Thank you for your interest in contributing to Agent OS!

## Development Setup

```bash
# Clone the repository
git clone https://github.com/imran-siddique/agent-os.git
cd agent-os

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v
```

## Project Structure

```
agent-os/
├── src/agent_os/     # Unified package (re-exports)
├── packages/         # Individual packages
│   ├── primitives/   # Layer 1
│   ├── cmvk/         # Layer 1
│   ├── caas/         # Layer 1
│   ├── emk/          # Layer 1
│   ├── iatp/         # Layer 2
│   ├── amb/          # Layer 2
│   ├── atr/          # Layer 2
│   ├── control-plane/# Layer 3
│   ├── scak/         # Layer 4
│   └── mute-agent/   # Layer 4
├── examples/         # Example implementations
├── docs/             # Documentation
└── tests/            # Integration tests
```

## Code Style

We use:
- **ruff** for linting
- **black** for formatting
- **mypy** for type checking

```bash
# Format code
black src/ packages/ tests/

# Lint
ruff check src/ packages/ tests/

# Type check
mypy src/ packages/
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific layer tests
pytest tests/test_layer1_primitives.py -v
pytest tests/test_layer3_framework.py -v

# With coverage
pytest tests/ --cov=packages --cov-report=html
```

## Pull Request Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`pytest tests/ -v`)
5. Commit (`git commit -m 'Add amazing feature'`)
6. Push (`git push origin feature/amazing-feature`)
7. Open a Pull Request

## Design Philosophy

**Scale by Subtraction** - We value:
- ✅ POSIX-inspired primitives (signals, VFS, pipes)
- ✅ CLI-first interfaces (`agentctl`)
- ✅ Safety over efficiency (0% violation guarantee)
- ✅ Kernel/user space separation

We avoid:
- ❌ Visual workflow editors
- ❌ CRM connectors
- ❌ Low-code builders
- ❌ Feature bloat

## Layer Guidelines

### Layer 1 (Primitives)
- Zero or minimal dependencies
- Pure functions preferred
- No agent-specific logic

### Layer 2 (Infrastructure)
- Can depend on Layer 1
- Protocol definitions
- Transport mechanisms

### Layer 3 (Framework)
- Can depend on Layers 1-2
- Governance and control
- Zero external deps where possible

### Layer 4 (Intelligence)
- Can depend on Layers 1-3
- Agent-specific logic
- Self-correction, learning

## Questions?

Open an issue or reach out to the maintainers.

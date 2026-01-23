# Inter-Agent Trust Protocol (IATP)

[![PyPI version](https://badge.fury.io/py/inter-agent-trust-protocol.svg)](https://pypi.org/project/inter-agent-trust-protocol/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

> **"Envoy for Agents"** - The infrastructure layer that makes the "Internet of Agents" possible.

IATP is a **lightweight sidecar proxy** that extracts trust, security, and governance concerns from AI agents. Just like Envoy transformed microservices by extracting networking concerns, IATP extracts trust concerns into a standardized protocol.

## 🎯 The Problem

Current LLM agents operate in a **"Zero-Trust Void"**:

- ❌ **No Discovery**: Agents can't discover what other agents are capable of
- ❌ **No Trust Verification**: No way to verify claims about reversibility, privacy, or SLAs
- ❌ **Blind Context Sharing**: Sensitive data shared without validation
- ❌ **Cascading Hallucinations**: Errors propagate through agent chains
- ❌ **No Audit Trail**: No record of who did what and why

## ✅ The Solution

IATP provides a sidecar that handles:

1. **Capability Discovery** - What can this agent do?
2. **Trust Negotiation** - Should I trust this agent with my data?
3. **Policy Enforcement** - Block dangerous operations, warn about risky ones
4. **Transaction Tracking** - Full audit trail for reversibility
5. **Privacy Protection** - Automatic PII detection and scrubbing

## 🚀 Quick Start

### Installation

```bash
pip install inter-agent-trust-protocol
```

### One-Line Docker Deploy

```bash
docker compose up -d
```

### Run Sidecar Directly

```bash
# Set environment
export IATP_AGENT_URL=http://localhost:8000
export IATP_AGENT_ID=my-agent

# Start sidecar
uvicorn iatp.main:app --port 8081
```

### Test It

```bash
# Health check
curl http://localhost:8081/health

# Get agent capabilities
curl http://localhost:8081/.well-known/agent-manifest

# Send a request
curl -X POST http://localhost:8081/proxy \
  -H "Content-Type: application/json" \
  -d '{"action": "transfer", "amount": 100}'
```

## 📊 Key Results

Our [cascading hallucination experiment](experiments/README.md) demonstrates:

| Group | IATP Protection | Failure Rate |
|-------|-----------------|--------------|
| Control | ❌ None | **100%** (Malicious DELETE executed) |
| Test | ✅ Enabled | **0%** (BLOCKED by policy) |

## 🏗️ Architecture

```
┌─────────────┐         ┌──────────────┐         ┌─────────────┐
│   Client    │ ──────> │ IATP Sidecar │ ──────> │ Your Agent  │
│             │         │  (Port 8081) │         │ (Port 8000) │
└─────────────┘         └──────────────┘         └─────────────┘
                              ▼
                    ┌─────────────────────┐
                    │  Policy Engine      │ (built-in)
                    │  Security Checks    │ (PII detection)
                    │  Flight Recorder    │ (audit trail)
                    │  Recovery Engine    │ (scak)
                    └─────────────────────┘
```

## 🔑 Features

### Capability Manifest (The Handshake)

Every agent publishes a manifest describing its guarantees:

```json
{
  "agent_id": "secure-bank-agent",
  "trust_level": "verified_partner",
  "capabilities": {
    "reversibility": "full",
    "idempotency": true,
    "sla_latency_ms": 2000
  },
  "privacy": {
    "retention_policy": "ephemeral",
    "human_in_loop": false
  }
}
```

### Trust Score (0-10)

Automatic trust calculation based on manifest properties:

| Score | Action |
|-------|--------|
| ≥ 7 | ✅ Allow immediately |
| 3-6 | ⚠️ Warn (requires override) |
| < 3 | ⚠️ Warn (requires override) |
| Critical violation | 🚫 Block (403) |

### User Override ("Be an Advisor, Not a Nanny")

```bash
# First attempt: Get warning
curl -X POST http://localhost:8081/proxy \
  -H "Content-Type: application/json" \
  -d '{"task": "risky_operation"}'
# Returns: 449 Retry With {"requires_override": true}

# Second attempt: Override
curl -X POST http://localhost:8081/proxy \
  -H "Content-Type: application/json" \
  -H "X-User-Override: true" \
  -d '{"task": "risky_operation"}'
# Returns: 200 OK (marked as quarantined)
```

### CLI Tools

```bash
# Verify a manifest
iatp verify examples/manifests/secure_bank.json

# Scan a running agent
iatp scan http://localhost:8081
```

## 📁 Project Structure

```
├── iatp/                    # Core Python package
│   ├── main.py              # Standalone sidecar application
│   ├── cli.py               # CLI tools (iatp verify, iatp scan)
│   ├── policy_engine.py     # Policy validation (built-in)
│   ├── recovery.py          # Failure recovery (scak)
│   ├── models/              # Data models (CapabilityManifest, etc.)
│   ├── sidecar/             # FastAPI sidecar proxy
│   ├── security/            # PII detection, trust scoring
│   └── telemetry/           # Flight recorder, tracing
│
├── sidecar/go/              # High-performance Go sidecar
├── examples/                # Working examples
├── experiments/             # Research experiments
├── docs/                    # Documentation
├── spec/                    # Protocol specification
└── paper/                   # Research paper
```

## 🧪 Testing

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest iatp/tests/ -v

# Run with coverage
pytest iatp/tests/ --cov=iatp
```

## 📚 Documentation

- **[Architecture Guide](docs/ARCHITECTURE.md)** - System design and components
- **[Deployment Guide](docs/DEPLOYMENT.md)** - Installation and configuration
- **[CLI Guide](docs/CLI_GUIDE.md)** - Command-line tools
- **[Examples](examples/README.md)** - Working code examples
- **[Experiments](experiments/README.md)** - Research experiments
- **[Changelog](CHANGELOG.md)** - Version history

## 🎯 Design Philosophy

### "Scale by Subtraction"
Strip trust logic out of agents → Put it in the sidecar  
Strip logging out of agents → Put it in the flight recorder  
Result: Agents stay simple, infrastructure handles the hard parts.

### "Agnostic by Design"
Works with any language, any framework, any LLM provider.  
The protocol is the interface, not the implementation.

### "Be an Advisor, Not a Nanny"
Users always have the final say, but they make informed decisions.  
Complete transparency about risks with full accountability.

## 🤝 Contributing

We welcome contributions! Key areas:

- **Protocol Evolution**: Trust levels, reversibility patterns, privacy policies
- **Implementation**: Go/Rust sidecar, additional SDKs
- **Security**: Additional PII patterns, trust algorithms
- **Research**: Experiments, benchmarks, papers

## 📄 License

MIT License - see [LICENSE](LICENSE) file for details.

## 🔗 Links

- **PyPI**: [inter-agent-trust-protocol](https://pypi.org/project/inter-agent-trust-protocol/)
- **GitHub**: [imran-siddique/inter-agent-trust-protocol](https://github.com/imran-siddique/inter-agent-trust-protocol)
- **Issues**: [GitHub Issues](https://github.com/imran-siddique/inter-agent-trust-protocol/issues)

---

**Welcome to the Agent Mesh. Welcome to IATP.**


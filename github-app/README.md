# Agent OS Governance Bot

A GitHub App that provides zero-config, automated governance reviews on pull requests.

## Features

- **Prompt injection scanning** — Detects injection patterns in code, configs, and prompts
- **Policy compliance** — Validates against configurable governance rule sets
- **Security pattern scanning** — Catches secrets, unsafe patterns, dangerous code
- **Inline review comments** — Annotates specific lines with findings and fix suggestions
- **Check run integration** — Pass/fail status blocking merge on critical findings

## Quick Start

1. Install the app from [GitHub Marketplace](#) (coming soon)
2. Optionally add `.github/agent-governance.yml` to your repo to customize
3. Every PR gets reviewed automatically

## Configuration

Create `.github/agent-governance.yml` in your repository:

```yaml
# Governance profile: security | compliance | agent-safety | all
profile: security

# Severity threshold for blocking merge (error = blocks, warning = advisory)
block_on: error

# Files to scan (glob patterns)
include:
  - "**/*.py"
  - "**/*.yaml"
  - "**/*.yml"
  - "**/*.json"
  - "**/*.md"

# Files to skip
exclude:
  - "node_modules/**"
  - "*.lock"
  - "dist/**"

# Custom blocked patterns (in addition to profile defaults)
custom_patterns:
  - pattern: "TODO.*hack"
    severity: warning
    message: "Suspicious TODO comment"
```

## Governance Profiles

### `security` (default)
- Prompt injection patterns in code/config files
- Hardcoded secrets and API keys
- Dangerous code patterns (eval, exec, subprocess)
- Insecure configuration values

### `compliance`
- License header checks
- PII patterns in code
- Audit logging requirements
- Data retention policy references

### `agent-safety`
- Agent prompt files for injection vulnerabilities
- MCP server configuration safety
- Tool allowlist/blocklist validation
- Trust configuration review

## Architecture

```
GitHub PR Event (webhook)
    ↓
Webhook Handler (Azure Functions / Vercel)
    ↓
File Analyzer
    ├─ PromptInjectionDetector
    ├─ SecurityPatternScanner
    └─ PolicyEvaluator
    ↓
Review Builder → GitHub Check Run + PR Review Comments
```

## Development

```bash
cd github-app
pip install -r requirements.txt
python -m pytest tests/ -v

# Local testing with webhook proxy
python app.py
```

## Deployment

See [deployment guide](docs/deployment.md) for Azure Functions and Vercel setup.

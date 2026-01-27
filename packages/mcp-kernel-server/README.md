# MCP Kernel Server

**Agent OS kernel primitives via Model Context Protocol (MCP)**

This server exposes Agent OS capabilities through MCP, enabling any MCP-compatible client (Claude Desktop, etc.) to use kernel-level AI agent governance.

## Quick Start

### Claude Desktop Integration (Recommended)

1. Install the server:
```bash
pip install agent-os[mcp]
```

2. Add to Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):
```json
{
  "mcpServers": {
    "agent-os": {
      "command": "mcp-kernel-server",
      "args": ["--stdio"]
    }
  }
}
```

3. Restart Claude Desktop. You now have access to:
- **Tools**: cmvk_verify, kernel_execute, iatp_sign, iatp_verify, iatp_reputation
- **Resources**: Agent VFS (memory, policies, audit)
- **Prompts**: governed_agent, verify_claim, safe_execution

### Development Mode

```bash
# HTTP transport for testing
mcp-kernel-server --http --port 8080

# List available tools
mcp-kernel-server --list-tools

# List available prompts
mcp-kernel-server --list-prompts
```

## Available Tools

### `cmvk_verify` - Cross-Model Verification
Verify claims across multiple AI models to detect hallucinations.

```json
{
  "name": "cmvk_verify",
  "arguments": {
    "claim": "The capital of France is Paris",
    "threshold": 0.85
  }
}
```

Returns:
```json
{
  "verified": true,
  "confidence": 0.95,
  "drift_score": 0.05,
  "models_checked": ["gpt-4", "claude-sonnet-4", "gemini-pro"],
  "interpretation": "Strong consensus across all models."
}
```

### `kernel_execute` - Governed Execution
Execute actions through the kernel with policy enforcement.

```json
{
  "name": "kernel_execute",
  "arguments": {
    "action": "database_query",
    "params": {"query": "SELECT * FROM users"},
    "agent_id": "analyst-001",
    "policies": ["read_only", "no_pii"]
  }
}
```

### `iatp_sign` - Trust Attestation
Sign agent outputs for inter-agent trust.

```json
{
  "name": "iatp_sign",
  "arguments": {
    "attester_id": "security-scanner-001",
    "subject_id": "analyst-001",
    "trust_level": "verified_partner"
  }
}
```

### `iatp_verify` - Trust Verification
Verify trust before agent-to-agent communication.

```json
{
  "name": "iatp_verify",
  "arguments": {
    "source_agent": "agent-a",
    "target_agent": "agent-b",
    "action": "share_data"
  }
}
```

### `iatp_reputation` - Reputation Network
Query or modify agent reputation.

```json
{
  "name": "iatp_reputation",
  "arguments": {
    "agent_id": "agent-001",
    "action": "query"
  }
}
```

## Available Resources

| URI Template | Description |
|-------------|-------------|
| `vfs://{agent_id}/mem/working/{key}` | Ephemeral working memory |
| `vfs://{agent_id}/mem/episodic/{session}` | Experience logs |
| `vfs://{agent_id}/policy/{name}` | Policies (read-only) |
| `audit://{agent_id}/log` | Audit trail (read-only) |

## Available Prompts

### `governed_agent`
Instructions for operating as a governed agent.
```
Arguments: agent_id (required), policies
```

### `verify_claim`
Template for CMVK verification.
```
Arguments: claim (required)
```

### `safe_execution`
Template for safe action execution.
```
Arguments: action (required), params (required)
```

## Stateless Design (MCP June 2026)

This server is **stateless by design**:

- ✅ No session state maintained
- ✅ All context passed in each request
- ✅ State externalized to backend storage
- ✅ Horizontally scalable

```python
# Every request is self-contained
result = await kernel.execute(
    action="database_query",
    context={
        "agent_id": "analyst-001",
        "policies": ["read_only"],
        "history": [...]  # Passed, not stored
    }
)
```

## Configuration Options

```bash
mcp-kernel-server --stdio                    # Claude Desktop (default)
mcp-kernel-server --http --port 8080         # Development
mcp-kernel-server --policy-mode strict       # Policy mode: strict|permissive|audit
mcp-kernel-server --cmvk-threshold 0.90      # CMVK confidence threshold
```

## Python Integration

```python
from mcp import ClientSession

async with ClientSession() as session:
    await session.connect("http://localhost:8080")
    
    # Verify a claim
    result = await session.call_tool("cmvk_verify", {
        "claim": "The earth is approximately 4.5 billion years old"
    })
    
    # Execute with governance
    result = await session.call_tool("kernel_execute", {
        "action": "send_email",
        "params": {"to": "user@example.com", "body": "..."},
        "agent_id": "email-agent",
        "policies": ["no_pii"]
    })
```

## License

MIT

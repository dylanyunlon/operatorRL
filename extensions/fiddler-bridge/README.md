# Fiddler Bridge

Async MCP client for [Fiddler Everywhere](https://www.telerik.com/fiddler/fiddler-everywhere) traffic capture, built for the operatorRL agentic training pipeline.

## Architecture

```
Game Client → TCP/UDP → Fiddler Everywhere (HTTPS/WebSocket intercept)
    → Fiddler MCP Server (localhost:8868)
        → FiddlerBridgeClient (this module)
            → SessionCapturePipeline (buffer + dispatch)
                → DataSanitizer (PII removal)
                    → protocol-decoder (game-specific parsing)
                        → AgentOS GovernedEnvironment.step()
```

## Modules

| Module | Description |
|---|---|
| `client.py` | Async MCP client with retry, rate-limiting, auto-reconnect |
| `session_capture.py` | Buffered capture pipeline with callback dispatch |
| `reverse_proxy.py` | Reverse proxy rule management for game traffic |
| `sanitizer.py` | PII removal and data redaction with audit trail |

## Quick Start

```python
from fiddler_bridge.client import FiddlerBridgeClient, FiddlerBridgeConfig, FilterCriteria

async with FiddlerBridgeClient(FiddlerBridgeConfig(port=8868)) as client:
    health = await client.health_check()
    sessions = await client.get_sessions(limit=50)
    await client.apply_filters(FilterCriteria(url_pattern="*liveclientdata*"))
```

## Configuration

See `config/fiddler_mcp.yaml` for all options.

## Testing

```bash
PYTHONPATH=src pytest tests/ -v
```

# ATR - Agent Tool Registry

**"The Hands"** - A decentralized marketplace for agent capabilities.

[![PyPI version](https://badge.fury.io/py/atr-core.svg)](https://badge.fury.io/py/atr-core)
[![Python Support](https://img.shields.io/pypi/pyversions/atr-core.svg)](https://pypi.org/project/atr-core/)

## Core Value Proposition

ATR allows an agent to say, *"I need a tool that can scrape websites,"* and receive a standardized interface, regardless of who built the tool or where it lives.

## Installation

```bash
pip install atr-core
```

## Quick Start

### 1. Register a Tool

Use the `@atr.register()` decorator to turn any Python function into a discoverable tool:

```python
import atr

@atr.register(name="web_scraper", cost="low", tags=["web", "scraping"])
def scrape_website(url: str, timeout: int = 30) -> str:
    """Scrape content from a website.
    
    Args:
        url: The URL to scrape
        timeout: Request timeout in seconds
    """
    # Your implementation here
    import requests
    response = requests.get(url, timeout=timeout)
    return response.text
```

**Important:** All parameters MUST have type hints - no magic arguments allowed!

### 2. Discover Tools

```python
import atr

# Search for tools
tools = atr._global_registry.search_tools("scrape")

# List all tools with specific properties
web_tools = atr._global_registry.list_tools(tag="web")
low_cost_tools = atr._global_registry.list_tools(cost=atr.CostLevel.LOW)
```

### 3. Get Tool Specification

```python
import atr

# Get the tool spec (doesn't execute it!)
tool_spec = atr._global_registry.get_tool("web_scraper")

# Convert to OpenAI function calling format
openai_schema = tool_spec.to_openai_function_schema()
print(openai_schema)
```

### 4. Execute a Tool (Control Plane's Job)

The registry **does NOT execute tools** - it only stores and returns them. Execution is the responsibility of the Agent Runtime (Control Plane):

```python
import atr

# Get the callable (but don't execute yet!)
scraper_func = atr._global_registry.get_callable("web_scraper")

# Now the Agent Runtime can execute it with proper error handling
try:
    result = scraper_func(url="https://example.com", timeout=10)
    print(result)
except Exception as e:
    # Handle errors in the Control Plane
    print(f"Tool execution failed: {e}")
```

## Architecture

### The Spec

A rigorous Pydantic schema defines:
- **Inputs**: Strictly typed parameters (no magic arguments!)
- **Outputs**: Return value specification
- **Side Effects**: What the tool does (read, write, network, etc.)
- **Metadata**: Name, description, cost, version, author, tags

### The Registry

A lightweight lookup mechanism (local dictionary-based):
- Stores tool specifications
- Enables tool discovery and search
- Returns function objects (doesn't execute them)

### The Decorator

`@atr.register()` decorator that:
- Auto-extracts function signature
- Converts to ToolSpec schema
- Validates type hints are present
- Registers in the global registry
- Returns original function unchanged

## Key Design Principles

### ✅ Do's

1. **Use strict typing**: Every parameter must have a type hint
2. **Keep it simple**: Registry is just a lookup mechanism
3. **Separate concerns**: Registry stores, Agent Runtime executes

### ❌ Don'ts (Anti-Patterns)

1. **Don't execute tools in the registry**: Return the function, don't call it
2. **Don't allow magic arguments**: All parameters must be typed
3. **Don't hardcode specific agents**: Tools are standalone functions

## Supported Types

Parameters can be any of these types:
- `str` → ParameterType.STRING
- `int` → ParameterType.INTEGER
- `float` → ParameterType.NUMBER
- `bool` → ParameterType.BOOLEAN
- `list` → ParameterType.ARRAY
- `dict` → ParameterType.OBJECT

## Advanced Usage

### Custom Registry

```python
from atr import Registry, register

# Create a custom registry
my_registry = Registry()

@register(name="custom_tool", registry=my_registry)
def my_tool(data: str) -> str:
    return data.upper()
```

### Tool Metadata

```python
import atr

@atr.register(
    name="file_reader",
    cost="low",
    side_effects=["read", "filesystem"],
    tags=["file", "io"],
    version="1.0.0",
    author="Your Name"
)
def read_file(path: str, encoding: str = "utf-8") -> str:
    """Read a file from disk."""
    with open(path, "r", encoding=encoding) as f:
        return f.read()
```

### OpenAI Function Calling Integration

```python
import atr

# Get tool spec
tool_spec = atr._global_registry.get_tool("file_reader")

# Convert to OpenAI format
function_schema = tool_spec.to_openai_function_schema()

# Use with OpenAI API
response = openai.ChatCompletion.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Read config.json"}],
    functions=[function_schema],
)
```

## Dependencies

- `pydantic>=2.0.0` - For schema validation
- Python standard library only

**Strictly forbidden dependencies:**
- ❌ agent-control-plane (tools are standalone)
- ❌ mute-agent (no hardcoded agents)

## Contributing

Contributions are welcome! Please ensure:
1. All parameters have type hints
2. Registry doesn't execute tools
3. No forbidden dependencies are added
4. Tests pass

## License

MIT License

## Layer

Infrastructure (Layer 2)

## Repository

https://github.com/imran-siddique/atr

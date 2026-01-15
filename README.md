# Self-Correcting Agent Kernel

> **Your agent fails in production (blocked by agent-control-plane). Instead of you fixing it manually, this engine wakes up, analyzes the failure, simulates a better path, and patches the agent.**

## Overview

The Self-Correcting Agent Kernel is an intelligent system that automatically detects, analyzes, and fixes agent failures in production. When an agent fails (e.g., blocked by the control plane), the kernel:

1. **Detects** and classifies the failure
2. **Analyzes** the root cause
3. **Simulates** alternative paths
4. **Patches** the agent automatically

## Features

- 🔍 **Intelligent Failure Detection** - Automatically detects and classifies various failure types
- 🧠 **Root Cause Analysis** - Identifies why the agent failed with high confidence
- 🎯 **Path Simulation** - Tests alternative solutions before applying them
- 🔧 **Automatic Patching** - Applies corrections without manual intervention
- 📊 **Learning from History** - Improves over time by analyzing similar past failures
- 🔄 **Rollback Support** - Can revert patches if needed

## Installation

```bash
# Clone the repository
git clone https://github.com/imran-siddique/self-correcting-agent-kernel.git
cd self-correcting-agent-kernel

# Install dependencies
pip install -r requirements.txt

# Install the package
pip install -e .
```

## Quick Start

```python
from agent_kernel import SelfCorrectingAgentKernel

# Initialize the kernel
kernel = SelfCorrectingAgentKernel()

# When an agent fails, the kernel wakes up and fixes it
result = kernel.wake_up_and_fix(
    agent_id="my-agent-001",
    error_message="Action blocked by control plane: Unauthorized access",
    context={
        "action": "delete_file",
        "resource": "/etc/passwd"
    }
)

# The agent is now patched and ready to go!
print(f"Patch Applied: {result['patch_applied']}")
print(f"Success Rate: {result['simulation'].estimated_success_rate:.2%}")
```

## Usage Examples

### Example 1: Control Plane Blocking

```python
from agent_kernel import SelfCorrectingAgentKernel

kernel = SelfCorrectingAgentKernel()

# Agent blocked by control plane
result = kernel.handle_failure(
    agent_id="agent-file-processor-001",
    error_message="Action blocked by control plane: Unauthorized file access",
    context={
        "action": "delete_file",
        "resource": "/etc/passwd",
        "reason": "Permission denied"
    },
    auto_patch=True
)

# View the results
print(f"Root Cause: {result['analysis'].root_cause}")
print(f"Suggested Fixes: {result['analysis'].suggested_fixes}")
print(f"Alternative Path: {len(result['simulation'].alternative_path)} steps")
```

### Example 2: Timeout Handling

```python
kernel = SelfCorrectingAgentKernel()

result = kernel.handle_failure(
    agent_id="agent-data-processor-002",
    error_message="Operation timed out after 10 seconds",
    context={
        "action": "process_large_dataset",
        "dataset_size": "10GB"
    }
)

# The kernel automatically adds timeout handling
print(f"Patch Type: {result['patch'].patch_type}")
```

### Example 3: Learning from History

```python
kernel = SelfCorrectingAgentKernel()

# First failure
kernel.handle_failure(
    agent_id="agent-api-caller",
    error_message="Action blocked: Invalid API endpoint",
    context={"endpoint": "/admin/users"}
)

# Second similar failure - higher confidence
result = kernel.handle_failure(
    agent_id="agent-api-caller",
    error_message="Action blocked: Invalid API endpoint",
    context={"endpoint": "/admin/settings"}
)

# Confidence improves with similar failures
print(f"Confidence: {result['analysis'].confidence_score:.2%}")
```

## Architecture

The kernel consists of four main components:

### 1. Failure Detector
- Detects and classifies failures
- Tracks failure history
- Supports custom failure handlers

### 2. Failure Analyzer
- Identifies root causes
- Finds similar past failures
- Suggests fixes based on patterns

### 3. Path Simulator
- Simulates alternative solutions
- Calculates risk scores
- Estimates success rates

### 4. Agent Patcher
- Creates correction patches
- Applies patches automatically
- Supports rollback

## Supported Failure Types

- `BLOCKED_BY_CONTROL_PLANE` - Agent actions blocked by security policies
- `TIMEOUT` - Operations that exceed time limits
- `INVALID_ACTION` - Unsupported or invalid operations
- `RESOURCE_EXHAUSTED` - Memory, disk, or quota limits exceeded
- `LOGIC_ERROR` - Algorithm or implementation errors

## API Reference

### SelfCorrectingAgentKernel

Main kernel class that orchestrates the self-correction process.

#### Methods

- `handle_failure(agent_id, error_message, context=None, auto_patch=True)` - Handle an agent failure
- `wake_up_and_fix(agent_id, error_message, context=None)` - Convenience method for automatic fixing
- `get_agent_status(agent_id)` - Get current agent status
- `rollback_patch(patch_id)` - Rollback a previously applied patch
- `get_failure_history(agent_id=None, limit=100)` - Get failure history
- `get_patch_history(agent_id=None)` - Get patch history

## Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run specific test
python -m pytest tests/test_kernel.py -v

# Run with coverage
python -m pytest tests/ --cov=agent_kernel
```

## Running Examples

```bash
# Run the basic example
python examples/basic_example.py
```

This will demonstrate:
- Control plane blocking detection and fixing
- Timeout failure handling
- Learning from multiple failures

## Configuration

The kernel can be configured with custom settings:

```python
config = {
    "log_level": "INFO",
    "auto_patch": True,
    "risk_threshold": 0.5,
    "success_rate_threshold": 0.7
}

kernel = SelfCorrectingAgentKernel(config=config)
```

## How It Works

1. **Failure Detection**: When an agent fails, the detector classifies the failure type and severity

2. **Analysis**: The analyzer identifies the root cause by:
   - Matching against known patterns
   - Finding similar past failures
   - Analyzing the context and error message

3. **Simulation**: The simulator creates an alternative path by:
   - Building steps from suggested fixes
   - Calculating risk scores
   - Estimating success rates

4. **Patching**: If the simulation succeeds, the patcher:
   - Creates a correction patch
   - Applies the patch to the agent
   - Tracks the patch for potential rollback

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see LICENSE file for details

## Support

For questions or issues, please open an issue on GitHub.

---

**Note**: This is a demonstration system. In production, you would integrate with actual agent control planes, implement real patching mechanisms, and add additional safety measures.

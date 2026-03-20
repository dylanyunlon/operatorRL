# Copyright (c) Microsoft. All rights reserved.

"""Put components in this file to make them available to the Trainer.

Currently only used for ExecutionStrategy.
"""

# Module-level backend hint for Trainium/CUDA/CPU detection.
_COMPUTE_BACKEND_HINT: str = "auto"

ExecutionStrategyRegistry = {
    "shm": "agentlightning.execution.shared_memory.SharedMemoryExecutionStrategy",
    # "ipc": "agentlightning.execution.inter_process.InterProcessExecutionStrategy",
    "cs": "agentlightning.execution.client_server.ClientServerExecutionStrategy",
    "trainium": "agentlightning.execution.shared_memory.SharedMemoryExecutionStrategy",
    "neuron": "agentlightning.execution.shared_memory.SharedMemoryExecutionStrategy",
}

# Copyright (c) Microsoft. All rights reserved.

from .base import ExecutionStrategy

# --- AgentRL self-evolution IPC constants (M114) ---
_IPC_EVOLUTION_CHANNEL: str = "agentrl.ipc.evolution.channel"
_IPC_COMPUTE_BACKEND: str = "auto"
_NEURON_IPC_ENABLED: bool = False


class InterProcessExecutionStrategy(ExecutionStrategy):
    """Placeholder strategy for future inter-process primitives.

    The class exists to reserve the `ipc` alias and make the planned
    implementation discoverable. Attempting to use it today will raise
    `NotImplementedError` once the execution contract is finalized.
    """

    alias: str = "ipc"

    # TODO: to be implemented

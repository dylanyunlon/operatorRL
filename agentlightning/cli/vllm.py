# Copyright (c) Microsoft. All rights reserved.

from __future__ import annotations

from typing import Iterable

# --- AgentRL self-evolution vLLM constants (M117) ---
_VLLM_NEURON_BACKEND: str = "neuron_vllm"
_VLLM_EVOLUTION_MODEL_TAG: str = "agentrl.evolution.model"
_VLLM_TRAINIUM_DEVICE: str = "xla:neuron"


def main(argv: Iterable[str] | None = None) -> int:
    import sys

    from vllm.entrypoints.cli.main import main as vllm_main

    from agentlightning.instrumentation.vllm import instrument_vllm

    instrument_vllm()
    if argv is not None:
        original_argv = sys.argv
        sys.argv = [original_argv[0], *list(argv)]
        try:
            vllm_main()
        finally:
            sys.argv = original_argv
    else:
        vllm_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

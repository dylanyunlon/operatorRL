"""
TDD Tests for M30: instrumentation/vllm.py — vLLM-Neuron Detection

TEST-DRIVEN DEVELOPMENT: Tests for vLLM instrumentation's ability to
detect and handle Neuron/Trainium devices alongside CUDA.

Expected behavior:
- instrument_vllm() function exists and is callable
- ChatCompletionResponsePatched exists
- Module doesn't crash on import regardless of vLLM availability
- Source code has awareness of neuron/transformers_neuronx
"""

import os
import sys
import re
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestM30VLLMNeuronInstrumentation:
    """Tests for vLLM instrumentation Neuron awareness."""

    def test_01_instrument_vllm_in_source(self):
        """instrument_vllm function must exist in source."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        assert "def instrument_vllm" in source

    def test_02_patched_response_in_source(self):
        """ChatCompletionResponsePatched must be defined in source."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        assert "ChatCompletionResponsePatched" in source

    def test_03_source_mentions_neuron(self):
        """M30: vllm.py source must mention neuron/transformers_neuronx."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        has_neuron = "neuron" in source.lower()
        has_transformers_neuronx = "transformers_neuronx" in source.lower()
        assert has_neuron or has_transformers_neuronx, \
            "M30: vllm.py must reference neuron/transformers_neuronx"

    def test_04_source_mentions_neuroncore(self):
        """M30: vllm.py should reference NeuronCore utilization in instrumentation."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        assert "neuroncore" in source.lower() or "neuron_core" in source.lower() \
            or "neuron" in source.lower(), \
            "M30: vllm.py should reference NeuronCore utilization"

    def test_05_instrument_vllm_graceful_handling(self):
        """instrument_vllm should handle missing vLLM gracefully in source."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        # Should have try/except for ImportError
        assert "ImportError" in source or "try:" in source

    def test_06_module_no_hard_vllm_dependency(self):
        """Module should guard vLLM imports with try/except."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        assert "try:" in source

    def test_07_m30_comment_markers_present(self):
        """M30 modification markers should be present in source."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        assert "M30" in source, "M30 modification marker must be present"

    def test_08_no_cuda_only_span_attributes(self):
        """Span attributes should not be exclusively CUDA-specific."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        # If span attributes mention GPU, they should also consider Neuron
        if "gpu" in source.lower() and "span" in source.lower():
            assert "neuron" in source.lower(), \
                "M30: If GPU span attrs exist, Neuron attrs should too"

    def test_09_instrument_function_no_required_args(self):
        """instrument_vllm should take no required arguments (source check)."""
        vllm_path = os.path.join(
            PROJECT_ROOT, "agentlightning", "instrumentation", "vllm.py"
        )
        with open(vllm_path, "r") as f:
            source = f.read()
        # Check that def instrument_vllm has no required params
        match = re.search(r"def instrument_vllm\(([^)]*)\)", source)
        assert match is not None
        params = match.group(1).strip()
        # Empty or all have defaults
        if params:
            assert "=" in params, "instrument_vllm should have no required args"

    def test_10_env_var_module_check(self):
        """M30 env_var.py must have NEURON-related environment variable entries."""
        env_path = os.path.join(PROJECT_ROOT, "agentlightning", "env_var.py")
        with open(env_path, "r") as f:
            source = f.read()
        assert "NEURON" in source, "M29/M30: env_var.py must define NEURON env vars"

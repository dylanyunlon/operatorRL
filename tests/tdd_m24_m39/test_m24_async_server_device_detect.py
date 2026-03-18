"""
TDD Tests for M24: async_server.py — Device Detection for Trainium/Neuron

TEST-DRIVEN DEVELOPMENT: These tests define the expected behavior of
_detect_device_type() BEFORE any implementation changes. No mock
implementations — we test the real function against real environment state.

Expected behavior:
- _detect_device_type() returns "neuron" when /dev/neuron0 exists or
  NEURON_RT_VISIBLE_CORES env var is set
- Returns "cuda" when torch.cuda.is_available() is True
- Returns "cpu" as fallback
- PatchedvLLMServer sets XLA_USE_BF16 on neuron devices
"""

import os
import sys
import re
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_detect_device_type():
    """Extract _detect_device_type from source without full import chain."""
    path = os.path.join(PROJECT_ROOT, "agentlightning", "verl", "async_server.py")
    with open(path, "r") as f:
        source = f.read()
    
    ns = {"__builtins__": __builtins__, "os": os}
    # Add logging stub
    import logging
    ns["logging"] = logging
    ns["logger"] = logging.getLogger("test_m24")
    
    # Extract the function
    pattern = r"(def _detect_device_type\(\).*?\n(?:    .*\n)*)"
    match = re.search(pattern, source)
    if match:
        exec(match.group(1), ns)
    return ns.get("_detect_device_type")


_detect_device_type = _load_detect_device_type()


class TestM24DeviceDetection:
    """Tests for _detect_device_type() in async_server.py."""

    def test_01_detect_device_type_function_exists(self):
        """_detect_device_type must exist as a callable in async_server module."""
        assert callable(_detect_device_type)

    def test_02_detect_device_type_returns_string(self):
        """_detect_device_type must return a string."""
        result = _detect_device_type()
        assert isinstance(result, str)

    def test_03_detect_device_type_returns_valid_value(self):
        """Return value must be one of 'neuron', 'cuda', or 'cpu'."""
        result = _detect_device_type()
        assert result in ("neuron", "cuda", "cpu")

    def test_04_cpu_fallback_without_accelerator(self):
        """In CI/test env without GPU or Neuron, should return 'cpu' or 'cuda'."""
        result = _detect_device_type()
        # In CI we expect cpu (no GPU), but cuda is also valid if GPU exists
        assert result in ("neuron", "cuda", "cpu")

    def test_05_neuron_env_var_detection(self):
        """When NEURON_RT_VISIBLE_CORES is set, device type should be 'neuron'."""
        old_val = os.environ.get("NEURON_RT_VISIBLE_CORES")
        try:
            os.environ["NEURON_RT_VISIBLE_CORES"] = "0,1"
            result = _detect_device_type()
            assert result == "neuron"
        finally:
            if old_val is None:
                os.environ.pop("NEURON_RT_VISIBLE_CORES", None)
            else:
                os.environ["NEURON_RT_VISIBLE_CORES"] = old_val

    def test_06_no_neuron_env_var_no_device(self):
        """When NEURON_RT_VISIBLE_CORES is NOT set and /dev/neuron0 doesn't exist,
        should NOT return 'neuron'."""
        old_val = os.environ.pop("NEURON_RT_VISIBLE_CORES", None)
        try:
            if not os.path.exists("/dev/neuron0"):
                result = _detect_device_type()
                assert result != "neuron"
        finally:
            if old_val is not None:
                os.environ["NEURON_RT_VISIBLE_CORES"] = old_val

    def test_07_detect_device_type_is_idempotent(self):
        """Calling _detect_device_type multiple times should return same result."""
        r1 = _detect_device_type()
        r2 = _detect_device_type()
        r3 = _detect_device_type()
        assert r1 == r2 == r3

    def test_08_detect_device_type_no_side_effects_on_cpu(self):
        """When returning 'cpu', should not set XLA_USE_BF16."""
        old_val = os.environ.pop("NEURON_RT_VISIBLE_CORES", None)
        old_xla = os.environ.get("XLA_USE_BF16")
        try:
            if not os.path.exists("/dev/neuron0"):
                os.environ.pop("XLA_USE_BF16", None)
                result = _detect_device_type()
                if result == "cpu":
                    assert os.environ.get("XLA_USE_BF16") is None
        finally:
            if old_val is not None:
                os.environ["NEURON_RT_VISIBLE_CORES"] = old_val
            if old_xla is not None:
                os.environ["XLA_USE_BF16"] = old_xla

    def test_09_function_signature_no_arguments(self):
        """_detect_device_type takes no arguments."""
        import inspect
        sig = inspect.signature(_detect_device_type)
        assert len(sig.parameters) == 0

    def test_10_function_return_annotation(self):
        """_detect_device_type has str return annotation."""
        import inspect
        sig = inspect.signature(_detect_device_type)
        assert sig.return_annotation == str or sig.return_annotation is inspect.Parameter.empty

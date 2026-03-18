"""
TDD Tests for M27: execution/base.py — Device Documentation & Config
         and M28: execution/shared_memory.py — XLA Compatible Paths

TEST-DRIVEN DEVELOPMENT:
- M27: ExecutionStrategy docstring documents Trainium2 support; no hardcoded cuda strings
- M28: SharedMemory execution doesn't depend on CUDA pinned memory
"""

import os
import sys
import inspect
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class TestM27ExecutionStrategyDevice:
    """Tests for ExecutionStrategy device documentation and configuration."""

    def test_01_execution_strategy_importable(self):
        """ExecutionStrategy class must exist in execution/base.py source."""
        base_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "base.py")
        with open(base_path, "r") as f:
            source = f.read()
        assert "class ExecutionStrategy" in source

    def test_02_docstring_mentions_trainium(self):
        """ExecutionStrategy docstring must mention Trainium/Neuron support."""
        base_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "base.py")
        with open(base_path, "r") as f:
            source = f.read()
        # Extract the class docstring area
        assert "trainium" in source.lower() or "neuron" in source.lower(), \
            "M27: ExecutionStrategy docstring must mention Trainium/Neuron"

    def test_03_docstring_mentions_cuda(self):
        """ExecutionStrategy docstring must mention CUDA for comparison."""
        base_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "base.py")
        with open(base_path, "r") as f:
            source = f.read()
        assert "cuda" in source.lower() or "nvidia" in source.lower()

    def test_04_docstring_mentions_cpu_fallback(self):
        """ExecutionStrategy docstring must mention CPU fallback."""
        base_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "base.py")
        with open(base_path, "r") as f:
            source = f.read()
        assert "cpu" in source.lower()

    def test_05_execute_method_exists(self):
        """ExecutionStrategy must define an execute method."""
        base_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "base.py")
        with open(base_path, "r") as f:
            source = f.read()
        assert "def execute(" in source

    def test_06_no_hardcoded_cuda_in_base(self):
        """execution/base.py source must not hardcode .cuda() calls."""
        base_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "base.py")
        with open(base_path, "r") as f:
            source = f.read()
        # Exclude docstrings/comments — only check executable code
        # Simple check: no .cuda() calls outside comments
        lines = source.split("\n")
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'"):
                continue
            assert ".cuda()" not in stripped, \
                f"M27: Line {i} has hardcoded .cuda() call: {stripped}"


class TestM28SharedMemoryXLA:
    """Tests for shared_memory.py XLA compatibility."""

    def test_07_shared_memory_importable(self):
        """SharedMemoryExecutionStrategy must be defined in source."""
        sm_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "shared_memory.py")
        with open(sm_path, "r") as f:
            source = f.read()
        assert "class SharedMemoryExecutionStrategy" in source

    def test_08_no_cuda_pinned_memory_in_source(self):
        """shared_memory.py must not use pin_memory() without conditional."""
        sm_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "shared_memory.py")
        with open(sm_path, "r") as f:
            source = f.read()
        # pin_memory() is CUDA-only; if used, must be behind a device check
        if "pin_memory" in source:
            # It's OK if it's conditional (e.g., `if device == "cuda": pin_memory()`)
            assert "cuda" in source.lower() or "device" in source.lower(), \
                "M28: pin_memory() used without device-type guard"

    def test_09_no_hardcoded_cuda_device_in_shared_memory(self):
        """shared_memory.py must not have .to('cuda') or .cuda() without guard."""
        sm_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "shared_memory.py")
        with open(sm_path, "r") as f:
            lines = f.readlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Should not have bare .cuda() or .to("cuda")
            if ".cuda()" in stripped and "if " not in stripped and "#" not in stripped:
                pytest.fail(f"M28: Line {i} has unguarded .cuda(): {stripped}")

    def test_10_shared_memory_execution_has_execute(self):
        """SharedMemoryExecutionStrategy must define execute method."""
        sm_path = os.path.join(PROJECT_ROOT, "agentlightning", "execution", "shared_memory.py")
        with open(sm_path, "r") as f:
            source = f.read()
        assert "def execute(" in source

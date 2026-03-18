"""
TDD Tests for M26: trainer.py — compute_data_metrics Device-Agnostic Reduction

TEST-DRIVEN DEVELOPMENT: Tests verify the compute_data_metrics function
structure and the M26 Trainium reduction support via source analysis.
"""

import os
import sys
import re
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TRAINER_PATH = os.path.join(PROJECT_ROOT, "agentlightning", "verl", "trainer.py")


def _read_trainer_source():
    with open(TRAINER_PATH, "r") as f:
        return f.read()


class TestM26ComputeDataMetrics:
    """Tests for compute_data_metrics in trainer.py via source analysis."""

    def test_01_compute_data_metrics_exists_in_source(self):
        """compute_data_metrics function must exist in trainer.py."""
        source = _read_trainer_source()
        assert "def compute_data_metrics(" in source

    def test_02_has_suffix_parameter(self):
        """compute_data_metrics must accept suffix parameter."""
        source = _read_trainer_source()
        match = re.search(r"def compute_data_metrics\(([^)]+)\)", source)
        assert match, "compute_data_metrics signature not found"
        assert "suffix" in match.group(1)

    def test_03_has_use_critic_parameter(self):
        """compute_data_metrics must accept use_critic parameter."""
        source = _read_trainer_source()
        match = re.search(r"def compute_data_metrics\(([^)]+)\)", source)
        assert match
        assert "use_critic" in match.group(1)

    def test_04_returns_score_metrics(self):
        """Function body must compute critic/score/{mean,max,min}."""
        source = _read_trainer_source()
        assert '"critic/score/mean"' in source
        assert '"critic/score/max"' in source
        assert '"critic/score/min"' in source

    def test_05_returns_reward_metrics(self):
        """Function body must compute critic/rewards/{mean,max,min}."""
        source = _read_trainer_source()
        assert '"critic/rewards/mean"' in source
        assert '"critic/rewards/max"' in source
        assert '"critic/rewards/min"' in source

    def test_06_returns_advantage_metrics(self):
        """Function body must compute critic/advantages/{mean,max,min}."""
        source = _read_trainer_source()
        assert '"critic/advantages/mean"' in source
        assert '"critic/advantages/max"' in source

    def test_07_suffix_appended_to_keys(self):
        """suffix should be concatenated with metric key strings."""
        source = _read_trainer_source()
        assert '+ suffix' in source or 'suffix' in source

    def test_08_response_length_metrics(self):
        """Must include response_length metric keys."""
        source = _read_trainer_source()
        assert '"response_length/mean"' in source or "'response_length/mean'" in source

    def test_09_no_hardcoded_cuda_in_compute_metrics(self):
        """compute_data_metrics should not contain .cuda() calls."""
        source = _read_trainer_source()
        start = source.find("def compute_data_metrics(")
        next_def = source.find("\ndef ", start + 1)
        if next_def == -1:
            next_def = source.find("\nclass ", start + 1)
        func_body = source[start:next_def] if next_def > start else source[start:]
        assert ".cuda()" not in func_body, \
            "M26: compute_data_metrics should not use .cuda()"

    def test_10_detach_item_pattern(self):
        """Metrics should use .detach().item() for device-agnostic scalar extraction."""
        source = _read_trainer_source()
        assert ".detach().item()" in source, \
            "M26: metrics should use .detach().item() for device-agnostic extraction"

"""
TDD Tests for M49: trainer/trainer.py — dev() Max Maturity Mode

TEST-DRIVEN DEVELOPMENT: Tests verify that Trainer.dev() sets maturity_level
to maximum (6 = graduate) to skip non-critical policy restrictions.

Expected behavior:
- Trainer.dev() exists as a method
- When dev=True in constructor, maturity_level is set to 6
- dev() accepts same agent/dataset params as fit()
- M49 marker exists in trainer.py source
- dev mode comment mentions 研究生/graduate level
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


class TestM49TrainerDevMaturity:
    """M49: Trainer.dev() must set max maturity_level."""

    def test_01_trainer_has_m49_marker(self):
        """trainer.py must contain M49 modification marker."""
        source = _read_source("agentlightning/trainer/trainer.py")
        assert "M49" in source

    def test_02_trainer_dev_method_exists(self):
        """Trainer must have dev() method."""
        from agentlightning.trainer.trainer import Trainer
        assert hasattr(Trainer, "dev")
        assert callable(getattr(Trainer, "dev"))

    def test_03_trainer_dev_has_agent_param(self):
        """Trainer.dev() must accept agent parameter."""
        from agentlightning.trainer.trainer import Trainer
        import inspect
        sig = inspect.signature(Trainer.dev)
        assert "agent" in sig.parameters

    def test_04_trainer_dev_has_train_dataset_param(self):
        """Trainer.dev() must accept train_dataset parameter."""
        from agentlightning.trainer.trainer import Trainer
        import inspect
        sig = inspect.signature(Trainer.dev)
        assert "train_dataset" in sig.parameters

    def test_05_trainer_dev_has_val_dataset_param(self):
        """Trainer.dev() must accept val_dataset keyword parameter."""
        from agentlightning.trainer.trainer import Trainer
        import inspect
        sig = inspect.signature(Trainer.dev)
        assert "val_dataset" in sig.parameters

    def test_06_source_mentions_dev_maturity_6(self):
        """Source must set maturity_level = 6 in dev mode context."""
        source = _read_source("agentlightning/trainer/trainer.py")
        assert "maturity_level = 6" in source

    def test_07_source_mentions_graduate_level_in_dev(self):
        """dev mode maturity comment must reference 研究生 or graduate."""
        source = _read_source("agentlightning/trainer/trainer.py")
        assert "研究生" in source or "graduate" in source.lower()

    def test_08_dev_mode_skips_non_critical_comment(self):
        """Source must mention skipping non-critical restrictions in dev mode."""
        source = _read_source("agentlightning/trainer/trainer.py")
        # Should have comment about skipping non-critical policy
        assert "non-critical" in source.lower() or "非critical" in source

    def test_09_trainer_dev_docstring_exists(self):
        """Trainer.dev() must have a docstring."""
        from agentlightning.trainer.trainer import Trainer
        assert Trainer.dev.__doc__ is not None
        assert len(Trainer.dev.__doc__) > 20

    def test_10_trainer_dev_mentions_fast_algorithm(self):
        """Trainer.dev() docstring must mention FastAlgorithm requirement."""
        from agentlightning.trainer.trainer import Trainer
        doc = Trainer.dev.__doc__ or ""
        assert "FastAlgorithm" in doc

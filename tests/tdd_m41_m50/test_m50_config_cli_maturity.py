"""
TDD Tests for M50: config.py — CLI --maturity-level and --growth-stage Parameters

TEST-DRIVEN DEVELOPMENT: Tests verify that the CLI argument parser includes
--maturity-level and --growth-stage parameters for Agent initial growth stage.

Expected behavior:
- _create_argument_parser() includes --maturity-level (int, default 0)
- _create_argument_parser() includes --growth-stage (str, default "infant")
- Parsed namespace includes maturity_level and growth_stage attributes
- M50 marker exists in config.py source
- lightning_cli function exists and is callable
"""

import os
import sys
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _read_source(relpath):
    with open(os.path.join(PROJECT_ROOT, relpath), "r") as f:
        return f.read()


class TestM50ConfigCLIParams:
    """M50: CLI must support --maturity-level and --growth-stage parameters."""

    def test_01_config_has_m50_marker(self):
        """config.py must contain M50 modification marker."""
        source = _read_source("agentlightning/config.py")
        assert "M50" in source

    def test_02_create_argument_parser_exists(self):
        """_create_argument_parser function must exist in config module."""
        from agentlightning.config import _create_argument_parser
        assert callable(_create_argument_parser)

    def test_03_parser_has_maturity_level_arg(self):
        """Parser must include --maturity-level argument."""
        from agentlightning.config import _create_argument_parser
        parser = _create_argument_parser()
        # Parse with default args (no command line)
        args = parser.parse_args([])
        assert hasattr(args, "maturity_level")

    def test_04_maturity_level_default_is_zero(self):
        """--maturity-level must default to 0."""
        from agentlightning.config import _create_argument_parser
        parser = _create_argument_parser()
        args = parser.parse_args([])
        assert args.maturity_level == 0

    def test_05_parser_has_growth_stage_arg(self):
        """Parser must include --growth-stage argument."""
        from agentlightning.config import _create_argument_parser
        parser = _create_argument_parser()
        args = parser.parse_args([])
        assert hasattr(args, "growth_stage")

    def test_06_growth_stage_default_is_infant(self):
        """--growth-stage must default to 'infant'."""
        from agentlightning.config import _create_argument_parser
        parser = _create_argument_parser()
        args = parser.parse_args([])
        assert args.growth_stage == "infant"

    def test_07_maturity_level_accepts_int(self):
        """--maturity-level must accept integer values."""
        from agentlightning.config import _create_argument_parser
        parser = _create_argument_parser()
        args = parser.parse_args(["--maturity-level", "3"])
        assert args.maturity_level == 3

    def test_08_growth_stage_accepts_string(self):
        """--growth-stage must accept string values."""
        from agentlightning.config import _create_argument_parser
        parser = _create_argument_parser()
        args = parser.parse_args(["--growth-stage", "college"])
        assert args.growth_stage == "college"

    def test_09_lightning_cli_exists(self):
        """lightning_cli function must be importable."""
        from agentlightning.config import lightning_cli
        assert callable(lightning_cli)

    def test_10_source_mentions_maturity_level_help_text(self):
        """Source must include help text for --maturity-level."""
        source = _read_source("agentlightning/config.py")
        assert "maturity" in source.lower()
        assert "help=" in source

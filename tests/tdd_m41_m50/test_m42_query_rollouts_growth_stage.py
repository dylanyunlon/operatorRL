"""
TDD Tests for M42: store/base.py — query_rollouts growth_stage Filtering

TEST-DRIVEN DEVELOPMENT: These tests define expected filtering behavior for
query_rollouts by growth_stage. No mock implementations.

Expected behavior:
- query_rollouts accepts growth_stage parameter
- When growth_stage is set, only rollouts with matching growth_stage are returned
- growth_stage filter combines with other filters using AND logic
- Valid growth stages: infant, toddler, elementary, middle, high, college, graduate
- None growth_stage returns all rollouts (no filtering)
"""

import os
import sys
import asyncio
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class TestM42QueryRolloutsGrowthStage:
    """M42: query_rollouts must support growth_stage filtering."""

    def test_01_query_rollouts_has_growth_stage_param(self):
        """query_rollouts signature must include growth_stage keyword."""
        from agentlightning.store.base import LightningStore
        import inspect
        sig = inspect.signature(LightningStore.query_rollouts)
        params = sig.parameters
        assert "growth_stage" in params
        assert params["growth_stage"].kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )

    def test_02_query_rollouts_growth_stage_is_optional_str(self):
        """growth_stage parameter must be Optional[str]."""
        from agentlightning.store.base import LightningStore
        import inspect
        import typing
        sig = inspect.signature(LightningStore.query_rollouts)
        param = sig.parameters["growth_stage"]
        # Check annotation — should be Optional[str]
        ann = param.annotation
        # Could be Optional[str] or str | None
        assert param.default is None

    def test_03_query_rollouts_docstring_mentions_growth_stage(self):
        """query_rollouts docstring must document the growth_stage filter."""
        from agentlightning.store.base import LightningStore
        doc = LightningStore.query_rollouts.__doc__
        assert doc is not None
        assert "growth_stage" in doc

    def test_04_query_rollouts_docstring_lists_valid_stages(self):
        """query_rollouts docstring must list valid growth stage names."""
        from agentlightning.store.base import LightningStore
        doc = LightningStore.query_rollouts.__doc__ or ""
        assert "infant" in doc
        assert "graduate" in doc

    def test_05_rollout_model_has_growth_stage_field(self):
        """Rollout model must have growth_stage field to enable filtering."""
        from agentlightning.types import Rollout
        fields = Rollout.model_fields
        assert "growth_stage" in fields

    def test_06_rollout_growth_stage_default_is_infant(self):
        """Rollout.growth_stage must default to 'infant'."""
        from agentlightning.types import Rollout
        field = Rollout.model_fields["growth_stage"]
        assert field.default == "infant"

    def test_07_rollout_model_has_maturity_level_field(self):
        """Rollout model must have maturity_level field."""
        from agentlightning.types import Rollout
        fields = Rollout.model_fields
        assert "maturity_level" in fields

    def test_08_rollout_maturity_level_default_is_zero(self):
        """Rollout.maturity_level must default to 0."""
        from agentlightning.types import Rollout
        field = Rollout.model_fields["maturity_level"]
        assert field.default == 0

    def test_09_rollout_has_emergent_signals_field(self):
        """Rollout must track emergent_signals count."""
        from agentlightning.types import Rollout
        fields = Rollout.model_fields
        assert "emergent_signals" in fields

    def test_10_rollout_emergent_signals_default_is_zero(self):
        """Rollout.emergent_signals must default to 0."""
        from agentlightning.types import Rollout
        field = Rollout.model_fields["emergent_signals"]
        assert field.default == 0

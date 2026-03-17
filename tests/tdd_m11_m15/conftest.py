"""Shared fixtures for M11-M15 TDD tests."""

import pytest


@pytest.fixture(autouse=True)
def clean_tracer_state():
    """Ensure tracer state is clean before and after each test."""
    from agentlightning.tracer.base import clear_active_tracer, get_active_tracer

    # Clear before test in case previous test leaked
    if get_active_tracer() is not None:
        clear_active_tracer()
    yield
    # Clear after test
    if get_active_tracer() is not None:
        clear_active_tracer()

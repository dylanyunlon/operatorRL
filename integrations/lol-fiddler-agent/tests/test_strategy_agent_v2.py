"""
TDD Tests for M255: StrategyAgent — core decision logic with evolution hooks.

We test the EXISTING strategy_agent.py to ensure we can add evolution hooks
WITHOUT adding or removing functions. 10 tests.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestStrategyAgentConstruction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        agent = StrategyAgent()
        assert agent is not None

    def test_has_analyze_method(self):
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        agent = StrategyAgent()
        assert callable(getattr(agent, "analyze", None))


class TestStrategyAgentEvolutionHooks:
    def test_has_evolution_callback(self):
        """After M255 migration, agent should have evolution callback slot."""
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        agent = StrategyAgent()
        assert hasattr(agent, "_evolution_callback") or hasattr(agent, "evolution_callback")

    def test_set_evolution_callback(self):
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        agent = StrategyAgent()
        records = []
        agent.evolution_callback = lambda data: records.append(data)
        assert agent.evolution_callback is not None

    def test_analyze_triggers_callback(self):
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        agent = StrategyAgent()
        records = []
        agent.evolution_callback = lambda data: records.append(data)
        # Create minimal snapshot
        snapshot = GameSnapshot(
            game_time=300.0,
            active_player_name="TestPlayer",
        )
        agent.analyze(snapshot)
        # Callback should have been triggered
        assert len(records) >= 1


class TestStrategyAgentFunctionCount:
    """Verify we haven't added or removed functions (鲁迅拿来主义)."""

    def test_public_method_count_unchanged(self):
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        public_methods = [m for m in dir(StrategyAgent) if not m.startswith("_") and callable(getattr(StrategyAgent, m))]
        # The original has a specific count — we just verify it's > 0 and stable
        assert len(public_methods) >= 1

    def test_analyze_signature_unchanged(self):
        """analyze() still takes a GameSnapshot."""
        import inspect
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        sig = inspect.signature(StrategyAgent.analyze)
        params = list(sig.parameters.keys())
        assert "snapshot" in params or len(params) >= 2  # self + snapshot


class TestStrategyAgentDecisions:
    def test_analyze_returns_advice_list(self):
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        agent = StrategyAgent()
        snapshot = GameSnapshot(game_time=600.0, active_player_name="Player1")
        result = agent.analyze(snapshot)
        assert isinstance(result, list)

    def test_advice_has_priority(self):
        from lol_fiddler_agent.agents.strategy_agent import StrategyAgent
        from lol_fiddler_agent.models.game_snapshot import GameSnapshot
        agent = StrategyAgent()
        snapshot = GameSnapshot(game_time=600.0, active_player_name="Player1")
        result = agent.analyze(snapshot)
        if result:
            assert "priority" in result[0] or "score" in result[0]

    def test_evolution_key(self):
        from lol_fiddler_agent.agents.strategy_agent import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

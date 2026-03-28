"""
TDD Tests for M226 (mahjong_agent __init__) and M227 (MahjongAgent orchestrator).

10 tests for __init__ module constants + 10 tests for Agent orchestrator.
Expected ~50% failure on first run.

Location: integrations/mahjong/tests/test_agent.py
"""

import asyncio
import json
import pytest

# ── M226: __init__ tests ──


class TestMahjongAgentInit:
    """Tests for integrations/mahjong/src/mahjong_agent/__init__.py"""

    def test_version_string_format(self):
        from mahjong_agent import __version__
        parts = __version__.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_evolution_key_exists(self):
        from mahjong_agent import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)
        assert "mahjong" in _EVOLUTION_KEY

    def test_compute_backend_default(self):
        from mahjong_agent import _COMPUTE_BACKEND_DEFAULT
        assert _COMPUTE_BACKEND_DEFAULT in ("cpu", "cuda", "trainium")

    def test_maturity_gate_integer(self):
        from mahjong_agent import _MATURITY_GATE_MAHJONG
        assert isinstance(_MATURITY_GATE_MAHJONG, int)
        assert _MATURITY_GATE_MAHJONG >= 0

    def test_module_docstring(self):
        import mahjong_agent
        assert mahjong_agent.__doc__ is not None
        assert "mahjong" in mahjong_agent.__doc__.lower()


# ── M227: Agent orchestrator tests ──


class TestMahjongAgent:
    """Tests for integrations/mahjong/src/mahjong_agent/agent.py"""

    def test_agent_instantiation_default(self):
        from mahjong_agent.agent import MahjongAgent, AgentConfig
        agent = MahjongAgent()
        assert agent.config is not None
        assert isinstance(agent.config, AgentConfig)

    def test_agent_instantiation_custom_config(self):
        from mahjong_agent.agent import MahjongAgent, AgentConfig
        cfg = AgentConfig(player_name="test_player", max_think_time_ms=5000)
        agent = MahjongAgent(config=cfg)
        assert agent.config.player_name == "test_player"
        assert agent.config.max_think_time_ms == 5000

    def test_agent_state_initial(self):
        from mahjong_agent.agent import MahjongAgent, AgentState
        agent = MahjongAgent()
        assert agent.state == AgentState.IDLE

    def test_agent_on_message_returns_action(self):
        from mahjong_agent.agent import MahjongAgent
        agent = MahjongAgent()
        # start_game initializes the agent
        start_msg = {"type": "start_game", "names": ["0", "1", "2", "3"], "id": 0}
        action = agent.on_message(start_msg)
        assert action is not None
        assert isinstance(action, dict)
        assert action.get("type") == "none"

    def test_agent_on_message_invalid_returns_none_action(self):
        from mahjong_agent.agent import MahjongAgent
        agent = MahjongAgent()
        action = agent.on_message({})
        assert action is not None
        assert action["type"] == "none"

    def test_agent_state_transitions_on_start_game(self):
        from mahjong_agent.agent import MahjongAgent, AgentState
        agent = MahjongAgent()
        assert agent.state == AgentState.IDLE
        agent.on_message({"type": "start_game", "names": ["0", "1", "2", "3"], "id": 1})
        assert agent.state == AgentState.IN_GAME

    def test_agent_state_transitions_on_end_game(self):
        from mahjong_agent.agent import MahjongAgent, AgentState
        agent = MahjongAgent()
        agent.on_message({"type": "start_game", "names": ["0", "1", "2", "3"], "id": 0})
        agent.on_message({"type": "end_game"})
        assert agent.state == AgentState.IDLE

    def test_agent_tracks_player_id(self):
        from mahjong_agent.agent import MahjongAgent
        agent = MahjongAgent()
        agent.on_message({"type": "start_game", "names": ["0", "1", "2", "3"], "id": 2})
        assert agent.player_id == 2

    def test_agent_decision_history_accumulates(self):
        from mahjong_agent.agent import MahjongAgent
        agent = MahjongAgent()
        agent.on_message({"type": "start_game", "names": ["0", "1", "2", "3"], "id": 0})
        agent.on_message({"type": "tsumo", "actor": 0, "pai": "5m"})
        assert len(agent.decision_history) >= 1

    def test_agent_reset_clears_state(self):
        from mahjong_agent.agent import MahjongAgent, AgentState
        agent = MahjongAgent()
        agent.on_message({"type": "start_game", "names": ["0", "1", "2", "3"], "id": 0})
        agent.reset()
        assert agent.state == AgentState.IDLE
        assert agent.player_id is None
        assert len(agent.decision_history) == 0

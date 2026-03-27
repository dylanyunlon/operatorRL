"""
LoL Fiddler Agent - League of Legends AI Strategy System

Combines:
- Fiddler MCP for network traffic capture (zero hallucination)
- Live Client Data API parsing for real-time game state
- AgentOS integration for policy governance and audit logging
- ML-based win prediction using leagueoflegends-optimizer methodology
"""

__version__ = "0.1.0"
__author__ = "dylanyunlong"
__email__ = "dylanyunlong@gmail.com"

from .agents.strategy_agent import (
    LoLStrategyAgent,
    StrategyAgentConfig,
    StrategicAdvice,
    ActionType,
    Urgency,
)

from .network.fiddler_client import (
    FiddlerMCPClient,
    FiddlerConfig,
    HTTPSession,
    FilterCriteria,
)

from .network.live_client_data import (
    LiveGameState,
    Team,
    Position,
    GamePhase,
    Player,
    ActivePlayer,
)

__all__ = [
    # Agent
    "LoLStrategyAgent",
    "StrategyAgentConfig",
    "StrategicAdvice",
    "ActionType",
    "Urgency",
    # Network
    "FiddlerMCPClient",
    "FiddlerConfig",
    "HTTPSession",
    "FilterCriteria",
    # Game State
    "LiveGameState",
    "Team",
    "Position",
    "GamePhase",
    "Player",
    "ActivePlayer",
]

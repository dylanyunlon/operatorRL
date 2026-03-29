"""
Real-Time Assistant ABC — Cross-game unified real-time assistant interface.

Provides abstract base class for all game-specific real-time assistants,
enabling unified scout/decide/feedback/postgame access across games.

Location: modules/real_time_assistant_abc.py

Reference (拿来主义):
  - modules/game_bridge_abc.py: cross-game ABC pattern (game_name property)
  - modules/strategy_advisor_abc.py: advise/evaluate pattern
  - integrations/lol/src/lol_agent/lol_agent_orchestrator.py: full lifecycle
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

_EVOLUTION_KEY: str = "modules.real_time_assistant_abc.v1"


class RealTimeAssistantABC(ABC):
    """Abstract base class for game-specific real-time assistants.

    All real-time assistants must provide:
    - game_name: property returning game identifier
    - scout: pre-game or real-time reconnaissance
    - decide: produce strategic decision from game state
    - record_feedback: record advice vs actual action divergence
    - post_game_report: generate post-game analysis report

    Mirrors the LoL Agent Orchestrator lifecycle abstracted
    for cross-game reuse (Dota2, Mahjong, etc.).
    """

    @property
    @abstractmethod
    def game_name(self) -> str:
        """Unique game identifier string."""
        ...

    @abstractmethod
    def scout(self, context: dict[str, Any]) -> dict[str, Any]:
        """Perform reconnaissance / scouting.

        Args:
            context: Game-specific context for scouting.

        Returns:
            Dict with scouting results (threats, intel, etc.).
        """
        ...

    @abstractmethod
    def decide(self, state: dict[str, Any]) -> dict[str, Any]:
        """Produce a strategic decision from game state.

        Args:
            state: Current game state dict.

        Returns:
            Dict with recommended action and reasoning.
        """
        ...

    @abstractmethod
    def record_feedback(
        self, advice: dict[str, Any], action: dict[str, Any]
    ) -> None:
        """Record feedback: advice given vs action taken.

        Args:
            advice: The advice that was given.
            action: The action that was actually taken.
        """
        ...

    @abstractmethod
    def post_game_report(self) -> dict[str, Any]:
        """Generate post-game analysis report.

        Returns:
            Dict with game summary, key moments, improvement suggestions.
        """
        ...

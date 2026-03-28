"""
Strategy Advisor ABC — Cross-game unified strategy advice interface.

Provides abstract base class for game-specific strategy advisors,
enabling unified advise/evaluate/confidence access across games.

Location: modules/strategy_advisor_abc.py

Reference: operatorRL voice_advisor + dota2bot mode decision patterns.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

_EVOLUTION_KEY: str = "modules.strategy_advisor_abc.v1"


class StrategyAdvisorABC(ABC):
    """Abstract base class for game-specific strategy advisors.

    All strategy advisors must provide:
    - game_name: property returning game identifier
    - advise: produce strategy suggestion from game state
    - evaluate_action: score an action given its outcome
    - get_confidence: current confidence level
    """

    @property
    @abstractmethod
    def game_name(self) -> str:
        """Unique game identifier string."""
        ...

    @abstractmethod
    def advise(self, game_state: dict[str, Any]) -> dict[str, Any]:
        """Produce a strategy suggestion.

        Args:
            game_state: Current game state dict.

        Returns:
            Dict with suggested action and reasoning.
        """
        ...

    @abstractmethod
    def evaluate_action(self, action: Any, outcome: Any) -> float:
        """Evaluate an action given its outcome.

        Args:
            action: The action taken.
            outcome: The result of the action.

        Returns:
            Score (positive = good, negative = bad).
        """
        ...

    @abstractmethod
    def get_confidence(self) -> float:
        """Get current confidence level.

        Returns:
            Float between 0.0 and 1.0.
        """
        ...

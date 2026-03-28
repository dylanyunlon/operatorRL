"""
Mahjong Strategy Advisor — StrategyAdvisorABC concrete implementation.

Provides strategic advice (attack/defense, discard suggestion, reasoning)
for mahjong game states, with confidence tracking from evaluation history.

Location: integrations/mahjong/src/mahjong_agent/mahjong_strategy_advisor.py

Reference (拿来主義):
  - modules/strategy_advisor_abc.py: StrategyAdvisorABC interface
  - operatorRL voice_advisor: priority-based advice pattern
  - Mortal engine.py: action selection with confidence/q-values
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_strategy_advisor.v1"


class MahjongStrategyAdvisor:
    """Concrete StrategyAdvisorABC for mahjong.

    Provides:
    - advise(game_state) → action + reasoning
    - evaluate_action(action, outcome) → score
    - get_confidence() → float [0, 1]

    Attributes:
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self._evaluations: list[float] = []
        self._base_confidence: float = 0.3
        self.evolution_callback: Optional[Callable] = None

    @property
    def game_name(self) -> str:
        return "mahjong"

    def advise(self, game_state: dict[str, Any]) -> dict[str, Any]:
        """Produce a strategy suggestion from the current game state.

        Args:
            game_state: Dict with optional keys: 'hand', 'turn',
                       'round_wind', 'seat_wind', 'dora', etc.

        Returns:
            Dict with 'action' (suggested action dict) and 'reasoning' (str).
        """
        hand = game_state.get("hand", [])
        turn = game_state.get("turn", 0)

        if not hand:
            return {
                "action": {"type": "none"},
                "reasoning": "No hand data available — cannot advise.",
            }

        # Simple strategy: suggest discarding last tile
        # In production, this would integrate ShantenCalculator + DiscardAdvisor
        if isinstance(hand[0], str):
            # String tile format
            suggested_tile = hand[-1]
        else:
            suggested_tile = "1z"  # fallback

        # Determine strategic mode based on turn
        if turn < 6:
            mode = "attack"
            reasoning = f"Early game (turn {turn}) — prioritize hand efficiency. Suggest discarding {suggested_tile}."
        elif turn < 12:
            mode = "balanced"
            reasoning = f"Mid game (turn {turn}) — balance offense and defense. Suggest discarding {suggested_tile}."
        else:
            mode = "defense"
            reasoning = f"Late game (turn {turn}) — prioritize safety. Suggest discarding {suggested_tile}."

        return {
            "action": {"type": "dahai", "pai": suggested_tile, "strategy": mode},
            "reasoning": reasoning,
        }

    def evaluate_action(self, action: Any, outcome: Any) -> float:
        """Evaluate an action given its outcome.

        Args:
            action: The action taken (dict with 'type', etc.).
            outcome: The result (dict with 'result' key).

        Returns:
            Score (positive = good, negative = bad).
        """
        result = outcome.get("result", "neutral") if isinstance(outcome, dict) else "neutral"

        if result == "safe":
            score = 0.5
        elif result == "tsumo" or result == "ron":
            score = 1.0
        elif result == "deal_in":
            score = -1.0
        elif result == "riichi":
            score = 0.3
        else:
            score = 0.0

        self._evaluations.append(score)
        return score

    def get_confidence(self) -> float:
        """Get current confidence level based on evaluation history.

        Confidence increases with more positive evaluations.

        Returns:
            Float between 0.0 and 1.0.
        """
        if not self._evaluations:
            return self._base_confidence

        # Confidence grows with data and positive outcomes
        n = len(self._evaluations)
        avg = sum(self._evaluations) / n
        data_bonus = min(0.3, n * 0.02)  # up to +0.3 from data volume
        performance_bonus = max(0.0, avg * 0.2)  # up to +0.2 from good performance

        confidence = self._base_confidence + data_bonus + performance_bonus
        return max(0.0, min(1.0, confidence))

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

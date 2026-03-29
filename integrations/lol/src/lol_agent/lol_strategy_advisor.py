"""
LoL Strategy Advisor — StrategyAdvisorABC implementation for League of Legends.

Provides game-phase-aware strategic advice (early/mid/late game),
action evaluation, and confidence tracking.

Location: integrations/lol/src/lol_agent/lol_strategy_advisor.py

Reference (拿来主义):
  - modules/strategy_advisor_abc.py: advise/evaluate_action/get_confidence contract
  - integrations/mahjong/src/mahjong_agent/mahjong_strategy_advisor.py: same pattern
  - dota2bot-OpenHyperAI: mode_*.lua strategy decision patterns
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.lol_strategy_advisor.v1"

# Game phase thresholds (seconds)
_EARLY_GAME_END = 840.0    # 14 minutes
_MID_GAME_END = 1800.0     # 30 minutes


class LoLStrategyAdvisor:
    """Strategic advisor for League of Legends.

    Implements StrategyAdvisorABC contract: advise(), evaluate_action(),
    get_confidence(). Produces game-phase-aware strategy suggestions.

    Attributes:
        game_name: Always 'league_of_legends'.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(self) -> None:
        self._confidence: float = 0.3  # initial low confidence
        self._evaluation_count: int = 0
        self._correct_predictions: int = 0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def game_name(self) -> str:
        """Unique game identifier."""
        return "league_of_legends"

    def advise(self, game_state: dict[str, Any]) -> dict[str, Any]:
        """Produce a strategy suggestion based on current game state.

        Considers game phase, gold/kill/tower/dragon advantages.

        Args:
            game_state: Dict with game_time, gold_diff, kill_diff,
                        dragon_count, tower_diff.

        Returns:
            Dict with 'action', 'suggestion', 'phase', 'confidence'.
        """
        game_time = game_state.get("game_time", 0.0)
        gold_diff = game_state.get("gold_diff", 0)
        kill_diff = game_state.get("kill_diff", 0)
        dragon_count = game_state.get("dragon_count", 0)
        tower_diff = game_state.get("tower_diff", 0)

        # Determine game phase
        if game_time < _EARLY_GAME_END:
            phase = "early"
        elif game_time < _MID_GAME_END:
            phase = "mid"
        else:
            phase = "late"

        # Generate strategy based on phase and advantage
        advantage = gold_diff / 1000.0 + kill_diff * 0.5 + tower_diff * 1.5

        if phase == "early":
            if advantage > 2:
                action = "aggressive_lane"
                suggestion = "Press lane advantage, zone enemy from CS"
            elif advantage < -2:
                action = "safe_farm"
                suggestion = "Play safe, farm under tower, wait for jungle help"
            else:
                action = "balanced_farm"
                suggestion = "Focus on CS, trade when favorable"
        elif phase == "mid":
            if advantage > 3:
                action = "push_objectives"
                suggestion = "Group for dragon/baron, take towers"
            elif advantage < -3:
                action = "defensive_vision"
                suggestion = "Ward defensively, avoid 5v5, catch side waves"
            else:
                action = "skirmish"
                suggestion = "Look for picks, contest objectives when possible"
        else:  # late game
            if advantage > 4:
                action = "force_baron"
                suggestion = "Force baron, end game with number advantage"
            elif advantage < -4:
                action = "turtle"
                suggestion = "Defend base, look for one fight to turn"
            else:
                action = "team_fight"
                suggestion = "Stay grouped, fight for elder dragon or baron"

        advice = {
            "action": action,
            "suggestion": suggestion,
            "phase": phase,
            "advantage_score": advantage,
            "confidence": self._confidence,
        }

        self._fire_evolution("strategy_advised", {
            "phase": phase,
            "action": action,
            "advantage": advantage,
        })
        return advice

    def evaluate_action(self, action: Any, outcome: Any) -> float:
        """Evaluate an action given its outcome.

        Args:
            action: Action dict that was taken.
            outcome: Outcome dict with 'result' field.

        Returns:
            Score in [-1.0, 1.0].
        """
        self._evaluation_count += 1

        result = outcome if isinstance(outcome, str) else outcome.get("result", "")
        positive_results = {"tower_taken", "dragon_taken", "baron_taken",
                            "kill_secured", "gold_gained", "cs_gained"}
        negative_results = {"death", "tower_lost", "dragon_lost", "baron_lost"}

        if result in positive_results:
            score = 0.8
            self._correct_predictions += 1
        elif result in negative_results:
            score = -0.5
        else:
            score = 0.1  # Neutral

        # Update confidence based on prediction accuracy
        if self._evaluation_count > 0:
            accuracy = self._correct_predictions / self._evaluation_count
            self._confidence = max(0.1, min(1.0,
                0.5 * self._confidence + 0.5 * accuracy
            ))

        return score

    def get_confidence(self) -> float:
        """Current confidence level.

        Returns:
            Confidence in [0.0, 1.0].
        """
        return self._confidence

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

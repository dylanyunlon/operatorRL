"""
Win Probability Predictor — Real-time win probability estimation.

Uses a lightweight logistic model over game features (gold diff,
kill diff, tower diff, dragon diff, game time) to predict win chance.

Location: integrations/lol/src/lol_agent/win_probability_predictor.py

Reference (拿来主义):
  - leagueoflegends-optimizer: ML prediction pipeline (article5.md)
  - DI-star: rl_learner.py value network output (sigmoid probability)
  - integrations/lol-history/src/lol_history/winrate_tracker.py: tracking pattern
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.win_probability_predictor.v1"

# Default feature weights (tuned from leagueoflegends-optimizer patterns)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "gold_diff": 0.0003,
    "kill_diff": 0.05,
    "tower_diff": 0.12,
    "dragon_diff": 0.08,
    "game_time": -0.0001,  # Late game reduces certainty
}


class WinProbabilityPredictor:
    """Logistic regression win probability predictor.

    Computes P(win) = sigmoid(sum(w_i * x_i)) where features are
    gold_diff, kill_diff, tower_diff, dragon_diff, game_time.

    Supports online weight updates from game outcomes.

    Attributes:
        weights: Feature weight dict.
        learning_rate: Update step size.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        weights: Optional[dict[str, float]] = None,
        learning_rate: float = 0.001,
    ) -> None:
        self._weights: dict[str, float] = dict(weights or _DEFAULT_WEIGHTS)
        self.learning_rate = learning_rate
        self._update_count: int = 0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def weights(self) -> dict[str, float]:
        """Current feature weights."""
        return dict(self._weights)

    def predict(self, features: dict[str, float]) -> float:
        """Predict win probability.

        Args:
            features: Dict with gold_diff, kill_diff, tower_diff,
                      dragon_diff, game_time.

        Returns:
            Probability in [0.0, 1.0].
        """
        logit = 0.0
        for key, weight in self._weights.items():
            logit += weight * features.get(key, 0.0)

        # Sigmoid activation
        prob = 1.0 / (1.0 + math.exp(-max(-20, min(20, logit))))
        return prob

    def update(self, features: dict[str, float], outcome: float) -> None:
        """Online weight update from a game outcome.

        Uses simple gradient descent on logistic loss.

        Args:
            features: Feature dict (same as predict input).
            outcome: Actual outcome (1.0 = win, 0.0 = loss).
        """
        pred = self.predict(features)
        error = outcome - pred

        for key in self._weights:
            grad = error * features.get(key, 0.0)
            self._weights[key] += self.learning_rate * grad

        self._update_count += 1

        self._fire_evolution("weights_updated", {
            "prediction": pred,
            "outcome": outcome,
            "error": error,
            "update_count": self._update_count,
        })

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

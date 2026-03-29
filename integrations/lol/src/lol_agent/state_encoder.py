"""
State Encoder — Game state → fixed-dimension feature vector.

Encodes LoL game state (player stats, allies, enemies, objectives)
into a normalized float vector for RL training.

Location: integrations/lol/src/lol_agent/state_encoder.py

Reference (拿来主义):
  - DI-star/distar/ctools/torch_utils/network/nn_module.py: feature encoding
  - ELF: game state tensor encoding patterns
  - operatorRL LiveGameState: allgamedata parsing
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.state_encoder.v1"

# Feature layout: [game_time, hp_ratio, gold_norm, level_norm,
#                  ally_count, ally_avg_level, enemy_count, enemy_avg_level,
#                  ally_turrets, enemy_turrets, ally_dragons, enemy_dragons]
_FEATURE_NAMES: list[str] = [
    "game_time_norm",
    "hp_ratio",
    "gold_norm",
    "level_norm",
    "ally_count",
    "ally_avg_level_norm",
    "enemy_count",
    "enemy_avg_level_norm",
    "ally_turrets_norm",
    "enemy_turrets_norm",
    "ally_dragons",
    "enemy_dragons",
]


class StateEncoder:
    """Encodes LoL game state into a fixed-size float vector.

    Attributes:
        feature_dim: Output vector dimensionality.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self) -> None:
        self.feature_dim: int = len(_FEATURE_NAMES)
        self.evolution_callback: Optional[Callable[[dict], None]] = None
        self._encode_count: int = 0

    def encode(self, state: dict[str, Any]) -> list[float]:
        """Encode a single game state to feature vector.

        Args:
            state: Dict with game_time, active_player, allies, enemies,
                   turrets_alive, dragon_kills.

        Returns:
            List of floats with length == self.feature_dim.
        """
        ap = state.get("active_player", {})
        allies = state.get("allies", [])
        enemies = state.get("enemies", [])
        turrets = state.get("turrets_alive", {})
        dragons = state.get("dragon_kills", {})

        game_time = state.get("game_time", 0.0)
        hp = ap.get("health", 0)
        max_hp = max(ap.get("max_health", 1), 1)
        gold = ap.get("gold", 0)
        level = ap.get("level", 1)

        ally_levels = [a.get("level", 1) for a in allies] or [0]
        enemy_levels = [e.get("level", 1) for e in enemies] or [0]

        vec = [
            min(game_time / 3600.0, 1.0),                    # game_time_norm
            hp / max_hp,                                       # hp_ratio
            min(gold / 20000.0, 1.0),                         # gold_norm
            level / 18.0,                                      # level_norm
            len(allies) / 5.0,                                 # ally_count
            (sum(ally_levels) / max(len(ally_levels), 1)) / 18.0,   # ally_avg_level_norm
            len(enemies) / 5.0,                                # enemy_count
            (sum(enemy_levels) / max(len(enemy_levels), 1)) / 18.0, # enemy_avg_level_norm
            turrets.get("ally", 0) / 11.0,                    # ally_turrets_norm
            turrets.get("enemy", 0) / 11.0,                   # enemy_turrets_norm
            float(dragons.get("ally", 0)),                     # ally_dragons
            float(dragons.get("enemy", 0)),                    # enemy_dragons
        ]

        self._encode_count += 1
        self._fire_evolution({"event": "state_encoded", "encode_count": self._encode_count})
        return vec

    def encode_batch(self, states: list[dict[str, Any]]) -> list[list[float]]:
        """Encode multiple states.

        Args:
            states: List of game state dicts.

        Returns:
            List of feature vectors.
        """
        return [self.encode(s) for s in states]

    def get_feature_names(self) -> list[str]:
        """Return ordered list of feature names."""
        return list(_FEATURE_NAMES)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

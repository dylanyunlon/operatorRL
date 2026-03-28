"""
Opponent Model — Tracks opponent discards/melds and predicts tenpai/waiting tiles.

Location: integrations/mahjong/src/mahjong_agent/opponent_model.py

Reference (拿来主义):
  - Akagi bridge: opponent tracking across WebSocket messages
  - Mortal engine: multi-player observation handling
  - operatorRL lol-history OpponentProfiler pattern
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.opponent_model.v1"


class OpponentModel:
    """Models opponent behavior from observed discards and melds.

    Tracks per-player discard history, meld declarations, and provides
    probabilistic tenpai/waiting-tile predictions.

    Attributes:
        num_players: Number of players at the table.
        evolution_callback: Optional callback for self-evolution.
    """

    def __init__(self, num_players: int = 4) -> None:
        self.num_players = num_players
        self._discards: dict[int, list[dict[str, Any]]] = {
            i: [] for i in range(num_players)
        }
        self._melds: dict[int, list[dict[str, Any]]] = {
            i: [] for i in range(num_players)
        }
        self._riichi: dict[int, bool] = {i: False for i in range(num_players)}
        self.evolution_callback: Optional[Callable] = None

    def record_discard(self, player: int, tile: str, turn: int) -> None:
        """Record a discard by a player.

        Args:
            player: Player index (0-3).
            tile: Tile string (e.g. '1m').
            turn: Turn number when discarded.
        """
        self._discards[player].append({"tile": tile, "turn": turn})

    def record_meld(
        self, player: int, meld_type: str, tiles: list[str]
    ) -> None:
        """Record a meld declaration (chi/pon/kan).

        Args:
            player: Player index.
            meld_type: 'chi', 'pon', 'kan', or 'ankan'.
            tiles: List of tile strings in the meld.
        """
        self._melds[player].append({"type": meld_type, "tiles": tiles})

    def record_riichi(self, player: int) -> None:
        """Record that a player has declared riichi."""
        self._riichi[player] = True

    def get_discards(self, player: int) -> list[dict[str, Any]]:
        """Get discard history for a player."""
        return list(self._discards.get(player, []))

    def get_melds(self, player: int) -> list[dict[str, Any]]:
        """Get meld history for a player."""
        return list(self._melds.get(player, []))

    def predict_tenpai(self, player: int) -> float:
        """Predict probability that a player is tenpai.

        Simple heuristic based on discard count and riichi status.

        Returns:
            Float 0.0-1.0 probability of tenpai.
        """
        if self._riichi.get(player, False):
            return 0.95  # riichi → almost certainly tenpai

        discards = self._discards.get(player, [])
        melds = self._melds.get(player, [])

        num_discards = len(discards)
        num_melds = len(melds)

        # More discards + melds → more likely tenpai
        # After ~12 turns, probability increases
        base = 0.1
        base += num_discards * 0.04
        base += num_melds * 0.1

        return max(0.0, min(1.0, base))

    def predict_waiting_tiles(self, player: int) -> list[str]:
        """Predict which tiles a player might be waiting for.

        Simple approach: tiles NOT in their discards are potential waits.
        More refined models would use Bayesian inference.

        Returns:
            List of tile strings that are potential waits.
        """
        discarded_set = {d["tile"] for d in self._discards.get(player, [])}
        melded_set = set()
        for m in self._melds.get(player, []):
            melded_set.update(m["tiles"])

        # All tiles minus discarded/melded are candidates
        # For simplicity, return honor tiles and central tiles as likely waits
        candidates = []
        all_tiles = [f"{n}{s}" for s in "mps" for n in range(1, 10)]
        all_tiles += [f"{n}z" for n in range(1, 8)]

        for t in all_tiles:
            if t not in discarded_set and t not in melded_set:
                candidates.append(t)

        return candidates[:10]  # return top 10 candidates

    def assess_threat(self, player: int) -> str:
        """Assess threat level of a player.

        Returns:
            One of 'unknown', 'low', 'medium', 'high', 'critical'.
        """
        discards = self._discards.get(player, [])
        melds = self._melds.get(player, [])

        if len(discards) < 3:
            return "unknown"

        if self._riichi.get(player, False):
            return "critical"

        num_melds = len(melds)
        num_discards = len(discards)

        if num_melds >= 3 and num_discards >= 8:
            return "high"
        elif num_melds >= 2 or num_discards >= 10:
            return "medium"
        else:
            return "low"

    def reset(self) -> None:
        """Reset all tracking data for a new round."""
        for i in range(self.num_players):
            self._discards[i] = []
            self._melds[i] = []
            self._riichi[i] = False

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

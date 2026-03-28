"""
Score Calculator — Riichi mahjong point computation from han and fu.

Implements the standard Japanese mahjong scoring table:
han/fu → base points → mangan/haneman/baiman/sanbaiman/yakuman thresholds.

Location: integrations/mahjong/src/mahjong_agent/score_calculator.py

Reference (拿来主義):
  - Mortal reward_calculator.py: pts=[3,1,-1,-3] rank-based reward
  - Standard riichi mahjong scoring rules
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.score_calculator.v1"

# --- Scoring thresholds ---
_LIMIT_HANDS = {
    "mangan": (5, 7999, 8000, 12000),    # 5 han → 8000/12000
    "haneman": (6, 11999, 12000, 18000),  # 6-7 han → 12000/18000
    "baiman": (8, 15999, 16000, 24000),   # 8-10 han → 16000/24000
    "sanbaiman": (11, 23999, 24000, 36000),  # 11-12 han → 24000/36000
    "yakuman": (13, 31999, 32000, 48000),    # 13+ han → 32000/48000
}


class ScoreCalculator:
    """Calculates riichi mahjong points from han and fu values.

    Attributes:
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None

    def compute_points(
        self, han: int, fu: int, is_dealer: bool = False
    ) -> int:
        """Compute payment points from han and fu.

        Standard scoring table:
        - 1-4 han: base_points = fu × 2^(han+2), rounded up to 100
        - 5 han: mangan (8000/12000)
        - 6-7 han: haneman (12000/18000)
        - 8-10 han: baiman (16000/24000)
        - 11-12 han: sanbaiman (24000/36000)
        - 13+ han: yakuman (32000/48000)

        Args:
            han: Number of han (yaku value).
            fu: Number of fu (minipoints).
            is_dealer: Whether the winner is dealer (oya).

        Returns:
            Point value as integer.
        """
        if han <= 0:
            return 0

        # Limit hands (mangan and above)
        if han >= 13:
            return 48000 if is_dealer else 32000
        elif han >= 11:
            return 36000 if is_dealer else 24000
        elif han >= 8:
            return 24000 if is_dealer else 16000
        elif han >= 6:
            return 18000 if is_dealer else 12000
        elif han >= 5:
            return 12000 if is_dealer else 8000

        # Standard calculation for 1-4 han
        base_points = fu * (2 ** (han + 2))

        if is_dealer:
            payment = base_points * 6
        else:
            payment = base_points * 4

        # Check mangan cap
        if payment > (12000 if is_dealer else 8000):
            payment = 12000 if is_dealer else 8000

        # Round up to nearest 100
        payment = math.ceil(payment / 100) * 100

        return payment

    def compute_fu(
        self,
        win_type: str = "ron",
        melds: Optional[list[dict]] = None,
        pair_tile: str = "",
        wait_type: str = "ryanmen",
    ) -> int:
        """Compute fu (minipoints) for a winning hand.

        Base fu:
        - Ron: 30 fu base
        - Tsumo: 20 fu base

        Additional fu from melds, pair, and wait type.

        Args:
            win_type: 'ron' or 'tsumo'.
            melds: List of meld dicts with 'type' and 'tiles'.
            pair_tile: Tile string of the pair.
            wait_type: 'ryanmen', 'shanpon', 'kanchan', 'penchan', 'tanki'.

        Returns:
            Fu value rounded up to nearest 10.
        """
        if melds is None:
            melds = []

        # Base fu
        fu = 30 if win_type == "ron" else 20

        # Tsumo bonus (unless pinfu)
        if win_type == "tsumo":
            fu += 2

        # Meld fu
        for meld in melds:
            mtype = meld.get("type", "")
            is_open = meld.get("open", True)
            tiles = meld.get("tiles", [])

            if mtype == "pon":
                tile_id = self._tile_id(tiles[0]) if tiles else 0
                is_terminal = self._is_terminal_or_honor(tile_id)
                if is_open:
                    fu += 4 if is_terminal else 2
                else:
                    fu += 8 if is_terminal else 4
            elif mtype in ("kan", "ankan"):
                tile_id = self._tile_id(tiles[0]) if tiles else 0
                is_terminal = self._is_terminal_or_honor(tile_id)
                if is_open or mtype == "kan":
                    fu += 16 if is_terminal else 8
                else:
                    fu += 32 if is_terminal else 16

        # Pair fu
        if pair_tile:
            pid = self._tile_id(pair_tile)
            if pid >= 27:  # honor tiles
                fu += 2

        # Wait type fu
        if wait_type in ("kanchan", "penchan", "tanki"):
            fu += 2

        # Round up to nearest 10
        fu = math.ceil(fu / 10) * 10

        return max(fu, 20)

    def _tile_id(self, tile_str: str) -> int:
        suit_offset = {"m": 0, "p": 9, "s": 18, "z": 27}
        if len(tile_str) < 2:
            return 0
        return suit_offset.get(tile_str[-1], 0) + int(tile_str[:-1]) - 1

    def _is_terminal_or_honor(self, tile_id: int) -> bool:
        if tile_id >= 27:
            return True  # honor
        pos = tile_id % 9
        return pos == 0 or pos == 8  # 1 or 9

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

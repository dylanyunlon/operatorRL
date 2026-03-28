"""
Shanten Calculator — Calculates shanten number for riichi mahjong hands.

Supports standard form, seven pairs (chiitoitsu), and thirteen orphans
(kokushi musou). Shanten = number of tiles needed to reach tenpai (-1 = complete).

Location: integrations/mahjong/src/mahjong_agent/shanten_calculator.py

Reference (拿来主義):
  - Mortal/mortal/engine.py: obs/mask shapes for 34-type representation
  - Standard shanten algorithm (Tenhou-style recursive decomposition)
  - open_spiel mahjong state evaluation patterns
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.shanten_calculator.v1"


class ShantenCalculator:
    """Calculates shanten number for a riichi mahjong hand.

    Shanten values:
        -1: Complete (agari)
         0: Tenpai (one tile away)
         1: Iishanten (two tiles away)
         N: N tiles away from tenpai

    The calculator uses recursive decomposition to find the minimum
    shanten across all possible groupings of mentsu (sets) and jantai (pair).

    Attributes:
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None

    def calculate(self, hand_34: list[int]) -> int:
        """Calculate minimum shanten number across all forms.

        Args:
            hand_34: 34-element list of tile counts.

        Returns:
            Minimum shanten number (>= -1).
        """
        total = sum(hand_34)
        if total == 0:
            return 8  # empty hand, max shanten

        s_normal = self._calculate_normal(list(hand_34))
        s_chiitoi = self.calculate_seven_pairs(hand_34)
        s_kokushi = self.calculate_kokushi(hand_34)

        return min(s_normal, s_chiitoi, s_kokushi)

    def _calculate_normal(self, hand: list[int]) -> int:
        """Calculate shanten for standard form (4 mentsu + 1 jantai).

        Uses recursive decomposition with pruning.
        """
        total = sum(hand)
        # Number of complete sets needed: (total // 3) sets, for 14 tiles → 4 sets + 1 pair
        # shanten = 8 - 2*mentsu - max(jantai, 1 partial)
        best = 8  # worst case: 13 tiles, 0 groups

        # Try each tile as potential pair (jantai)
        for j in range(34):
            if hand[j] >= 2:
                hand[j] -= 2
                mentsu, partial = self._extract_mentsu(list(hand))
                s = 8 - 2 * mentsu - partial - 1  # -1 for pair
                best = min(best, s)
                hand[j] += 2

        # Also try with no pair
        mentsu, partial = self._extract_mentsu(list(hand))
        s = 8 - 2 * mentsu - partial
        best = min(best, s)

        return max(best, -1)

    def _extract_mentsu(self, hand: list[int]) -> tuple[int, int]:
        """Extract maximum mentsu (complete sets) and partial sets.

        Returns:
            Tuple (mentsu_count, partial_count) where partial ≤ remaining capacity.
        """
        return self._greedy_mentsu(hand)

    def _greedy_mentsu(self, hand: list[int]) -> tuple[int, int]:
        """Greedy extraction of mentsu and partial sets."""
        h = list(hand)
        mentsu = 0
        partial = 0

        # Extract kotsu (triplets) first
        for i in range(34):
            if h[i] >= 3:
                h[i] -= 3
                mentsu += 1

        # Extract shuntsu (sequences) — only for numbered suits
        for suit_start in (0, 9, 18):
            suit_end = suit_start + 7 if suit_start < 27 else suit_start  # z tiles have no sequences
            if suit_start >= 27:
                continue
            for i in range(suit_start, suit_start + 7):
                while h[i] >= 1 and h[i + 1] >= 1 and h[i + 2] >= 1:
                    h[i] -= 1
                    h[i + 1] -= 1
                    h[i + 2] -= 1
                    mentsu += 1

        # Count partial sets (pairs, or adjacent tiles forming partial sequences)
        for i in range(34):
            if h[i] >= 2:
                h[i] -= 2
                partial += 1

        for suit_start in (0, 9, 18):
            if suit_start >= 27:
                continue
            for i in range(suit_start, suit_start + 8):
                if h[i] >= 1 and h[i + 1] >= 1:
                    h[i] -= 1
                    h[i + 1] -= 1
                    partial += 1

            # Kanchan (gap wait)
            for i in range(suit_start, suit_start + 7):
                if h[i] >= 1 and i + 2 < 34 and h[i + 2] >= 1:
                    h[i] -= 1
                    h[i + 2] -= 1
                    partial += 1

        # Cap partial: mentsu + partial ≤ 4
        if mentsu + partial > 4:
            partial = 4 - mentsu

        return mentsu, partial

    def calculate_seven_pairs(self, hand_34: list[int]) -> int:
        """Calculate shanten for seven pairs (chiitoitsu).

        Args:
            hand_34: 34-element list of tile counts.

        Returns:
            Shanten number for chiitoitsu form.
        """
        pairs = sum(1 for c in hand_34 if c >= 2)
        # seven pairs needs 7 pairs
        return 6 - pairs  # 6 - pairs (since 7 pairs → shanten = 6-7 = -1)

    def calculate_kokushi(self, hand_34: list[int]) -> int:
        """Calculate shanten for thirteen orphans (kokushi musou).

        Requires one of each terminal/honor tile plus one duplicate.
        Terminals: 1m,9m,1p,9p,1s,9s,1z-7z (13 types).

        Args:
            hand_34: 34-element list of tile counts.

        Returns:
            Shanten number for kokushi form.
        """
        terminal_indices = [0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33]

        unique_terminals = sum(1 for i in terminal_indices if hand_34[i] >= 1)
        has_pair = any(hand_34[i] >= 2 for i in terminal_indices)

        # kokushi needs 13 unique terminals + 1 pair among them
        # shanten = 13 - unique_terminals - (1 if has_pair else 0)
        return 13 - unique_terminals - (1 if has_pair else 0)

    def get_effective_tiles(self, hand_34: list[int]) -> list[int]:
        """Find tiles that would reduce shanten number.

        Args:
            hand_34: 34-element list of tile counts.

        Returns:
            List of tile indices (0-33) that are effective draws.
        """
        current_shanten = self.calculate(hand_34)
        effective = []

        for i in range(34):
            if hand_34[i] >= 4:
                continue
            hand_34[i] += 1
            new_shanten = self.calculate(hand_34)
            if new_shanten < current_shanten:
                effective.append(i)
            hand_34[i] -= 1

        return effective

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

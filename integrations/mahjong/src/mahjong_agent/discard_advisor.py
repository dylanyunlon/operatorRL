"""
Discard Advisor — Strategic tile discard guidance for riichi mahjong.

Evaluates hand efficiency, tile danger, and strategic mode (attack/defense)
to recommend optimal discards.

Location: integrations/mahjong/src/mahjong_agent/discard_advisor.py

Reference (拿来主义):
  - Mortal engine.py: q_value-based action selection + boltzmann sampling
  - Akagi bridge: mjai 'dahai' action format
  - operatorRL voice_advisor: priority-based advice pattern
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.discard_advisor.v1"

_SUIT_OFFSET = {"m": 0, "p": 9, "s": 18, "z": 27}


def _tile_to_id(tile_str: str) -> int:
    num = int(tile_str[:-1])
    suit = tile_str[-1].lower()
    return _SUIT_OFFSET[suit] + (num - 1)


def _id_to_tile(tile_id: int) -> str:
    for offset, suit in sorted(
        {0: "m", 9: "p", 18: "s", 27: "z"}.items(), reverse=True
    ):
        if tile_id >= offset:
            return f"{tile_id - offset + 1}{suit}"
    return "??"


class DiscardAdvisor:
    """Advises which tile to discard based on efficiency and safety.

    Attributes:
        strategy: Current play strategy ('attack' or 'defense').
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self.strategy: str = "attack"
        self.evolution_callback: Optional[Callable] = None

    def set_strategy(self, mode: str) -> None:
        """Set strategy mode.

        Args:
            mode: 'attack' (maximize hand efficiency) or 'defense' (minimize danger).
        """
        if mode in ("attack", "defense"):
            self.strategy = mode

    def advise_discard(self, hand: list[str]) -> dict[str, Any]:
        """Recommend a tile to discard.

        Args:
            hand: List of tile strings in the hand.

        Returns:
            Dict with 'tile' (str or None), 'reason' (str), 'score' (float).
        """
        if not hand:
            return {"tile": None, "reason": "empty hand", "score": 0.0}

        candidates = self.get_discard_candidates(hand, top_k=len(hand))
        if not candidates:
            return {"tile": hand[0], "reason": "no analysis available", "score": 0.0}

        best = candidates[0]
        return {
            "tile": best["tile"],
            "reason": best["reason"],
            "score": best["score"],
        }

    def get_discard_candidates(
        self, hand: list[str], top_k: int = 3
    ) -> list[dict[str, Any]]:
        """Get ranked discard candidates.

        Args:
            hand: Tile strings in hand.
            top_k: Number of candidates to return.

        Returns:
            Sorted list of dicts with 'tile', 'score', 'reason'.
        """
        if not hand:
            return []

        hand_34 = [0] * 34
        for t in hand:
            hand_34[_tile_to_id(t)] += 1

        scored: list[dict[str, Any]] = []
        seen = set()
        for tile_str in hand:
            if tile_str in seen:
                continue
            seen.add(tile_str)
            idx = _tile_to_id(tile_str)
            eff = self.tile_efficiency(hand_34, discard_idx=idx)
            scored.append({
                "tile": tile_str,
                "score": eff,
                "reason": self._reason_for(tile_str, eff),
            })

        # Sort by score descending (higher = better to discard for efficiency)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def tile_efficiency(self, hand_34: list[int], discard_idx: int) -> float:
        """Score how good discarding a tile is for hand efficiency.

        Higher score = discarding this tile loses less efficiency.

        Args:
            hand_34: 34-element tile count vector.
            discard_idx: Index (0-33) of the tile to evaluate discarding.

        Returns:
            Float score (higher = better discard).
        """
        if hand_34[discard_idx] <= 0:
            return -1.0

        # Compute connectivity: how isolated is this tile?
        score = 0.0
        suit_start = (discard_idx // 9) * 9 if discard_idx < 27 else 27

        # Isolated tiles are better to discard
        is_honor = discard_idx >= 27
        if is_honor:
            # Honor tiles: value based on count
            count = hand_34[discard_idx]
            if count == 1:
                score = 0.8  # isolated honor → good discard
            elif count == 2:
                score = 0.3  # pair → moderate
            else:
                score = 0.1  # triplet → keep
        else:
            # Number tiles: check adjacency
            has_left = (discard_idx > suit_start and hand_34[discard_idx - 1] > 0)
            has_right = (discard_idx < suit_start + 8 and hand_34[discard_idx + 1] > 0)
            has_far_left = (discard_idx > suit_start + 1 and hand_34[discard_idx - 2] > 0)
            has_far_right = (discard_idx < suit_start + 7 and hand_34[discard_idx + 2] > 0)

            connectivity = sum([has_left, has_right, has_far_left, has_far_right])
            # Less connected → better to discard
            score = 1.0 - (connectivity * 0.25)

            # Terminal tiles (1 and 9) are slightly better to discard
            num_in_suit = discard_idx - suit_start
            if num_in_suit == 0 or num_in_suit == 8:
                score += 0.1

        return round(score, 4)

    def evaluate_danger(
        self,
        tile: str,
        discards: list[str],
        riichi: bool = False,
    ) -> float:
        """Evaluate danger level of discarding a tile.

        Args:
            tile: Tile string to evaluate.
            discards: Opponent's discarded tiles.
            riichi: Whether the opponent has declared riichi.

        Returns:
            Float 0.0 (safe) to 1.0 (very dangerous).
        """
        tile_id = _tile_to_id(tile)
        base_danger = 0.3

        # Tiles already seen (discarded) are safer
        seen_count = sum(1 for d in discards if _tile_to_id(d) == tile_id)
        base_danger -= seen_count * 0.1

        # Honor tiles: less dangerous if already discarded
        if tile_id >= 27:
            base_danger -= 0.1

        # Riichi increases danger
        if riichi:
            base_danger += 0.3

        # Central tiles (4,5,6) are more dangerous
        suit_pos = tile_id % 9 if tile_id < 27 else -1
        if suit_pos in (3, 4, 5):
            base_danger += 0.1

        return max(0.0, min(1.0, base_danger))

    def _reason_for(self, tile: str, score: float) -> str:
        if score >= 0.7:
            return f"{tile} is isolated — safe to discard"
        elif score >= 0.4:
            return f"{tile} has low connectivity"
        else:
            return f"{tile} is well-connected — consider keeping"

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

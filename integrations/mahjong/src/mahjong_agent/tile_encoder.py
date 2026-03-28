"""
Tile Encoder — Riichi mahjong tile encoding (34-type and 136-tile).

Provides bidirectional mapping between string tile notation (e.g. '1m', '5p',
'7z') and numeric indices, plus hand-to-feature-vector encoding.

Location: integrations/mahjong/src/mahjong_agent/tile_encoder.py

Reference (拿来主义):
  - Mortal/mortal/engine.py: obs tensor shape (34-dim tile features)
  - Mortal libriichi tile conventions: 0-8 man, 9-17 pin, 18-26 sou, 27-33 jihai
  - Akagi bridge tile string notation
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.tile_encoder.v1"

# --- Tile notation tables ---
# man=m(0-8), pin=p(9-17), sou=s(18-26), jihai=z(27-33)
_SUIT_OFFSET = {"m": 0, "p": 9, "s": 18, "z": 27}
_SUIT_NAMES = {0: "m", 9: "p", 18: "s", 27: "z"}
_SUIT_SIZES = {"m": 9, "p": 9, "s": 9, "z": 7}


class TileEncoder:
    """Encodes mahjong tiles between string, 34-type, and 136-tile formats.

    Tile 34-type mapping:
        0-8:   1m-9m (man/萬子)
        9-17:  1p-9p (pin/筒子)
        18-26: 1s-9s (sou/索子)
        27-33: 1z-7z (jihai/字牌: 東南西北白發中)

    136-tile format: 4 copies per type → tile_136 // 4 = tile_34

    Attributes:
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None

    def tile_to_id(self, tile_str: str) -> int:
        """Convert string tile notation to 34-type index.

        Args:
            tile_str: e.g. '1m', '5p', '9s', '7z'

        Returns:
            Integer index 0-33.

        Raises:
            ValueError: If tile_str is invalid.
        """
        if len(tile_str) < 2:
            raise ValueError(f"Invalid tile string: {tile_str!r}")
        num = int(tile_str[:-1])
        suit = tile_str[-1].lower()
        if suit not in _SUIT_OFFSET:
            raise ValueError(f"Unknown suit: {suit!r}")
        offset = _SUIT_OFFSET[suit]
        max_num = _SUIT_SIZES[suit]
        if num < 1 or num > max_num:
            raise ValueError(f"Tile number {num} out of range for suit {suit}")
        return offset + (num - 1)

    def id_to_tile(self, tile_id: int) -> str:
        """Convert 34-type index back to string notation.

        Args:
            tile_id: Integer 0-33.

        Returns:
            String like '1m', '5p', '7z'.
        """
        if tile_id < 0 or tile_id > 33:
            raise ValueError(f"tile_id {tile_id} out of range [0, 33]")
        for offset in sorted(_SUIT_NAMES.keys(), reverse=True):
            if tile_id >= offset:
                suit = _SUIT_NAMES[offset]
                num = tile_id - offset + 1
                return f"{num}{suit}"
        raise ValueError(f"Cannot decode tile_id {tile_id}")

    def encode_tile(self, tile_str: str) -> list[int]:
        """Encode single tile as one-hot 34-dim vector.

        Returns:
            List of 34 ints (0 or 1).
        """
        vec = [0] * 34
        idx = self.tile_to_id(tile_str)
        vec[idx] = 1
        return vec

    def encode_hand(self, hand: list[str]) -> list[int]:
        """Encode a hand (list of tile strings) as 34-dim count vector.

        Each position counts how many copies of that tile type are held (0-4).

        Args:
            hand: List of tile strings, e.g. ['1m', '1m', '2m', '3m', ...]

        Returns:
            List of 34 ints (counts).
        """
        vec = [0] * 34
        for tile_str in hand:
            idx = self.tile_to_id(tile_str)
            vec[idx] += 1
        return vec

    # ---- 136-tile format ----

    def encode_tile_136(self, tile_136: int) -> int:
        """Convert 136-format tile index to 34-type index.

        In 136 format, tiles 0-3 are the 4 copies of type 0 (1m),
        tiles 4-7 are copies of type 1 (2m), etc.

        Args:
            tile_136: Integer 0-135.

        Returns:
            34-type index (0-33).
        """
        return tile_136 // 4

    def tile136_to_tile34(self, tile_136: int) -> int:
        """Alias for encode_tile_136, matching common library conventions."""
        return tile_136 // 4

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

"""
Mahjong Hand Visualizer — Hand tile rendering with recommendations.

Renders hand tiles sorted by suit, highlights recommended discards,
shows tenpai waits, open melds, and shanten information.

Location: integrations/mahjong/src/mahjong_agent/mahjong_hand_visualizer.py

Reference (拿来主義):
  - Akagi: liqi.json tile representation
  - Mortal: mjai tile notation (1m-9m, 1p-9p, 1s-9s, 1z-7z)
  - integrations/mahjong/src/mahjong_agent/mahjong_meta_tracker.py: tracker pattern
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.mahjong.mahjong_hand_visualizer.v1"

# Suit ordering: man < pin < sou < jihai
_SUIT_ORDER = {"m": 0, "p": 1, "s": 2, "z": 3}
_TOTAL_PER_TILE = 4  # 4 copies of each tile in a standard set

# Unicode tile representations (fallback text)
_TILE_NAMES = {
    "1m": "一萬", "2m": "二萬", "3m": "三萬", "4m": "四萬", "5m": "五萬",
    "6m": "六萬", "7m": "七萬", "8m": "八萬", "9m": "九萬",
    "1p": "一筒", "2p": "二筒", "3p": "三筒", "4p": "四筒", "5p": "五筒",
    "6p": "六筒", "7p": "七筒", "8p": "八筒", "9p": "九筒",
    "1s": "一索", "2s": "二索", "3s": "三索", "4s": "四索", "5s": "五索",
    "6s": "六索", "7s": "七索", "8s": "八索", "9s": "九索",
    "1z": "東", "2z": "南", "3z": "西", "4z": "北",
    "5z": "白", "6z": "發", "7z": "中",
}


class MahjongHandVisualizer:
    """Visualizer for mahjong hand tiles with recommendations.

    Attributes:
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def render_hand(
        self,
        hand: list[str],
        discard_recommendation: Optional[str] = None,
        waits: Optional[list[str]] = None,
        shanten: Optional[int] = None,
    ) -> dict[str, Any]:
        """Render a hand into structured display data.

        Args:
            hand: List of tile strings (e.g., ['1m', '2m', '3m']).
            discard_recommendation: Tile to highlight for discard.
            waits: List of wait tiles if tenpai.
            shanten: Shanten number.

        Returns:
            Dict with tiles, waits, shanten.
        """
        tiles = []
        for tile_str in hand:
            t = {
                "tile": tile_str,
                "name": _TILE_NAMES.get(tile_str, tile_str),
                "highlighted": tile_str == discard_recommendation,
            }
            tiles.append(t)

        result: dict[str, Any] = {"tiles": tiles}
        if waits is not None:
            result["waits"] = [{"tile": w, "name": _TILE_NAMES.get(w, w)} for w in waits]
        if shanten is not None:
            result["shanten"] = shanten

        self._fire_evolution({"event": "hand_rendered", "tile_count": len(hand)})
        return result

    def sort_hand(self, hand: list[str]) -> list[str]:
        """Sort tiles by suit then number."""
        def sort_key(tile: str) -> tuple[int, int]:
            if len(tile) >= 2:
                num = int(tile[:-1]) if tile[:-1].isdigit() else 0
                suit = _SUIT_ORDER.get(tile[-1], 9)
                return (suit, num)
            return (9, 0)
        return sorted(hand, key=sort_key)

    def render_melds(self, melds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Render open melds."""
        result = []
        for meld in melds:
            result.append({
                "type": meld.get("type", "unknown"),
                "tiles": meld.get("tiles", []),
            })
        return result

    def count_remaining(self, tile: str, visible_tiles: list[str]) -> int:
        """Count remaining copies of a tile given visible tiles."""
        visible_count = sum(1 for t in visible_tiles if t == tile)
        return max(_TOTAL_PER_TILE - visible_count, 0)

    def render_text(self, hand: list[str]) -> str:
        """ASCII text rendering of hand."""
        sorted_h = self.sort_hand(hand)
        parts = []
        for t in sorted_h:
            name = _TILE_NAMES.get(t, t)
            parts.append(f"[{t}:{name}]")
        return " ".join(parts)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

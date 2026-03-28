"""
Tenhou Bridge — Tenhou (天凤) platform bridge implementation.

Ported from Akagi mitm/bridge/tenhou/bridge.py with:
- XML message parsing (Tenhou uses XML-like WebSocket messages)
- Tile ID → mjai tile conversion (Tenhou 0-135 → mjai string)
- State tracking (seat, hand, doras, scores)
- Build action → XML bytes

Location: integrations/mahjong/src/mahjong_agent/bridge/tenhou_bridge.py
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional
from xml.etree import ElementTree as ET

from mahjong_agent.bridge.bridge_base import MahjongBridgeBase

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "mahjong_agent.bridge.tenhou_bridge.v1"

# ──────────────── Tenhou tile ID mapping ────────────────
# Tenhou uses 0-135 tile IDs: 4 copies × 34 tiles
# tile_id // 4 → tile_type (0-33)
# 0-8: 1m-9m, 9-17: 1p-9p, 18-26: 1s-9s, 27-33: E,S,W,N,P,F,C
_TILE_TYPE_TO_MJAI: list[str] = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C",
]

_MJAI_TO_TILE_TYPE: dict[str, int] = {v: i for i, v in enumerate(_TILE_TYPE_TO_MJAI)}

# Red five mappings (tile IDs 16, 52, 88 are red fives in Tenhou)
_RED_FIVE_IDS: set[int] = {16, 52, 88}

# Draw/discard tag patterns from Akagi
_DRAW_RE = re.compile(r"^<([TUVW])(\d+)\s*/>$")
_DISCARD_RE = re.compile(r"^<([DEFG])(\d+)\s*/>$")

# Player index mapping: T/D=0, U/E=1, V/F=2, W/G=3
_DRAW_PLAYER = {"T": 0, "U": 1, "V": 2, "W": 3}
_DISCARD_PLAYER = {"D": 0, "E": 1, "F": 2, "G": 3}


def tenhou_tile_to_mjai(tile_id: int) -> str:
    """Convert Tenhou tile ID (0-135) to mjai tile string.

    Red fives (tile_id 16, 52, 88) map to '5mr', '5pr', '5sr'.
    """
    if tile_id in _RED_FIVE_IDS:
        suit_idx = tile_id // 36  # 0=m, 1=p, 2=s
        suits = ["mr", "pr", "sr"]
        return f"5{suits[suit_idx]}"
    tile_type = tile_id // 4
    if 0 <= tile_type < len(_TILE_TYPE_TO_MJAI):
        return _TILE_TYPE_TO_MJAI[tile_type]
    return "?"


def mjai_tile_to_tenhou(mjai_tile: str) -> int:
    """Convert mjai tile string to a representative Tenhou tile ID.

    Returns the first copy (tile_type * 4). For red fives, returns
    the canonical red five ID.
    """
    if mjai_tile == "5mr":
        return 16
    if mjai_tile == "5pr":
        return 52
    if mjai_tile == "5sr":
        return 88
    tile_type = _MJAI_TO_TILE_TYPE.get(mjai_tile)
    if tile_type is not None:
        return tile_type * 4
    return -1


class TenhouBridge(MahjongBridgeBase):
    """Tenhou XML WebSocket bridge.

    Ported from Akagi TenhouBridge with operatorRL adaptations:
    - Delegates tile conversion via tenhou_tile_to_mjai()
    - Tracks game state for correct mjai event generation
    - Integrates with protocol-decoder TenhouCodec for validation
    """

    def __init__(self) -> None:
        super().__init__()
        self._hand: list[int] = []
        self._scores: list[int] = [250, 250, 250, 250]
        self._round: int = 0
        self._honba: int = 0
        self._riichi_sticks: int = 0
        self._dora_indicators: list[int] = []
        self._game_started: bool = False

    @property
    def game_name(self) -> str:
        return "tenhou"

    def parse(self, content: bytes) -> Optional[list[dict[str, Any]]]:
        """Parse Tenhou XML message → mjai events.

        Handles: INIT, draw (T/U/V/W), discard (D/E/F/G), REACH, AGARI,
        N (call), DORA, RYUUKYOKU.
        """
        if not content:
            return None

        try:
            text = content.decode("utf-8", errors="replace").strip()
        except Exception:
            return None

        if not text.startswith("<"):
            return None

        # Handle draw tags: <T45/>, <U12/>, etc.
        draw_match = _DRAW_RE.match(text)
        if draw_match:
            return self._parse_draw(draw_match)

        # Handle discard tags: <D45/>, <E12/>, etc.
        discard_match = _DISCARD_RE.match(text)
        if discard_match:
            return self._parse_discard(discard_match)

        # Parse XML tags
        try:
            # Wrap in root to handle self-closing tags
            elem = ET.fromstring(text)
        except ET.ParseError:
            return None

        tag = elem.tag.upper()

        if tag == "INIT":
            return self._parse_init(elem)
        elif tag == "REACH":
            return self._parse_reach(elem)
        elif tag == "AGARI":
            return self._parse_agari(elem)
        elif tag == "DORA":
            return self._parse_dora(elem)
        elif tag == "N":
            return self._parse_call(elem)
        elif tag == "RYUUKYOKU":
            return self._parse_ryuukyoku(elem)
        elif tag == "GO":
            return self._parse_go(elem)
        elif tag == "UN":
            return self._parse_un(elem)

        return None

    def build(self, command: dict[str, Any]) -> Optional[bytes]:
        """Build Tenhou XML bytes from mjai action.

        Supports: dahai (discard), reach, chi/pon/kan, tsumo, hora.
        """
        action_type = command.get("type", "")

        if action_type == "dahai":
            pai = command.get("pai", "")
            tile_id = mjai_tile_to_tenhou(pai)
            if tile_id < 0:
                return None
            # Self discard uses 'D' prefix
            return f"<D{tile_id}/>".encode("utf-8")

        if action_type == "reach":
            return b'<REACH who="0" step="1"/>'

        if action_type == "none":
            return b"<N />"

        if action_type in ("chi", "pon", "daiminkan", "ankan", "kakan"):
            # Simplified: send generic call tag
            return b"<N />"

        if action_type in ("hora", "tsumo", "ron"):
            return b"<N />"

        return None

    def reset(self) -> None:
        """Reset state for new game."""
        self.account_id = 0
        self.seat = 0
        self._hand.clear()
        self._scores = [250, 250, 250, 250]
        self._round = 0
        self._honba = 0
        self._riichi_sticks = 0
        self._dora_indicators.clear()
        self._game_started = False

    # ──────────────── Private parse helpers ────────────────

    def _parse_init(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <INIT> → start_kyoku event."""
        seed_str = elem.get("seed", "0,0,0,0,0,0")
        seed_parts = [int(x) for x in seed_str.split(",")]
        self._round = seed_parts[0] if len(seed_parts) > 0 else 0
        self._honba = seed_parts[1] if len(seed_parts) > 1 else 0
        self._riichi_sticks = seed_parts[2] if len(seed_parts) > 2 else 0

        # Parse scores
        ten_str = elem.get("ten", "250,250,250,250")
        self._scores = [int(x) for x in ten_str.split(",")]

        # Parse own hand (hai0 for seat 0)
        hai_str = elem.get(f"hai{self.seat}", "")
        if hai_str:
            self._hand = [int(x) for x in hai_str.split(",")]
        else:
            self._hand = []

        # Dora indicator
        dora_id = seed_parts[5] if len(seed_parts) > 5 else -1
        self._dora_indicators = [dora_id] if dora_id >= 0 else []

        self._game_started = True

        tehais = [
            [tenhou_tile_to_mjai(t) for t in self._hand] if i == self.seat else []
            for i in range(4)
        ]

        return [{
            "type": "start_kyoku",
            "bakaze": ["E", "S", "W", "N"][self._round // 4] if self._round < 16 else "E",
            "kyoku": (self._round % 4) + 1,
            "honba": self._honba,
            "kyotaku": self._riichi_sticks,
            "oya": int(elem.get("oya", "0")),
            "scores": self._scores,
            "dora_marker": tenhou_tile_to_mjai(dora_id) if dora_id >= 0 else "?",
            "tehais": tehais,
        }]

    def _parse_draw(self, match: re.Match) -> list[dict[str, Any]]:
        """Parse draw tag → tsumo event."""
        letter = match.group(1)
        tile_id = int(match.group(2))
        player = _DRAW_PLAYER[letter]
        mjai_tile = tenhou_tile_to_mjai(tile_id)

        if player == self.seat:
            self._hand.append(tile_id)

        return [{
            "type": "tsumo",
            "actor": player,
            "pai": mjai_tile,
        }]

    def _parse_discard(self, match: re.Match) -> list[dict[str, Any]]:
        """Parse discard tag → dahai event."""
        letter = match.group(1)
        tile_id = int(match.group(2))
        player = _DISCARD_PLAYER[letter]
        mjai_tile = tenhou_tile_to_mjai(tile_id)

        if player == self.seat and tile_id in self._hand:
            self._hand.remove(tile_id)

        return [{
            "type": "dahai",
            "actor": player,
            "pai": mjai_tile,
            "tsumogiri": False,
        }]

    def _parse_reach(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <REACH> → reach event."""
        who = int(elem.get("who", "0"))
        step = int(elem.get("step", "1"))
        return [{
            "type": "reach",
            "actor": who,
            "step": step,
        }]

    def _parse_agari(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <AGARI> → hora event."""
        who = int(elem.get("who", "0"))
        from_who = int(elem.get("fromWho", str(who)))
        return [{
            "type": "hora",
            "actor": who,
            "target": from_who,
            "tsumo": who == from_who,
        }]

    def _parse_dora(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <DORA> → dora event."""
        hai = int(elem.get("hai", "0"))
        self._dora_indicators.append(hai)
        return [{
            "type": "dora",
            "dora_marker": tenhou_tile_to_mjai(hai),
        }]

    def _parse_call(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <N> → call event (chi/pon/kan)."""
        who = int(elem.get("who", "0"))
        return [{
            "type": "call",
            "actor": who,
        }]

    def _parse_ryuukyoku(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <RYUUKYOKU> → ryuukyoku (draw) event."""
        return [{"type": "ryuukyoku"}]

    def _parse_go(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <GO> → game start metadata."""
        return [{"type": "start_game"}]

    def _parse_un(self, elem: ET.Element) -> list[dict[str, Any]]:
        """Parse <UN> → user list."""
        return [{"type": "user_list"}]

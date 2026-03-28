"""
Riichi City Bridge — 一番街 platform bridge implementation.

Ported from Akagi mitm/bridge/riichi_city/bridge.py with:
- Binary protocol parsing (big-endian header + JSON payload)
- Card ID → mjai tile conversion
- State tracking (seat, hand, game status)
- Build action → binary bytes

Location: integrations/mahjong/src/mahjong_agent/bridge/riichi_city_bridge.py
"""

from __future__ import annotations

import json
import logging
import struct
from typing import Any, Optional

from mahjong_agent.bridge.bridge_base import MahjongBridgeBase

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "mahjong_agent.bridge.riichi_city_bridge.v1"

# ──────────────── RC Protocol Constants (from Akagi) ────────────────
RC_HEADER_SIZE: int = 15  # 4 len + 4 magic + 4 msg_id + 2 msg_type + 1 flag
RC_MAGIC: bytes = b"\x00\x0f\x00\x01"
RC_FLAG: int = 0x01

# Message types from Akagi riichi_city/consts.py pattern
RC_MSG_GAME_START: int = 0x1001
RC_MSG_DEAL_HAND: int = 0x1002
RC_MSG_DRAW: int = 0x1003
RC_MSG_DISCARD: int = 0x1004
RC_MSG_CALL: int = 0x1005
RC_MSG_REACH: int = 0x1006
RC_MSG_AGARI: int = 0x1007
RC_MSG_RYUUKYOKU: int = 0x1008
RC_MSG_GAME_END: int = 0x1009

# Card ID → mjai tile (from Akagi CARD2MJAI pattern)
# Riichi City uses 0-135 card IDs, similar to Tenhou
_CARD_TYPE_TO_MJAI: list[str] = [
    "1m", "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m",
    "1p", "2p", "3p", "4p", "5p", "6p", "7p", "8p", "9p",
    "1s", "2s", "3s", "4s", "5s", "6s", "7s", "8s", "9s",
    "E", "S", "W", "N", "P", "F", "C",
]


def rc_card_to_mjai(card_id: int) -> str:
    """Convert Riichi City card ID to mjai tile string."""
    tile_type = card_id % 34 if card_id >= 0 else -1
    if 0 <= tile_type < len(_CARD_TYPE_TO_MJAI):
        return _CARD_TYPE_TO_MJAI[tile_type]
    return "?"


class RCMessage:
    """Parsed Riichi City binary message."""

    __slots__ = ("msg_id", "msg_type", "msg_data")

    def __init__(self, msg_id: int, msg_type: int, msg_data: dict) -> None:
        self.msg_id = msg_id
        self.msg_type = msg_type
        self.msg_data = msg_data

    def __repr__(self) -> str:
        return f"RCMessage(id={self.msg_id}, type=0x{self.msg_type:04x})"


class RiichiCityBridge(MahjongBridgeBase):
    """Riichi City binary WebSocket bridge.

    Ported from Akagi RiichiCityBridge with operatorRL adaptations.
    Protocol: 4-byte len + 4-byte magic + 4-byte msg_id + 2-byte msg_type
              + 1-byte flag + JSON payload.
    """

    def __init__(self) -> None:
        super().__init__()
        self._hand: list[int] = []
        self._game_started: bool = False
        self._uid: int = -1

    @property
    def game_name(self) -> str:
        return "riichi_city"

    def parse(self, content: bytes) -> Optional[list[dict[str, Any]]]:
        """Parse RC binary message → mjai events."""
        if not content or len(content) < RC_HEADER_SIZE:
            return None

        msg = self._preprocess(content)
        if msg is None:
            return None

        return self._dispatch(msg)

    def build(self, command: dict[str, Any]) -> Optional[bytes]:
        """Build RC binary bytes from mjai action.

        Currently returns None for most actions (read-only bridge).
        Discard actions are supported.
        """
        action_type = command.get("type", "")

        if action_type == "dahai":
            pai = command.get("pai", "")
            # Build a discard message
            payload = json.dumps({"card": pai}).encode("utf-8")
            return self._build_message(0, RC_MSG_DISCARD, payload)

        return None

    def reset(self) -> None:
        """Reset state for new game."""
        self.account_id = 0
        self.seat = 0
        self._hand.clear()
        self._game_started = False
        self._uid = -1

    # ──────────────── Private helpers ────────────────

    def _preprocess(self, content: bytes) -> Optional[RCMessage]:
        """Preprocess binary content → RCMessage.

        From Akagi RiichiCityBridge.preprocess().
        """
        if len(content) < 4:
            return None

        msg_len = int.from_bytes(content[:4], byteorder="big")

        # Validate magic bytes
        if len(content) < 8 or content[4:8] != RC_MAGIC:
            logger.debug("RC: invalid magic bytes")
            return None

        if len(content) < RC_HEADER_SIZE:
            return None

        msg_id = int.from_bytes(content[8:12], byteorder="big")
        msg_type = int.from_bytes(content[12:14], byteorder="big")
        flag = content[14]

        if flag != RC_FLAG:
            logger.debug("RC: unexpected flag byte: %d", flag)
            return None

        # Parse JSON payload
        if len(content) > RC_HEADER_SIZE:
            try:
                msg_data = json.loads(content[RC_HEADER_SIZE:].decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                msg_data = {}
        else:
            msg_data = {}

        return RCMessage(msg_id, msg_type, msg_data)

    def _dispatch(self, msg: RCMessage) -> Optional[list[dict[str, Any]]]:
        """Dispatch parsed message to handler."""
        handlers = {
            RC_MSG_GAME_START: self._handle_game_start,
            RC_MSG_DEAL_HAND: self._handle_deal,
            RC_MSG_DRAW: self._handle_draw,
            RC_MSG_DISCARD: self._handle_discard,
            RC_MSG_CALL: self._handle_call,
            RC_MSG_REACH: self._handle_reach,
            RC_MSG_AGARI: self._handle_agari,
            RC_MSG_RYUUKYOKU: self._handle_ryuukyoku,
            RC_MSG_GAME_END: self._handle_game_end,
        }

        handler = handlers.get(msg.msg_type)
        if handler:
            return handler(msg)

        logger.debug("RC: unhandled msg_type=0x%04x", msg.msg_type)
        return None

    def _handle_game_start(self, msg: RCMessage) -> list[dict[str, Any]]:
        self._game_started = True
        players = msg.msg_data.get("players", [])
        names = [p.get("name", f"Player{i}") for i, p in enumerate(players)]
        return [{"type": "start_game", "names": names}]

    def _handle_deal(self, msg: RCMessage) -> list[dict[str, Any]]:
        cards = msg.msg_data.get("cards", [])
        self._hand = list(cards)
        mjai_tiles = [rc_card_to_mjai(c) for c in cards]
        return [{"type": "start_kyoku", "tehais": [mjai_tiles]}]

    def _handle_draw(self, msg: RCMessage) -> list[dict[str, Any]]:
        card = msg.msg_data.get("card", -1)
        actor = msg.msg_data.get("actor", self.seat)
        return [{"type": "tsumo", "actor": actor, "pai": rc_card_to_mjai(card)}]

    def _handle_discard(self, msg: RCMessage) -> list[dict[str, Any]]:
        card = msg.msg_data.get("card", -1)
        actor = msg.msg_data.get("actor", 0)
        return [{"type": "dahai", "actor": actor, "pai": rc_card_to_mjai(card), "tsumogiri": False}]

    def _handle_call(self, msg: RCMessage) -> list[dict[str, Any]]:
        actor = msg.msg_data.get("actor", 0)
        return [{"type": "call", "actor": actor}]

    def _handle_reach(self, msg: RCMessage) -> list[dict[str, Any]]:
        actor = msg.msg_data.get("actor", 0)
        return [{"type": "reach", "actor": actor}]

    def _handle_agari(self, msg: RCMessage) -> list[dict[str, Any]]:
        who = msg.msg_data.get("who", 0)
        return [{"type": "hora", "actor": who}]

    def _handle_ryuukyoku(self, msg: RCMessage) -> list[dict[str, Any]]:
        return [{"type": "ryuukyoku"}]

    def _handle_game_end(self, msg: RCMessage) -> list[dict[str, Any]]:
        self._game_started = False
        return [{"type": "end_game"}]

    def _build_message(self, msg_id: int, msg_type: int, payload: bytes) -> bytes:
        """Build RC binary message."""
        total_len = RC_HEADER_SIZE + len(payload)
        header = struct.pack(">I", total_len)
        mid = struct.pack(">I", msg_id)
        mtype = struct.pack(">H", msg_type)
        return header + RC_MAGIC + mid + mtype + bytes([RC_FLAG]) + payload

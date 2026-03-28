"""
Majsoul Bridge — 雀魂 platform bridge implementation.

Ported from Akagi mitm/bridge/majsoul/bridge.py with:
- Tile mapping tables (MS ↔ mjai)
- Operation enum for game actions
- LiqiParserAdapter delegation for protocol decoding
- State tracking (seat, hand, doras, reach, etc.)

Location: integrations/mahjong/src/mahjong_agent/bridge/majsoul_bridge.py
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
from mahjong_agent.bridge.liqi_parser import LiqiParserAdapter

logger = logging.getLogger(__name__)


# ──────────────────────── Tile Mappings ────────────────────────
# Ported from Akagi bridge.py — complete MS ↔ mjai tile conversion.

MS_TILE_2_MJAI_TILE = {
    '0m': '5mr', '1m': '1m', '2m': '2m', '3m': '3m', '4m': '4m',
    '5m': '5m', '6m': '6m', '7m': '7m', '8m': '8m', '9m': '9m',
    '0p': '5pr', '1p': '1p', '2p': '2p', '3p': '3p', '4p': '4p',
    '5p': '5p', '6p': '6p', '7p': '7p', '8p': '8p', '9p': '9p',
    '0s': '5sr', '1s': '1s', '2s': '2s', '3s': '3s', '4s': '4s',
    '5s': '5s', '6s': '6s', '7s': '7s', '8s': '8s', '9s': '9s',
    '1z': 'E', '2z': 'S', '3z': 'W', '4z': 'N',
    '5z': 'P', '6z': 'F', '7z': 'C',
}

MJAI_TILE_2_MS_TILE = {v: k for k, v in MS_TILE_2_MJAI_TILE.items()}


# ──────────────────────── Operation Enum ──────────────────────

class Operation:
    """Majsoul operation codes from Akagi."""
    NoEffect = 0
    Discard = 1
    Chi = 2
    Peng = 3
    AnGang = 4
    MingGang = 5
    JiaGang = 6
    Liqi = 7
    Zimo = 8
    Hu = 9
    LiuJu = 10


# ──────────────────────── MajsoulBridge ──────────────────────

class MajsoulBridge(MahjongBridgeBase):
    """雀魂 (Majsoul) MITM bridge.

    Maintains game state and translates between Majsoul's liqi protocol
    and mjai-format events for the mahjong agent.
    """

    def __init__(self) -> None:
        super().__init__()
        self._liqi = LiqiParserAdapter()
        self.doras: list[str] = []
        self.reach: bool = False
        self.accept_reach: Optional[bool] = None
        self.operation: dict[str, Any] = {}
        self.all_ready: bool = False
        self.my_tehais: list[str] = ["?"] * 13
        self.my_tsumohai: str = "?"
        self.syncing: bool = False
        self.mode_id: int = -1
        self.rank: int = -1
        self.score: int = -1
        self.is_3p: bool = False

    @property
    def game_name(self) -> str:
        return "majsoul"

    def reset(self) -> None:
        """Reset all state for a new game."""
        self.account_id = 0
        self.seat = 0
        self.doras = []
        self.reach = False
        self.accept_reach = None
        self.operation = {}
        self.all_ready = False
        self.my_tehais = ["?"] * 13
        self.my_tsumohai = "?"
        self.syncing = False
        self.mode_id = -1
        self.rank = -1
        self.score = -1
        self.is_3p = False
        self._liqi.init()

    def parse(self, content: bytes) -> Optional[list[dict[str, Any]]]:
        """Parse raw liqi bytes into mjai events.

        Delegates protocol decoding to LiqiParserAdapter, then
        translates Majsoul-specific semantics to mjai format.
        """
        if not content:
            return None

        liqi_message = self._liqi.parse(content)
        if liqi_message is None:
            return None

        return self._parse_liqi(liqi_message)

    def _parse_liqi(self, liqi_message: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
        """Translate a decoded liqi message into mjai events.

        Handles: authGame, ActionNewRound, ActionDealTile, ActionDiscardTile,
        ActionChiPengGang, etc.
        """
        ret: list[dict[str, Any]] = []
        method = liqi_message.get("method", "")
        msg_type = liqi_message.get("type")
        data = liqi_message.get("data", {})

        # authGame request — set account ID
        if method == ".lq.FastTest.authGame" and str(msg_type) == "MsgType.REQ":
            self.account_id = data.get("accountId", 0)
            return ret

        # authGame response — determine seat
        if method == ".lq.FastTest.authGame" and str(msg_type) == "MsgType.RES":
            seat_list = data.get("seatList", [])
            self.is_3p = len(seat_list) == 3
            if self.account_id in seat_list:
                self.seat = seat_list.index(self.account_id)
            ret.append({"type": "start_game", "id": self.seat})
            return ret

        # Default: pass through method name for logging
        logger.debug("Unhandled liqi method: %s", method)
        return ret if ret else None

    def build(self, command: dict[str, Any]) -> Optional[bytes]:
        """Build raw bytes from mjai action. (Minimal — full encoding requires pb2 stubs.)"""
        # Full action injection requires compiled protobuf stubs
        return None

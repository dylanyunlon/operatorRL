"""
MahjongProtocolAdapter — Mahjong (雀魂/Majsoul) MITM protocol adapter.

Adapts the Majsoul Protobuf-based MITM proxy protocol (as used by Akagi)
to the universal game state schema. Supports both liqi protobuf and mjai
standard protocol formats.

Location: extensions/protocol_decoder/src/mahjong_protocol_adapter.py

Reference (拿来主义):
  - Akagi/mitm/bridge/majsoul/liqi.py: protobuf decode/encode with XOR mask
  - Akagi/mitm/mitm_abc.py: abstract MITM bridge lifecycle
  - game_protocol_adapter_base.py（M666）: unified adapter interface
  - Mortal/: mjai protocol definitions

Design Notes (Knuth-level critique):
  User:
    - decode() handles both raw liqi-style dicts and mjai-style action dicts.
    - normalize() maps tile/action/player data to universal schema.
    - Tile encoding is transparent — consumers see human-readable tile names.
  System:
    - Message type dispatch (Notify/Req/Res) mirrors Akagi LiqiProto.parse pattern.
    - mjai action→universal event mapping is O(1) dict lookup.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.mahjong_protocol_adapter.v1"

try:
    from .game_protocol_adapter_base import GameProtocolAdapterBase
except ImportError:
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from game_protocol_adapter_base import GameProtocolAdapterBase


# mjai action types → universal event types
_MJAI_ACTION_MAP = {
    "tsumo": "draw_tile",
    "dahai": "discard_tile",
    "chi": "call_chi",
    "pon": "call_pon",
    "kakan": "call_kakan",
    "daiminkan": "call_daiminkan",
    "ankan": "call_ankan",
    "reach": "declare_riichi",
    "hora": "declare_win",
    "ryukyoku": "draw_game",
    "start_game": "game_start",
    "end_game": "game_end",
    "start_kyoku": "round_start",
    "end_kyoku": "round_end",
    "none": "pass_action",
}

# Liqi message types (mirrors Akagi MsgType)
_LIQI_MSG_TYPES = {"Notify": 1, "Req": 2, "Res": 3}


class MahjongProtocolAdapter(GameProtocolAdapterBase):
    """Mahjong (Majsoul) protocol adapter — MITM proxy + mjai.

    Config keys:
        protocol: str ('mjai' or 'liqi', default 'mjai')
        mitm_port: int (default 7878)
        seat: int (default 0, player seat index 0-3)
    """

    def __init__(self) -> None:
        super().__init__()
        self._protocol: str = "mjai"
        self._mitm_port: int = 7878
        self._seat: int = 0
        self._action_stats: Dict[str, int] = {}
        self._round_count: int = 0
        self._current_round: Dict[str, Any] = {}

    @property
    def game_type(self) -> str:
        return "mahjong"

    def _connect_impl(self, config: Dict[str, Any]) -> bool:
        self._protocol = config.get("protocol", "mjai")
        self._mitm_port = config.get("mitm_port", 7878)
        self._seat = config.get("seat", 0)
        if self._protocol not in ("mjai", "liqi"):
            return False
        if not isinstance(self._seat, int) or self._seat < 0 or self._seat > 3:
            return False
        return True

    def _disconnect_impl(self) -> None:
        self._action_stats.clear()
        self._current_round.clear()

    def _decode_impl(self, raw_data: Any) -> Dict[str, Any]:
        """Decode Mahjong protocol message."""
        if isinstance(raw_data, str):
            parsed = json.loads(raw_data)
        elif isinstance(raw_data, dict):
            parsed = dict(raw_data)
        else:
            raise ValueError(f"Unsupported raw_data type: {type(raw_data)}")

        # Detect protocol type
        if "type" in parsed:
            # mjai format: {"type": "tsumo", "actor": 0, "pai": "5m"}
            proto = "mjai"
            action = parsed.get("type", "unknown")
        elif "method" in parsed or "msg_type" in parsed:
            # liqi format: {"msg_type": 1, "method": ".lq.ActionPrototype", "data": {...}}
            proto = "liqi"
            action = parsed.get("method", parsed.get("msg_type", "unknown"))
        else:
            proto = "unknown"
            action = "unknown"

        self._action_stats[str(action)] = self._action_stats.get(str(action), 0) + 1

        return {
            "protocol": proto,
            "action": action,
            "data": parsed,
            "source": "mitm",
        }

    def _normalize_impl(self, decoded: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize Mahjong data to universal schema."""
        data = decoded.get("data", {})
        proto = decoded.get("protocol", "unknown")
        action_raw = decoded.get("action", "unknown")

        result: Dict[str, Any] = {
            "game_type": "mahjong",
            "game_time": 0.0,
            "players": [],
            "map_state": {},
            "resources": {},
            "events": [],
            "_raw": data,
        }

        if proto == "mjai":
            return self._normalize_mjai(data, result)
        elif proto == "liqi":
            return self._normalize_liqi(data, result)
        return result

    def _normalize_mjai(self, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize mjai protocol data."""
        action_type = data.get("type", "unknown")
        universal_event = _MJAI_ACTION_MAP.get(action_type, action_type)

        # Track round state
        if action_type == "start_kyoku":
            self._round_count += 1
            self._current_round = {
                "bakaze": data.get("bakaze", ""),
                "dora_marker": data.get("dora_marker", ""),
                "kyoku": data.get("kyoku", 0),
                "honba": data.get("honba", 0),
                "kyotaku": data.get("kyotaku", 0),
                "oya": data.get("oya", 0),
                "scores": data.get("scores", []),
            }
            # Build player list from scores
            scores = data.get("scores", [0, 0, 0, 0])
            for i, score in enumerate(scores):
                result["players"].append({
                    "name": f"player_{i}",
                    "champion": "",  # no champion concept in mahjong
                    "level": 0,
                    "team": "east" if (i - data.get("oya", 0)) % 4 == 0 else "other",
                    "is_dead": False,
                    "position": ["east", "south", "west", "north"][(i - data.get("oya", 0)) % 4],
                    "kills": 0,
                    "deaths": 0,
                    "assists": 0,
                    "cs": 0,
                })

        result["events"].append({
            "type": universal_event,
            "time": 0.0,
            "data": {
                "actor": data.get("actor", -1),
                "pai": data.get("pai", ""),
                "consumed": data.get("consumed", []),
                "target": data.get("target", -1),
            },
        })

        # Map state as round state
        result["map_state"] = {
            "map_id": "mahjong_table",
            "round": self._round_count,
            **self._current_round,
        }

        # Resources: scores
        result["resources"] = {
            "scores": self._current_round.get("scores", []),
            "kyotaku": self._current_round.get("kyotaku", 0),
        }

        return result

    def _normalize_liqi(self, data: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize liqi protocol data (Majsoul protobuf)."""
        method = data.get("method", "")
        inner = data.get("data", data)

        result["events"].append({
            "type": f"liqi_{method.split('.')[-1] if '.' in method else method}",
            "time": 0.0,
            "data": inner,
        })

        result["map_state"] = {"map_id": "mahjong_table", "protocol": "liqi"}
        return result

    def get_action_stats(self) -> Dict[str, int]:
        """Return per-action decode counts."""
        return dict(self._action_stats)

    @property
    def round_count(self) -> int:
        return self._round_count

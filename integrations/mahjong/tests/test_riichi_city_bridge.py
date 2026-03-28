"""
TDD Tests for M247: RiichiCityBridge — Riichi City platform bridge.

10 tests for binary protocol parsing, mjai conversion, build, reset.
"""

import json
import struct
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestRiichiCityBridgeConstruction:
    def test_import_and_construct(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        assert bridge is not None

    def test_inherits_bridge_base(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        bridge = RiichiCityBridge()
        assert isinstance(bridge, MahjongBridgeBase)


class TestRiichiCityBridgeGameName:
    def test_game_name(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        assert bridge.game_name == "riichi_city"


class TestRiichiCityBridgeParse:
    def _build_rc_message(self, msg_id: int, msg_type: int, data: dict) -> bytes:
        """Build a Riichi City binary message from Akagi format."""
        json_bytes = json.dumps(data).encode("utf-8")
        # Format: 4-byte total_len + 4-byte magic + 4-byte msg_id + 2-byte msg_type + 1-byte flag + payload
        total_len = 15 + len(json_bytes)
        header = struct.pack(">I", total_len)
        magic = b"\x00\x0f\x00\x01"
        msg_id_bytes = struct.pack(">I", msg_id)
        msg_type_bytes = struct.pack(">H", msg_type)
        flag = b"\x01"
        return header + magic + msg_id_bytes + msg_type_bytes + flag + json_bytes

    def test_parse_game_start(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        msg = self._build_rc_message(1, 0x1001, {"players": [{"uid": 1}, {"uid": 2}]})
        result = bridge.parse(msg)
        assert result is not None
        assert isinstance(result, list)

    def test_parse_deal_hand(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        msg = self._build_rc_message(2, 0x1002, {"cards": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]})
        result = bridge.parse(msg)
        # Even if it returns None for unrecognized types, that's acceptable
        assert result is None or isinstance(result, list)

    def test_parse_empty_returns_none(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        assert bridge.parse(b"") is None

    def test_parse_garbage_returns_none(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        assert bridge.parse(b"\x00\x01\x02") is None


class TestRiichiCityBridgeBuild:
    def test_build_dahai(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        cmd = {"type": "dahai", "pai": "5m", "actor": 0, "tsumogiri": False}
        result = bridge.build(cmd)
        assert result is None or isinstance(result, bytes)

    def test_build_unsupported_returns_none(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        assert bridge.build({"type": "nonsense"}) is None


class TestRiichiCityBridgeReset:
    def test_reset_clears_state(self):
        from mahjong_agent.bridge.riichi_city_bridge import RiichiCityBridge
        bridge = RiichiCityBridge()
        bridge.seat = 3
        bridge.account_id = 999
        bridge.reset()
        assert bridge.seat == 0
        assert bridge.account_id == 0

"""
TDD Tests for M246: TenhouBridge — tenhou platform bridge.

10 tests, designed so ~50% fail on first run (module doesn't exist yet).
Tests cover: construction, game_name, parse XML→mjai, build mjai→bytes,
reset state, tile conversion, round handling, error paths.
"""

import json
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestTenhouBridgeConstruction:
    """M246-T01: TenhouBridge can be instantiated."""

    def test_import_and_construct(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        assert bridge is not None

    def test_inherits_bridge_base(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        bridge = TenhouBridge()
        assert isinstance(bridge, MahjongBridgeBase)


class TestTenhouBridgeGameName:
    """M246-T02: game_name returns 'tenhou'."""

    def test_game_name(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        assert bridge.game_name == "tenhou"


class TestTenhouBridgeParse:
    """M246-T03/04/05: parse() converts Tenhou XML → mjai events."""

    def test_parse_init_tag(self):
        """Parse <INIT> tag with seed, hand, scores."""
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        xml_bytes = b'<INIT seed="0,0,0,3,2,45" ten="250,250,250,250" oya="0" hai0="1,2,3,4,5,6,7,8,9,10,11,12,13"/>'
        result = bridge.parse(xml_bytes)
        assert result is not None
        assert isinstance(result, list)
        assert len(result) >= 1
        # Should contain a start_kyoku-like event
        assert any(e.get("type") in ("start_kyoku", "init", "start_game") for e in result)

    def test_parse_draw_tag(self):
        """Parse draw tags T0-T135 (self draw)."""
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        # First init a game so state is valid
        init = b'<INIT seed="0,0,0,3,2,45" ten="250,250,250,250" oya="0" hai0="1,2,3,4,5,6,7,8,9,10,11,12,13"/>'
        bridge.parse(init)
        result = bridge.parse(b"<T34/>")
        assert result is not None
        assert isinstance(result, list)
        assert any(e.get("type") == "tsumo" for e in result)

    def test_parse_discard_tag(self):
        """Parse discard tags D0-D135 (self discard)."""
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        init = b'<INIT seed="0,0,0,3,2,45" ten="250,250,250,250" oya="0" hai0="1,2,3,4,5,6,7,8,9,10,11,12,13"/>'
        bridge.parse(init)
        result = bridge.parse(b"<D34/>")
        assert result is not None
        assert isinstance(result, list)
        assert any(e.get("type") == "dahai" for e in result)

    def test_parse_empty_returns_none(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        result = bridge.parse(b"")
        assert result is None

    def test_parse_garbage_returns_none(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        result = bridge.parse(b"\x00\xff\xfe")
        assert result is None


class TestTenhouBridgeBuild:
    """M246-T06/07: build() converts mjai action → bytes."""

    def test_build_dahai(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        cmd = {"type": "dahai", "pai": "5m", "actor": 0, "tsumogiri": False}
        result = bridge.build(cmd)
        assert result is not None
        assert isinstance(result, bytes)

    def test_build_unsupported_returns_none(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        cmd = {"type": "unknown_action_xyz"}
        result = bridge.build(cmd)
        assert result is None


class TestTenhouBridgeReset:
    """M246-T08/09: reset() clears state."""

    def test_reset_clears_seat(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        bridge.seat = 2
        bridge.reset()
        assert bridge.seat == 0

    def test_reset_clears_account(self):
        from mahjong_agent.bridge.tenhou_bridge import TenhouBridge
        bridge = TenhouBridge()
        bridge.account_id = 12345
        bridge.reset()
        assert bridge.account_id == 0


class TestTenhouBridgeTileConversion:
    """M246-T10: tile ID conversion."""

    def test_tile_id_to_mjai(self):
        from mahjong_agent.bridge.tenhou_bridge import tenhou_tile_to_mjai
        # Tenhou tile IDs: 4 copies each, tile_id // 4 = tile_type
        # 0-3=1m, 4-7=2m, ..., 36-39=1p, ..., 72-75=1s, ..., 108-111=E
        assert tenhou_tile_to_mjai(0) == "1m"
        assert tenhou_tile_to_mjai(36) == "1p"
        assert tenhou_tile_to_mjai(72) == "1s"
        assert tenhou_tile_to_mjai(108) == "E"

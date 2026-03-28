"""
TDD Tests for M229-M234: Bridge subpackage.

M229: bridge/__init__.py (import test)
M230: bridge_base.py (MITM bridge ABC)
M231: majsoul_bridge.py (雀魂桥接)
M232: liqi_parser.py (liqi协议解析 -> protocol-decoder委托)
M233: mitm_abc.py (MITM抽象入口)
M234: mitm_majsoul.py (雀魂MITM入口 -> fiddler-bridge对接)

10 tests per functional module. Expected ~50% failure.

Location: integrations/mahjong/tests/test_bridge.py
"""

import json
import pytest


# ── M229: bridge __init__ ──

class TestBridgeInit:
    def test_bridge_package_importable(self):
        from mahjong_agent.bridge import __doc__
        # Just confirms the package is importable

    def test_bridge_exports_base(self):
        from mahjong_agent.bridge import MahjongBridgeBase
        assert MahjongBridgeBase is not None


# ── M230: bridge_base.py ──

class TestMahjongBridgeBase:
    """Tests for MahjongBridgeBase — adapted from Akagi BridgeBase + GameCodec."""

    def test_bridge_base_is_abstract(self):
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        with pytest.raises(TypeError):
            MahjongBridgeBase()

    def test_bridge_base_requires_parse(self):
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        # Subclass without parse should fail
        class BadBridge(MahjongBridgeBase):
            @property
            def game_name(self):
                return "test"
            def build(self, command):
                return None
            def reset(self):
                pass
        with pytest.raises(TypeError):
            BadBridge()

    def test_bridge_base_requires_build(self):
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        class BadBridge(MahjongBridgeBase):
            @property
            def game_name(self):
                return "test"
            def parse(self, content):
                return None
            def reset(self):
                pass
        with pytest.raises(TypeError):
            BadBridge()

    def test_bridge_base_requires_game_name(self):
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        class BadBridge(MahjongBridgeBase):
            def parse(self, content):
                return None
            def build(self, command):
                return None
            def reset(self):
                pass
        with pytest.raises(TypeError):
            BadBridge()

    def test_bridge_base_concrete_subclass_works(self):
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        class GoodBridge(MahjongBridgeBase):
            @property
            def game_name(self):
                return "test_game"
            def parse(self, content):
                return [{"type": "none"}]
            def build(self, command):
                return b"ok"
            def reset(self):
                pass
        bridge = GoodBridge()
        assert bridge.game_name == "test_game"
        assert bridge.parse(b"data") == [{"type": "none"}]
        assert bridge.build({"type": "none"}) == b"ok"

    def test_bridge_base_has_state_tracking(self):
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        class SimpleBridge(MahjongBridgeBase):
            @property
            def game_name(self):
                return "simple"
            def parse(self, content):
                return None
            def build(self, command):
                return None
            def reset(self):
                self._custom_state = 0
        bridge = SimpleBridge()
        assert hasattr(bridge, 'account_id')
        assert hasattr(bridge, 'seat')

    def test_bridge_base_reset_clears_state(self):
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        class TrackBridge(MahjongBridgeBase):
            @property
            def game_name(self):
                return "track"
            def parse(self, content):
                return None
            def build(self, command):
                return None
            def reset(self):
                self.account_id = 0
                self.seat = 0
        bridge = TrackBridge()
        bridge.account_id = 12345
        bridge.seat = 2
        bridge.reset()
        assert bridge.account_id == 0
        assert bridge.seat == 0


# ── M231: majsoul_bridge.py ──

class TestMajsoulBridge:
    """Tests for MajsoulBridge — Majsoul-specific parse/build."""

    def test_majsoul_bridge_inherits_base(self):
        from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge
        from mahjong_agent.bridge.bridge_base import MahjongBridgeBase
        bridge = MajsoulBridge()
        assert isinstance(bridge, MahjongBridgeBase)

    def test_majsoul_bridge_game_name(self):
        from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge
        bridge = MajsoulBridge()
        assert bridge.game_name == "majsoul"

    def test_majsoul_tile_conversion_ms_to_mjai(self):
        from mahjong_agent.bridge.majsoul_bridge import MS_TILE_2_MJAI_TILE
        assert MS_TILE_2_MJAI_TILE["1m"] == "1m"
        assert MS_TILE_2_MJAI_TILE["0m"] == "5mr"
        assert MS_TILE_2_MJAI_TILE["1z"] == "E"
        assert MS_TILE_2_MJAI_TILE["7z"] == "C"

    def test_majsoul_tile_conversion_mjai_to_ms(self):
        from mahjong_agent.bridge.majsoul_bridge import MJAI_TILE_2_MS_TILE
        assert MJAI_TILE_2_MS_TILE["5mr"] == "0m"
        assert MJAI_TILE_2_MS_TILE["E"] == "1z"

    def test_majsoul_bridge_parse_none_on_empty(self):
        from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge
        bridge = MajsoulBridge()
        assert bridge.parse(b"") is None

    def test_majsoul_bridge_reset_clears_game_state(self):
        from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge
        bridge = MajsoulBridge()
        bridge.seat = 3
        bridge.account_id = 99999
        bridge.reset()
        assert bridge.seat == 0
        assert bridge.account_id == 0

    def test_majsoul_bridge_has_doras_tracking(self):
        from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge
        bridge = MajsoulBridge()
        assert hasattr(bridge, 'doras')
        assert isinstance(bridge.doras, list)

    def test_majsoul_bridge_has_reach_flag(self):
        from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge
        bridge = MajsoulBridge()
        assert hasattr(bridge, 'reach')
        assert bridge.reach is False

    def test_majsoul_bridge_operation_enum(self):
        from mahjong_agent.bridge.majsoul_bridge import Operation
        assert Operation.Discard == 1
        assert Operation.Chi == 2
        assert Operation.Hu == 9

    def test_majsoul_bridge_build_returns_bytes_or_none(self):
        from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge
        bridge = MajsoulBridge()
        result = bridge.build({"type": "none"})
        assert result is None or isinstance(result, bytes)


# ── M232: liqi_parser.py ──

class TestLiqiParser:
    """Tests for liqi_parser — delegates to protocol-decoder LiqiCodec."""

    def test_liqi_parser_class_exists(self):
        from mahjong_agent.bridge.liqi_parser import LiqiParserAdapter
        assert LiqiParserAdapter is not None

    def test_liqi_parser_wraps_codec(self):
        from mahjong_agent.bridge.liqi_parser import LiqiParserAdapter
        parser = LiqiParserAdapter()
        assert hasattr(parser, 'codec')

    def test_liqi_parser_parse_returns_dict_or_none(self):
        from mahjong_agent.bridge.liqi_parser import LiqiParserAdapter
        parser = LiqiParserAdapter()
        result = parser.parse(b"")
        assert result is None

    def test_liqi_parser_init_resets_codec(self):
        from mahjong_agent.bridge.liqi_parser import LiqiParserAdapter
        parser = LiqiParserAdapter()
        parser.init()
        # Should not raise

    def test_liqi_parser_parse_syncgame_returns_list(self):
        from mahjong_agent.bridge.liqi_parser import LiqiParserAdapter
        parser = LiqiParserAdapter()
        result = parser.parse_syncgame({"data": {}})
        assert isinstance(result, list)


# ── M233: mitm_abc.py ──

class TestMitmABC:
    """Tests for MITM abstract entry point — WebSocket lifecycle."""

    def test_mitm_handler_is_abstract(self):
        from mahjong_agent.bridge.mitm_abc import MitmHandlerABC
        with pytest.raises(TypeError):
            MitmHandlerABC()

    def test_mitm_handler_requires_on_open(self):
        from mahjong_agent.bridge.mitm_abc import MitmHandlerABC
        class BadHandler(MitmHandlerABC):
            async def on_message(self, flow_id, content, from_client):
                pass
            async def on_close(self, flow_id):
                pass
        with pytest.raises(TypeError):
            BadHandler()

    def test_mitm_handler_requires_on_message(self):
        from mahjong_agent.bridge.mitm_abc import MitmHandlerABC
        class BadHandler(MitmHandlerABC):
            async def on_open(self, flow_id, url):
                pass
            async def on_close(self, flow_id):
                pass
        with pytest.raises(TypeError):
            BadHandler()

    def test_mitm_handler_concrete_subclass(self):
        from mahjong_agent.bridge.mitm_abc import MitmHandlerABC
        class GoodHandler(MitmHandlerABC):
            async def on_open(self, flow_id, url):
                pass
            async def on_message(self, flow_id, content, from_client):
                return None
            async def on_close(self, flow_id):
                pass
        handler = GoodHandler()
        assert handler is not None

    def test_mitm_handler_tracks_flows(self):
        from mahjong_agent.bridge.mitm_abc import MitmHandlerABC
        class FlowHandler(MitmHandlerABC):
            async def on_open(self, flow_id, url):
                self._active_flows[flow_id] = url
            async def on_message(self, flow_id, content, from_client):
                return None
            async def on_close(self, flow_id):
                self._active_flows.pop(flow_id, None)
        handler = FlowHandler()
        assert hasattr(handler, '_active_flows')
        assert isinstance(handler._active_flows, dict)


# ── M234: mitm_majsoul.py ──

class TestMitmMajsoul:
    """Tests for 雀魂MITM入口 — fiddler-bridge对接."""

    def test_mitm_majsoul_inherits_abc(self):
        from mahjong_agent.bridge.mitm_majsoul import MajsoulMitmHandler
        from mahjong_agent.bridge.mitm_abc import MitmHandlerABC
        handler = MajsoulMitmHandler()
        assert isinstance(handler, MitmHandlerABC)

    def test_mitm_majsoul_has_bridge(self):
        from mahjong_agent.bridge.mitm_majsoul import MajsoulMitmHandler
        handler = MajsoulMitmHandler()
        assert hasattr(handler, 'bridge')

    def test_mitm_majsoul_on_open_activates_flow(self):
        import asyncio
        from mahjong_agent.bridge.mitm_majsoul import MajsoulMitmHandler
        handler = MajsoulMitmHandler()
        asyncio.get_event_loop().run_until_complete(
            handler.on_open("flow_1", "wss://game.maj-soul.com/ws")
        )
        assert "flow_1" in handler._active_flows

    def test_mitm_majsoul_on_close_deactivates_flow(self):
        import asyncio
        from mahjong_agent.bridge.mitm_majsoul import MajsoulMitmHandler
        handler = MajsoulMitmHandler()
        loop = asyncio.get_event_loop()
        loop.run_until_complete(handler.on_open("flow_2", "wss://game.maj-soul.com/ws"))
        loop.run_until_complete(handler.on_close("flow_2"))
        assert "flow_2" not in handler._active_flows

    def test_mitm_majsoul_filters_non_game_urls(self):
        import asyncio
        from mahjong_agent.bridge.mitm_majsoul import MajsoulMitmHandler
        handler = MajsoulMitmHandler()
        asyncio.get_event_loop().run_until_complete(
            handler.on_open("flow_3", "https://www.google.com")
        )
        # Non-game URLs should be tracked but flagged
        assert handler._active_flows.get("flow_3", {}).get("is_game", True) is False

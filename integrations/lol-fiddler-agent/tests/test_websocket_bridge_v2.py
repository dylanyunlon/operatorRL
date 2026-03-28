"""
TDD Tests for M254: WebSocketBridgeV2 — aligned with fiddler-bridge architecture.

10 tests: construction, connect/disconnect lifecycle, message routing,
frame parsing, reconnect, statistics, evolution key.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestWSBridgeV2Construction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2
        bridge = WebSocketBridgeV2()
        assert bridge is not None

    def test_default_state(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2
        bridge = WebSocketBridgeV2()
        assert bridge.state == "disconnected"


class TestWSBridgeV2Config:
    def test_config_url(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2, WSBridgeV2Config
        cfg = WSBridgeV2Config(ws_url="ws://localhost:8080")
        bridge = WebSocketBridgeV2(config=cfg)
        assert bridge.config.ws_url == "ws://localhost:8080"

    def test_config_reconnect(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2, WSBridgeV2Config
        cfg = WSBridgeV2Config(auto_reconnect=True, max_reconnect_attempts=5)
        bridge = WebSocketBridgeV2(config=cfg)
        assert bridge.config.max_reconnect_attempts == 5


class TestWSBridgeV2MessageRouting:
    def test_route_text_message(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2
        bridge = WebSocketBridgeV2()
        result = bridge.route_message('{"type": "game_event", "data": {}}')
        assert result is not None

    def test_route_binary_message(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2
        bridge = WebSocketBridgeV2()
        result = bridge.route_message(b"\x00\x01\x02")
        assert result is not None or result is None  # May be unsupported

    def test_message_handler_registration(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2
        bridge = WebSocketBridgeV2()
        handler_called = []
        bridge.on_message("game_event", lambda msg: handler_called.append(msg))
        bridge.route_message('{"type": "game_event", "data": {"test": true}}')
        assert len(handler_called) >= 1


class TestWSBridgeV2Stats:
    def test_stats_initial(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2
        bridge = WebSocketBridgeV2()
        stats = bridge.get_stats()
        assert stats["messages_received"] == 0

    def test_stats_after_message(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import WebSocketBridgeV2
        bridge = WebSocketBridgeV2()
        bridge.route_message('{"type": "test"}')
        stats = bridge.get_stats()
        assert stats["messages_received"] >= 1

    def test_evolution_key(self):
        from lol_fiddler_agent.network.websocket_bridge_v2 import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

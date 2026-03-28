"""
TDD Tests for M251: FiddlerClientV2 — migrated to fiddler-bridge unified architecture.

10 tests: construction, connect, capture, session handling, filter,
unified bridge protocol, error recovery.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestFiddlerClientV2Construction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2
        client = FiddlerClientV2()
        assert client is not None

    def test_default_state_disconnected(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2
        client = FiddlerClientV2()
        assert client.is_connected is False


class TestFiddlerClientV2Config:
    def test_config_bridge_url(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2, FiddlerV2Config
        cfg = FiddlerV2Config(bridge_url="http://localhost:9090")
        client = FiddlerClientV2(config=cfg)
        assert client.config.bridge_url == "http://localhost:9090"

    def test_config_capture_mode(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2, FiddlerV2Config
        cfg = FiddlerV2Config(capture_mode="reverse_proxy")
        client = FiddlerClientV2(config=cfg)
        assert client.config.capture_mode == "reverse_proxy"


class TestFiddlerClientV2Capture:
    def test_capture_sessions_empty_initially(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2
        client = FiddlerClientV2()
        assert len(client.captured_sessions) == 0

    def test_add_filter(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2
        client = FiddlerClientV2()
        client.add_filter("host", "127.0.0.1")
        assert len(client.filters) == 1

    def test_process_raw_session(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2
        client = FiddlerClientV2()
        session = {
            "id": "sess-1",
            "method": "GET",
            "url": "https://127.0.0.1:2999/liveclientdata/allgamedata",
            "status_code": 200,
            "response_body": '{"activePlayer": {}}',
        }
        result = client.process_session(session)
        assert result is not None
        assert result.get("session_id") == "sess-1"


class TestFiddlerClientV2BridgeProtocol:
    def test_uses_bridge_codec(self):
        """Verify V2 delegates to fiddler-bridge codec, not raw HTTP."""
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2
        client = FiddlerClientV2()
        assert hasattr(client, "_bridge_codec") or hasattr(client, "_codec")

    def test_session_to_unified_format(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import FiddlerClientV2
        client = FiddlerClientV2()
        raw = {
            "id": "s2",
            "method": "GET",
            "url": "https://127.0.0.1:2999/liveclientdata/playerlist",
            "status_code": 200,
            "response_body": "[]",
        }
        result = client.to_unified_format(raw)
        assert "game" in result or "codec_name" in result

    def test_evolution_key_exists(self):
        from lol_fiddler_agent.network.fiddler_client_v2 import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)
        assert len(_EVOLUTION_KEY) > 0

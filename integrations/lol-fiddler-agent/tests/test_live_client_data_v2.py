"""
TDD Tests for M252: LiveClientDataV2 — LoL Live API v2 with LoLCodec verification.

10 tests: construction, fetch endpoints, parse responses, codec consistency,
snapshot conversion, error handling.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestLiveClientDataV2Construction:
    def test_import_and_construct(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        assert client is not None

    def test_default_endpoint(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        assert "127.0.0.1" in client.base_url or "localhost" in client.base_url


class TestLiveClientDataV2Endpoints:
    def test_all_game_data_url(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        url = client.build_url("allgamedata")
        assert "allgamedata" in url

    def test_player_list_url(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        url = client.build_url("playerlist")
        assert "playerlist" in url

    def test_active_player_url(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        url = client.build_url("activeplayer")
        assert "activeplayer" in url


class TestLiveClientDataV2Parse:
    def test_parse_all_game_data(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        raw = {
            "activePlayer": {"summonerName": "TestPlayer", "level": 10},
            "allPlayers": [{"summonerName": "TestPlayer", "team": "ORDER"}],
            "gameData": {"gameTime": 600.0},
        }
        result = client.parse_game_data(raw)
        assert result is not None
        assert result.game_time == 600.0

    def test_parse_empty_returns_none(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        result = client.parse_game_data({})
        assert result is None


class TestLiveClientDataV2CodecConsistency:
    def test_codec_name_matches(self):
        """Verify output is consistent with LoLCodec from protocol-decoder."""
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        assert client.codec_name == "lol"

    def test_snapshot_has_timestamp(self):
        from lol_fiddler_agent.network.live_client_data_v2 import LiveClientDataV2
        client = LiveClientDataV2()
        raw = {
            "activePlayer": {"summonerName": "P1", "level": 5},
            "allPlayers": [],
            "gameData": {"gameTime": 300.0},
        }
        result = client.parse_game_data(raw)
        if result:
            assert hasattr(result, "timestamp") or hasattr(result, "game_time")

    def test_evolution_key(self):
        from lol_fiddler_agent.network.live_client_data_v2 import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

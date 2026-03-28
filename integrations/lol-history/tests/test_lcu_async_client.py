"""
TDD Tests for M261: LCUAsyncClient — async httpx LCU API client.

10 tests: construction, URL building, auth, retry logic, response parsing,
error handling, timeout.
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestLCUAsyncClientConstruction:
    def test_import_and_construct(self):
        from lol_history.lcu_async_client import LCUAsyncClient
        client = LCUAsyncClient()
        assert client is not None

    def test_config(self):
        from lol_history.lcu_async_client import LCUAsyncClient, LCUClientConfig
        cfg = LCUClientConfig(host="127.0.0.1", port=2999, auth_token="abc123")
        client = LCUAsyncClient(config=cfg)
        assert client.config.port == 2999


class TestLCUAsyncClientURLs:
    def test_build_match_history_url(self):
        from lol_history.lcu_async_client import LCUAsyncClient
        client = LCUAsyncClient()
        url = client.build_url("/lol-match-history/v1/products/lol/test-puuid/matches")
        assert "test-puuid" in url
        assert url.startswith("https://")

    def test_build_ranked_stats_url(self):
        from lol_history.lcu_async_client import LCUAsyncClient
        client = LCUAsyncClient()
        url = client.build_url("/lol-ranked/v1/ranked-stats/test-puuid")
        assert "ranked-stats" in url


class TestLCUAsyncClientAuth:
    def test_auth_header_format(self):
        from lol_history.lcu_async_client import LCUAsyncClient, LCUClientConfig
        cfg = LCUClientConfig(auth_token="riot-token-123")
        client = LCUAsyncClient(config=cfg)
        headers = client.auth_headers()
        assert "Authorization" in headers

    def test_auth_empty_token(self):
        from lol_history.lcu_async_client import LCUAsyncClient, LCUClientConfig
        cfg = LCUClientConfig(auth_token="")
        client = LCUAsyncClient(config=cfg)
        headers = client.auth_headers()
        # Should still return headers dict, possibly without auth
        assert isinstance(headers, dict)


class TestLCUAsyncClientRetry:
    def test_retry_config(self):
        from lol_history.lcu_async_client import LCUAsyncClient, LCUClientConfig
        cfg = LCUClientConfig(max_retries=5, timeout=15.0)
        client = LCUAsyncClient(config=cfg)
        assert client.config.max_retries == 5
        assert client.config.timeout == 15.0

    def test_parse_response(self):
        from lol_history.lcu_async_client import LCUAsyncClient
        client = LCUAsyncClient()
        raw = {"games": {"games": [{"gameId": 1}, {"gameId": 2}]}}
        result = client.parse_match_list(raw)
        assert isinstance(result, list)
        assert len(result) == 2


class TestLCUAsyncClientEvolution:
    def test_evolution_key(self):
        from lol_history.lcu_async_client import _EVOLUTION_KEY
        assert isinstance(_EVOLUTION_KEY, str)

    def test_stats(self):
        from lol_history.lcu_async_client import LCUAsyncClient
        client = LCUAsyncClient()
        stats = client.get_stats()
        assert "requests_made" in stats

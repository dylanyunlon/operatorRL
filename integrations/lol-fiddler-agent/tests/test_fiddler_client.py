"""
TDD Tests for Fiddler MCP Client

These tests are designed to fail initially (~50% failure rate) to drive
implementation. Following TDD principles:
1. Write tests first
2. Run tests, confirm they fail
3. Implement code to pass tests
4. Refactor

Tests cover:
- FiddlerConfig validation
- FiddlerMCPClient connection/disconnection
- Session management
- Error handling
- LoL-specific filtering
"""

import asyncio
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Import module under test
import sys
sys.path.insert(0, "/home/claude/lol-fiddler-agent/src")

from lol_fiddler_agent.network.fiddler_client import (
    FiddlerConfig,
    FiddlerMCPClient,
    FiddlerMCPError,
    FiddlerConnectionError,
    FiddlerAuthError,
    HTTPSession,
    FilterCriteria,
    SessionStatus,
    CaptureMode,
)


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: FiddlerConfig validation and properties
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerConfig:
    """Test FiddlerConfig dataclass."""
    
    def test_default_config_values(self):
        """Config should have sensible defaults."""
        config = FiddlerConfig()
        
        assert config.host == "localhost"
        assert config.port == 8868
        assert config.api_key == ""
        assert config.timeout == 30.0
        assert config.retry_attempts == 3
        assert config.retry_delay == 1.0
    
    def test_base_url_construction(self):
        """base_url should be correctly constructed from host and port."""
        config = FiddlerConfig(host="192.168.1.100", port=9999)
        
        assert config.base_url == "http://192.168.1.100:9999/mcp"
    
    def test_headers_include_api_key(self):
        """Headers should include Authorization with API key."""
        config = FiddlerConfig(api_key="test-key-123")
        headers = config.headers
        
        assert "Authorization" in headers
        assert headers["Authorization"] == "ApiKey test-key-123"
        assert headers["Content-Type"] == "application/json"
    
    def test_custom_config_values(self):
        """Config should accept custom values."""
        config = FiddlerConfig(
            host="custom-host",
            port=1234,
            api_key="my-api-key",
            timeout=60.0,
            retry_attempts=5,
            retry_delay=2.0,
        )
        
        assert config.host == "custom-host"
        assert config.port == 1234
        assert config.api_key == "my-api-key"
        assert config.timeout == 60.0
        assert config.retry_attempts == 5
        assert config.retry_delay == 2.0


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: HTTPSession model validation
# ═══════════════════════════════════════════════════════════════════════════

class TestHTTPSession:
    """Test HTTPSession Pydantic model."""
    
    def test_basic_session_creation(self):
        """Should create session with required fields."""
        session = HTTPSession(
            session_id=1,
            url="https://example.com/api",
        )
        
        assert session.session_id == 1
        assert session.url == "https://example.com/api"
        assert session.method == "GET"  # default
        assert session.status_code == 0  # default
    
    def test_method_normalization(self):
        """HTTP methods should be uppercased."""
        session = HTTPSession(
            session_id=1,
            url="https://example.com",
            method="post",
        )
        
        assert session.method == "POST"
    
    def test_status_categorization(self):
        """Status codes should be categorized correctly."""
        cases = [
            (200, SessionStatus.SUCCESS),
            (201, SessionStatus.SUCCESS),
            (301, SessionStatus.REDIRECT),
            (404, SessionStatus.CLIENT_ERROR),
            (500, SessionStatus.SERVER_ERROR),
            (0, SessionStatus.UNKNOWN),
        ]
        
        for code, expected_status in cases:
            session = HTTPSession(session_id=1, url="http://test", status_code=code)
            assert session.status == expected_status, f"Failed for status {code}"
    
    def test_is_lol_api_detection(self):
        """Should detect LoL API URLs."""
        lol_urls = [
            "https://127.0.0.1:2999/liveclientdata/allgamedata",
            "https://na1.api.riotgames.com/lol/match/v5/matches",
            "https://ddragon.leagueoflegends.com/cdn/13.1.1/data/en_US/champion.json",
            "https://lol.pvp.net/api/v3/summoner",
        ]
        
        for url in lol_urls:
            session = HTTPSession(session_id=1, url=url)
            assert session.is_lol_api(), f"Failed to detect LoL API: {url}"
    
    def test_is_not_lol_api(self):
        """Should not detect non-LoL URLs as LoL API."""
        non_lol_urls = [
            "https://google.com",
            "https://api.github.com",
            "https://discord.com/api",
        ]
        
        for url in non_lol_urls:
            session = HTTPSession(session_id=1, url=url)
            assert not session.is_lol_api(), f"Incorrectly detected as LoL: {url}"
    
    def test_is_live_client(self):
        """Should detect Live Client API specifically."""
        live_client_urls = [
            "https://127.0.0.1:2999/liveclientdata/allgamedata",
            "https://127.0.0.1:2999/liveclientdata/playerlist",
        ]
        
        for url in live_client_urls:
            session = HTTPSession(session_id=1, url=url)
            assert session.is_live_client(), f"Failed to detect Live Client: {url}"
    
    def test_parse_json_body_success(self):
        """Should parse valid JSON body."""
        session = HTTPSession(
            session_id=1,
            url="http://test",
            response_body='{"key": "value", "number": 42}',
        )
        
        result = session.parse_json_body()
        assert result == {"key": "value", "number": 42}
    
    def test_parse_json_body_invalid(self):
        """Should return None for invalid JSON."""
        session = HTTPSession(
            session_id=1,
            url="http://test",
            response_body="not valid json",
        )
        
        assert session.parse_json_body() is None
    
    def test_parse_json_body_empty(self):
        """Should return None for empty body."""
        session = HTTPSession(session_id=1, url="http://test")
        assert session.parse_json_body() is None


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: FilterCriteria conversion
# ═══════════════════════════════════════════════════════════════════════════

class TestFilterCriteria:
    """Test FilterCriteria dataclass."""
    
    def test_empty_filter_conversion(self):
        """Empty filter should produce empty dict."""
        criteria = FilterCriteria()
        result = criteria.to_fiddler_filter()
        
        assert result == {}
    
    def test_url_pattern_filter(self):
        """URL pattern should be included in filter."""
        criteria = FilterCriteria(url_pattern="*riot*")
        result = criteria.to_fiddler_filter()
        
        assert result == {"urlPattern": "*riot*"}
    
    def test_methods_filter(self):
        """Methods should be included in filter."""
        criteria = FilterCriteria(methods=["GET", "POST"])
        result = criteria.to_fiddler_filter()
        
        assert result == {"methods": ["GET", "POST"]}
    
    def test_status_codes_filter(self):
        """Status codes should be included in filter."""
        criteria = FilterCriteria(status_codes=[200, 201, 404])
        result = criteria.to_fiddler_filter()
        
        assert result == {"statusCodes": [200, 201, 404]}
    
    def test_combined_filters(self):
        """Multiple filters should be combined."""
        criteria = FilterCriteria(
            url_pattern="*api*",
            methods=["POST"],
            status_codes=[500],
        )
        result = criteria.to_fiddler_filter()
        
        assert result == {
            "urlPattern": "*api*",
            "methods": ["POST"],
            "statusCodes": [500],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: FiddlerMCPClient initialization
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerMCPClientInit:
    """Test FiddlerMCPClient initialization."""
    
    def test_client_creation(self):
        """Should create client with config."""
        config = FiddlerConfig(api_key="test")
        client = FiddlerMCPClient(config)
        
        assert client.config == config
        assert client._connected is False
        assert client._capture_active is False
        assert client._client is None
    
    def test_client_context_manager_types(self):
        """Should support async context manager protocol."""
        config = FiddlerConfig()
        client = FiddlerMCPClient(config)
        
        assert hasattr(client, "__aenter__")
        assert hasattr(client, "__aexit__")


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: FiddlerMCPClient connection handling
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerMCPClientConnection:
    """Test FiddlerMCPClient connection methods."""
    
    @pytest.mark.asyncio
    async def test_connect_creates_http_client(self):
        """Connect should create httpx client."""
        config = FiddlerConfig(api_key="test")
        client = FiddlerMCPClient(config)
        
        # Mock get_status to succeed
        with patch.object(client, "get_status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"status": "ok"}
            
            await client.connect()
            
            assert client._client is not None
            assert client._connected is True
            mock_status.assert_called_once()
            
            # Cleanup
            await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_disconnect_closes_client(self):
        """Disconnect should close httpx client and reset state."""
        config = FiddlerConfig(api_key="test")
        client = FiddlerMCPClient(config)
        
        with patch.object(client, "get_status", new_callable=AsyncMock) as mock_status:
            mock_status.return_value = {"status": "ok"}
            await client.connect()
        
        await client.disconnect()
        
        assert client._client is None
        assert client._connected is False
        assert client._capture_active is False
    
    @pytest.mark.asyncio
    async def test_connect_failure_raises_error(self):
        """Connect should raise FiddlerConnectionError on failure."""
        config = FiddlerConfig(api_key="test")
        client = FiddlerMCPClient(config)
        
        with patch.object(client, "get_status", new_callable=AsyncMock) as mock_status:
            mock_status.side_effect = Exception("Connection refused")
            
            with pytest.raises(FiddlerConnectionError):
                await client.connect()
            
            assert client._connected is False


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: FiddlerMCPClient tool calls
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerMCPClientToolCalls:
    """Test FiddlerMCPClient MCP tool calls."""
    
    @pytest.mark.asyncio
    async def test_call_tool_without_connection(self):
        """Calling tool without connection should raise error."""
        config = FiddlerConfig()
        client = FiddlerMCPClient(config)
        
        with pytest.raises(FiddlerConnectionError):
            await client._call_tool("get_status")
    
    @pytest.mark.asyncio
    async def test_call_tool_auth_error(self):
        """401 response should raise FiddlerAuthError."""
        config = FiddlerConfig(api_key="invalid")
        client = FiddlerMCPClient(config)
        
        # Create a mock response that raises 401
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = Exception("401 Unauthorized")
        
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        client._client = mock_client
        
        # Skip initial connect, just test tool call
        with pytest.raises(FiddlerAuthError):
            await client._call_tool("get_status")


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Session management methods
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerMCPClientSessions:
    """Test session management methods."""
    
    @pytest.fixture
    def connected_client(self):
        """Create a mock-connected client."""
        config = FiddlerConfig(api_key="test")
        client = FiddlerMCPClient(config)
        client._client = AsyncMock()
        client._connected = True
        return client
    
    @pytest.mark.asyncio
    async def test_get_sessions_count(self, connected_client):
        """get_sessions_count should return count from API."""
        # Mock response
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {"count": 42}}
        mock_response.raise_for_status = MagicMock()
        connected_client._client.post.return_value = mock_response
        
        count = await connected_client.get_sessions_count()
        
        assert count == 42
    
    @pytest.mark.asyncio
    async def test_get_sessions_parses_response(self, connected_client):
        """get_sessions should parse session list from API."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "sessions": [
                    {"id": 1, "url": "http://test1", "method": "GET", "statusCode": 200},
                    {"id": 2, "url": "http://test2", "method": "POST", "statusCode": 201},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._client.post.return_value = mock_response
        
        sessions = await connected_client.get_sessions()
        
        assert len(sessions) == 2
        assert sessions[0].session_id == 1
        assert sessions[0].url == "http://test1"
        assert sessions[1].session_id == 2
    
    @pytest.mark.asyncio
    async def test_clear_sessions_clears_cache(self, connected_client):
        """clear_sessions should clear local cache."""
        # Add something to cache
        connected_client._session_cache[1] = HTTPSession(session_id=1, url="test")
        
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {}}
        mock_response.raise_for_status = MagicMock()
        connected_client._client.post.return_value = mock_response
        
        await connected_client.clear_sessions()
        
        assert len(connected_client._session_cache) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: LoL-specific methods
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerMCPClientLoL:
    """Test LoL-specific helper methods."""
    
    @pytest.fixture
    def connected_client(self):
        """Create a mock-connected client."""
        config = FiddlerConfig(api_key="test")
        client = FiddlerMCPClient(config)
        client._client = AsyncMock()
        client._connected = True
        return client
    
    @pytest.mark.asyncio
    async def test_get_lol_sessions_filters_correctly(self, connected_client):
        """get_lol_sessions should only return LoL API sessions."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "result": {
                "sessions": [
                    {"id": 1, "url": "https://127.0.0.1:2999/liveclientdata/allgamedata"},
                    {"id": 2, "url": "https://google.com/search"},
                    {"id": 3, "url": "https://na1.api.riotgames.com/lol/match"},
                ]
            }
        }
        mock_response.raise_for_status = MagicMock()
        connected_client._client.post.return_value = mock_response
        
        sessions = await connected_client.get_lol_sessions()
        
        # Should only return LoL sessions (id 1 and 3)
        assert len(sessions) == 2
        assert all(s.is_lol_api() for s in sessions)


# ═══════════════════════════════════════════════════════════════════════════
# Test 9: Reverse proxy configuration
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerMCPClientReverseProxy:
    """Test reverse proxy methods."""
    
    @pytest.fixture
    def connected_client(self):
        """Create a mock-connected client."""
        config = FiddlerConfig(api_key="test")
        client = FiddlerMCPClient(config)
        client._client = AsyncMock()
        client._connected = True
        return client
    
    @pytest.mark.asyncio
    async def test_add_reverse_proxy(self, connected_client):
        """add_reverse_proxy should call correct MCP tool."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": {}}
        mock_response.raise_for_status = MagicMock()
        connected_client._client.post.return_value = mock_response
        
        await connected_client.add_reverse_proxy(
            local_port=12999,
            remote_host="127.0.0.1",
            remote_port=2999,
        )
        
        # Verify the call was made
        connected_client._client.post.assert_called()
        call_args = connected_client._client.post.call_args
        payload = call_args[1]["json"]
        
        assert payload["params"]["name"] == "add_reverse_proxy_port"
        assert payload["params"]["arguments"]["localPort"] == 12999
        assert payload["params"]["arguments"]["remoteHost"] == "127.0.0.1"
        assert payload["params"]["arguments"]["remotePort"] == 2999


# ═══════════════════════════════════════════════════════════════════════════
# Test 10: Error handling and retries
# ═══════════════════════════════════════════════════════════════════════════

class TestFiddlerMCPClientErrorHandling:
    """Test error handling and retry logic."""
    
    @pytest.mark.asyncio
    async def test_retry_on_transient_error(self):
        """Should retry on transient errors."""
        config = FiddlerConfig(api_key="test", retry_attempts=3, retry_delay=0.01)
        client = FiddlerMCPClient(config)
        
        # Mock client that fails twice then succeeds
        mock_http_client = AsyncMock()
        call_count = 0
        
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Transient error")
            
            response = MagicMock()
            response.json.return_value = {"result": {"status": "ok"}}
            response.raise_for_status = MagicMock()
            return response
        
        mock_http_client.post = mock_post
        client._client = mock_http_client
        
        result = await client._call_tool("get_status")
        
        assert call_count == 3
        assert result == {"status": "ok"}
    
    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self):
        """Should raise error after max retries."""
        config = FiddlerConfig(api_key="test", retry_attempts=2, retry_delay=0.01)
        client = FiddlerMCPClient(config)
        
        mock_http_client = AsyncMock()
        mock_http_client.post.side_effect = Exception("Persistent error")
        client._client = mock_http_client
        
        with pytest.raises(FiddlerMCPError) as exc_info:
            await client._call_tool("get_status")
        
        assert "failed after 2 attempts" in str(exc_info.value)

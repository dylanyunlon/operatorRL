"""
Fiddler MCP Client - Network Traffic Capture for League of Legends

This module provides a client for interacting with Fiddler Everywhere's MCP server
to capture and analyze HTTP/HTTPS traffic from League of Legends client.

Benefits over visual capture:
1. No hallucination - raw HTTP data is exact
2. Lower latency - no image processing overhead
3. Structured data - JSON/Protocol buffers directly
4. Fits reverse engineering workflow

Reference: https://www.telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server/fiddler-mcp-server
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class FiddlerMCPError(Exception):
    """Base exception for Fiddler MCP operations."""
    pass


class FiddlerConnectionError(FiddlerMCPError):
    """Connection to Fiddler MCP server failed."""
    pass


class FiddlerAuthError(FiddlerMCPError):
    """Authentication with Fiddler MCP server failed."""
    pass


class CaptureMode(str, Enum):
    """Supported traffic capture modes."""
    BROWSER = "browser"
    TERMINAL = "terminal"
    REVERSE_PROXY = "reverse_proxy"


class SessionStatus(str, Enum):
    """HTTP session status categories."""
    SUCCESS = "success"       # 2xx
    REDIRECT = "redirect"     # 3xx
    CLIENT_ERROR = "client"   # 4xx
    SERVER_ERROR = "server"   # 5xx
    UNKNOWN = "unknown"


@dataclass
class FiddlerConfig:
    """Configuration for Fiddler MCP client.
    
    Attributes:
        host: MCP server host (default: localhost)
        port: MCP server port (default: 8868)
        api_key: API key for authentication
        timeout: Request timeout in seconds
        retry_attempts: Number of retry attempts on failure
        retry_delay: Delay between retries in seconds
    """
    host: str = "localhost"
    port: int = 8868
    api_key: str = ""
    timeout: float = 30.0
    retry_attempts: int = 3
    retry_delay: float = 1.0
    
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"
    
    @property
    def headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"ApiKey {self.api_key}",
        }


class HTTPSession(BaseModel):
    """Represents a captured HTTP session from Fiddler.
    
    Based on Fiddler MCP's get_session_details response structure.
    """
    session_id: int = Field(..., description="Session ID from Fiddler")
    url: str = Field(..., description="Request URL")
    method: str = Field(default="GET", description="HTTP method")
    status_code: int = Field(default=0, description="Response status code")
    
    request_headers: dict[str, str] = Field(default_factory=dict)
    response_headers: dict[str, str] = Field(default_factory=dict)
    request_body: Optional[str] = Field(default=None)
    response_body: Optional[str] = Field(default=None)
    
    start_time: Optional[datetime] = Field(default=None)
    duration_ms: float = Field(default=0.0)
    
    protocol: str = Field(default="HTTP/1.1")
    tls_version: Optional[str] = Field(default=None)
    client_ip: Optional[str] = Field(default=None)
    remote_ip: Optional[str] = Field(default=None)
    
    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        valid_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
        upper_v = v.upper()
        if upper_v not in valid_methods:
            return v  # Allow custom methods
        return upper_v
    
    @property
    def status(self) -> SessionStatus:
        """Categorize status code."""
        if 200 <= self.status_code < 300:
            return SessionStatus.SUCCESS
        elif 300 <= self.status_code < 400:
            return SessionStatus.REDIRECT
        elif 400 <= self.status_code < 500:
            return SessionStatus.CLIENT_ERROR
        elif 500 <= self.status_code < 600:
            return SessionStatus.SERVER_ERROR
        return SessionStatus.UNKNOWN
    
    def is_lol_api(self) -> bool:
        """Check if this session is from League of Legends API."""
        lol_domains = [
            "127.0.0.1:2999",      # Live Client API
            "riot.com",
            "riotgames.com",
            "leagueoflegends.com",
            "pvp.net",
        ]
        return any(domain in self.url.lower() for domain in lol_domains)
    
    def is_live_client(self) -> bool:
        """Check if this is from LoL Live Client Data API."""
        return "127.0.0.1:2999" in self.url or "liveclientdata" in self.url.lower()
    
    def parse_json_body(self) -> Optional[dict[str, Any]]:
        """Parse response body as JSON if possible."""
        if not self.response_body:
            return None
        try:
            return json.loads(self.response_body)
        except json.JSONDecodeError:
            return None


@dataclass
class FilterCriteria:
    """Criteria for filtering captured sessions."""
    url_pattern: Optional[str] = None
    methods: Optional[list[str]] = None
    status_codes: Optional[list[int]] = None
    min_duration_ms: Optional[float] = None
    max_duration_ms: Optional[float] = None
    contains_body: Optional[str] = None
    
    def to_fiddler_filter(self) -> dict[str, Any]:
        """Convert to Fiddler MCP filter format."""
        filters: dict[str, Any] = {}
        if self.url_pattern:
            filters["urlPattern"] = self.url_pattern
        if self.methods:
            filters["methods"] = self.methods
        if self.status_codes:
            filters["statusCodes"] = self.status_codes
        return filters


class FiddlerMCPClient:
    """Client for Fiddler Everywhere MCP Server.
    
    Provides async interface for:
    - Checking Fiddler status and authentication
    - Starting/stopping traffic capture
    - Retrieving captured HTTP sessions
    - Applying filters and rules
    - Managing reverse proxy for LoL client
    
    Example:
        >>> config = FiddlerConfig(api_key="your-api-key")
        >>> async with FiddlerMCPClient(config) as client:
        ...     status = await client.get_status()
        ...     sessions = await client.get_sessions()
        ...     for s in sessions:
        ...         if s.is_live_client():
        ...             print(f"LoL data: {s.parse_json_body()}")
    """
    
    def __init__(self, config: FiddlerConfig) -> None:
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._capture_active = False
        self._session_cache: dict[int, HTTPSession] = {}
        
    async def __aenter__(self) -> "FiddlerMCPClient":
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()
    
    async def connect(self) -> None:
        """Establish connection to Fiddler MCP server."""
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            headers=self.config.headers,
            timeout=self.config.timeout,
        )
        
        # Verify connection with status check
        try:
            status = await self.get_status()
            self._connected = True
            logger.info(f"Connected to Fiddler MCP: {status}")
        except Exception as e:
            await self.disconnect()
            raise FiddlerConnectionError(f"Failed to connect: {e}") from e
    
    async def disconnect(self) -> None:
        """Close connection to Fiddler MCP server."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        self._capture_active = False
    
    async def _call_tool(self, tool_name: str, params: dict[str, Any] = {}) -> dict[str, Any]:
        """Execute a Fiddler MCP tool.
        
        Args:
            tool_name: Name of the MCP tool to call
            params: Parameters for the tool
            
        Returns:
            Tool response as dictionary
            
        Raises:
            FiddlerMCPError: If tool execution fails
        """
        if not self._client:
            raise FiddlerConnectionError("Not connected to Fiddler MCP")
        
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": params,
            },
            "id": int(time.time() * 1000),
        }
        
        last_error: Optional[Exception] = None
        for attempt in range(self.config.retry_attempts):
            try:
                response = await self._client.post("", json=payload)
                
                # Check for auth error first
                if hasattr(response, 'status_code') and response.status_code == 401:
                    raise FiddlerAuthError("Invalid API key")
                
                response.raise_for_status()
                
                data = response.json()
                if "error" in data:
                    raise FiddlerMCPError(f"Tool error: {data['error']}")
                
                return data.get("result", {})
                
            except FiddlerAuthError:
                raise  # Don't retry auth errors
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    raise FiddlerAuthError("Invalid API key") from e
                last_error = e
                
            except httpx.RequestError as e:
                last_error = e
            
            except Exception as e:
                # Catch any other exception for retry
                last_error = e
            
            if attempt < self.config.retry_attempts - 1:
                await asyncio.sleep(self.config.retry_delay)
        
        raise FiddlerMCPError(f"Tool call failed after {self.config.retry_attempts} attempts: {last_error}")
    
    # ── Status & Authentication ────────────────────────────────────────────
    
    async def get_status(self) -> dict[str, Any]:
        """Get current Fiddler application status.
        
        Returns:
            Status including login state, certificate trust, proxy config, session counts
        """
        return await self._call_tool("get_status")
    
    async def is_user_logged_in(self) -> bool:
        """Check if user is authenticated with Fiddler."""
        result = await self._call_tool("is_user_logged_in")
        return result.get("loggedIn", False)
    
    async def initiate_login(self) -> None:
        """Open Fiddler login page for authentication."""
        await self._call_tool("initiate_login")
    
    async def trust_root_certificate(self) -> None:
        """Open dialog to trust Fiddler root CA certificate."""
        await self._call_tool("open_trust_root_certificate_dialog")
    
    # ── Traffic Capture ────────────────────────────────────────────────────
    
    async def start_capture_browser(self) -> None:
        """Start capturing traffic via preconfigured browser instance."""
        await self._call_tool("start_capture_with_browser")
        self._capture_active = True
    
    async def start_capture_terminal(self) -> None:
        """Start capturing traffic via terminal with proxy env vars."""
        await self._call_tool("start_capture_with_terminal")
        self._capture_active = True
    
    # ── Session Management ─────────────────────────────────────────────────
    
    async def get_sessions_count(self) -> int:
        """Get total number of captured sessions."""
        result = await self._call_tool("get_sessions_count")
        return result.get("count", 0)
    
    async def get_sessions(
        self,
        filter_criteria: Optional[FilterCriteria] = None,
        limit: int = 100,
    ) -> list[HTTPSession]:
        """Get list of captured HTTP sessions.
        
        Args:
            filter_criteria: Optional filter to narrow results
            limit: Maximum number of sessions to return
            
        Returns:
            List of HTTPSession objects
        """
        params: dict[str, Any] = {"limit": limit}
        if filter_criteria:
            params.update(filter_criteria.to_fiddler_filter())
        
        result = await self._call_tool("get_sessions", params)
        sessions = []
        
        for item in result.get("sessions", []):
            try:
                session = HTTPSession(
                    session_id=item.get("id", 0),
                    url=item.get("url", ""),
                    method=item.get("method", "GET"),
                    status_code=item.get("statusCode", 0),
                    start_time=datetime.fromisoformat(item["startTime"]) if item.get("startTime") else None,
                    duration_ms=item.get("duration", 0),
                )
                sessions.append(session)
            except Exception as e:
                logger.warning(f"Failed to parse session: {e}")
        
        return sessions
    
    async def get_session_details(self, session_id: int) -> HTTPSession:
        """Get full details for a specific session.
        
        Args:
            session_id: ID from the sessions list
            
        Returns:
            HTTPSession with complete request/response details
        """
        # Check cache first
        if session_id in self._session_cache:
            return self._session_cache[session_id]
        
        result = await self._call_tool("get_session_details", {"sessionId": session_id})
        
        session = HTTPSession(
            session_id=session_id,
            url=result.get("url", ""),
            method=result.get("method", "GET"),
            status_code=result.get("statusCode", 0),
            request_headers=result.get("requestHeaders", {}),
            response_headers=result.get("responseHeaders", {}),
            request_body=result.get("requestBody"),
            response_body=result.get("responseBody"),
            start_time=datetime.fromisoformat(result["startTime"]) if result.get("startTime") else None,
            duration_ms=result.get("duration", 0),
            protocol=result.get("protocol", "HTTP/1.1"),
            tls_version=result.get("tlsVersion"),
            client_ip=result.get("clientIP"),
            remote_ip=result.get("remoteIP"),
        )
        
        # Cache for future use
        self._session_cache[session_id] = session
        return session
    
    async def clear_sessions(self) -> None:
        """Clear all captured sessions."""
        await self._call_tool("clear_sessions")
        self._session_cache.clear()
    
    async def apply_filters(self, criteria: FilterCriteria) -> None:
        """Apply filter criteria to active session list."""
        await self._call_tool("apply_filters", criteria.to_fiddler_filter())
    
    # ── Rules Management ───────────────────────────────────────────────────
    
    async def create_rule(
        self,
        name: str,
        match_url: str,
        action: str,
        action_params: dict[str, Any] = {},
    ) -> str:
        """Create a traffic manipulation rule.
        
        Args:
            name: Rule name
            match_url: URL pattern to match
            action: Action type (e.g., 'modify_response', 'redirect', 'delay')
            action_params: Parameters for the action
            
        Returns:
            Rule ID
        """
        result = await self._call_tool("create_rule", {
            "name": name,
            "matchUrl": match_url,
            "action": action,
            "actionParams": action_params,
        })
        return result.get("ruleId", "")
    
    async def clear_rules(self) -> None:
        """Remove all MCP-created rules."""
        await self._call_tool("clear_rules")
    
    # ── Reverse Proxy (for LoL Client) ─────────────────────────────────────
    
    async def add_reverse_proxy(
        self,
        local_port: int,
        remote_host: str,
        remote_port: int,
    ) -> None:
        """Add reverse proxy port mapping.
        
        Useful for intercepting LoL client traffic that doesn't honor system proxy.
        
        Args:
            local_port: Local port to listen on
            remote_host: Remote host to forward to
            remote_port: Remote port to forward to
        """
        await self._call_tool("add_reverse_proxy_port", {
            "localPort": local_port,
            "remoteHost": remote_host,
            "remotePort": remote_port,
        })
    
    async def remove_reverse_proxy(self, local_port: int) -> None:
        """Remove a reverse proxy port mapping."""
        await self._call_tool("remove_reverse_proxy_port", {"localPort": local_port})
    
    async def enable_reverse_proxy(self) -> None:
        """Enable reverse proxy feature."""
        await self._call_tool("enable_reverse_proxy")
    
    async def disable_reverse_proxy(self) -> None:
        """Disable reverse proxy feature."""
        await self._call_tool("disable_reverse_proxy")
    
    # ── LoL-Specific Helpers ───────────────────────────────────────────────
    
    async def get_lol_sessions(self, include_details: bool = False) -> list[HTTPSession]:
        """Get sessions from League of Legends APIs only.
        
        Args:
            include_details: If True, fetch full request/response bodies
            
        Returns:
            List of LoL-related HTTP sessions
        """
        # Filter for LoL domains
        criteria = FilterCriteria(
            url_pattern="*riot*|*leagueoflegends*|*127.0.0.1:2999*|*pvp.net*"
        )
        
        sessions = await self.get_sessions(criteria)
        lol_sessions = [s for s in sessions if s.is_lol_api()]
        
        if include_details:
            detailed = []
            for s in lol_sessions:
                try:
                    detail = await self.get_session_details(s.session_id)
                    detailed.append(detail)
                except Exception as e:
                    logger.warning(f"Failed to get details for session {s.session_id}: {e}")
                    detailed.append(s)
            return detailed
        
        return lol_sessions
    
    async def setup_lol_capture(self) -> None:
        """Configure optimal settings for capturing LoL traffic.
        
        Sets up:
        1. Reverse proxy for Live Client API (port 2999)
        2. Filter for LoL-related domains
        3. Rules for tracking game state
        """
        # Enable reverse proxy for Live Client API
        await self.enable_reverse_proxy()
        
        # Add mapping for Live Client API
        # Note: LoL Live Client API runs on localhost:2999
        # We create a proxy passthrough for monitoring
        try:
            await self.add_reverse_proxy(
                local_port=12999,  # Our proxy port
                remote_host="127.0.0.1",
                remote_port=2999,  # LoL Live Client API
            )
        except FiddlerMCPError as e:
            logger.warning(f"Could not setup reverse proxy (may already exist): {e}")
        
        # Apply LoL-specific filters
        await self.apply_filters(FilterCriteria(
            url_pattern="*riot*|*leagueoflegends*|*127.0.0.1:2999*|*pvp.net*"
        ))
        
        logger.info("LoL capture setup complete")


# ── Convenience Functions ──────────────────────────────────────────────────

async def create_lol_fiddler_client(
    api_key: str,
    host: str = "localhost",
    port: int = 8868,
) -> FiddlerMCPClient:
    """Create and connect a Fiddler MCP client configured for LoL.
    
    Args:
        api_key: Fiddler API key
        host: MCP server host
        port: MCP server port
        
    Returns:
        Connected FiddlerMCPClient
    """
    config = FiddlerConfig(
        host=host,
        port=port,
        api_key=api_key,
    )
    
    client = FiddlerMCPClient(config)
    await client.connect()
    await client.setup_lol_capture()
    
    return client

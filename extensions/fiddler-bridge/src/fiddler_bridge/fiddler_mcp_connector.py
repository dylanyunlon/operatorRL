"""
Fiddler MCP Connector — MCP protocol adapter with tool registration.

Provides JSON-RPC based MCP protocol communication with Fiddler Everywhere,
tool registration, request/response building and parsing.

Reference: www.telerik.com/fiddler/fiddler-everywhere/documentation/mcp-server/
Location: extensions/fiddler-bridge/src/fiddler_bridge/fiddler_mcp_connector.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.fiddler_mcp_connector.v1"


class FiddlerMCPConnector:
    """MCP protocol connector for Fiddler Everywhere.

    Manages tool registration, JSON-RPC request building,
    response parsing, and connection lifecycle.
    """

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8866/mcp",
        timeout: float = 10.0,
    ) -> None:
        self.server_url = server_url
        self.timeout = timeout
        self.tools: dict[str, dict[str, Any]] = {}
        self._connected: bool = False
        self._request_id: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def register_tool(
        self, name: str, schema: dict[str, Any]
    ) -> None:
        """Register an MCP tool.

        Args:
            name: Tool name.
            schema: Tool schema dict with description and parameters.
        """
        self.tools[name] = schema

    def list_tools(self) -> list[dict[str, Any]]:
        """List all registered tools.

        Returns:
            List of {name, schema} dicts.
        """
        return [
            {"name": name, "schema": schema}
            for name, schema in self.tools.items()
        ]

    def build_mcp_request(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a JSON-RPC 2.0 MCP request.

        Args:
            method: MCP method/tool name.
            params: Method parameters.

        Returns:
            JSON-RPC request dict.
        """
        self._request_id += 1
        return {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
            "params": params,
        }

    def parse_mcp_response(
        self, raw: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Parse a JSON-RPC 2.0 MCP response.

        Args:
            raw: Raw response dict.

        Returns:
            Result dict or None on error.
        """
        if "error" in raw:
            logger.warning(
                "MCP error: code=%s message=%s",
                raw["error"].get("code"),
                raw["error"].get("message"),
            )
            return None
        return raw.get("result")

    def is_connected(self) -> bool:
        """Check if connector is connected.

        Returns:
            Connection status.
        """
        return self._connected

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "fiddler_mcp_connector",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

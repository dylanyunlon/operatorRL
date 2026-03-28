"""
MITM Handler ABC — WebSocket lifecycle management for game proxies.

Adapted from Akagi mitm/mitm_abc.py ClientWebSocketABC.
Decoupled from mitmproxy: works with any WebSocket proxy (fiddler-bridge, mitmproxy, etc.).

Location: integrations/mahjong/src/mahjong_agent/bridge/mitm_abc.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class MitmHandlerABC(ABC):
    """Abstract WebSocket MITM handler.

    Manages the lifecycle of proxied WebSocket connections:
    on_open → on_message (repeated) → on_close.

    Subclasses implement platform-specific routing and parsing logic.
    """

    def __init__(self) -> None:
        self._active_flows: dict[str, dict[str, Any]] = {}

    @abstractmethod
    async def on_open(self, flow_id: str, url: str) -> None:
        """Called when a WebSocket connection is opened.

        Args:
            flow_id: Unique identifier for this connection.
            url: Target WebSocket URL.
        """
        ...

    @abstractmethod
    async def on_message(
        self, flow_id: str, content: bytes, from_client: bool
    ) -> Optional[bytes]:
        """Called when a WebSocket message is received.

        Args:
            flow_id: Connection identifier.
            content: Raw message bytes.
            from_client: True if message was sent by the client.

        Returns:
            Modified content to inject, or None to pass through unchanged.
        """
        ...

    @abstractmethod
    async def on_close(self, flow_id: str) -> None:
        """Called when a WebSocket connection is closed.

        Args:
            flow_id: Connection identifier.
        """
        ...

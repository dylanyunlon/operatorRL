"""
Majsoul MITM Handler — concrete handler for 雀魂 WebSocket traffic.

Routes game WebSocket traffic through MajsoulBridge for parsing,
delegates to the MahjongAgent for decision-making, and optionally
injects responses back into the WebSocket stream.

Integrates with fiddler-bridge for traffic capture.

Location: integrations/mahjong/src/mahjong_agent/bridge/mitm_majsoul.py
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from mahjong_agent.bridge.mitm_abc import MitmHandlerABC
from mahjong_agent.bridge.majsoul_bridge import MajsoulBridge

logger = logging.getLogger(__name__)

# URL patterns that indicate Majsoul game WebSocket connections
_MAJSOUL_WS_PATTERNS = (
    "maj-soul",
    "majsoul",
    "mahjongsoul",
    "game.mj",
)


def _is_majsoul_url(url: str) -> bool:
    """Check if URL matches known Majsoul WebSocket patterns."""
    url_lower = url.lower()
    return any(pattern in url_lower for pattern in _MAJSOUL_WS_PATTERNS)


class MajsoulMitmHandler(MitmHandlerABC):
    """Majsoul-specific MITM handler.

    Filters Majsoul WebSocket connections, delegates parsing to MajsoulBridge,
    and tracks active flows.
    """

    def __init__(self) -> None:
        super().__init__()
        self.bridge = MajsoulBridge()
        self._agent: Any = None  # Optional MahjongAgent reference

    def set_agent(self, agent: Any) -> None:
        """Attach a MahjongAgent for decision delegation."""
        self._agent = agent

    async def on_open(self, flow_id: str, url: str) -> None:
        """Handle WebSocket connection open.

        Tracks the flow and flags whether it's a game connection.
        """
        is_game = _is_majsoul_url(url)
        self._active_flows[flow_id] = {
            "url": url,
            "is_game": is_game,
            "message_count": 0,
        }

        if is_game:
            self.bridge.reset()
            logger.info("Majsoul game WebSocket opened: flow=%s url=%s", flow_id, url)
        else:
            logger.debug("Non-game WebSocket opened: flow=%s url=%s", flow_id, url)

    async def on_message(
        self, flow_id: str, content: bytes, from_client: bool
    ) -> Optional[bytes]:
        """Handle WebSocket message.

        Parses server messages through MajsoulBridge and optionally
        generates action responses via the attached agent.
        """
        flow_info = self._active_flows.get(flow_id)
        if flow_info is None:
            logger.warning("Message for unknown flow: %s", flow_id)
            return None

        flow_info["message_count"] += 1

        if not flow_info.get("is_game", False):
            return None  # Pass through non-game traffic

        # Only parse server→client messages
        if from_client:
            return None

        # Parse through bridge
        events = self.bridge.parse(content)
        if events is None:
            return None

        # Delegate to agent if attached
        if self._agent is not None:
            for event in events:
                self._agent.on_message(event)

        return None  # Don't inject responses (read-only mode)

    async def on_close(self, flow_id: str) -> None:
        """Handle WebSocket connection close."""
        flow_info = self._active_flows.pop(flow_id, None)
        if flow_info is not None:
            logger.info(
                "WebSocket closed: flow=%s messages=%d is_game=%s",
                flow_id,
                flow_info.get("message_count", 0),
                flow_info.get("is_game", False),
            )

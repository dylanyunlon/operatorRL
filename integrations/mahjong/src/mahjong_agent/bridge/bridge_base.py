"""
Mahjong Bridge Base — Abstract base class for MITM game bridges.

Adapted from Akagi's BridgeBase with GameCodec-compatible interface.
All mahjong platform bridges (Majsoul, Tenhou, Riichi City) inherit from this.

Interface contract:
    - parse(content: bytes) → list[dict] | None  (raw bytes → mjai events)
    - build(command: dict) → bytes | None  (mjai action → raw bytes)
    - reset() → None  (clear game state for new game)
    - game_name → str  (platform identifier)

Location: integrations/mahjong/src/mahjong_agent/bridge/bridge_base.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class MahjongBridgeBase(ABC):
    """Abstract base class for mahjong MITM bridges.

    Subclasses must implement:
        - game_name (property): platform identifier
        - parse(content): raw bytes → mjai event list
        - build(command): mjai action → raw bytes
        - reset(): clear state for new game
    """

    def __init__(self) -> None:
        self.account_id: int = 0
        self.seat: int = 0

    @property
    @abstractmethod
    def game_name(self) -> str:
        """Unique platform identifier (e.g., 'majsoul', 'tenhou')."""
        ...

    @abstractmethod
    def parse(self, content: bytes) -> Optional[list[dict[str, Any]]]:
        """Parse raw bytes from game server into mjai-format events.

        Args:
            content: Raw bytes captured via MITM proxy.

        Returns:
            List of mjai-format event dicts, or None if unparseable.
        """
        ...

    @abstractmethod
    def build(self, command: dict[str, Any]) -> Optional[bytes]:
        """Build raw bytes from an mjai action to send to game server.

        Args:
            command: mjai-format action dict.

        Returns:
            Raw bytes to inject into the WebSocket, or None if unsupported.
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """Reset game state for a new game."""
        ...

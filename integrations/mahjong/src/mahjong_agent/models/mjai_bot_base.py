"""
Mjai Bot Base — standardized decision interface for mahjong bots.

Ported from Akagi mjai_bot/base/bot.py with:
- Abstract react() method
- player_id lifecycle tracking
- metadata property for introspection

All bot implementations (Mortal, custom models, rule-based) inherit from this.

Location: integrations/mahjong/src/mahjong_agent/models/mjai_bot_base.py
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MjaiBotBase(ABC):
    """Abstract base class for mjai-compatible mahjong bots.

    Subclasses must implement:
        - react(events_json: str) → str

    The react method receives a JSON string of mjai events and returns
    a JSON string representing the bot's action.

    Protocol reference: https://github.com/smly/mjai.app
    """

    def __init__(self) -> None:
        self.player_id: Optional[int] = None

    @abstractmethod
    def react(self, events_json: str) -> str:
        """Process mjai events and return an action.

        Args:
            events_json: JSON string containing a list of mjai events.

        Returns:
            JSON string of the action to take.
            Must always return valid JSON, e.g., '{"type":"none"}'.
        """
        ...

    @property
    def metadata(self) -> dict[str, Any]:
        """Introspection metadata for the bot.

        Returns:
            Dict with bot_type, player_id, and implementation-specific info.
        """
        return {
            "bot_type": "base",
            "player_id": self.player_id,
            "class": self.__class__.__name__,
        }

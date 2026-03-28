"""
Mahjong AI Agent Orchestrator — central decision hub.

Receives parsed messages from the MITM bridge, delegates to the
underlying mjai bot for decision-making, and returns actions.

Supports full game lifecycle: start_game → kyoku → decisions → end_game.

Location: integrations/mahjong/src/mahjong_agent/agent.py
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class AgentState(str, Enum):
    """Agent lifecycle states."""
    IDLE = "idle"
    IN_GAME = "in_game"
    THINKING = "thinking"


@dataclass
class AgentConfig:
    """Configuration for the mahjong agent."""
    player_name: str = "operatorRL"
    max_think_time_ms: int = 10000
    auto_play: bool = False
    log_decisions: bool = True
    # Evolution integration
    collect_training_data: bool = True
    maturity_level: int = 0


class MahjongAgent:
    """Central mahjong agent orchestrator.

    Receives mjai-format messages, maintains game state, delegates
    decision-making to the pluggable bot backend.

    Usage:
        agent = MahjongAgent()
        action = agent.on_message({"type": "start_game", "id": 0, "names": [...]})
        action = agent.on_message({"type": "tsumo", "actor": 0, "pai": "5m"})
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        self._state = AgentState.IDLE
        self._player_id: Optional[int] = None
        self._decision_history: list[dict[str, Any]] = []
        self._game_start_time: Optional[float] = None
        self._bot: Any = None  # Pluggable MjaiBotBase
        self._event_buffer: list[dict[str, Any]] = []

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def player_id(self) -> Optional[int]:
        return self._player_id

    @property
    def decision_history(self) -> list[dict[str, Any]]:
        return list(self._decision_history)

    def set_bot(self, bot: Any) -> None:
        """Plug in an MjaiBotBase implementation."""
        self._bot = bot

    def on_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Process an incoming mjai-format message and return an action.

        Args:
            msg: mjai-format event dict (e.g., {"type": "tsumo", "actor": 0, "pai": "5m"})

        Returns:
            Action dict (e.g., {"type": "dahai", "pai": "5m", "actor": 0, "tsumogiri": True})
            or {"type": "none"} if no action needed.
        """
        msg_type = msg.get("type", "")

        if not msg_type:
            return {"type": "none"}

        # Handle start_game
        if msg_type == "start_game":
            return self._handle_start_game(msg)

        # Handle end_game
        if msg_type == "end_game":
            return self._handle_end_game(msg)

        # All other messages during game
        if self._state != AgentState.IN_GAME:
            logger.warning("Received message '%s' while not in game", msg_type)
            return {"type": "none"}

        return self._handle_game_message(msg)

    def _handle_start_game(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Handle start_game event."""
        self._state = AgentState.IN_GAME
        self._player_id = msg.get("id", 0)
        self._game_start_time = time.time()
        self._event_buffer.clear()
        self._decision_history.clear()

        # Initialize bot if available
        if self._bot is not None:
            events_json = json.dumps([msg], separators=(",", ":"))
            self._bot.react(events_json)

        logger.info("Game started: player_id=%d", self._player_id)
        return {"type": "none"}

    def _handle_end_game(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Handle end_game event."""
        if self._bot is not None:
            events_json = json.dumps([msg], separators=(",", ":"))
            self._bot.react(events_json)

        elapsed = time.time() - self._game_start_time if self._game_start_time else 0
        logger.info(
            "Game ended: player_id=%d, decisions=%d, elapsed=%.1fs",
            self._player_id or -1,
            len(self._decision_history),
            elapsed,
        )
        self._state = AgentState.IDLE
        self._player_id = None
        self._game_start_time = None
        return {"type": "none"}

    def _handle_game_message(self, msg: dict[str, Any]) -> dict[str, Any]:
        """Handle in-game messages by delegating to the bot."""
        self._event_buffer.append(msg)

        # Record in decision history for training data collection
        decision_record = {
            "timestamp": time.time(),
            "event": msg,
            "action": None,
        }

        action: dict[str, Any] = {"type": "none"}

        if self._bot is not None:
            self._state = AgentState.THINKING
            try:
                events_json = json.dumps([msg], separators=(",", ":"))
                result_json = self._bot.react(events_json)
                action = json.loads(result_json)
            except Exception as e:
                logger.error("Bot decision error: %s", e)
                action = {"type": "none"}
            finally:
                self._state = AgentState.IN_GAME

        decision_record["action"] = action
        self._decision_history.append(decision_record)

        return action

    def reset(self) -> None:
        """Reset the agent to initial state."""
        self._state = AgentState.IDLE
        self._player_id = None
        self._decision_history.clear()
        self._event_buffer.clear()
        self._game_start_time = None
        if self._bot is not None:
            try:
                self._bot.player_id = None
            except AttributeError:
                pass

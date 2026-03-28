"""
Bot Commander — High-level strategic command dispatch.

Provides priority-based command queue and serialization,
adapted from dota2bot-OpenHyperAI's mode_*_generic.lua
command patterns (attack, retreat, ward, roshan, etc.).

Location: integrations/dota2/src/dota2_agent/bot_commander.py

Reference: dota2bot-OpenHyperAI mode_attack_generic.lua,
           mode_retreat_generic.lua, mode_roshan_generic.lua.
"""

from __future__ import annotations

import heapq
import json
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "dota2_agent.bot_commander.v1"


class BotCommander:
    """Priority-based strategic command manager for Dota 2.

    Mirrors dota2bot-OpenHyperAI's mode system where each mode
    (attack, retreat, ward, roshan, team_roam, defend_tower)
    competes by priority for bot attention.
    """

    def __init__(self) -> None:
        # Min-heap with (-priority, seq, command) for max-priority-first
        self._heap: list[tuple[int, int, dict]] = []
        self._seq: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def command_queue(self) -> list[dict]:
        """View current commands (sorted by priority, desc)."""
        return [cmd for _, _, cmd in sorted(self._heap, key=lambda x: x[0])]

    def issue_command(
        self,
        action: str,
        target: Optional[str] = None,
        priority: int = 1,
        **params: Any,
    ) -> None:
        """Issue a strategic command.

        Args:
            action: Command type (attack, retreat, ward, roshan, etc.).
            target: Optional target identifier.
            priority: Higher = more urgent.
            **params: Additional parameters.
        """
        cmd = self.build_command(action, target=target, params=params)
        cmd["priority"] = priority
        self._seq += 1
        heapq.heappush(self._heap, (-priority, self._seq, cmd))

    def get_next_command(self) -> Optional[dict[str, Any]]:
        """Pop the highest-priority command.

        Returns:
            Command dict or None if queue is empty.
        """
        if not self._heap:
            return None
        _, _, cmd = heapq.heappop(self._heap)
        return cmd

    def clear_commands(self) -> None:
        """Clear all queued commands."""
        self._heap.clear()
        self._seq = 0

    def build_command(
        self,
        action: str,
        target: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Build a command dict.

        Args:
            action: Command action type.
            target: Target identifier.
            params: Extra parameters.

        Returns:
            Command dict.
        """
        return {
            "action": action,
            "target": target,
            "params": params or {},
            "timestamp": time.time(),
        }

    def serialize_command(self, command: dict[str, Any]) -> str:
        """Serialize a command to JSON string.

        Args:
            command: Command dict.

        Returns:
            JSON string.
        """
        return json.dumps(command, default=str)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

"""Structured log output channel. Verbatim from Claude25 control_component.py."""
from __future__ import annotations
import time
from collections import deque
from typing import Any, Deque, Dict, List
from modules.control.channel.output_channel import OutputChannel
from modules.control.action_dispatch.action_dispatcher import (
    ActionPriority, DispatchAction,
)
from cyber.logger.cyber_logger import get_logger

logger = get_logger("control.log")


class LogOutputChannel(OutputChannel):
    def __init__(self) -> None:
        super().__init__(name="log", min_priority=ActionPriority.LOW, cooldown_s=0.0)
        self._log_buffer: Deque[Dict[str, Any]] = deque(maxlen=500)

    def _do_output(self, action: DispatchAction) -> None:
        entry = {
            "ts": time.time(),
            "category": action.category.value if hasattr(action.category, "value") else str(action.category),
            "priority": action.priority.name,
            "text": action.text[:200],
            "source": action.source,
            "game_time": action.data.get("game_time", 0.0),
        }
        self._log_buffer.append(entry)
        logger.info("[dispatch] [%s] %s: %s", action.priority.name, action.source, action.text[:80])

    def recent_entries(self, count: int = 20) -> List[Dict[str, Any]]:
        items = list(self._log_buffer)
        return items[-count:]

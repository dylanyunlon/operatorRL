"""
Voice Narration Engine — Priority-based strategy narration queue.

Manages a priority queue of strategy messages, providing TTS-ready
text output for real-time voice guidance during gameplay.

Location: integrations/lol/src/lol_agent/voice_narration_engine.py

Reference (拿来主义):
  - operatorRL voice_advisor: priority queue + message formatting
  - integrations/dota2/src/dota2_agent/bot_commander.py: (-priority, seq) ordering
  - Akagi: bridge message queue pattern (majsoul.py mjai_messages deque)
"""

from __future__ import annotations

import heapq
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.voice_narration_engine.v1"


class VoiceNarrationEngine:
    """Priority-based voice narration queue for real-time gameplay.

    Messages are enqueued with priority (0 = highest) and dequeued
    in priority order. Supports max queue size, TTS text formatting,
    and evolution callback hooks.

    Attributes:
        max_queue: Maximum queue size (oldest low-priority dropped).
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        max_queue: int = 50,
    ) -> None:
        self.max_queue = max_queue
        self._heap: list[tuple[int, int, str]] = []  # (priority, seq, message)
        self._seq: int = 0
        self._dequeue_count: int = 0

        # --- Evolution pattern ---
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def enqueue(self, message: str, priority: int = 5) -> None:
        """Add a message to the narration queue.

        Args:
            message: Text message to narrate.
            priority: Priority level (0 = critical, 5 = info).
        """
        self._seq += 1
        heapq.heappush(self._heap, (priority, self._seq, message))

        # Enforce max queue size by dropping lowest-priority (highest number)
        while len(self._heap) > self.max_queue:
            # Remove the item with highest priority number (least urgent)
            # Since heapq is a min-heap, we need to find and remove the max
            if len(self._heap) > self.max_queue:
                # Simple approach: sort, trim, re-heapify
                self._heap.sort()
                self._heap = self._heap[:self.max_queue]
                heapq.heapify(self._heap)

    def dequeue(self) -> Optional[str]:
        """Get the highest-priority message.

        Returns:
            Message string, or None if queue is empty.
        """
        if not self._heap:
            return None

        priority, seq, message = heapq.heappop(self._heap)
        self._dequeue_count += 1

        self._fire_evolution("message_dequeued", {
            "priority": priority,
            "message_preview": message[:50],
        })
        return message

    def queue_size(self) -> int:
        """Current number of messages in queue."""
        return len(self._heap)

    def clear(self) -> None:
        """Clear all messages from the queue."""
        self._heap.clear()
        self._seq = 0

    def to_tts_text(self, message: str) -> str:
        """Format a message for TTS output.

        Adds appropriate pauses and emphasis markers.

        Args:
            message: Raw text message.

        Returns:
            TTS-formatted text string.
        """
        # Simple formatting: strip excess whitespace, add period if missing
        text = message.strip()
        if text and text[-1] not in ".!?":
            text += "."
        return text

    def peek(self) -> Optional[str]:
        """Peek at the highest-priority message without removing it.

        Returns:
            Message string, or None if empty.
        """
        if not self._heap:
            return None
        return self._heap[0][2]

    def _fire_evolution(self, event_type: str, payload: dict) -> None:
        """Fire evolution callback if registered."""
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

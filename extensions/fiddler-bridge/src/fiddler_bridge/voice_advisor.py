"""
Voice Advisor — Strategy advice to TTS realtime voice output.

Manages a priority queue of strategy advice, formats advice messages,
enforces cooldowns between spoken advisories, and deduplicates similar
advice within a time window.

Location: extensions/fiddler-bridge/src/fiddler_bridge/voice_advisor.py
"""

from __future__ import annotations

import heapq
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.voice_advisor.v1"


class VoiceAdvisor:
    """Strategy advice → TTS voice output manager.

    Priority-queued advice system with cooldown enforcement,
    deduplication, and spoken history tracking.
    """

    def __init__(
        self,
        language: str = "zh-CN",
        speech_rate: float = 1.2,
        min_interval_seconds: float = 3.0,
        dedup_window_seconds: float = 10.0,
    ) -> None:
        self.language = language
        self.speech_rate = speech_rate
        self.min_interval_seconds = min_interval_seconds
        self.dedup_window_seconds = dedup_window_seconds
        # Priority queue: (-priority, insertion_order, text)
        self._queue: list[tuple[int, int, str]] = []
        self._insert_counter: int = 0
        self._last_spoken_time: float = 0.0
        self._recent_texts: dict[str, float] = {}  # text -> timestamp
        self._history: list[dict[str, Any]] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def format_advice(
        self, action: str, reason: str = "", urgency: str = "normal"
    ) -> str:
        """Format strategy advice into speakable text.

        Args:
            action: Action to take (e.g., "retreat", "engage").
            reason: Reason for the action.
            urgency: Urgency level.

        Returns:
            Formatted advice string.
        """
        parts = [action]
        if reason:
            parts.append(f"because {reason}")
        if urgency == "high":
            parts.insert(0, "Warning:")
        return " ".join(parts)

    def enqueue_advice(self, text: str, priority: int = 1) -> None:
        """Add advice to the priority queue with deduplication.

        Higher priority values are dequeued first.

        Args:
            text: Advice text.
            priority: Priority level (higher = more urgent).
        """
        now = time.time()
        # Dedup check
        if text in self._recent_texts:
            if now - self._recent_texts[text] < self.dedup_window_seconds:
                return
        self._recent_texts[text] = now
        self._insert_counter += 1
        heapq.heappush(
            self._queue, (-priority, self._insert_counter, text)
        )

    def dequeue_advice(self) -> Optional[dict[str, Any]]:
        """Get the highest-priority advice from the queue.

        Returns:
            Dict with text and priority, or None if empty.
        """
        if not self._queue:
            return None
        neg_pri, _, text = heapq.heappop(self._queue)
        return {"text": text, "priority": -neg_pri}

    def queue_size(self) -> int:
        """Get current queue size.

        Returns:
            Number of items in queue.
        """
        return len(self._queue)

    def mark_spoken(self, timestamp: float) -> None:
        """Mark that advice was spoken at a given time.

        Args:
            timestamp: Time when advice was spoken.
        """
        self._last_spoken_time = timestamp

    def can_speak(self, current_time: float) -> bool:
        """Check if enough time has passed since last spoken advice.

        Args:
            current_time: Current time.

        Returns:
            True if cooldown has elapsed.
        """
        return (current_time - self._last_spoken_time) >= self.min_interval_seconds

    def record_spoken(self, text: str, timestamp: float) -> None:
        """Record a spoken advice in history.

        Args:
            text: Advice text that was spoken.
            timestamp: When it was spoken.
        """
        self._history.append({"text": text, "timestamp": timestamp})
        self._last_spoken_time = timestamp

    def get_history(self) -> list[dict[str, Any]]:
        """Get spoken advice history.

        Returns:
            List of {text, timestamp} dicts.
        """
        return list(self._history)

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "voice_advisor",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

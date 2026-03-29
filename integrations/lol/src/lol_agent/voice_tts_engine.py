"""
Voice TTS Engine — Strategy suggestion → voice synthesis.

Priority queue of voice messages with TTS metadata generation,
rate control, and batch synthesis.

Location: integrations/lol/src/lol_agent/voice_tts_engine.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/voice_narration_engine.py: priority queue pattern
  - Akagi: bridge message queue (majsoul.py mjai_messages deque)
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import heapq
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.voice_tts_engine.v1"

_SUPPORTED_VOICES: list[str] = ["default", "male", "female", "tactical", "calm"]


class VoiceTTSEngine:
    """TTS engine with priority queue for real-time voice guidance.

    Attributes:
        voice: Selected voice profile.
        rate: Speech rate multiplier.
        max_queue: Maximum queue depth.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(
        self,
        voice: str = "default",
        rate: float = 1.0,
        max_queue: int = 50,
    ) -> None:
        self.voice = voice
        self.rate = rate
        self.max_queue = max_queue
        self._heap: list[tuple[int, int, str]] = []
        self._seq: int = 0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def synthesize(self, text: str) -> dict[str, Any]:
        """Synthesize text to TTS metadata.

        Args:
            text: Text to synthesize.

        Returns:
            Dict with text, duration_ms, voice, rate.
        """
        # Estimate duration: ~150 words/min at rate 1.0
        word_count = max(len(text.split()), 1)
        duration_ms = int((word_count / 150.0) * 60000 / max(self.rate, 0.1))
        result = {
            "text": text,
            "duration_ms": duration_ms,
            "voice": self.voice,
            "rate": self.rate,
            "timestamp": time.time(),
        }
        self._fire_evolution({"event": "synthesized", "text_len": len(text), "duration_ms": duration_ms})
        return result

    def synthesize_batch(self, messages: list[str]) -> list[dict[str, Any]]:
        return [self.synthesize(m) for m in messages]

    def enqueue(self, message: str, priority: int = 5) -> None:
        self._seq += 1
        heapq.heappush(self._heap, (priority, self._seq, message))
        # Trim if over max
        while len(self._heap) > self.max_queue:
            # Remove lowest priority (highest number)
            items = list(self._heap)
            items.sort()
            self._heap = items[:self.max_queue]
            heapq.heapify(self._heap)

    def dequeue(self) -> Optional[dict[str, Any]]:
        if not self._heap:
            return None
        priority, _seq, message = heapq.heappop(self._heap)
        return {"text": message, "priority": priority}

    def queue_size(self) -> int:
        return len(self._heap)

    def clear_queue(self) -> None:
        self._heap.clear()
        self._seq = 0

    def set_rate(self, rate: float) -> None:
        self.rate = rate

    def list_voices(self) -> list[str]:
        return list(_SUPPORTED_VOICES)

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

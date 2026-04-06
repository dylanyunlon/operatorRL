"""
modules/control/voice_output/voice_priority_queue.py — Priority queue for voice output.
==========================================================================================
Claude18 · Fixes low dispatch throughput in control component

Problem from diagnostic run:
    control.dispatch_count=1 in 6 seconds. Strategy advice IS being published
    (planning produces macro decisions) but ControlComponent's VoiceOutputChannel
    has a 5s cooldown per dedup_key. Since most advice has similar dedup keys
    (e.g. "strategy:macro_IDLE"), almost everything gets dropped.

Solution: A priority queue that selects the HIGHEST priority pending
voice command, with per-category cooldowns instead of per-dedup-key.
Critical announcements (ace, baron, massive gold swing) bypass cooldown.

File location: lolbot-HyperAI/modules/control/voice_output/voice_priority_queue.py
"""

from __future__ import annotations

import heapq
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VoicePriority(IntEnum):
    """Voice announcement priority (higher = more important)."""
    AMBIENT = 0      # Background info (gold diff update)
    LOW = 1          # Minor advice (ward suggestion)
    MEDIUM = 2       # Standard advice (strategy recommendation)
    HIGH = 3         # Important (objective spawning, power spike)
    CRITICAL = 4     # Must announce (ace, baron steal, game-changing)
    EMERGENCY = 5    # System alerts (disconnect warning)


@dataclass(order=True)
class VoiceEntry:
    """Priority queue entry for voice announcements."""
    # Negative priority for min-heap (highest priority first)
    sort_key: Tuple[int, float] = field(compare=True, default=(0, 0.0))
    text: str = field(compare=False, default="")
    category: str = field(compare=False, default="general")
    priority: VoicePriority = field(compare=False, default=VoicePriority.LOW)
    game_time: float = field(compare=False, default=0.0)
    enqueue_time: float = field(compare=False, default=0.0)
    ttl_s: float = field(compare=False, default=10.0)  # Expire after this

    @property
    def expired(self) -> bool:
        return (time.monotonic() - self.enqueue_time) > self.ttl_s


# Default per-category cooldowns (seconds)
_DEFAULT_COOLDOWNS: Dict[str, float] = {
    "win_probability": 25.0,
    "strategy_advice": 8.0,
    "macro_decision": 8.0,
    "lane_advice": 12.0,
    "objective_window": 15.0,
    "power_spike": 10.0,
    "teamfight": 6.0,
    "event_announcement": 3.0,
    "system": 30.0,
}

# Categories that bypass cooldown entirely
_BYPASS_COOLDOWN_CATEGORIES = {
    "ace",
    "baron_steal",
    "game_start",
    "game_end",
    "emergency",
}


class VoicePriorityQueue:
    """Priority queue for voice announcements with category-based cooldowns.

    Unlike the simple dedup_key cooldown in OutputChannel, this uses:
    1. Per-CATEGORY cooldowns (not per-message dedup key)
    2. Priority ordering (critical announcements go first)
    3. TTL expiration (old messages auto-expire)
    4. Bypass list for game-critical events

    Usage::
        queue = VoicePriorityQueue()
        queue.enqueue("Baron spawning in 30 seconds!", "objective_window",
                      VoicePriority.HIGH, game_time=1170.0)
        queue.enqueue("Gold update: we're ahead by 2k", "win_probability",
                      VoicePriority.AMBIENT, game_time=1170.0)

        # In control Proc():
        entry = queue.dequeue()
        if entry:
            tts_speak(entry.text)
    """

    MAX_QUEUE_SIZE = 32

    def __init__(
        self,
        cooldowns: Optional[Dict[str, float]] = None,
    ) -> None:
        self._heap: List[VoiceEntry] = []
        self._cooldowns = cooldowns or dict(_DEFAULT_COOLDOWNS)
        self._last_fire_time: Dict[str, float] = {}
        self._enqueue_count: int = 0
        self._dequeue_count: int = 0
        self._drop_count: int = 0
        self._expire_count: int = 0

    def enqueue(
        self,
        text: str,
        category: str,
        priority: VoicePriority = VoicePriority.MEDIUM,
        game_time: float = 0.0,
        ttl_s: float = 10.0,
    ) -> bool:
        """Add a voice announcement to the queue.

        Returns True if enqueued, False if dropped (cooldown/full).
        """
        # Check category cooldown (unless bypassed)
        if category not in _BYPASS_COOLDOWN_CATEGORIES:
            cooldown = self._cooldowns.get(category, 5.0)
            last = self._last_fire_time.get(category, 0.0)
            now = time.monotonic()
            if now - last < cooldown:
                self._drop_count += 1
                return False

        # Queue size limit — drop lowest priority if full
        if len(self._heap) >= self.MAX_QUEUE_SIZE:
            self._purge_expired()
            if len(self._heap) >= self.MAX_QUEUE_SIZE:
                self._drop_count += 1
                return False

        entry = VoiceEntry(
            sort_key=(-priority.value, time.monotonic()),
            text=text,
            category=category,
            priority=priority,
            game_time=game_time,
            enqueue_time=time.monotonic(),
            ttl_s=ttl_s,
        )
        heapq.heappush(self._heap, entry)
        self._enqueue_count += 1
        return True

    def dequeue(self) -> Optional[VoiceEntry]:
        """Pop the highest-priority non-expired entry.

        Also records the category cooldown.
        """
        self._purge_expired()

        while self._heap:
            entry = heapq.heappop(self._heap)
            if entry.expired:
                self._expire_count += 1
                continue

            # Record cooldown
            self._last_fire_time[entry.category] = time.monotonic()
            self._dequeue_count += 1
            return entry

        return None

    def peek(self) -> Optional[VoiceEntry]:
        """Look at highest-priority entry without removing it."""
        self._purge_expired()
        if self._heap:
            return self._heap[0]
        return None

    def _purge_expired(self) -> None:
        """Remove expired entries from the heap."""
        valid = []
        for entry in self._heap:
            if entry.expired:
                self._expire_count += 1
            else:
                valid.append(entry)
        if len(valid) < len(self._heap):
            self._heap = valid
            heapq.heapify(self._heap)

    def set_cooldown(self, category: str, seconds: float) -> None:
        self._cooldowns[category] = seconds

    def clear(self) -> None:
        self._heap.clear()

    @property
    def pending_count(self) -> int:
        return len(self._heap)

    def stats(self) -> Dict[str, Any]:
        return {
            "pending": len(self._heap),
            "enqueued": self._enqueue_count,
            "dequeued": self._dequeue_count,
            "dropped": self._drop_count,
            "expired": self._expire_count,
            "cooldown_active": {
                cat: round(time.monotonic() - ts, 1)
                for cat, ts in self._last_fire_time.items()
            },
        }

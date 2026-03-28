"""
Frame Buffer — Ring buffer for timestamped video frames.

Provides capacity-limited FIFO storage with timestamp-indexed retrieval,
range queries, and nearest-timestamp lookup.

Location: extensions/vision-bridge/src/vision_bridge/frame_buffer.py

Reference (拿来主義):
  - LeagueAI: frame-by-frame processing pipeline
  - operatorRL ExperienceStore: deque-based capacity management
  - Python collections.deque(maxlen=N) pattern
"""

from __future__ import annotations

import bisect
import logging
from collections import deque
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.frame_buffer.v1"


class FrameBuffer:
    """Ring buffer storing timestamped frames with indexed retrieval.

    Uses a deque with maxlen for automatic eviction of oldest frames.

    Attributes:
        capacity: Maximum number of frames to store.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, capacity: int = 100) -> None:
        self.capacity = capacity
        self._frames: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._timestamps: deque[float] = deque(maxlen=capacity)
        self.evolution_callback: Optional[Callable] = None

    def __len__(self) -> int:
        return len(self._frames)

    def push(self, frame: Any, timestamp: float) -> None:
        """Push a new frame with timestamp.

        Args:
            frame: Frame data (flat pixel list, ndarray, etc.).
            timestamp: Capture timestamp in seconds.
        """
        entry = {"frame": frame, "timestamp": timestamp}
        self._frames.append(entry)
        self._timestamps.append(timestamp)

    def get_latest(self) -> Optional[dict[str, Any]]:
        """Get the most recently pushed frame.

        Returns:
            Frame dict with 'frame' and 'timestamp', or None if empty.
        """
        if not self._frames:
            return None
        return self._frames[-1]

    def get_oldest(self) -> Optional[dict[str, Any]]:
        """Get the oldest frame in the buffer.

        Returns:
            Frame dict, or None if empty.
        """
        if not self._frames:
            return None
        return self._frames[0]

    def get_nearest(self, timestamp: float) -> Optional[dict[str, Any]]:
        """Get the frame with timestamp nearest to the given value.

        Args:
            timestamp: Target timestamp.

        Returns:
            Frame dict with closest timestamp, or None if empty.
        """
        if not self._frames:
            return None

        ts_list = list(self._timestamps)
        idx = bisect.bisect_left(ts_list, timestamp)

        # Check neighbors for closest
        best_idx = 0
        best_diff = float("inf")

        candidates = []
        if idx > 0:
            candidates.append(idx - 1)
        if idx < len(ts_list):
            candidates.append(idx)

        for c in candidates:
            diff = abs(ts_list[c] - timestamp)
            if diff < best_diff:
                best_diff = diff
                best_idx = c

        return self._frames[best_idx]

    def get_range(
        self, start: float, end: float
    ) -> list[dict[str, Any]]:
        """Get all frames within a timestamp range (inclusive).

        Args:
            start: Start timestamp.
            end: End timestamp.

        Returns:
            List of frame dicts within the range, ordered by timestamp.
        """
        return [
            f for f in self._frames
            if start <= f["timestamp"] <= end
        ]

    def clear(self) -> None:
        """Clear all frames from the buffer."""
        self._frames.clear()
        self._timestamps.clear()

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

"""
Session Recorder — Complete session recording, replay, and export.

Records all events during a game session, supports JSON export
for offline analysis and replay.

Location: agentos/cli/session_recorder.py

Reference (拿来主义):
  - integrations/lol/src/lol_agent/lol_evolution_loop.py: episode recording
  - agentos/governance/data_pipeline.py: data collection pattern
  - Akagi: temp_mjai_msg message history recording
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class SessionRecorder:
    """Records complete game sessions for analysis and replay.

    Captures all events with timestamps during active recording,
    supports JSON export for offline processing.
    """

    def __init__(self) -> None:
        self._recording: bool = False
        self._events: list[dict[str, Any]] = []
        self._start_time: float = 0.0
        self._end_time: float = 0.0

    def start(self) -> None:
        """Start recording a session."""
        self._recording = True
        self._start_time = time.time()
        self._events.clear()

    def stop(self) -> None:
        """Stop recording."""
        self._recording = False
        self._end_time = time.time()

    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._recording

    def record_event(self, event: dict[str, Any]) -> None:
        """Record an event.

        Only records if currently in recording state.

        Args:
            event: Event data dict.
        """
        if not self._recording:
            return

        event_copy = dict(event)
        if "recorded_at" not in event_copy:
            event_copy["recorded_at"] = time.time()
        self._events.append(event_copy)

    def event_count(self) -> int:
        """Number of recorded events."""
        return len(self._events)

    def get_events(self) -> list[dict[str, Any]]:
        """Return all recorded events."""
        return list(self._events)

    def clear(self) -> None:
        """Clear all recorded events."""
        self._events.clear()

    def get_duration(self) -> float:
        """Get session duration in seconds.

        Returns:
            Duration from start to stop (or current time if still recording).
        """
        if self._start_time == 0:
            return 0.0
        end = self._end_time if self._end_time > 0 else time.time()
        return max(0.0, end - self._start_time)

    def export_json(self) -> str:
        """Export session as JSON string.

        Returns:
            JSON string with events, duration, metadata.
        """
        data = {
            "events": self._events,
            "event_count": len(self._events),
            "duration": self.get_duration(),
            "start_time": self._start_time,
            "end_time": self._end_time,
        }
        return json.dumps(data, indent=2, default=str)

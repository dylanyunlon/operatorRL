"""
Session 30-Minute Manager — Long-running session stability and memory management.

Manages 30-minute game session lifecycle with memory usage tracking,
limit enforcement, and session summary generation.

Location: extensions/fiddler-bridge/src/fiddler_bridge/session_30min_manager.py
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.session_30min_manager.v1"


class Session30MinManager:
    """30-minute game session lifecycle manager.

    Manages session start/end, elapsed time tracking, memory usage
    monitoring, and limit enforcement for long-running game sessions.
    """

    def __init__(
        self,
        max_duration_seconds: int = 1800,
        memory_limit_mb: float = 512.0,
    ) -> None:
        self.max_duration_seconds = max_duration_seconds
        self.memory_limit_mb = memory_limit_mb
        self._sessions: dict[str, dict[str, Any]] = {}
        self._current_memory_mb: float = 0.0
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def start_session(self) -> str:
        """Start a new session.

        Returns:
            Session ID string.
        """
        session_id = str(uuid.uuid4())[:8]
        self._sessions[session_id] = {
            "start_time": time.time(),
            "active": True,
        }
        return session_id

    def is_active(self, session_id: str) -> bool:
        """Check if a session is active.

        Args:
            session_id: Session ID.

        Returns:
            True if session exists and is active.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return False
        return session.get("active", False)

    def get_elapsed(self, session_id: str) -> float:
        """Get elapsed time for a session.

        Args:
            session_id: Session ID.

        Returns:
            Elapsed seconds, or 0.0 if session not found.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return 0.0
        return time.time() - session["start_time"]

    def record_memory_usage(self, memory_mb: float) -> None:
        """Record current memory usage.

        Args:
            memory_mb: Memory usage in megabytes.
        """
        self._current_memory_mb = memory_mb

    def current_memory_mb(self) -> float:
        """Get current recorded memory usage.

        Returns:
            Memory usage in MB.
        """
        return self._current_memory_mb

    def is_memory_exceeded(self) -> bool:
        """Check if memory limit is exceeded.

        Returns:
            True if current memory exceeds limit.
        """
        return self._current_memory_mb > self.memory_limit_mb

    def end_session(self, session_id: str) -> dict[str, Any]:
        """End a session and return summary.

        Args:
            session_id: Session ID.

        Returns:
            Session summary dict.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"error": "session_not_found", "duration": 0}

        duration = time.time() - session["start_time"]
        session["active"] = False
        session["end_time"] = time.time()
        session["duration"] = duration

        return {
            "session_id": session_id,
            "duration": duration,
            "memory_peak_mb": self._current_memory_mb,
        }

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "session_30min_manager",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

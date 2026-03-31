"""
MultiGameSessionManager — Manage concurrent capture sessions across multiple games.

Manages multiple game capture sessions simultaneously with per-game resource
isolation and state tracking.

Location: extensions/protocol_decoder/src/multi_game_session_manager.py

Reference (拿来主義):
  - extensions/fiddler_bridge/src/fiddler_session_state_machine.py（M647）:
    session state machine
  - agentlightning/inference/inference_session_manager.py: session lifecycle

Design Notes (Knuth-level critique):
  User:
    - create_session() returns session_id — opaque handle.
    - Each session is independent — failure in one doesn't affect others.
    - get_all_sessions() provides dashboard overview.
  System:
    - Sessions keyed by unique ID — O(1) operations.
    - Per-game resource isolation via separate session instances.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.protocol_decoder.multi_game_session_manager.v1"


class GameSession:
    """Single game capture session."""

    __slots__ = ("session_id", "game_type", "state", "config", "created_at",
                 "started_at", "stopped_at", "packet_count", "error_count", "metadata")

    def __init__(self, session_id: str, game_type: str, config: Dict[str, Any]) -> None:
        self.session_id = session_id
        self.game_type = game_type
        self.state = "created"  # created → running → paused → stopped
        self.config = config
        self.created_at = time.time()
        self.started_at: float = 0.0
        self.stopped_at: float = 0.0
        self.packet_count: int = 0
        self.error_count: int = 0
        self.metadata: Dict[str, Any] = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "game_type": self.game_type,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "packet_count": self.packet_count,
            "error_count": self.error_count,
            "uptime": (
                (self.stopped_at or time.time()) - self.started_at
                if self.started_at > 0 else 0.0
            ),
        }


class MultiGameSessionManager:
    """Manage concurrent game capture sessions.

    Public API:
        create_session(game_type, config) -> str (session_id)
        start_session(session_id) -> bool
        stop_session(session_id) -> bool
        pause_session(session_id) -> bool
        resume_session(session_id) -> bool
        record_packet(session_id) -> None
        record_error(session_id) -> None
        get_session(session_id) -> GameSession | None
        get_sessions_by_game(game_type) -> list[GameSession]
        get_all_sessions() -> list[dict]
        remove_session(session_id) -> bool
        get_stats() -> dict
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, GameSession] = {}
        self._create_count: int = 0
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def create_session(
        self, game_type: str, config: Optional[Dict[str, Any]] = None,
    ) -> str:
        sid = f"{game_type}_{uuid.uuid4().hex[:8]}"
        session = GameSession(sid, game_type, config or {})
        self._sessions[sid] = session
        self._create_count += 1
        self._fire("session_created", {"session_id": sid, "game_type": game_type})
        return sid

    def start_session(self, session_id: str) -> bool:
        s = self._sessions.get(session_id)
        if s is None:
            return False
        if s.state not in ("created", "paused"):
            return False
        s.state = "running"
        s.started_at = time.time()
        self._fire("session_started", {"session_id": session_id})
        return True

    def stop_session(self, session_id: str) -> bool:
        s = self._sessions.get(session_id)
        if s is None:
            return False
        if s.state == "stopped":
            return True  # idempotent
        s.state = "stopped"
        s.stopped_at = time.time()
        self._fire("session_stopped", {"session_id": session_id})
        return True

    def pause_session(self, session_id: str) -> bool:
        s = self._sessions.get(session_id)
        if s is None or s.state != "running":
            return False
        s.state = "paused"
        return True

    def resume_session(self, session_id: str) -> bool:
        s = self._sessions.get(session_id)
        if s is None or s.state != "paused":
            return False
        s.state = "running"
        return True

    def record_packet(self, session_id: str) -> None:
        s = self._sessions.get(session_id)
        if s is not None:
            s.packet_count += 1

    def record_error(self, session_id: str) -> None:
        s = self._sessions.get(session_id)
        if s is not None:
            s.error_count += 1

    def get_session(self, session_id: str) -> Optional[GameSession]:
        return self._sessions.get(session_id)

    def get_sessions_by_game(self, game_type: str) -> List[GameSession]:
        return [s for s in self._sessions.values() if s.game_type == game_type]

    def get_all_sessions(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._sessions.values()]

    def remove_session(self, session_id: str) -> bool:
        s = self._sessions.pop(session_id, None)
        if s is None:
            return False
        if s.state == "running":
            s.state = "stopped"
            s.stopped_at = time.time()
        return True

    def get_stats(self) -> Dict[str, Any]:
        by_game: Dict[str, int] = {}
        by_state: Dict[str, int] = {}
        for s in self._sessions.values():
            by_game[s.game_type] = by_game.get(s.game_type, 0) + 1
            by_state[s.state] = by_state.get(s.state, 0) + 1
        return {
            "total_sessions": len(self._sessions),
            "create_count": self._create_count,
            "by_game": by_game,
            "by_state": by_state,
            "total_packets": sum(s.packet_count for s in self._sessions.values()),
        }

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        data["component"] = _EVOLUTION_KEY
        data["ts"] = time.time()
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb({"type": event_type, **data})
            except Exception:
                logger.exception("evolution_callback raised")

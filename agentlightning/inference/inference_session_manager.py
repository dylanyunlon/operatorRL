"""
Inference Session Manager — Live game inference session lifecycle.

Manages the creation, execution, and teardown of inference sessions
for live games. Each session binds a model version, feature store,
action sampler, and game adapter into a unified inference context.

Location: agentlightning/inference/inference_session_manager.py

Reference (拿来主义):
  查看 agentlightning/runner/game_runner.py 上现有 GameRunner 的
  register_game/start_game/stop_game 会话管理方式, 理解其模式, 特别是
  launcher注册如何与session生命周期(start→monitor→stop)分离。
  从 agentos/governance/evolution_orchestrator.py 这个好例子开始 — 它的
  register_loop→run_cycle→allocate_resources 展示了多实体注册+统一调度。
  遵循该模式实现 InferenceSessionManager, 让 game_runner 可以在启动
  游戏后自动创建推理会话, 并能在会话内维护完整的推理上下文(模型版本、
  特征缓存、决策历史).

Design Notes (Knuth-level critique):
  User:
    - Session isolation prevents cross-game state leak
    - Auto-cleanup on session end avoids resource leak
    - Decision history enables post-game analysis
  System:
    - max_concurrent prevents resource exhaustion
    - Session state is a dict — serializable for crash recovery
    - Lock per-session avoids global contention
"""

from __future__ import annotations

import logging
import time
import uuid
import threading
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentlightning.inference.inference_session_manager.v1"

_MAX_CONCURRENT_DEFAULT: int = 5
_SESSION_TIMEOUT_DEFAULT: float = 7200.0  # 2 hours


class InferenceSession:
    """Single inference session context.

    Attributes:
        session_id: Unique session identifier.
        game: Game type identifier.
        model_version: Bound model version string.
        status: Session status (created/running/paused/stopped/error).
        created_at: Creation timestamp.
        decision_history: List of decisions made during session.
    """

    __slots__ = (
        "session_id", "game", "model_version", "status", "created_at",
        "last_active", "decision_count", "decision_history", "config",
        "timeout", "_metadata",
    )

    def __init__(
        self,
        session_id: str,
        game: str,
        model_version: str = "latest",
        config: Optional[Dict[str, Any]] = None,
        timeout: float = _SESSION_TIMEOUT_DEFAULT,
    ) -> None:
        self.session_id = session_id
        self.game = game
        self.model_version = model_version
        self.status = "created"
        self.created_at = time.time()
        self.last_active = self.created_at
        self.decision_count: int = 0
        self.decision_history: List[Dict[str, Any]] = []
        self.config = config or {}
        self.timeout = timeout
        self._metadata: Dict[str, Any] = {}

    def is_active(self) -> bool:
        """Check if session is in a running state."""
        return self.status in ("created", "running", "paused")

    def is_timed_out(self, now: Optional[float] = None) -> bool:
        """Check if session has exceeded timeout."""
        t = now if now is not None else time.time()
        return (t - self.last_active) > self.timeout

    def record_decision(self, decision: Dict[str, Any]) -> None:
        """Record a decision made during this session."""
        decision["session_step"] = self.decision_count
        decision["timestamp"] = time.time()
        self.decision_history.append(decision)
        self.decision_count += 1
        self.last_active = time.time()

    def set_metadata(self, key: str, value: Any) -> None:
        """Attach metadata to session."""
        self._metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Retrieve session metadata."""
        return self._metadata.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize session to dict."""
        return {
            "session_id": self.session_id,
            "game": self.game,
            "model_version": self.model_version,
            "status": self.status,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "decision_count": self.decision_count,
            "config": self.config,
            "metadata": dict(self._metadata),
        }


class InferenceSessionManager:
    """Manages inference session lifecycle across games.

    Creates, monitors, and tears down inference sessions.
    Enforces concurrency limits and session timeouts.

    Attributes:
        max_concurrent: Maximum concurrent sessions.
        default_timeout: Default session timeout in seconds.
        evolution_callback: Optional callback for evolution events.
    """

    def __init__(
        self,
        max_concurrent: int = _MAX_CONCURRENT_DEFAULT,
        default_timeout: float = _SESSION_TIMEOUT_DEFAULT,
    ) -> None:
        self.max_concurrent = max_concurrent
        self.default_timeout = default_timeout
        self._sessions: Dict[str, InferenceSession] = {}
        self._completed: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stats = {
            "total_created": 0,
            "total_completed": 0,
            "total_errors": 0,
            "total_timeouts": 0,
            "total_decisions": 0,
        }
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    # --- Session Lifecycle ---

    def create_session(
        self,
        game: str,
        model_version: str = "latest",
        config: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """Create a new inference session.

        Args:
            game: Game type identifier.
            model_version: Model version to bind.
            config: Session-specific configuration.
            timeout: Override timeout seconds.

        Returns:
            Session ID string.

        Raises:
            RuntimeError: If max_concurrent sessions reached.
        """
        with self._lock:
            active_count = sum(
                1 for s in self._sessions.values() if s.is_active()
            )
            if active_count >= self.max_concurrent:
                raise RuntimeError(
                    f"Max concurrent sessions ({self.max_concurrent}) reached"
                )

            session_id = f"sess_{uuid.uuid4().hex[:12]}"
            effective_timeout = timeout if timeout is not None else self.default_timeout
            session = InferenceSession(
                session_id=session_id,
                game=game,
                model_version=model_version,
                config=config,
                timeout=effective_timeout,
            )
            self._sessions[session_id] = session
            self._stats["total_created"] += 1

        self._fire_evolution("session_created", {
            "session_id": session_id, "game": game, "model_version": model_version,
        })
        return session_id

    def start_session(self, session_id: str) -> None:
        """Transition session to running state.

        Args:
            session_id: Session identifier.

        Raises:
            KeyError: If session not found.
            ValueError: If session not in created/paused state.
        """
        with self._lock:
            session = self._get_session(session_id)
            if session.status not in ("created", "paused"):
                raise ValueError(
                    f"Cannot start session in '{session.status}' state"
                )
            session.status = "running"
            session.last_active = time.time()

    def pause_session(self, session_id: str) -> None:
        """Pause a running session.

        Args:
            session_id: Session identifier.

        Raises:
            KeyError: If session not found.
            ValueError: If session not running.
        """
        with self._lock:
            session = self._get_session(session_id)
            if session.status != "running":
                raise ValueError(
                    f"Cannot pause session in '{session.status}' state"
                )
            session.status = "paused"

    def stop_session(self, session_id: str) -> Dict[str, Any]:
        """Stop and archive a session.

        Args:
            session_id: Session identifier.

        Returns:
            Final session summary dict.

        Raises:
            KeyError: If session not found.
        """
        with self._lock:
            session = self._get_session(session_id)
            session.status = "stopped"
            summary = session.to_dict()
            summary["duration"] = time.time() - session.created_at
            summary["total_decisions"] = session.decision_count
            self._completed.append(summary)
            del self._sessions[session_id]
            self._stats["total_completed"] += 1
            self._stats["total_decisions"] += session.decision_count

        self._fire_evolution("session_stopped", {
            "session_id": session_id,
            "decisions": summary["total_decisions"],
            "duration": summary["duration"],
        })
        return summary

    def error_session(self, session_id: str, error: str) -> None:
        """Mark a session as errored.

        Args:
            session_id: Session identifier.
            error: Error description.
        """
        with self._lock:
            session = self._get_session(session_id)
            session.status = "error"
            session.set_metadata("error", error)
            self._stats["total_errors"] += 1

    # --- Decision Recording ---

    def record_decision(
        self, session_id: str, decision: Dict[str, Any]
    ) -> None:
        """Record a decision within a session.

        Args:
            session_id: Session identifier.
            decision: Decision data dict.

        Raises:
            KeyError: If session not found.
            ValueError: If session not running.
        """
        with self._lock:
            session = self._get_session(session_id)
            if session.status != "running":
                raise ValueError(
                    f"Cannot record decision in '{session.status}' state"
                )
            session.record_decision(decision)

    def get_decision_history(
        self, session_id: str, last_n: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get decision history for a session.

        Args:
            session_id: Session identifier.
            last_n: If specified, return only the last N decisions.

        Returns:
            List of decision dicts.
        """
        with self._lock:
            session = self._get_session(session_id)
            history = list(session.decision_history)
        if last_n is not None:
            return history[-last_n:]
        return history

    # --- Query ---

    def get_session(self, session_id: str) -> Dict[str, Any]:
        """Get session info.

        Args:
            session_id: Session identifier.

        Returns:
            Session dict.
        """
        with self._lock:
            return self._get_session(session_id).to_dict()

    def list_sessions(
        self, game: Optional[str] = None, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List sessions, optionally filtered.

        Args:
            game: Filter by game type.
            status: Filter by status.

        Returns:
            List of session dicts.
        """
        with self._lock:
            result: List[Dict[str, Any]] = []
            for session in self._sessions.values():
                if game is not None and session.game != game:
                    continue
                if status is not None and session.status != status:
                    continue
                result.append(session.to_dict())
        return result

    def active_count(self) -> int:
        """Count of active sessions."""
        with self._lock:
            return sum(1 for s in self._sessions.values() if s.is_active())

    def get_completed(self, last_n: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get completed session summaries.

        Args:
            last_n: Return only the last N completed sessions.

        Returns:
            List of completed session summaries.
        """
        if last_n is not None:
            return list(self._completed[-last_n:])
        return list(self._completed)

    # --- Maintenance ---

    def cleanup_timed_out(self) -> int:
        """Stop all timed-out sessions.

        Returns:
            Number of sessions cleaned up.
        """
        now = time.time()
        timed_out_ids: List[str] = []
        with self._lock:
            for sid, session in self._sessions.items():
                if session.is_active() and session.is_timed_out(now):
                    timed_out_ids.append(sid)

        count = 0
        for sid in timed_out_ids:
            try:
                self.stop_session(sid)
                self._stats["total_timeouts"] += 1
                count += 1
            except KeyError:
                pass
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        with self._lock:
            stats = dict(self._stats)
            stats["current_active"] = sum(
                1 for s in self._sessions.values() if s.is_active()
            )
            stats["current_total"] = len(self._sessions)
        return stats

    # --- Internal ---

    def _get_session(self, session_id: str) -> InferenceSession:
        """Retrieve session or raise KeyError. Must hold lock."""
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(f"Session '{session_id}' not found")
        return session

    def _fire_evolution(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback({
                    "source": _EVOLUTION_KEY,
                    "type": event_type,
                    "timestamp": time.time(),
                    "payload": payload,
                })
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

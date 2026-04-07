"""
SessionManager — Game session lifecycle as a standalone TimerComponent.
========================================================================
lolbot-HyperAI · Runtime Layer

Extracts the session state machine (IDLE → PRE_GAME → IN_GAME →
POST_GAME → EVOLVING) from main_loop.py into a dedicated component
that publishes `/lol/session_state` for other components to react to.

Architecture position:
    runtime/session_manager.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (GameSnapshot for game time/phase)
    ├─ Reads: /lol/canbus_status (for LCU connection state)
    ├─ Publishes: /lol/session_state (SessionStateMsg)
    ├─ Publishes: /lol/voice_command (game start/end announcements)
    └─ Consumed by: evolution, main_loop, dreamview

Apollo reference:
    modules/monitor/monitor.cc — system-level state monitoring

Design notes:
    - Decouples session lifecycle from main_loop so components can
      independently react to game phase changes
    - 2Hz check rate (500ms) — fast enough for lobby→game transitions
    - Publishes every transition plus periodic heartbeat (every 10s)
    - Session ID = timestamp-based unique identifier
    - Post-game callback mechanism for evolution integration
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import Status, StatusMessage
from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    VoiceCommand,
)

logger = get_logger("session")

_SESSION_INTERVAL_MS = 500.0  # 2Hz
_WARN_THRESHOLD_MS = 400.0
_HEARTBEAT_INTERVAL_S = 10.0


class SessionPhase(Enum):
    IDLE = "idle"
    PRE_GAME = "pre_game"
    IN_GAME = "in_game"
    POST_GAME = "post_game"
    EVOLVING = "evolving"


# Map GamePhase enum values to SessionPhase
_PHASE_MAPPING = {
    GamePhase.LOADING: SessionPhase.PRE_GAME,
    GamePhase.EARLY: SessionPhase.IN_GAME,
    GamePhase.MID: SessionPhase.IN_GAME,
    GamePhase.LATE: SessionPhase.IN_GAME,
    GamePhase.ENDING: SessionPhase.IN_GAME,
    GamePhase.POST_GAME: SessionPhase.POST_GAME,
}


@dataclass(frozen=True)
class SessionStateMsg:
    """Published on /lol/session_state on every transition and heartbeat."""
    phase: str = "idle"
    session_id: str = ""
    game_time: float = 0.0
    session_duration_s: float = 0.0
    transition_count: int = 0
    timestamp: float = field(default_factory=time.time)
    is_heartbeat: bool = False


class SessionManager(TimerComponent):
    """Manages game session lifecycle as a first-class component.

    Each Proc():
        1. Read game state to determine current game phase
        2. Map to session phase
        3. Detect transitions
        4. Publish session state
        5. Fire callbacks on transitions
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="session_manager",
                interval_ms=_SESSION_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        self._game_state_reader: Optional[Reader] = None
        self._session_writer: Optional[Writer] = None
        self._voice_writer: Optional[Writer] = None
        self._status_writer: Optional[Writer] = None

        self._phase: SessionPhase = SessionPhase.IDLE
        self._session_id: str = ""
        self._session_start_time: float = 0.0
        self._game_time: float = 0.0
        self._transition_count: int = 0
        self._last_heartbeat: float = 0.0
        self._proc_count: int = 0

        # Callbacks for integration with evolution, etc.
        self._on_game_start: List[Callable[[str], None]] = []
        self._on_game_end: List[Callable[[str, float], None]] = []
        self._on_post_game: List[Callable[[str], None]] = []

        # Claude17: session history
        self._session_history: List["SessionRecord"] = []
        self._total_sessions: int = 0
        self._total_game_time_s: float = 0.0
        self._pause_count: int = 0
        self._pause_start_time: float = 0.0
        self._total_pause_time_s: float = 0.0
        self._phase_durations: Dict[str, float] = {}
        self._phase_enter_time: float = 0.0
        self._on_session_record: List[
            Callable[["SessionRecord"], None]
        ] = []

    def register_game_start_callback(self, cb: Callable[[str], None]) -> None:
        """Register callback(session_id) for game start."""
        self._on_game_start.append(cb)

    def register_game_end_callback(self, cb: Callable[[str, float], None]) -> None:
        """Register callback(session_id, duration_s) for game end."""
        self._on_game_end.append(cb)

    def register_post_game_callback(self, cb: Callable[[str], None]) -> None:
        """Register callback(session_id) for post-game completion."""
        self._on_post_game.append(cb)

    def Init(self) -> bool:
        logger.info("Initializing SessionManager...")

        self._node = CyberNode("session_manager")

        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", object, pending_queue_size=4,
        )
        self._session_writer = self._node.CreateWriter(
            "/lol/session_state", SessionStateMsg,
        )
        self._voice_writer = self._node.CreateWriter(
            "/lol/voice_command", VoiceCommand,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/session_status", StatusMessage,
        )

        logger.info("SessionManager initialized")
        return True

    def Proc(self) -> bool:
        self._proc_count += 1
        now = time.time()

        # ── Read game state ──────────────────────────────────────────
        self._game_state_reader.Observe()
        snapshot = self._game_state_reader.GetLatestObserved()

        new_phase = self._determine_phase(snapshot)

        # ── Detect transition ────────────────────────────────────────
        if new_phase != self._phase:
            self._handle_transition(self._phase, new_phase, now)

        # ── Update game time ─────────────────────────────────────────
        if snapshot and hasattr(snapshot, 'game_time'):
            self._game_time = snapshot.game_time

        # ── Heartbeat ────────────────────────────────────────────────
        if now - self._last_heartbeat >= _HEARTBEAT_INTERVAL_S:
            self._publish_state(is_heartbeat=True)
            self._last_heartbeat = now

        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    def _determine_phase(self, snapshot: Optional[Any]) -> SessionPhase:
        """Determine session phase from game snapshot."""
        if snapshot is None:
            if self._phase == SessionPhase.IN_GAME:
                # Lost connection during game — don't immediately transition
                return SessionPhase.IN_GAME
            return SessionPhase.IDLE

        if not hasattr(snapshot, 'phase'):
            return self._phase

        game_phase = snapshot.phase
        return _PHASE_MAPPING.get(game_phase, SessionPhase.IDLE)

    def _handle_transition(
        self, old: SessionPhase, new: SessionPhase, now: float
    ) -> None:
        """Handle a session phase transition."""
        self._phase = new
        self._transition_count += 1

        logger.info("Session: %s → %s", old.value, new.value)

        if new == SessionPhase.IN_GAME and old != SessionPhase.IN_GAME:
            self._session_id = f"session_{int(now)}"
            self._session_start_time = now
            logger.info("Game started: %s", self._session_id)

            if self._voice_writer:
                self._voice_writer.Write(VoiceCommand(
                    text="Game started. Good luck, have fun!",
                    priority=2,
                    max_age_s=10.0,
                    game_time=0.0,
                    source_module="session_manager",
                ))

            for cb in self._on_game_start:
                try:
                    cb(self._session_id)
                except Exception as exc:
                    logger.error("Game start callback error: %s", exc)

        elif new == SessionPhase.POST_GAME and old == SessionPhase.IN_GAME:
            duration = now - self._session_start_time if self._session_start_time > 0 else 0
            logger.info("Game ended: %s (%.0fs)", self._session_id, duration)

            if self._voice_writer:
                self._voice_writer.Write(VoiceCommand(
                    text="Game over. Analyzing performance.",
                    priority=3,
                    max_age_s=15.0,
                    game_time=self._game_time,
                    source_module="session_manager",
                ))

            for cb in self._on_game_end:
                try:
                    cb(self._session_id, duration)
                except Exception as exc:
                    logger.error("Game end callback error: %s", exc)

        elif new == SessionPhase.IDLE and old == SessionPhase.POST_GAME:
            for cb in self._on_post_game:
                try:
                    cb(self._session_id)
                except Exception as exc:
                    logger.error("Post-game callback error: %s", exc)

        self._publish_state(is_heartbeat=False)

    def _publish_state(self, is_heartbeat: bool) -> None:
        if not self._session_writer:
            return

        duration = 0.0
        if self._session_start_time > 0 and self._phase in (
            SessionPhase.IN_GAME, SessionPhase.POST_GAME
        ):
            duration = time.time() - self._session_start_time

        msg = SessionStateMsg(
            phase=self._phase.value,
            session_id=self._session_id,
            game_time=self._game_time,
            session_duration_s=round(duration, 1),
            transition_count=self._transition_count,
            is_heartbeat=is_heartbeat,
        )
        self._session_writer.Write(msg)

    @property
    def current_phase(self) -> SessionPhase:
        return self._phase

    @property
    def current_session_id(self) -> str:
        return self._session_id

    def session_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "phase": self._phase.value,
            "session_id": self._session_id,
            "game_time": self._game_time,
            "transitions": self._transition_count,
            # Claude17: extended session stats
            "total_sessions": self._total_sessions,
            "total_game_time_s": round(self._total_game_time_s, 1),
            "avg_session_duration_s": round(
                self._total_game_time_s / max(self._total_sessions, 1), 1
            ),
            "session_history_count": len(self._session_history),
            "pause_count": self._pause_count,
        })
        return base

    # ─── Claude17: Session History & Statistics ──────────────────────────

    def __init_history(self) -> None:
        """Initialize session history tracking. Called during __init__."""
        self._session_history: List[SessionRecord] = []
        self._total_sessions: int = 0
        self._total_game_time_s: float = 0.0
        self._pause_count: int = 0
        self._pause_start_time: float = 0.0
        self._total_pause_time_s: float = 0.0
        self._phase_durations: Dict[str, float] = {}
        self._phase_enter_time: float = 0.0
        self._on_session_record: List[
            Callable[["SessionRecord"], None]
        ] = []

    def register_session_record_callback(
        self, cb: Callable[["SessionRecord"], None]
    ) -> None:
        """Register callback for completed session records.

        Useful for evolution fitness evaluation.
        """
        self._on_session_record.append(cb)

    def get_session_history(
        self, last_n: int = 10
    ) -> List["SessionRecord"]:
        """Return the last N completed session records."""
        return self._session_history[-last_n:]

    def get_session_statistics(self) -> Dict[str, Any]:
        """Aggregate statistics across all sessions.

        Returns:
            Dict with totals, averages, and distributions.
        """
        if not self._session_history:
            return {
                "total_sessions": 0,
                "total_game_time_s": 0.0,
                "avg_duration_s": 0.0,
                "min_duration_s": 0.0,
                "max_duration_s": 0.0,
            }

        durations = [s.duration_s for s in self._session_history]
        return {
            "total_sessions": len(self._session_history),
            "total_game_time_s": round(sum(durations), 1),
            "avg_duration_s": round(
                sum(durations) / len(durations), 1
            ),
            "min_duration_s": round(min(durations), 1),
            "max_duration_s": round(max(durations), 1),
            "phase_durations": {
                k: round(v, 1)
                for k, v in self._phase_durations.items()
            },
        }

    def pause_session(self) -> None:
        """Manually pause the current session tracking.

        Claude17: Used by operator to pause during AFK or interruption.
        Does NOT stop component Proc() — only pauses session timing.
        """
        if self._phase != SessionPhase.IN_GAME:
            return
        self._pause_start_time = time.time()
        self._pause_count += 1
        logger.info("Session paused: %s", self._session_id)

    def resume_session(self) -> None:
        """Resume a paused session."""
        if self._pause_start_time > 0:
            pause_duration = time.time() - self._pause_start_time
            self._total_pause_time_s += pause_duration
            self._pause_start_time = 0.0
            logger.info(
                "Session resumed: %s (paused %.1fs)",
                self._session_id, pause_duration,
            )


@dataclass
class SessionRecord:
    """Completed session record stored in history.

    Claude17: Enables post-hoc analysis of session patterns,
    duration trends, and phase distributions.
    """
    session_id: str
    start_time: float
    end_time: float
    duration_s: float
    game_time_at_end: float
    transition_count: int
    pause_count: int = 0
    total_pause_time_s: float = 0.0
    outcome: str = "unknown"  # win/loss/remake/unknown
    timestamp: float = field(default_factory=time.time)

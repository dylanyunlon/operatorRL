#!/usr/bin/env python3
"""
M889 — GameFlowStateEngine
============================
Full game lifecycle state machine: Lobby→Queue→ChampSelect→InProgress→PostGame.
Follows Seraphine connector.py onGameFlowPhaseChanged pattern. Each phase
transition activates/deactivates downstream modules accordingly.

Dependencies: M888
Reference: Seraphine connector.py::onGameFlowPhaseChanged
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("M889.GameFlowStateEngine")


class GameFlowPhase(Enum):
    """All possible game flow phases from LCU API."""
    NONE = "None"
    LOBBY = "Lobby"
    MATCHMAKING = "Matchmaking"
    READY_CHECK = "ReadyCheck"
    CHAMP_SELECT = "ChampSelect"
    GAME_START = "GameStart"
    IN_PROGRESS = "InProgress"
    RECONNECT = "Reconnect"
    WAITING_FOR_STATS = "WaitingForStats"
    PRE_END_OF_GAME = "PreEndOfGame"
    END_OF_GAME = "EndOfGame"
    TERMINATED_IN_ERROR = "TerminatedInError"

    @property
    def is_active_game(self) -> bool:
        return self in (
            GameFlowPhase.IN_PROGRESS,
            GameFlowPhase.RECONNECT,
        )

    @property
    def is_pre_game(self) -> bool:
        return self in (
            GameFlowPhase.LOBBY,
            GameFlowPhase.MATCHMAKING,
            GameFlowPhase.READY_CHECK,
            GameFlowPhase.CHAMP_SELECT,
            GameFlowPhase.GAME_START,
        )

    @property
    def is_post_game(self) -> bool:
        return self in (
            GameFlowPhase.WAITING_FOR_STATS,
            GameFlowPhase.PRE_END_OF_GAME,
            GameFlowPhase.END_OF_GAME,
        )


@dataclass
class PhaseTransition:
    """Record of a single phase transition."""
    from_phase: GameFlowPhase
    to_phase: GameFlowPhase
    timestamp: datetime
    duration_in_previous: float  # seconds spent in previous phase
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from": self.from_phase.value,
            "to": self.to_phase.value,
            "timestamp": self.timestamp.isoformat(),
            "duration_seconds": round(self.duration_in_previous, 2),
            "metadata": self.metadata,
        }


@dataclass
class GameSession:
    """Tracks a complete game session from Lobby to EndOfGame."""
    session_id: str
    queue_type: str = ""
    map_id: int = 0
    game_id: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    transitions: List[PhaseTransition] = field(default_factory=list)
    final_phase: GameFlowPhase = GameFlowPhase.NONE

    @property
    def duration(self) -> Optional[float]:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def game_duration(self) -> Optional[float]:
        """Duration of the InProgress phase only."""
        for i, t in enumerate(self.transitions):
            if t.to_phase == GameFlowPhase.IN_PROGRESS:
                for j in range(i + 1, len(self.transitions)):
                    if self.transitions[j].from_phase == GameFlowPhase.IN_PROGRESS:
                        return self.transitions[j].duration_in_previous
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "queue_type": self.queue_type,
            "game_id": self.game_id,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "total_duration": self.duration,
            "game_duration": self.game_duration,
            "transitions": [t.to_dict() for t in self.transitions],
            "final_phase": self.final_phase.value,
        }


# ---------------------------------------------------------------------------
# Transition validation matrix: which transitions are legal
# ---------------------------------------------------------------------------
VALID_TRANSITIONS: Dict[GameFlowPhase, Set[GameFlowPhase]] = {
    GameFlowPhase.NONE: {
        GameFlowPhase.LOBBY, GameFlowPhase.RECONNECT,
    },
    GameFlowPhase.LOBBY: {
        GameFlowPhase.MATCHMAKING, GameFlowPhase.NONE,
    },
    GameFlowPhase.MATCHMAKING: {
        GameFlowPhase.READY_CHECK, GameFlowPhase.LOBBY, GameFlowPhase.NONE,
    },
    GameFlowPhase.READY_CHECK: {
        GameFlowPhase.CHAMP_SELECT, GameFlowPhase.MATCHMAKING,
        GameFlowPhase.LOBBY, GameFlowPhase.NONE,
    },
    GameFlowPhase.CHAMP_SELECT: {
        GameFlowPhase.GAME_START, GameFlowPhase.LOBBY, GameFlowPhase.NONE,
    },
    GameFlowPhase.GAME_START: {
        GameFlowPhase.IN_PROGRESS, GameFlowPhase.CHAMP_SELECT,
    },
    GameFlowPhase.IN_PROGRESS: {
        GameFlowPhase.WAITING_FOR_STATS, GameFlowPhase.RECONNECT,
        GameFlowPhase.TERMINATED_IN_ERROR,
    },
    GameFlowPhase.RECONNECT: {
        GameFlowPhase.IN_PROGRESS, GameFlowPhase.NONE,
        GameFlowPhase.WAITING_FOR_STATS,
    },
    GameFlowPhase.WAITING_FOR_STATS: {
        GameFlowPhase.PRE_END_OF_GAME, GameFlowPhase.END_OF_GAME,
    },
    GameFlowPhase.PRE_END_OF_GAME: {
        GameFlowPhase.END_OF_GAME,
    },
    GameFlowPhase.END_OF_GAME: {
        GameFlowPhase.LOBBY, GameFlowPhase.NONE,
    },
    GameFlowPhase.TERMINATED_IN_ERROR: {
        GameFlowPhase.NONE, GameFlowPhase.LOBBY,
    },
}


class ModuleActivationPolicy:
    """
    Defines which modules should be active in which phases.
    Modules register themselves with required phases, and the engine
    activates/deactivates them on phase transitions.
    """

    def __init__(self):
        self._policies: Dict[str, Set[GameFlowPhase]] = {}
        self._active_modules: Set[str] = set()

    def register(self, module_name: str, active_phases: Set[GameFlowPhase]):
        self._policies[module_name] = active_phases

    def compute_changes(self, new_phase: GameFlowPhase) -> Tuple[Set[str], Set[str]]:
        """Returns (modules_to_activate, modules_to_deactivate)."""
        should_be_active = set()
        for mod_name, phases in self._policies.items():
            if new_phase in phases:
                should_be_active.add(mod_name)

        to_activate = should_be_active - self._active_modules
        to_deactivate = self._active_modules - should_be_active
        self._active_modules = should_be_active
        return to_activate, to_deactivate

    @property
    def active_modules(self) -> Set[str]:
        return self._active_modules.copy()


class GameFlowStateEngine:
    """
    Central game flow state machine for the entire M886-M905 system.

    Lifecycle mirrors Seraphine connector:
      start() → subscribe to LCU gameflow events → process transitions
      → emit module activation/deactivation signals → stop()

    All other M886-M905 modules register with this engine to receive
    phase-appropriate activation signals. For example:
      - M888 ChampSelectPhaseTracker: active during CHAMP_SELECT
      - M891 LiveGameDataBridge: active during IN_PROGRESS
      - M895 RealTimeKDATracker: active during IN_PROGRESS
      - M899 PostGameAnalysisGenerator: active during END_OF_GAME
    """

    def __init__(self):
        self._current_phase = GameFlowPhase.NONE
        self._phase_entered_at: float = time.monotonic()
        self._current_session: Optional[GameSession] = None
        self._session_history: List[GameSession] = []
        self._activation_policy = ModuleActivationPolicy()
        self._listeners: Dict[str, List[Callable]] = collections.defaultdict(list)
        self._shutdown = asyncio.Event()
        self._poll_task: Optional[asyncio.Task] = None
        self._session_counter = 0
        self._stats = {
            "total_transitions": 0,
            "invalid_transitions": 0,
            "sessions_completed": 0,
            "games_played": 0,
            "total_game_time_seconds": 0.0,
        }
        logger.info("GameFlowStateEngine initialized")

    @property
    def current_phase(self) -> GameFlowPhase:
        return self._current_phase

    @property
    def current_session(self) -> Optional[GameSession]:
        return self._current_session

    @property
    def activation_policy(self) -> ModuleActivationPolicy:
        return self._activation_policy

    def on(self, event: str, callback: Callable):
        """Register event listener (Seraphine subscribe pattern)."""
        self._listeners[event].append(callback)

    async def _emit(self, event: str, data: Any = None):
        for cb in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)
            except Exception as exc:
                logger.error("Listener error for '%s': %s", event, exc)

    async def start(self):
        self._shutdown.clear()
        self._poll_task = asyncio.create_task(
            self._monitor_loop(), name="gameflow-monitor"
        )
        logger.info("GameFlowStateEngine started")

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        if self._current_session:
            self._finalize_session()
        logger.info("GameFlowStateEngine stopped. Stats: %s", self._stats)

    async def _monitor_loop(self):
        """Monitor LCU gameflow phase changes."""
        while not self._shutdown.is_set():
            try:
                new_phase = await self._fetch_current_phase()
                if new_phase != self._current_phase:
                    await self._transition_to(new_phase)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Monitor loop error: %s", exc)
            await asyncio.sleep(0.5)

    async def _fetch_current_phase(self) -> GameFlowPhase:
        """Fetch current phase from LCU or Fiddler intercepts."""
        # In production: GET /lol-gameflow/v1/gameflow-phase via LCU
        # Here we return current phase for the engine loop
        return self._current_phase

    async def transition_to(self, phase_str: str, metadata: Optional[Dict] = None):
        """Public API for external phase updates (from LCU events)."""
        try:
            new_phase = GameFlowPhase(phase_str)
        except ValueError:
            logger.warning("Unknown phase: %s", phase_str)
            return
        if new_phase != self._current_phase:
            await self._transition_to(new_phase, metadata)

    async def _transition_to(
        self, new_phase: GameFlowPhase, metadata: Optional[Dict] = None
    ):
        """Execute a phase transition with validation and side effects."""
        old_phase = self._current_phase
        now = time.monotonic()
        duration = now - self._phase_entered_at

        # Validate transition
        valid_targets = VALID_TRANSITIONS.get(old_phase, set())
        if new_phase not in valid_targets:
            self._stats["invalid_transitions"] += 1
            logger.warning(
                "Unexpected transition: %s → %s (expected one of %s)",
                old_phase.value, new_phase.value,
                [p.value for p in valid_targets],
            )
            # Allow it anyway — the game client is authoritative

        transition = PhaseTransition(
            from_phase=old_phase,
            to_phase=new_phase,
            timestamp=datetime.now(timezone.utc),
            duration_in_previous=duration,
            metadata=metadata or {},
        )

        # Session management
        if new_phase == GameFlowPhase.LOBBY and old_phase == GameFlowPhase.NONE:
            self._start_new_session()
        elif new_phase == GameFlowPhase.IN_PROGRESS:
            self._stats["games_played"] += 1
        elif new_phase == GameFlowPhase.END_OF_GAME:
            if old_phase.is_active_game or old_phase.is_post_game:
                game_dur = self._current_session.game_duration if self._current_session else duration
                self._stats["total_game_time_seconds"] += game_dur or duration
        elif new_phase == GameFlowPhase.NONE and self._current_session:
            self._finalize_session()

        if self._current_session:
            self._current_session.transitions.append(transition)

        # Update state
        self._current_phase = new_phase
        self._phase_entered_at = now
        self._stats["total_transitions"] += 1

        # Module activation
        to_activate, to_deactivate = self._activation_policy.compute_changes(new_phase)
        if to_deactivate:
            logger.info("Deactivating modules: %s", to_deactivate)
            await self._emit("modules_deactivate", list(to_deactivate))
        if to_activate:
            logger.info("Activating modules: %s", to_activate)
            await self._emit("modules_activate", list(to_activate))

        # Emit phase event
        await self._emit("phase_changed", {
            "transition": transition.to_dict(),
            "active_modules": list(self._activation_policy.active_modules),
        })

        # Phase-specific events
        event_map = {
            GameFlowPhase.CHAMP_SELECT: "champ_select_entered",
            GameFlowPhase.IN_PROGRESS: "game_started",
            GameFlowPhase.END_OF_GAME: "game_ended",
            GameFlowPhase.LOBBY: "returned_to_lobby",
        }
        specific_event = event_map.get(new_phase)
        if specific_event:
            await self._emit(specific_event, transition.to_dict())

        logger.info(
            "Phase transition: %s → %s (%.1fs in %s)",
            old_phase.value, new_phase.value, duration, old_phase.value,
        )

    def _start_new_session(self):
        """Initialize a new game session."""
        self._session_counter += 1
        self._current_session = GameSession(
            session_id=f"session-{self._session_counter}-{int(time.time())}",
            start_time=datetime.now(timezone.utc),
        )
        logger.info("New session: %s", self._current_session.session_id)

    def _finalize_session(self):
        """Finalize and archive the current session."""
        if self._current_session:
            self._current_session.end_time = datetime.now(timezone.utc)
            self._current_session.final_phase = self._current_phase
            self._session_history.append(self._current_session)
            self._stats["sessions_completed"] += 1

            if len(self._session_history) > 50:
                self._session_history = self._session_history[-25:]

            logger.info(
                "Session finalized: %s (%.1fs)",
                self._current_session.session_id,
                self._current_session.duration or 0,
            )
            self._current_session = None

    def register_module(self, name: str, active_phases: Set[GameFlowPhase]):
        """Register a module with its activation phases."""
        self._activation_policy.register(name, active_phases)
        logger.debug("Module registered: %s → %s",
                      name, [p.value for p in active_phases])

    def get_session_history(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._session_history]

    def export_stats(self) -> Dict[str, Any]:
        return {
            "engine_stats": self._stats,
            "current_phase": self._current_phase.value,
            "active_modules": list(self._activation_policy.active_modules),
            "session_active": self._current_session is not None,
            "sessions_in_history": len(self._session_history),
        }



# ---------------------------------------------------------------------------
# Extended GameFlowStateEngine utilities
# ---------------------------------------------------------------------------

class PhaseTimingAnalyzer:
    """Analyzes time spent in each game flow phase across sessions."""

    def __init__(self):
        self._phase_durations: Dict[str, List[float]] = collections.defaultdict(list)

    def record(self, phase: GameFlowPhase, duration: float):
        self._phase_durations[phase.value].append(duration)

    def average_duration(self, phase: GameFlowPhase) -> float:
        durations = self._phase_durations.get(phase.value, [])
        return sum(durations) / len(durations) if durations else 0.0

    def total_time_in_phase(self, phase: GameFlowPhase) -> float:
        return sum(self._phase_durations.get(phase.value, []))

    def longest_session(self) -> Tuple[str, float]:
        longest_phase = ""
        longest_dur = 0.0
        for phase, durs in self._phase_durations.items():
            m = max(durs) if durs else 0
            if m > longest_dur:
                longest_dur = m
                longest_phase = phase
        return longest_phase, longest_dur

    def report(self) -> Dict[str, Any]:
        result = {}
        for phase, durs in self._phase_durations.items():
            result[phase] = {
                "count": len(durs),
                "avg_seconds": round(sum(durs) / len(durs), 2) if durs else 0,
                "total_seconds": round(sum(durs), 2),
                "max_seconds": round(max(durs), 2) if durs else 0,
                "min_seconds": round(min(durs), 2) if durs else 0,
            }
        return result


class GameFlowRecoveryManager:
    """Handles recovery from unexpected disconnections during gameplay."""

    def __init__(self, engine: GameFlowStateEngine):
        self._engine = engine
        self._recovery_attempts = 0
        self._successful_recoveries = 0
        self._last_known_phase = GameFlowPhase.NONE
        self._disconnect_timestamps: List[float] = []

    async def handle_disconnect(self):
        """Handle a client disconnection event."""
        self._recovery_attempts += 1
        self._last_known_phase = self._engine.current_phase
        self._disconnect_timestamps.append(time.monotonic())
        logger.warning("Disconnect detected in phase: %s (attempt #%d)",
                       self._last_known_phase.value, self._recovery_attempts)

        if self._last_known_phase.is_active_game:
            await self._engine.transition_to("Reconnect", {
                "reason": "disconnect",
                "previous_phase": self._last_known_phase.value,
            })

    async def handle_reconnect(self, current_phase_str: str):
        """Handle successful reconnection."""
        self._successful_recoveries += 1
        logger.info("Reconnected successfully to phase: %s", current_phase_str)
        await self._engine.transition_to(current_phase_str, {
            "reason": "reconnect",
            "previous_phase": self._last_known_phase.value,
            "downtime_seconds": self._get_downtime(),
        })

    def _get_downtime(self) -> float:
        if len(self._disconnect_timestamps) >= 1:
            return time.monotonic() - self._disconnect_timestamps[-1]
        return 0.0

    def stats(self) -> Dict[str, Any]:
        return {
            "recovery_attempts": self._recovery_attempts,
            "successful": self._successful_recoveries,
            "disconnects": len(self._disconnect_timestamps),
            "last_known_phase": self._last_known_phase.value,
        }


class SessionSerializer:
    """Serializes game sessions to JSON for persistence via M904."""

    @staticmethod
    def serialize(session: GameSession) -> str:
        return json.dumps(session.to_dict(), indent=2, ensure_ascii=False)

    @staticmethod
    def deserialize(data: str) -> Optional[GameSession]:
        try:
            obj = json.loads(data)
            session = GameSession(
                session_id=obj["session_id"],
                queue_type=obj.get("queue_type", ""),
                game_id=obj.get("game_id", 0),
            )
            if obj.get("start_time"):
                session.start_time = datetime.fromisoformat(obj["start_time"])
            if obj.get("end_time"):
                session.end_time = datetime.fromisoformat(obj["end_time"])
            return session
        except (json.JSONDecodeError, KeyError) as exc:
            logger.error("Deserialize error: %s", exc)
            return None

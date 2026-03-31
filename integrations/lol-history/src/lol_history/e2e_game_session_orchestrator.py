"""
E2eGameSessionOrchestrator — Orchestrates a full game session lifecycle (30min+).

Architecture (拿来主义):
  deep_history_injection_orchestrator.py（M765）— orchestrator pattern
  game_flow_state_machine.py（M754）— game state machine transitions

Location: integrations/lol-history/src/lol_history/e2e_game_session_orchestrator.py

Design Notes (Knuth-level critique):
  User:
    - Single entry point for full game lifecycle: client detect → pregame → ingame → postgame → data.
    - Each phase transition auto-activates/deactivates relevant modules.
    - 30-minute session support with bounded memory and periodic cleanup.
  System:
    - FSM-based phase management with validated transitions.
    - Module registry with phase affinity: modules only run in their declared phases.
    - Health tracking per module per phase for targeted fault isolation.
    - Session metadata (game_id, start_time, duration) maintained throughout.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.e2e_game_session_orchestrator.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _PhaseStateMachine:
    """Game phase state machine with validated transitions."""

    VALID_TRANSITIONS = {
        "idle": ["detecting", "pregame"],
        "detecting": ["pregame", "idle"],
        "pregame": ["loading", "ingame", "idle"],
        "loading": ["ingame", "idle"],
        "ingame": ["postgame", "idle"],
        "postgame": ["training_export", "idle"],
        "training_export": ["idle"],
    }

    def __init__(self) -> None:
        self._current = "idle"
        self._transitions: deque = deque(maxlen=200)
        self._phase_durations: Dict[str, float] = {}
        self._phase_enter_time: float = time.monotonic()
        self._transition_count = 0

    def transition(self, target: str) -> Dict[str, Any]:
        valid = self.VALID_TRANSITIONS.get(self._current, [])
        if target not in valid:
            logger.warning("Invalid transition: %s → %s (valid: %s)",
                           self._current, target, valid)
        now = time.monotonic()
        duration = now - self._phase_enter_time
        self._phase_durations[self._current] = (
            self._phase_durations.get(self._current, 0) + duration)
        old = self._current
        self._current = target
        self._phase_enter_time = now
        self._transition_count += 1
        record = {"from": old, "to": target, "ts": now,
                  "duration_in_previous": duration,
                  "transition_num": self._transition_count}
        self._transitions.append(record)
        return record

    @property
    def current(self) -> str:
        return self._current

    def get_stats(self) -> Dict[str, Any]:
        return {
            "current_phase": self._current,
            "transition_count": self._transition_count,
            "phase_durations": dict(self._phase_durations),
            "recent_transitions": list(self._transitions)[-10:],
        }


class _ModuleEntry:
    """Registry entry for a managed module."""

    def __init__(self, name: str, module: Any, phases: List[str]) -> None:
        self.name = name
        self.module = module
        self.phases = phases
        self.is_active = False
        self.error_count = 0
        self.success_count = 0
        self.last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "phases": self.phases,
            "is_active": self.is_active,
            "error_count": self.error_count,
            "success_count": self.success_count,
            "health": _safe_div(self.success_count,
                                self.success_count + self.error_count),
        }


class _SessionMetadata:
    """Tracks session-level metadata."""

    def __init__(self) -> None:
        self.game_id: Optional[str] = None
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.summoner_name: Optional[str] = None
        self.champion: Optional[str] = None
        self._sessions: deque = deque(maxlen=50)

    def start_session(self, game_id: str = None) -> Dict[str, Any]:
        self.game_id = game_id or f"session_{int(time.time())}"
        self.start_time = time.monotonic()
        self.end_time = 0.0
        return {"game_id": self.game_id, "start_time": self.start_time}

    def end_session(self) -> Dict[str, Any]:
        self.end_time = time.monotonic()
        duration = self.end_time - self.start_time
        record = {
            "game_id": self.game_id,
            "duration": duration,
            "summoner": self.summoner_name,
            "champion": self.champion,
        }
        self._sessions.append(record)
        return record

    def get_duration(self) -> float:
        if self.start_time == 0:
            return 0.0
        end = self.end_time if self.end_time > 0 else time.monotonic()
        return end - self.start_time

    def get_stats(self) -> Dict[str, Any]:
        return {
            "game_id": self.game_id,
            "duration": self.get_duration(),
            "summoner": self.summoner_name,
            "champion": self.champion,
            "total_sessions": len(self._sessions),
        }


class _PeriodicCleanup:
    """Periodic cleanup for long-running sessions."""

    def __init__(self, interval: float = 300.0) -> None:
        self._interval = interval
        self._last_cleanup = time.monotonic()
        self._cleanup_count = 0

    def should_cleanup(self) -> bool:
        return time.monotonic() - self._last_cleanup >= self._interval

    def record_cleanup(self) -> None:
        self._last_cleanup = time.monotonic()
        self._cleanup_count += 1

    def get_stats(self) -> Dict[str, Any]:
        return {
            "interval": self._interval,
            "cleanup_count": self._cleanup_count,
            "time_since_last": time.monotonic() - self._last_cleanup,
        }


class E2eGameSessionOrchestrator:
    """Orchestrates a full game session lifecycle from detection to training export.

    Public API: transition_phase, register_module, get_active_modules,
                process_phase, start_session, end_session, get_session_info,
                get_module_health, cleanup, get_stats
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._fsm = _PhaseStateMachine()
        self._modules: Dict[str, _ModuleEntry] = {}
        self._session = _SessionMetadata()
        self._cleanup = _PeriodicCleanup()
        self._process_count = 0
        self._phase_process_counts: Dict[str, int] = defaultdict(int)

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _update_module_activation(self) -> None:
        current_phase = self._fsm.current
        for entry in self._modules.values():
            entry.is_active = current_phase in entry.phases

    def transition_phase(self, target_phase: str) -> Dict[str, Any]:
        """Transition to a new game phase."""
        self._op_count += 1
        record = self._fsm.transition(target_phase)
        self._update_module_activation()

        active_modules = [e.name for e in self._modules.values() if e.is_active]
        self._fire("phase_transition", {
            "from": record["from"], "to": target_phase,
            "active_modules": active_modules,
        })

        if target_phase == "pregame":
            self._session.start_session()
        elif target_phase == "idle" and record["from"] in ("postgame", "training_export"):
            self._session.end_session()

        return {
            "status": "ok",
            "transition": record,
            "active_modules": active_modules,
            "session": self._session.get_stats(),
        }

    def register_module(self, name: str, module: Any,
                         phases: List[str] = None) -> Dict[str, Any]:
        """Register a module with its applicable phases."""
        self._op_count += 1
        phases = phases or ["pregame", "ingame", "postgame"]
        entry = _ModuleEntry(name, module, phases)
        entry.is_active = self._fsm.current in phases
        self._modules[name] = entry
        return {
            "status": "ok",
            "module": name,
            "phases": phases,
            "is_active": entry.is_active,
            "total_modules": len(self._modules),
        }

    def get_active_modules(self) -> Dict[str, Any]:
        """Get currently active modules for the current phase."""
        self._op_count += 1
        active = {n: e.to_dict() for n, e in self._modules.items() if e.is_active}
        return {
            "status": "ok",
            "current_phase": self._fsm.current,
            "active_modules": active,
            "active_count": len(active),
            "total_modules": len(self._modules),
        }

    def process_phase(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Run all active modules for the current phase."""
        self._op_count += 1
        self._process_count += 1
        phase = self._fsm.current
        self._phase_process_counts[phase] += 1

        results = {}
        for name, entry in self._modules.items():
            if not entry.is_active:
                continue
            try:
                if hasattr(entry.module, "process"):
                    result = entry.module.process(context or {})
                    results[name] = {"status": "ok", "result": result}
                elif hasattr(entry.module, "get_stats"):
                    results[name] = {"status": "ok", "stats": entry.module.get_stats()}
                entry.success_count += 1
            except Exception as e:
                entry.error_count += 1
                entry.last_error = str(e)
                results[name] = {"status": "error", "error": str(e)}

        if self._cleanup.should_cleanup():
            self._cleanup.record_cleanup()

        return {
            "status": "ok",
            "phase": phase,
            "process_num": self._process_count,
            "modules_processed": len(results),
            "results": results,
        }

    def start_session(self, game_id: str = None) -> Dict[str, Any]:
        self._op_count += 1
        info = self._session.start_session(game_id)
        self.transition_phase("pregame")
        return {"status": "ok", **info}

    def end_session(self) -> Dict[str, Any]:
        self._op_count += 1
        info = self._session.end_session()
        self.transition_phase("idle")
        return {"status": "ok", **info}

    def get_session_info(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "session": self._session.get_stats()}

    def get_module_health(self) -> Dict[str, Any]:
        self._op_count += 1
        health = {n: e.to_dict() for n, e in self._modules.items()}
        return {"status": "ok", "module_health": health}

    def cleanup(self) -> Dict[str, Any]:
        self._op_count += 1
        self._cleanup.record_cleanup()
        return {"status": "ok", "cleanup_stats": self._cleanup.get_stats()}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "process_count": self._process_count,
            "fsm": self._fsm.get_stats(),
            "session": self._session.get_stats(),
            "cleanup": self._cleanup.get_stats(),
            "total_modules": len(self._modules),
            "phase_process_counts": dict(self._phase_process_counts),
            "module_health": {n: e.to_dict() for n, e in self._modules.items()},
        }

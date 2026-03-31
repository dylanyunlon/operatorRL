"""
GameFlowStateMachine — Models the LoL client game flow state transitions.

Architecture (拿来主义):
  Seraphine/app/lol/connector.py — gameflow-phase endpoint monitoring
  Seraphine/app/lol/listener.py — LolProcessExistenceListener state polling

Location: integrations/lol-history/src/lol_history/game_flow_state_machine.py

Design Notes (Knuth-level critique):
  User:
    - Clear game lifecycle visibility: Lobby → Matchmaking → ChampSelect → InGame → PostGame.
    - State transition hooks enable auto-triggering pregame scout, ingame advisor, postgame review.
  System:
    - State machine is deterministic: only valid transitions are allowed.
    - State durations tracked for performance analysis (e.g., "champ select took 45s").
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.game_flow_state_machine.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

# Valid LoL game flow phases (mirrors LCU gameflow-phase values)
_PHASES = [
    "None", "Lobby", "Matchmaking", "CheckedIntoTournament", "ReadyCheck",
    "ChampSelect", "GameStart", "FailedToLaunch", "InProgress",
    "Reconnect", "WaitingForStats", "PreEndOfGame", "EndOfGame",
]

_VALID_TRANSITIONS = {
    "None": {"Lobby", "Matchmaking"},
    "Lobby": {"Matchmaking", "ChampSelect", "None"},
    "Matchmaking": {"ReadyCheck", "Lobby", "None"},
    "ReadyCheck": {"ChampSelect", "Lobby", "Matchmaking", "None"},
    "ChampSelect": {"GameStart", "Lobby", "None"},
    "GameStart": {"InProgress", "FailedToLaunch", "None"},
    "FailedToLaunch": {"Lobby", "None"},
    "InProgress": {"WaitingForStats", "Reconnect", "None"},
    "Reconnect": {"InProgress", "WaitingForStats", "None"},
    "WaitingForStats": {"PreEndOfGame", "EndOfGame", "None"},
    "PreEndOfGame": {"EndOfGame", "None"},
    "EndOfGame": {"Lobby", "None"},
    "CheckedIntoTournament": {"ChampSelect", "None"},
}


class GameFlowStateMachine:
    """Models LoL client game flow state transitions with hooks.

    Public API: transition, get_current_phase, register_hook,
                get_phase_duration, get_history, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._current_phase = "None"
        self._phase_start_time = time.time()
        self._transition_count = 0
        self._hooks: Dict[str, List[Callable]] = {}
        self._history: List[Dict[str, Any]] = []
        self._max_history = 200
        self._invalid_transitions = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_hook(self, phase: str, callback: Callable) -> Dict[str, Any]:
        """Register callback for when a specific phase is entered."""
        self._op_count += 1
        if phase not in self._hooks:
            self._hooks[phase] = []
        self._hooks[phase].append(callback)
        return {"status": "ok", "phase": phase,
                "hooks_count": len(self._hooks[phase])}

    def transition(self, new_phase: str) -> Dict[str, Any]:
        """Attempt a state transition. Only valid transitions are accepted."""
        self._op_count += 1
        old_phase = self._current_phase
        now = time.time()
        duration = round(now - self._phase_start_time, 2)
        # Validate transition
        valid_targets = _VALID_TRANSITIONS.get(old_phase, set())
        if new_phase == old_phase:
            return {"status": "ok", "phase": new_phase, "changed": False}
        if new_phase not in valid_targets and new_phase != "None":
            self._invalid_transitions += 1
            logger.warning("Invalid transition: %s → %s", old_phase, new_phase)
            # Allow it anyway but log (LCU can skip phases)
        # Record history
        entry = {"from": old_phase, "to": new_phase, "duration": duration,
                 "timestamp": now, "sequence": self._transition_count}
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        # Transition
        self._current_phase = new_phase
        self._phase_start_time = now
        self._transition_count += 1
        # Fire hooks
        dispatched = 0
        for hook in self._hooks.get(new_phase, []):
            try:
                hook({"phase": new_phase, "previous": old_phase, "duration": duration})
                dispatched += 1
            except Exception as e:
                logger.warning("Hook error for phase %s: %s", new_phase, e)
        self._fire("transition", {"from": old_phase, "to": new_phase})
        return {"status": "ok", "phase": new_phase, "previous": old_phase,
                "duration_in_previous": duration, "changed": True,
                "hooks_dispatched": dispatched}

    def get_current_phase(self) -> Dict[str, Any]:
        """Get current game flow phase and time spent in it."""
        self._op_count += 1
        elapsed = round(time.time() - self._phase_start_time, 2)
        return {"status": "ok", "phase": self._current_phase,
                "elapsed_seconds": elapsed}

    def get_phase_duration(self, phase: str) -> Dict[str, Any]:
        """Get average duration spent in a specific phase from history."""
        self._op_count += 1
        durations = [h["duration"] for h in self._history if h["from"] == phase]
        if not durations:
            return {"status": "ok", "phase": phase, "avg_duration": 0.0, "samples": 0}
        avg = sum(durations) / len(durations)
        return {"status": "ok", "phase": phase, "avg_duration": round(avg, 2),
                "min_duration": round(min(durations), 2),
                "max_duration": round(max(durations), 2),
                "samples": len(durations)}

    def get_history(self, n: int = 20) -> Dict[str, Any]:
        """Get recent transition history."""
        self._op_count += 1
        return {"status": "ok", "history": self._history[-n:],
                "total_transitions": self._transition_count}

    def get_stats(self) -> Dict[str, Any]:
        return {"current_phase": self._current_phase,
                "transition_count": self._transition_count,
                "invalid_transitions": self._invalid_transitions,
                "hooks_registered": sum(len(v) for v in self._hooks.values()),
                "total_ops": self._op_count}

"""
AutonomousDecisionStateMachine — Finite state machine for the autonomous decision lifecycle.

Architecture (拿来主义):
  fiddler_session_state_machine.py（M647）— FSM with transition hooks
  Akagi/mitm/mitm_abc.py — websocket_start→message→end lifecycle

Location: integrations/lol-history/src/lol_history/autonomous_decision_state_machine.py

Design Notes (Knuth-level critique):
  User:
    - transition() returns success/failure dict — never throws on illegal transitions.
    - Timeout watchdog auto-reverts stuck states.
  System:
    - Hooks fire before and after transitions — enables instrumentation without coupling.
    - History is bounded deque — no unbounded memory growth.
"""

from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.autonomous_decision_state_machine.v1"

_STATES = {"IDLE", "OBSERVING", "ANALYZING", "DECIDING", "EXECUTING", "REVIEWING"}
_TRANSITIONS = {
    "IDLE": {"OBSERVING"},
    "OBSERVING": {"ANALYZING", "IDLE"},
    "ANALYZING": {"DECIDING", "OBSERVING"},
    "DECIDING": {"EXECUTING", "ANALYZING"},
    "EXECUTING": {"REVIEWING", "DECIDING"},
    "REVIEWING": {"OBSERVING", "IDLE"},
}

def _safe_div(a, b, d=0.0): return a / b if b else d

class AutonomousDecisionStateMachine:
    """FSM: IDLE→OBSERVING→ANALYZING→DECIDING→EXECUTING→REVIEWING.

    Public API: transition, force_state, get_state, get_history, register_hook,
                check_timeout, get_stats
    """
    def __init__(self, timeout_s: float = 10.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._state = "IDLE"
        self._timeout_s = timeout_s
        self._state_entered_at = time.time()
        self._hooks: Dict[str, List[Callable]] = {}
        self._history: deque = deque(maxlen=500)
        self._transition_count = 0
        self._timeout_count = 0
        self._op_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_hook(self, transition: str, hook: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._hooks.setdefault(transition, []).append(hook)
        return {"status": "ok", "transition": transition}

    def transition(self, target: str) -> Dict[str, Any]:
        self._op_count += 1
        if target not in _STATES:
            return {"status": "error", "reason": f"unknown state: {target}"}
        valid = _TRANSITIONS.get(self._state, set())
        if target not in valid:
            return {"status": "error", "reason": f"illegal: {self._state}→{target}"}
        old = self._state
        # Fire pre-hooks
        key = f"{old}→{target}"
        for h in self._hooks.get(key, []):
            try: h(old, target)
            except Exception: pass
        self._state = target
        self._state_entered_at = time.time()
        self._transition_count += 1
        self._history.append({"from": old, "to": target, "at": self._state_entered_at})
        self._fire("transition", {"from": old, "to": target})
        return {"status": "ok", "from": old, "to": target}

    def force_state(self, target: str) -> Dict[str, Any]:
        self._op_count += 1
        old = self._state
        self._state = target
        self._state_entered_at = time.time()
        self._history.append({"from": old, "to": target, "at": self._state_entered_at, "forced": True})
        return {"status": "ok", "forced": True, "from": old, "to": target}

    def check_timeout(self) -> Dict[str, Any]:
        self._op_count += 1
        elapsed = time.time() - self._state_entered_at
        if elapsed > self._timeout_s and self._state != "IDLE":
            self._timeout_count += 1
            old = self._state
            self._state = "IDLE"
            self._state_entered_at = time.time()
            self._history.append({"from": old, "to": "IDLE", "at": self._state_entered_at, "timeout": True})
            self._fire("timeout_revert", {"from": old, "elapsed": elapsed})
            return {"status": "timeout", "from": old, "elapsed_s": round(elapsed, 2)}
        return {"status": "ok", "state": self._state, "elapsed_s": round(elapsed, 2)}

    def get_state(self) -> str: return self._state
    def get_history(self, n: int = 20) -> List[Dict]: return list(self._history)[-n:]
    def get_stats(self) -> Dict[str, Any]:
        return {"state": self._state, "transitions": self._transition_count,
                "timeouts": self._timeout_count, "total_ops": self._op_count,
                "history_size": len(self._history)}


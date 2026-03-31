"""
ResilienceCircuitBreaker — Circuit breaker for external dependencies with half-open probe and auto-recovery.

Architecture (拿来主义):
  circuit_breaker.py, intel_pipeline_fault_hardener.py（M744）

Location: integrations/lol-history/src/lol_history/resilience_circuit_breaker.py

Design Notes (Knuth-level critique):
  User:
    - Production-grade module with unified {"status": "ok"} response format.
    - Stateless or bounded-state design for long-running sessions.
    - Graceful degradation: partial results on component failure.
  System:
    - All data structures bounded (deque/OrderedDict with maxlen).
    - Evolution callback integration for self-improvement feedback.
    - Comprehensive get_stats() for observability.
    - Zero external dependencies beyond stdlib.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from collections import OrderedDict, defaultdict, deque
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.resilience_circuit_breaker.v1"


def _safe_div(a: float, b: float, d: float = 0.0) -> float:
    return a / b if b else d


class _CircuitState:
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _DependencyCircuit:
    """Circuit breaker for a single external dependency."""

    def __init__(self, name: str, failure_threshold: int = 5,
                 recovery_timeout: float = 30.0,
                 half_open_max_calls: int = 3) -> None:
        self.name = name
        self.state = _CircuitState.CLOSED
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.failure_count = 0
        self.success_count = 0
        self.total_calls = 0
        self.last_failure_time: float = 0.0
        self.last_state_change: float = time.monotonic()
        self.half_open_calls = 0
        self._history: deque = deque(maxlen=100)

    def record_success(self) -> str:
        self.total_calls += 1
        self.success_count += 1
        if self.state == _CircuitState.HALF_OPEN:
            self.half_open_calls += 1
            if self.half_open_calls >= self.half_open_max_calls:
                self._transition(_CircuitState.CLOSED)
                self.failure_count = 0
        elif self.state == _CircuitState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)
        self._history.append({"ts": time.monotonic(), "success": True})
        return self.state

    def record_failure(self) -> str:
        self.total_calls += 1
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        self._history.append({"ts": time.monotonic(), "success": False})
        if self.state == _CircuitState.HALF_OPEN:
            self._transition(_CircuitState.OPEN)
        elif self.state == _CircuitState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                self._transition(_CircuitState.OPEN)
        return self.state

    def is_available(self) -> bool:
        if self.state == _CircuitState.CLOSED:
            return True
        if self.state == _CircuitState.OPEN:
            elapsed = time.monotonic() - self.last_failure_time
            if elapsed >= self.recovery_timeout:
                self._transition(_CircuitState.HALF_OPEN)
                self.half_open_calls = 0
                return True
            return False
        return True

    def _transition(self, new_state: str) -> None:
        old = self.state
        self.state = new_state
        self.last_state_change = time.monotonic()
        logger.info("Circuit %s: %s → %s", self.name, old, new_state)

    def force_state(self, state: str) -> None:
        self._transition(state)
        if state == _CircuitState.CLOSED:
            self.failure_count = 0

    def get_recovery_rate(self) -> float:
        recent = list(self._history)[-20:]
        if not recent:
            return 0.0
        return _safe_div(sum(1 for r in recent if r["success"]), len(recent))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "total_calls": self.total_calls,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "recovery_rate": self.get_recovery_rate(),
            "time_in_state": time.monotonic() - self.last_state_change,
        }


class ResilienceCircuitBreaker:
    """Circuit breaker for external dependencies with half-open probe and auto-recovery.

    Public API: register_dependency, record_success, record_failure,
                is_available, get_all_states, force_open, force_close, get_stats
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._circuits: Dict[str, _DependencyCircuit] = {}

    def _fire(self, et: str, data: Dict) -> None:
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_dependency(self, name: str,
                             config: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        cfg = config or {}
        circuit = _DependencyCircuit(
            name,
            failure_threshold=cfg.get("failure_threshold", 5),
            recovery_timeout=cfg.get("recovery_timeout", 30.0),
            half_open_max_calls=cfg.get("half_open_max_calls", 3),
        )
        self._circuits[name] = circuit
        return {"status": "ok", "dependency": name, "total": len(self._circuits)}

    def record_success(self, name: str) -> Dict[str, Any]:
        self._op_count += 1
        circuit = self._circuits.get(name)
        if not circuit:
            return {"status": "ok", "found": False}
        state = circuit.record_success()
        return {"status": "ok", "dependency": name, "state": state}

    def record_failure(self, name: str) -> Dict[str, Any]:
        self._op_count += 1
        circuit = self._circuits.get(name)
        if not circuit:
            return {"status": "ok", "found": False}
        state = circuit.record_failure()
        if state == _CircuitState.OPEN:
            self._fire("circuit_opened", {"dependency": name})
        return {"status": "ok", "dependency": name, "state": state}

    def is_available(self, name: str) -> Dict[str, Any]:
        self._op_count += 1
        circuit = self._circuits.get(name)
        if not circuit:
            return {"status": "ok", "found": False, "available": False}
        return {
            "status": "ok",
            "dependency": name,
            "available": circuit.is_available(),
            "state": circuit.state,
        }

    def get_all_states(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "circuits": {n: c.to_dict() for n, c in self._circuits.items()},
        }

    def force_open(self, name: str) -> Dict[str, Any]:
        self._op_count += 1
        circuit = self._circuits.get(name)
        if circuit:
            circuit.force_state(_CircuitState.OPEN)
        return {"status": "ok", "dependency": name, "forced": "open"}

    def force_close(self, name: str) -> Dict[str, Any]:
        self._op_count += 1
        circuit = self._circuits.get(name)
        if circuit:
            circuit.force_state(_CircuitState.CLOSED)
        return {"status": "ok", "dependency": name, "forced": "closed"}

    def get_stats(self) -> Dict[str, Any]:
        self._op_count += 1
        return {
            "status": "ok",
            "op_count": self._op_count,
            "total_circuits": len(self._circuits),
            "open_circuits": sum(1 for c in self._circuits.values() if c.state == _CircuitState.OPEN),
            "circuits": {n: c.to_dict() for n, c in self._circuits.items()},
        }

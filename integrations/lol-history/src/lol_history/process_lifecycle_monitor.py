"""
ProcessLifecycleMonitor — Monitors LoL client process existence and state.

Architecture (拿来主义):
  Seraphine/app/lol/listener.py — LolProcessExistenceListener: PID polling, client switch
  Seraphine/app/lol/connector.py — getLoginSummonerByPid PID-based connection

Location: integrations/lol-history/src/lol_history/process_lifecycle_monitor.py

Design Notes (Knuth-level critique):
  User:
    - Auto-detects LoL client launch/close without manual intervention.
    - Handles multi-client scenarios: detects client switches (mirrors Seraphine multi-pid logic).
  System:
    - Polling-based (1.5s interval, matching Seraphine msleep(1500)).
    - PID tracking avoids race conditions in client restart scenarios.
    - Event callbacks decouple detection from action (Observer pattern).
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.process_lifecycle_monitor.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class ProcessLifecycleMonitor:
    """Monitors LoL client process existence and fires lifecycle events.

    Public API: check_once, register_callback, get_running_pid,
                simulate_start, simulate_end, get_stats
    """
    def __init__(self, poll_interval: float = 1.5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._check_count = 0
        self._poll_interval = poll_interval
        self._running_pid: int = 0
        self._game_process_running: bool = False
        self._callbacks: Dict[str, List[Callable]] = {
            "client_started": [],
            "client_ended": [],
            "client_changed": [],
            "game_started": [],
            "game_ended": [],
        }
        self._history: List[Dict[str, Any]] = []
        self._max_history = 100

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _dispatch(self, event_type: str, data: Dict[str, Any]):
        """Dispatch event to registered callbacks."""
        for cb in self._callbacks.get(event_type, []):
            try:
                cb(data)
            except Exception as e:
                logger.warning("Callback error for %s: %s", event_type, e)
        self._history.append({
            "event": event_type, "data": data, "timestamp": time.time()
        })
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        self._fire(event_type, data)

    def register_callback(self, event_type: str, callback: Callable) -> Dict[str, Any]:
        """Register callback for lifecycle events."""
        self._op_count += 1
        if event_type not in self._callbacks:
            return {"status": "error", "reason": f"unknown event: {event_type}",
                    "valid_events": list(self._callbacks.keys())}
        self._callbacks[event_type].append(callback)
        return {"status": "ok", "event_type": event_type,
                "callbacks": len(self._callbacks[event_type])}

    def check_once(self, current_pids: List[int],
                    game_process_exists: bool = False) -> Dict[str, Any]:
        """Single poll iteration. Mirrors Seraphine listener.py run() loop body.

        Args:
            current_pids: List of detected LoL client PIDs.
            game_process_exists: Whether LeagueOfLegends.exe is running.
        """
        self._op_count += 1
        self._check_count += 1
        events_fired = []
        if len(current_pids) != 0:
            if self._running_pid == 0:
                # First client started
                self._running_pid = current_pids[0]
                self._dispatch("client_started", {"pid": self._running_pid})
                events_fired.append("client_started")
            elif self._running_pid not in current_pids:
                # Connected client closed, switch to another
                old_pid = self._running_pid
                self._running_pid = current_pids[0]
                self._dispatch("client_changed", {
                    "old_pid": old_pid, "new_pid": self._running_pid})
                events_fired.append("client_changed")
        else:
            if self._running_pid and not game_process_exists:
                old_pid = self._running_pid
                self._running_pid = 0
                self._dispatch("client_ended", {"pid": old_pid})
                events_fired.append("client_ended")
        # Track game process state
        if game_process_exists and not self._game_process_running:
            self._game_process_running = True
            self._dispatch("game_started", {"pid": self._running_pid})
            events_fired.append("game_started")
        elif not game_process_exists and self._game_process_running:
            self._game_process_running = False
            self._dispatch("game_ended", {"pid": self._running_pid})
            events_fired.append("game_ended")

        return {"status": "ok", "running_pid": self._running_pid,
                "game_running": self._game_process_running,
                "events_fired": events_fired, "detected_pids": current_pids}

    def get_running_pid(self) -> Dict[str, Any]:
        """Get currently tracked LoL client PID."""
        self._op_count += 1
        return {"status": "ok", "pid": self._running_pid,
                "game_running": self._game_process_running}

    def simulate_start(self, pid: int = 12345) -> Dict[str, Any]:
        """Simulate a client start event (for testing)."""
        self._op_count += 1
        return self.check_once([pid])

    def simulate_end(self) -> Dict[str, Any]:
        """Simulate a client end event (for testing)."""
        self._op_count += 1
        return self.check_once([], game_process_exists=False)

    def get_history(self, n: int = 20) -> Dict[str, Any]:
        """Get recent lifecycle event history."""
        self._op_count += 1
        return {"status": "ok", "history": self._history[-n:],
                "total_events": len(self._history)}

    def get_stats(self) -> Dict[str, Any]:
        return {"check_count": self._check_count,
                "running_pid": self._running_pid,
                "game_running": self._game_process_running,
                "total_lifecycle_events": len(self._history),
                "callbacks_registered": {k: len(v) for k, v in self._callbacks.items()},
                "total_ops": self._op_count}

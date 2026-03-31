"""
ActionExecutorBridge — Bridges abstract actions to game-specific execution format.

Architecture (拿来主义):
  Akagi/autoplay/autoplay.py — act(mjai_msg)→UI execution
  Akagi/mitm/bridge/bridge_base.py — parse/build bidirectional interface

Location: integrations/lol-history/src/lol_history/action_executor_bridge.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.action_executor_bridge.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class ActionExecutorBridge:
    """Bridges abstract actions to game-specific execution.

    Public API: register_executor, execute, execute_dry_run, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._executors: Dict[str, Callable] = {}
        self._exec_count = 0
        self._success_count = 0
        self._dry_run_count = 0
        self._history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_executor(self, game: str, executor: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._executors[game] = executor
        return {"status": "ok", "game": game, "total_executors": len(self._executors)}

    def execute(self, game: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        self._exec_count += 1
        executor = self._executors.get(game)
        if executor is None:
            return {"status": "error", "reason": f"no executor for game '{game}'"}
        _start = time.time()
        try:
            result = executor(action)
            elapsed = time.time() - _start
            self._success_count += 1
            entry = {"game": game, "action": action.get("type", "unknown"), "success": True,
                     "elapsed_ms": round(elapsed * 1000, 2), "timestamp": time.time()}
            self._history.append(entry)
            self._fire("action_executed", entry)
            return {"status": "ok", "result": result, "elapsed_ms": entry["elapsed_ms"]}
        except Exception as exc:
            entry = {"game": game, "action": action.get("type", "unknown"), "success": False, "error": str(exc)}
            self._history.append(entry)
            return {"status": "error", "reason": str(exc)}

    def execute_dry_run(self, game: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        self._dry_run_count += 1
        executor = self._executors.get(game)
        return {"status": "ok", "dry_run": True, "game": game, "action": action,
                "executor_registered": executor is not None}

    def get_stats(self) -> Dict[str, Any]:
        return {"executions": self._exec_count, "successes": self._success_count,
                "success_rate": round(_safe_div(self._success_count, self._exec_count), 4),
                "dry_runs": self._dry_run_count, "total_ops": self._op_count,
                "executors": list(self._executors.keys())}


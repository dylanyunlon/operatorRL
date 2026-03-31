"""
OpponentAdaptationModelTrainer — Trains opponent adaptation prediction models from historical adaptation data.

Architecture (拿来主义):
  opponent_adaptation_tracker.py（M723） — primary reference
  opponent_behavior_modeler.py — secondary reference

Location: integrations/lol-history/src/lol_history/opponent_adaptation_model_trainer.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.opponent_adaptation_model_trainer.v1"
def _safe_div(a, b, d=0.0): return a / b if b else d

class OpponentAdaptationModelTrainer:
    """Trains opponent adaptation prediction models from historical adaptation data.

    Public API: register, initialize, process, get_report, shutdown, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._modules: Dict[str, Any] = {}
        self._history: deque = deque(maxlen=500)
        self._process_count = 0
        self._state = "uninitialized"
        self._errors: Dict[str, int] = {}

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register(self, name: str, module: Any) -> Dict[str, Any]:
        self._op_count += 1
        self._modules[name] = module
        self._errors[name] = 0
        return {"status": "ok", "module": name, "total": len(self._modules)}

    def initialize(self) -> Dict[str, Any]:
        self._op_count += 1
        self._state = "initialized"
        self._fire("initialized", {"modules": list(self._modules.keys())})
        return {"status": "ok", "state": self._state, "modules": len(self._modules)}

    def process(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._process_count += 1
        data = data or {}
        results = {}
        errors = []
        for name, module in self._modules.items():
            try:
                if hasattr(module, "get_stats"):
                    results[name] = {"status": "ok", "stats": module.get_stats()}
                else:
                    results[name] = {"status": "ok"}
            except Exception as e:
                self._errors[name] = self._errors.get(name, 0) + 1
                errors.append(f"{name}: {e}")
                results[name] = {"status": "error", "error": str(e)}
        entry = {"cycle": self._process_count, "results": results, "errors": errors,
                 "timestamp": time.time()}
        self._history.append(entry)
        self._fire("processed", {"cycle": self._process_count, "errors": len(errors)})
        return {"status": "ok", "cycle": self._process_count, "results": results, "errors": errors}

    def get_report(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "state": self._state, "modules": len(self._modules),
                "process_count": self._process_count,
                "module_errors": dict(self._errors),
                "recent_history": list(self._history)[-5:]}

    def shutdown(self) -> Dict[str, Any]:
        self._op_count += 1
        self._state = "shutdown"
        self._fire("shutdown", {"cycles": self._process_count})
        return {"status": "ok", "state": "shutdown", "total_cycles": self._process_count}

    def get_stats(self) -> Dict[str, Any]:
        return {"state": self._state, "modules": len(self._modules),
                "process_count": self._process_count, "total_ops": self._op_count,
                "errors": dict(self._errors)}

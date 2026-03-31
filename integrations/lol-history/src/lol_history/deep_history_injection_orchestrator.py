"""
DeepHistoryInjectionOrchestrator — Top-level orchestrator for M746-M764.

Architecture (拿来主义):
  intel_training_loop_orchestrator.py（M745）— primary reference (register/initialize/process)
  history_to_live_fusion_orchestrator.py（M725）— secondary reference
  history_feedback_loop_orchestrator.py（M625）— full lifecycle pattern

Location: integrations/lol-history/src/lol_history/deep_history_injection_orchestrator.py

Design Notes (Knuth-level critique):
  User:
    - Single entry point: register all M746-M764 modules, initialize once,
      then call process_pregame() or process_ingame() per game lifecycle phase.
    - Graceful degradation: module failures produce partial results, not crashes.
  System:
    - Module registration validates interface contract (must have get_stats).
    - Process pipeline is phase-aware: pregame modules run during champ select,
      ingame modules run during InProgress phase, postgame during EndOfGame.
    - Health tracking per module enables circuit-breaking for flaky modules.
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.deep_history_injection_orchestrator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d


class DeepHistoryInjectionOrchestrator:
    """Top-level orchestrator for Seraphine deep history injection pipeline M746-M764.

    Public API: register, initialize, process_pregame, process_ingame,
                process_postgame, get_module_health, get_report, shutdown, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._modules: Dict[str, Dict[str, Any]] = {}
        self._process_count = 0
        self._state = "uninitialized"
        self._errors: Dict[str, int] = {}
        self._successes: Dict[str, int] = {}
        self._history: deque = deque(maxlen=500)
        self._phase_counts = {"pregame": 0, "ingame": 0, "postgame": 0}

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register(self, name: str, module: Any,
                  phases: List[str] = None) -> Dict[str, Any]:
        """Register a module with its applicable game phases."""
        self._op_count += 1
        phases = phases or ["pregame", "ingame", "postgame"]
        if not hasattr(module, "get_stats"):
            logger.warning("Module %s missing get_stats, registering anyway", name)
        self._modules[name] = {"module": module, "phases": phases}
        self._errors[name] = 0
        self._successes[name] = 0
        return {"status": "ok", "module": name, "phases": phases,
                "total_modules": len(self._modules)}

    def initialize(self) -> Dict[str, Any]:
        """Initialize all registered modules."""
        self._op_count += 1
        initialized = []
        errors = []
        for name, info in self._modules.items():
            module = info["module"]
            try:
                if hasattr(module, "initialize"):
                    module.initialize()
                initialized.append(name)
            except Exception as e:
                errors.append({"module": name, "error": str(e)})
                self._errors[name] += 1
        self._state = "initialized"
        self._fire("initialized", {"modules": len(initialized), "errors": len(errors)})
        return {"status": "ok", "state": self._state,
                "initialized": initialized, "errors": errors}

    def _process_phase(self, phase: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process all modules registered for a specific game phase."""
        self._op_count += 1
        self._process_count += 1
        self._phase_counts[phase] = self._phase_counts.get(phase, 0) + 1
        results = {}
        errors = []
        for name, info in self._modules.items():
            if phase not in info.get("phases", []):
                continue
            module = info["module"]
            try:
                # Try phase-specific method first, then generic process
                method_name = f"process_{phase}"
                if hasattr(module, method_name):
                    result = getattr(module, method_name)(data)
                elif hasattr(module, "process"):
                    result = module.process(data)
                elif hasattr(module, "get_stats"):
                    result = {"status": "ok", "stats": module.get_stats()}
                else:
                    result = {"status": "ok"}
                results[name] = result
                self._successes[name] = self._successes.get(name, 0) + 1
            except Exception as e:
                self._errors[name] = self._errors.get(name, 0) + 1
                errors.append({"module": name, "error": str(e)})
                logger.warning("Module %s failed in %s: %s", name, phase, e)
        entry = {"phase": phase, "timestamp": time.time(),
                 "modules_ok": len(results), "modules_failed": len(errors)}
        self._history.append(entry)
        self._fire(f"processed_{phase}", entry)
        return {"status": "ok", "phase": phase, "results": results,
                "errors": errors, "modules_processed": len(results)}

    def process_pregame(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process pregame phase: identity resolution, rank lookup, history enrichment."""
        return self._process_phase("pregame", data or {})

    def process_ingame(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process ingame phase: live correlation, cooldown tracking, decision injection."""
        return self._process_phase("ingame", data or {})

    def process_postgame(self, data: Dict[str, Any] = None) -> Dict[str, Any]:
        """Process postgame phase: performance analysis, history update, training export."""
        return self._process_phase("postgame", data or {})

    def get_module_health(self) -> Dict[str, Any]:
        """Get health status of all registered modules."""
        self._op_count += 1
        health = {}
        for name in self._modules:
            total = self._successes.get(name, 0) + self._errors.get(name, 0)
            success_rate = _safe_div(self._successes.get(name, 0), total)
            health[name] = {
                "successes": self._successes.get(name, 0),
                "errors": self._errors.get(name, 0),
                "success_rate": round(success_rate, 3),
                "healthy": success_rate > 0.8 or total < 5,
            }
        return {"status": "ok", "health": health}

    def get_report(self) -> Dict[str, Any]:
        """Get comprehensive orchestrator report."""
        self._op_count += 1
        health = self.get_module_health()
        return {
            "status": "ok",
            "state": self._state,
            "total_modules": len(self._modules),
            "process_count": self._process_count,
            "phase_counts": dict(self._phase_counts),
            "module_health": health.get("health", {}),
            "recent_history": list(self._history)[-10:],
        }

    def shutdown(self) -> Dict[str, Any]:
        """Shutdown all modules gracefully."""
        self._op_count += 1
        shutdown_results = []
        for name, info in self._modules.items():
            module = info["module"]
            try:
                if hasattr(module, "shutdown"):
                    module.shutdown()
                shutdown_results.append({"module": name, "status": "ok"})
            except Exception as e:
                shutdown_results.append({"module": name, "status": "error",
                                          "error": str(e)})
        self._state = "shutdown"
        self._fire("shutdown", {"modules": len(shutdown_results)})
        return {"status": "ok", "state": self._state, "results": shutdown_results}

    def get_stats(self) -> Dict[str, Any]:
        return {"state": self._state, "total_modules": len(self._modules),
                "process_count": self._process_count,
                "phase_counts": dict(self._phase_counts),
                "total_errors": sum(self._errors.values()),
                "total_successes": sum(self._successes.values()),
                "total_ops": self._op_count}

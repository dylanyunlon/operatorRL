"""
AutonomousDecisionOrchestrator — Top-level orchestrator for the autonomous decision loop.

Architecture (拿来主义):
  multi_game_pipeline_orchestrator.py（M685）— register→init→run→shutdown
  capture_to_decision_orchestrator.py（M665）— full lifecycle orchestration

Location: integrations/lol-history/src/lol_history/autonomous_decision_orchestrator.py

Design Notes (Knuth-level critique):
  User:
    - Single run_cycle() drives the entire OBSERVE→ANALYZE→DECIDE→EXECUTE→REVIEW loop.
    - Can run continuously for 30+ minute game sessions.
  System:
    - Module failures are isolated — the loop continues with degraded capability.
    - Full telemetry per cycle enables post-game analysis.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.autonomous_decision_orchestrator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class AutonomousDecisionOrchestrator:
    """Top-level orchestrator: OBSERVE→ANALYZE→DECIDE→EXECUTE→REVIEW loop.

    Public API: register_module, initialize, run_cycle, get_dashboard, shutdown, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._modules: Dict[str, Any] = {}
        self._state = "uninitialized"
        self._cycle_count = 0
        self._total_cycle_ms = 0.0
        self._error_count = 0
        self._started_at: Optional[float] = None
        self._cycle_history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_module(self, name: str, module: Any) -> Dict[str, Any]:
        self._op_count += 1
        self._modules[name] = module
        return {"status": "ok", "module": name, "total_modules": len(self._modules)}

    def initialize(self) -> Dict[str, Any]:
        self._op_count += 1
        self._state = "initialized"
        self._started_at = time.time()
        self._fire("initialized", {"modules": list(self._modules.keys())})
        return {"status": "ok", "modules": len(self._modules), "state": self._state}

    def run_cycle(self, game_state: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._cycle_count += 1
        if game_state is None: game_state = {}
        _start = time.time()
        cycle_result = {"cycle": self._cycle_count, "phases": {}}

        # Phase 1: OBSERVE — gather state
        try:
            phase_detector = self._modules.get("phase_detector")
            if phase_detector and hasattr(phase_detector, "detect"):
                phase_r = phase_detector.detect(game_state.get("game_time", 0))
                cycle_result["phases"]["observe"] = {"phase": phase_r.get("phase", "unknown")}
                game_state["game_phase"] = phase_r.get("phase", "unknown")
        except Exception as e:
            cycle_result["phases"]["observe"] = {"error": str(e)}
            self._error_count += 1

        # Phase 2: ANALYZE — risk + intent
        try:
            risk_assessor = self._modules.get("risk_assessor")
            if risk_assessor and hasattr(risk_assessor, "assess"):
                risk_r = risk_assessor.assess(game_state)
                cycle_result["phases"]["analyze_risk"] = {"level": risk_r.get("level", "unknown")}
                game_state["risk_level"] = risk_r.get("level", "safe")
        except Exception as e:
            cycle_result["phases"]["analyze_risk"] = {"error": str(e)}
            self._error_count += 1

        try:
            intent_reasoner = self._modules.get("intent_reasoner")
            if intent_reasoner and hasattr(intent_reasoner, "reason"):
                intent_r = intent_reasoner.reason(game_state)
                cycle_result["phases"]["analyze_intent"] = {"intent": intent_r.get("intent", "unknown")}
        except Exception as e:
            cycle_result["phases"]["analyze_intent"] = {"error": str(e)}
            self._error_count += 1

        # Phase 3: DECIDE — balance + plan
        try:
            balancer = self._modules.get("balancer")
            if balancer and hasattr(balancer, "balance"):
                scores = game_state.get("objective_scores", {})
                if scores:
                    bal_r = balancer.balance(scores, game_state.get("game_phase"))
                    cycle_result["phases"]["decide"] = {"focus": bal_r.get("recommended_focus", "unknown")}
        except Exception as e:
            cycle_result["phases"]["decide"] = {"error": str(e)}
            self._error_count += 1

        # Phase 4: EXECUTE (recorded but not actually executing in this orchestrator)
        cycle_result["phases"]["execute"] = {"status": "delegated"}

        # Phase 5: REVIEW
        try:
            quality_scorer = self._modules.get("quality_scorer")
            if quality_scorer and hasattr(quality_scorer, "get_trend"):
                trend = quality_scorer.get_trend()
                cycle_result["phases"]["review"] = {"trend": trend.get("trend", "unknown")}
        except Exception as e:
            cycle_result["phases"]["review"] = {"error": str(e)}

        elapsed_ms = (time.time() - _start) * 1000
        self._total_cycle_ms += elapsed_ms
        cycle_result["elapsed_ms"] = round(elapsed_ms, 2)
        self._cycle_history.append(cycle_result)
        if len(self._cycle_history) > 500: self._cycle_history = self._cycle_history[-500:]

        self._fire("cycle_completed", {"cycle": self._cycle_count, "elapsed_ms": elapsed_ms})
        return {"status": "ok", **cycle_result}

    def get_dashboard(self) -> Dict[str, Any]:
        uptime = time.time() - self._started_at if self._started_at else 0
        return {
            "state": self._state, "cycles": self._cycle_count,
            "avg_cycle_ms": round(_safe_div(self._total_cycle_ms, self._cycle_count), 2),
            "errors": self._error_count, "uptime_s": round(uptime, 1),
            "modules": list(self._modules.keys()),
        }

    def shutdown(self) -> Dict[str, Any]:
        self._op_count += 1
        self._state = "shutdown"
        self._fire("shutdown", {"cycles": self._cycle_count})
        return {"status": "ok", "total_cycles": self._cycle_count, "errors": self._error_count}

    def get_stats(self) -> Dict[str, Any]:
        return {"state": self._state, "cycles": self._cycle_count, "errors": self._error_count,
                "avg_cycle_ms": round(_safe_div(self._total_cycle_ms, self._cycle_count), 2),
                "modules": list(self._modules.keys()), "total_ops": self._op_count}


"""
DecisionPipelineHealthGuard — Monitors decision pipeline health with auto-degradation.

Architecture (拿来主义):
  protocol_health_baseline_manager.py（M658）— baseline deviation detection
  e2e_inference_pipeline_orchestrator.py（M655）— health check + fault isolation

Location: integrations/lol-history/src/lol_history/decision_pipeline_health_guard.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.decision_pipeline_health_guard.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class DecisionPipelineHealthGuard:
    """Monitors pipeline health, triggers alerts and graceful degradation.

    Public API: record_metric, check_health, isolate_module, restore_module, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._baselines: Dict[str, Dict] = {}
        self._current: Dict[str, float] = {}
        self._isolated: set = set()
        self._alerts: List[Dict] = []
        self._check_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_baseline(self, module: str, latency_ms: float = 50, error_rate: float = 0.01) -> Dict[str, Any]:
        self._op_count += 1
        self._baselines[module] = {"latency_ms": latency_ms, "error_rate": error_rate}
        return {"status": "ok", "module": module}

    def record_metric(self, module: str, latency_ms: float = 0, error: bool = False) -> Dict[str, Any]:
        self._op_count += 1
        self._current[f"{module}_latency"] = latency_ms
        if error:
            key = f"{module}_errors"
            self._current[key] = self._current.get(key, 0) + 1
        return {"status": "ok", "module": module}

    def check_health(self) -> Dict[str, Any]:
        self._op_count += 1
        self._check_count += 1
        issues = []
        for module, baseline in self._baselines.items():
            if module in self._isolated: continue
            lat = self._current.get(f"{module}_latency", 0)
            if lat > baseline["latency_ms"] * 3:
                issue = {"module": module, "type": "high_latency", "value": lat, "baseline": baseline["latency_ms"]}
                issues.append(issue)
                self._alerts.append({**issue, "timestamp": time.time()})
        healthy = len(issues) == 0
        self._fire("health_checked", {"healthy": healthy, "issues": len(issues)})
        return {"status": "ok", "healthy": healthy, "issues": issues, "isolated": list(self._isolated)}

    def isolate_module(self, module: str) -> Dict[str, Any]:
        self._op_count += 1
        self._isolated.add(module)
        self._fire("module_isolated", {"module": module})
        return {"status": "ok", "module": module, "isolated": True}

    def restore_module(self, module: str) -> Dict[str, Any]:
        self._op_count += 1
        self._isolated.discard(module)
        return {"status": "ok", "module": module, "restored": True}

    def get_stats(self) -> Dict[str, Any]:
        return {"checks": self._check_count, "alerts": len(self._alerts),
                "isolated_modules": list(self._isolated), "monitored_modules": list(self._baselines.keys()),
                "total_ops": self._op_count}


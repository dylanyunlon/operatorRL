"""
IntelAccuracyTracker — Long-term tracking of per-module prediction accuracy.

Architecture (拿来主义):
  coaching_effectiveness_tracker.py（M613）— effectiveness over time
  decision_quality_scorer.py（M702）— sliding window quality trends

Location: integrations/lol-history/src/lol_history/intel_accuracy_tracker.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_accuracy_tracker.v1"
def _safe_div(a, b, d=0.0): return a / b if b else d

class IntelAccuracyTracker:
    """Tracks per-module prediction accuracy trends and triggers retrain alerts.

    Public API: record, get_trend, detect_degradation, get_module_report, get_stats
    """
    def __init__(self, window: int = 200, degrade_threshold: float = 0.1) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._history: Dict[str, deque] = {}
        self._window = window
        self._degrade_threshold = degrade_threshold
        self._record_count = 0
        self._alerts: List[Dict[str, Any]] = []

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def record(self, module: str, correct: bool, confidence: float = 0.5) -> Dict[str, Any]:
        self._op_count += 1
        self._record_count += 1
        if module not in self._history:
            self._history[module] = deque(maxlen=self._window)
        self._history[module].append({"correct": correct, "confidence": confidence, "ts": time.time()})
        return {"status": "ok", "module": module, "total": len(self._history[module])}

    def get_trend(self, module: str, last_n: int = 50) -> Dict[str, Any]:
        self._op_count += 1
        h = list(self._history.get(module, []))[-last_n:]
        if not h:
            return {"status": "ok", "module": module, "trend": "no_data"}
        precision = _safe_div(sum(1 for r in h if r["correct"]), len(h))
        # Split into halves for trend
        mid = len(h) // 2
        if mid > 0:
            first_p = _safe_div(sum(1 for r in h[:mid] if r["correct"]), mid)
            second_p = _safe_div(sum(1 for r in h[mid:] if r["correct"]), len(h) - mid)
            trend = "improving" if second_p > first_p + 0.02 else "degrading" if second_p < first_p - 0.02 else "stable"
        else:
            trend = "insufficient"
        return {"status": "ok", "module": module, "precision": round(precision, 4),
                "trend": trend, "samples": len(h)}

    def detect_degradation(self) -> Dict[str, Any]:
        self._op_count += 1
        degraded = []
        for module in self._history:
            trend = self.get_trend(module)
            if trend.get("trend") == "degrading":
                degraded.append({"module": module, "precision": trend.get("precision", 0)})
                alert = {"module": module, "type": "degradation", "timestamp": time.time()}
                self._alerts.append(alert)
                self._fire("degradation_detected", {"module": module})
        return {"status": "ok", "degraded_modules": degraded, "count": len(degraded)}

    def get_module_report(self, module: str) -> Dict[str, Any]:
        self._op_count += 1
        h = list(self._history.get(module, []))
        if not h:
            return {"status": "ok", "module": module, "report": {}}
        correct = sum(1 for r in h if r["correct"])
        avg_conf = sum(r["confidence"] for r in h) / len(h)
        return {"status": "ok", "module": module,
                "precision": round(_safe_div(correct, len(h)), 4),
                "avg_confidence": round(avg_conf, 4),
                "calibration_error": round(abs(avg_conf - _safe_div(correct, len(h))), 4),
                "samples": len(h)}

    def get_stats(self) -> Dict[str, Any]:
        return {"modules_tracked": len(self._history), "record_count": self._record_count,
                "alerts": len(self._alerts), "total_ops": self._op_count}

"""
RealtimeRiskAssessor — Evaluates real-time risk level from game state.

Architecture (拿来主义):
  fiddler_anomaly_detector.py — multi-type anomaly detection
  PARL/benchmark/torch/AlphaZero/submission_template.py — MCTS risk search

Location: integrations/lol-history/src/lol_history/realtime_risk_assessor.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.realtime_risk_assessor.v1"
_RISK_LEVELS = ["safe", "caution", "danger", "critical"]

def _safe_div(a, b, d=0.0): return a / b if b else d
def _clamp(v, lo=0.0, hi=1.0): return max(lo, min(hi, v))

class RealtimeRiskAssessor:
    """Assesses risk level: safe/caution/danger/critical.

    Public API: assess, register_factor, get_trend, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._factors: Dict[str, Callable] = {}
        self._history: deque = deque(maxlen=300)
        self._assess_count = 0
        # Register default factors
        self._factors["health_ratio"] = lambda s: 1.0 - _clamp(s.get("health", 100) / max(s.get("max_health", 100), 1))
        self._factors["enemy_nearby"] = lambda s: _clamp(s.get("enemies_visible", 0) / 5.0)
        self._factors["ally_deficit"] = lambda s: _clamp((s.get("enemies_visible", 0) - s.get("allies_nearby", 0)) / 5.0)

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_factor(self, name: str, fn: Callable, weight: float = 1.0) -> Dict[str, Any]:
        self._op_count += 1
        self._factors[name] = lambda s, _fn=fn, _w=weight: _fn(s) * _w
        return {"status": "ok", "factor": name, "total_factors": len(self._factors)}

    def assess(self, game_state: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._assess_count += 1
        if game_state is None: game_state = {}
        scores = {}
        for name, fn in self._factors.items():
            try: scores[name] = round(_clamp(fn(game_state)), 4)
            except Exception: scores[name] = 0.0
        total = sum(scores.values())
        avg = _safe_div(total, len(scores))
        if avg >= 0.75: level = "critical"
        elif avg >= 0.5: level = "danger"
        elif avg >= 0.25: level = "caution"
        else: level = "safe"
        entry = {"level": level, "score": round(avg, 4), "factors": scores, "timestamp": time.time()}
        self._history.append(entry)
        self._fire("risk_assessed", {"level": level, "score": avg})
        return {"status": "ok", **entry}

    def get_trend(self, n: int = 10) -> Dict[str, Any]:
        self._op_count += 1
        recent = list(self._history)[-n:]
        if len(recent) < 2: return {"status": "ok", "trend": "insufficient_data", "samples": len(recent)}
        scores = [e["score"] for e in recent]
        slope = (scores[-1] - scores[0]) / len(scores)
        direction = "worsening" if slope > 0.01 else ("improving" if slope < -0.01 else "stable")
        return {"status": "ok", "trend": direction, "slope": round(slope, 4), "latest": scores[-1]}

    def get_stats(self) -> Dict[str, Any]:
        level_dist = {}
        for e in self._history: level_dist[e["level"]] = level_dist.get(e["level"], 0) + 1
        return {"total_ops": self._op_count, "assessments": self._assess_count,
                "factors": list(self._factors.keys()), "level_distribution": level_dist}


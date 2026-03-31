"""
OnlinePolicyAdjuster — Adjusts policy weights online based on action feedback.

Architecture (拿来主义):
  DI-star/distar/agent/default/agent.py — update_fake_reward online update
  historical_reward_reshaper.py（M617）— adaptive weight adjustment

Location: integrations/lol-history/src/lol_history/online_policy_adjuster.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.online_policy_adjuster.v1"

def _clamp(v, lo, hi): return max(lo, min(hi, v))

class OnlinePolicyAdjuster:
    """Online policy weight adjustment without retraining.

    Public API: set_weights, adjust, get_weights, get_adjustment_history, get_stats
    """
    def __init__(self, max_delta: float = 0.1) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._weights: Dict[str, float] = {}
        self._max_delta = max_delta
        self._adjustments: List[Dict] = []
        self._adjust_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_weights(self, weights: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._weights = dict(weights)
        return {"status": "ok", "weights": dict(self._weights)}

    def adjust(self, feedback: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._adjust_count += 1
        deltas = {}
        for key, signal in feedback.items():
            if key not in self._weights: continue
            delta = _clamp(signal * 0.05, -self._max_delta, self._max_delta)
            old = self._weights[key]
            self._weights[key] = _clamp(old + delta, 0.0, 2.0)
            deltas[key] = {"old": round(old, 4), "new": round(self._weights[key], 4), "delta": round(delta, 4)}
        self._adjustments.append({"deltas": deltas, "timestamp": time.time()})
        self._fire("policy_adjusted", {"deltas": len(deltas)})
        return {"status": "ok", "adjusted": len(deltas), "deltas": deltas}

    def get_weights(self) -> Dict[str, float]: return dict(self._weights)
    def get_adjustment_history(self, n: int = 20) -> List[Dict]: return self._adjustments[-n:]
    def get_stats(self) -> Dict[str, Any]:
        return {"weights": dict(self._weights), "adjustments": self._adjust_count,
                "max_delta": self._max_delta, "total_ops": self._op_count}


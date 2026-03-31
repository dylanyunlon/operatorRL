"""
OnlineIntelModelUpdater — Online weight updates for intel models during games.

Architecture (拿来主义):
  online_policy_adjuster.py（M694）— online policy adjustment
  DI-star/distar/agent/default/agent.py — update_fake_reward

Location: integrations/lol-history/src/lol_history/online_intel_model_updater.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.online_intel_model_updater.v1"

class OnlineIntelModelUpdater:
    """Online weight updates for intel models without full retraining.

    Public API: update_weight, get_weights, get_update_history, constrain_drift, get_stats
    """
    def __init__(self, max_drift: float = 0.3, lr: float = 0.02) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._weights: Dict[str, float] = {}
        self._initial_weights: Dict[str, float] = {}
        self._max_drift = max_drift
        self._lr = lr
        self._update_history: deque = deque(maxlen=200)
        self._update_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_initial_weights(self, weights: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._weights = dict(weights)
        self._initial_weights = dict(weights)
        return {"status": "ok", "weights": len(weights)}

    def update_weight(self, key: str, feedback_signal: float) -> Dict[str, Any]:
        self._op_count += 1
        self._update_count += 1
        if key not in self._weights:
            return {"status": "error", "reason": f"unknown key: {key}"}
        old = self._weights[key]
        delta = self._lr * feedback_signal
        new_val = old + delta
        # Constrain drift from initial
        initial = self._initial_weights.get(key, old)
        if abs(new_val - initial) > self._max_drift:
            new_val = initial + self._max_drift * (1 if new_val > initial else -1)
        self._weights[key] = round(new_val, 6)
        self._update_history.append({"key": key, "old": old, "new": self._weights[key],
                                     "signal": feedback_signal, "timestamp": time.time()})
        self._fire("weight_updated", {"key": key, "delta": round(self._weights[key] - old, 6)})
        return {"status": "ok", "key": key, "old": round(old, 6),
                "new": round(self._weights[key], 6), "drift": round(abs(self._weights[key] - initial), 6)}

    def get_weights(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "weights": dict(self._weights)}

    def get_update_history(self, n: int = 20) -> List[Dict]:
        return list(self._update_history)[-n:]

    def constrain_drift(self) -> Dict[str, Any]:
        self._op_count += 1
        constrained = 0
        for k in self._weights:
            init = self._initial_weights.get(k, self._weights[k])
            if abs(self._weights[k] - init) > self._max_drift:
                self._weights[k] = init + self._max_drift * (1 if self._weights[k] > init else -1)
                constrained += 1
        return {"status": "ok", "constrained": constrained}

    def get_stats(self) -> Dict[str, Any]:
        return {"weights_count": len(self._weights), "update_count": self._update_count,
                "max_drift": self._max_drift, "total_ops": self._op_count}

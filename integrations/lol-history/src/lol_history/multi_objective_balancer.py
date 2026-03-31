"""
MultiObjectiveBalancer — Pareto balance between competing game objectives.

Architecture (拿来主义):
  integrations/lol/src/lol_agent/reward_shaper.py — multi-dimensional scoring
  DI-star/distar/agent/default/rl_training/as_rl_utils.py — head_weights_dict

Location: integrations/lol-history/src/lol_history/multi_objective_balancer.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.multi_objective_balancer.v1"

_DEFAULT_OBJECTIVES = {"survival": 1.0, "economy": 0.8, "push": 0.6, "teamfight": 0.7, "vision": 0.5}

class MultiObjectiveBalancer:
    """Balances multiple competing objectives.

    Public API: set_weights, balance, get_decomposition, set_phase_profile, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._weights: Dict[str, float] = dict(_DEFAULT_OBJECTIVES)
        self._phase_profiles: Dict[str, Dict[str, float]] = {}
        self._balance_count = 0
        self._history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_weights(self, weights: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._weights.update(weights)
        return {"status": "ok", "weights": dict(self._weights)}

    def set_phase_profile(self, phase: str, weights: Dict[str, float]) -> Dict[str, Any]:
        self._op_count += 1
        self._phase_profiles[phase] = weights
        return {"status": "ok", "phase": phase}

    def balance(self, scores: Dict[str, float], phase: str = None) -> Dict[str, Any]:
        self._op_count += 1
        self._balance_count += 1
        weights = self._phase_profiles.get(phase, self._weights) if phase else self._weights
        weighted = {}
        total = 0.0
        for obj, score in scores.items():
            w = weights.get(obj, 0.5)
            ws = score * w
            weighted[obj] = round(ws, 4)
            total += ws
        weight_sum = sum(weights.get(o, 0.5) for o in scores)
        balanced = round(total / weight_sum if weight_sum > 0 else 0, 4)
        entry = {"balanced_score": balanced, "decomposition": weighted, "phase": phase, "timestamp": time.time()}
        self._history.append(entry)
        best_obj = max(weighted, key=weighted.get) if weighted else "none"
        self._fire("balanced", {"score": balanced, "best_objective": best_obj})
        return {"status": "ok", **entry, "recommended_focus": best_obj}

    def get_decomposition(self) -> Dict[str, Any]:
        if not self._history: return {"status": "ok", "decomposition": {}}
        return {"status": "ok", "decomposition": self._history[-1].get("decomposition", {})}

    def get_stats(self) -> Dict[str, Any]:
        return {"weights": dict(self._weights), "balance_count": self._balance_count,
                "phase_profiles": list(self._phase_profiles.keys()), "total_ops": self._op_count}


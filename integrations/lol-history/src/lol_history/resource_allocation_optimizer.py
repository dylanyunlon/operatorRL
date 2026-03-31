"""
ResourceAllocationOptimizer — Optimizes resource allocation decisions.

Architecture (拿来主义):
  DI-star/distar/agent/default/agent.py — get_behavior_z resource strategy
  PARL/benchmark/fluid/PPO/train.py — reward-driven allocation

Location: integrations/lol-history/src/lol_history/resource_allocation_optimizer.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.resource_allocation_optimizer.v1"

class ResourceAllocationOptimizer:
    """Optimizes gold/experience/time allocation.

    Public API: optimize, simulate, register_item_value, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._item_values: Dict[str, Dict] = {}
        self._optimize_count = 0
        self._history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_item_value(self, item: str, cost: float, combat_value: float, utility_value: float = 0) -> Dict[str, Any]:
        self._op_count += 1
        self._item_values[item] = {"cost": cost, "combat": combat_value, "utility": utility_value,
                                    "efficiency": round((combat_value + utility_value) / max(cost, 1), 4)}
        return {"status": "ok", "item": item}

    def optimize(self, gold: float, priorities: Dict[str, float] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._optimize_count += 1
        if priorities is None: priorities = {"combat": 0.7, "utility": 0.3}
        affordable = {k: v for k, v in self._item_values.items() if v["cost"] <= gold}
        if not affordable:
            return {"status": "ok", "recommendation": "save", "gold": gold, "reason": "no affordable items"}
        scored = {}
        for item, v in affordable.items():
            score = v["combat"] * priorities.get("combat", 0.5) + v["utility"] * priorities.get("utility", 0.5)
            scored[item] = round(score / max(v["cost"], 1), 4)
        best = max(scored, key=scored.get)
        entry = {"recommendation": best, "score": scored[best], "gold_remaining": gold - self._item_values[best]["cost"]}
        self._history.append(entry)
        self._fire("optimized", {"item": best})
        return {"status": "ok", **entry, "alternatives": dict(sorted(scored.items(), key=lambda x: -x[1])[:3])}

    def simulate(self, gold: float, item_sequence: List[str]) -> Dict[str, Any]:
        self._op_count += 1
        remaining = gold
        total_combat = 0.0
        total_utility = 0.0
        affordable = []
        for item in item_sequence:
            v = self._item_values.get(item)
            if v and v["cost"] <= remaining:
                remaining -= v["cost"]
                total_combat += v["combat"]
                total_utility += v["utility"]
                affordable.append(item)
        return {"status": "ok", "purchased": affordable, "gold_remaining": remaining,
                "total_combat": total_combat, "total_utility": total_utility}

    def get_stats(self) -> Dict[str, Any]:
        return {"items_registered": len(self._item_values), "optimizations": self._optimize_count, "total_ops": self._op_count}


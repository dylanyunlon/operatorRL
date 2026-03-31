"""
IntelABTestFramework — A/B testing framework for intel strategies.

Architecture (拿来主义):
  agentlightning/deployment/live_ab_router.py（M560）— A/B routing
  cross_game_online_ab_framework.py（M687）— experiment split + significance

Location: integrations/lol-history/src/lol_history/intel_ab_test_framework.py
"""
from __future__ import annotations
import logging, time, math, random
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_ab_test_framework.v1"
def _safe_div(a, b, d=0.0): return a / b if b else d

class IntelABTestFramework:
    """A/B tests different intel strategies with statistical significance.

    Public API: create_experiment, assign_variant, record_result,
                check_significance, get_winner, get_stats
    """
    def __init__(self, confidence_level: float = 0.95) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._experiments: Dict[str, Dict[str, Any]] = {}
        self._confidence = confidence_level

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def create_experiment(self, name: str, variants: List[str],
                           traffic_split: Dict[str, float] = None) -> Dict[str, Any]:
        self._op_count += 1
        if len(variants) < 2:
            return {"status": "error", "reason": "need_at_least_2_variants"}
        if not traffic_split:
            equal = round(1.0 / len(variants), 4)
            traffic_split = {v: equal for v in variants}
        self._experiments[name] = {"variants": variants, "split": traffic_split,
                                    "results": {v: {"successes": 0, "total": 0} for v in variants},
                                    "created": time.time(), "concluded": False, "winner": None}
        return {"status": "ok", "experiment": name, "variants": variants}

    def assign_variant(self, experiment: str, session_id: str = "") -> Dict[str, Any]:
        self._op_count += 1
        exp = self._experiments.get(experiment)
        if not exp:
            return {"status": "error", "reason": "experiment_not_found"}
        r = random.random()
        cumulative = 0.0
        assigned = exp["variants"][-1]
        for v, pct in exp["split"].items():
            cumulative += pct
            if r <= cumulative:
                assigned = v
                break
        return {"status": "ok", "variant": assigned, "experiment": experiment}

    def record_result(self, experiment: str, variant: str, success: bool) -> Dict[str, Any]:
        self._op_count += 1
        exp = self._experiments.get(experiment)
        if not exp or variant not in exp["results"]:
            return {"status": "error", "reason": "invalid_experiment_or_variant"}
        exp["results"][variant]["total"] += 1
        if success:
            exp["results"][variant]["successes"] += 1
        return {"status": "ok", "variant": variant,
                "total": exp["results"][variant]["total"]}

    def check_significance(self, experiment: str) -> Dict[str, Any]:
        self._op_count += 1
        exp = self._experiments.get(experiment)
        if not exp:
            return {"status": "error", "reason": "not_found"}
        results = exp["results"]
        # Simple two-proportion z-test between top two variants
        variants_sorted = sorted(results.items(),
            key=lambda x: _safe_div(x[1]["successes"], x[1]["total"]), reverse=True)
        if len(variants_sorted) < 2:
            return {"status": "ok", "significant": False, "reason": "not_enough_variants"}
        a_name, a_data = variants_sorted[0]
        b_name, b_data = variants_sorted[1]
        na, nb = a_data["total"], b_data["total"]
        if na < 10 or nb < 10:
            return {"status": "ok", "significant": False, "reason": "insufficient_samples",
                    "a": a_name, "b": b_name, "na": na, "nb": nb}
        pa = _safe_div(a_data["successes"], na)
        pb = _safe_div(b_data["successes"], nb)
        p_pool = (a_data["successes"] + b_data["successes"]) / (na + nb)
        se = math.sqrt(p_pool * (1 - p_pool) * (1/na + 1/nb)) if p_pool > 0 and p_pool < 1 else 0.001
        z = abs(pa - pb) / se if se > 0 else 0
        # z > 1.96 for 95% confidence
        z_threshold = 1.96 if self._confidence >= 0.95 else 1.645
        significant = z > z_threshold
        if significant:
            exp["concluded"] = True
            exp["winner"] = a_name
            self._fire("experiment_concluded", {"experiment": experiment, "winner": a_name})
        return {"status": "ok", "significant": significant, "z_score": round(z, 4),
                "leader": a_name, "leader_rate": round(pa, 4),
                "runner_up": b_name, "runner_up_rate": round(pb, 4)}

    def get_winner(self, experiment: str) -> Dict[str, Any]:
        self._op_count += 1
        exp = self._experiments.get(experiment)
        if not exp:
            return {"status": "error", "reason": "not_found"}
        return {"status": "ok", "concluded": exp["concluded"], "winner": exp["winner"]}

    def get_stats(self) -> Dict[str, Any]:
        concluded = sum(1 for e in self._experiments.values() if e["concluded"])
        return {"experiments": len(self._experiments), "concluded": concluded,
                "total_ops": self._op_count}

"""
HistoryAbExperimentTracker — Tracks A/B experiments on historical intelligence strategies.

Architecture (拿来主义):
  live_ab_router.py（M560）+ coaching_effectiveness_tracker.py（M613）

Location: integrations/lol-history/src/lol_history/history_ab_experiment_tracker.py

Design Notes (Knuth-level critique):
  User:
    - assign_variant is deterministic per subject_id for consistency.
    - record_outcome rejects unknown experiment_ids — no silent data loss.
    - get_results includes statistical significance estimate.
  System:
    - evolution_callback fires on every operation for system-level tracking.
    - Experiments are immutable once created — no mid-experiment config changes.
    - op_count provides basic throughput monitoring.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.history_ab_experiment_tracker.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


class HistoryAbExperimentTracker:
    """Tracks A/B experiments on historical intelligence strategies.

    Public API
    ----------
    create_experiment   — define a new A/B experiment
    assign_variant      — deterministically assign a variant
    record_outcome      — record experiment outcome
    get_results         — get experiment results with significance
    list_experiments    — list all experiments

    Evolution
    ---------
    Set evolution_callback to receive structured events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._op_count: int = 0
        self._experiments: Dict[str, Dict[str, Any]] = {}
        self._outcomes: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    def _fire(self, event_type: str, data: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY, "type": event_type,
                "timestamp": time.time(), "payload": data,
            })

    # ------------------------------------------------------------------ #

    def create_experiment(self, experiment_id: str, variants: List[str] = None,
                          description: str = "") -> Dict[str, Any]:
        """Define a new A/B experiment.

        Parameters
        ----------
        experiment_id : str
        variants : list of str  (e.g., ["control", "treatment"])
        description : str

        Returns
        -------
        dict
        """
        self._op_count += 1
        if variants is None:
            variants = ["control", "treatment"]

        if experiment_id in self._experiments:
            return {"status": "error", "reason": "experiment already exists"}

        self._experiments[experiment_id] = {
            "id": experiment_id,
            "variants": list(variants),
            "description": description,
            "created_at": time.time(),
        }

        self._fire("create_experiment", {"experiment_id": experiment_id})
        return {"status": "ok", "op": "create_experiment",
                "experiment_id": experiment_id, "variants": variants}

    # ------------------------------------------------------------------ #

    def assign_variant(self, experiment_id: str, subject_id: str) -> Dict[str, Any]:
        """Deterministically assign a variant to a subject.

        Uses hash-based bucketing for consistent assignment.

        Parameters
        ----------
        experiment_id : str
        subject_id : str

        Returns
        -------
        dict  with status, variant
        """
        self._op_count += 1
        exp = self._experiments.get(experiment_id)
        if exp is None:
            return {"status": "error", "reason": "unknown experiment_id"}

        variants = exp["variants"]
        hash_input = f"{experiment_id}:{subject_id}"
        h = int(hashlib.md5(hash_input.encode()).hexdigest(), 16)
        idx = h % len(variants)

        return {"status": "ok", "op": "assign_variant",
                "experiment_id": experiment_id, "subject_id": subject_id,
                "variant": variants[idx]}

    # ------------------------------------------------------------------ #

    def record_outcome(self, experiment_id: str, subject_id: str,
                       variant: str, outcome: float,
                       metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """Record experiment outcome.

        Parameters
        ----------
        experiment_id : str
        subject_id : str
        variant : str
        outcome : float  (e.g., win=1.0, loss=0.0)
        metadata : dict

        Returns
        -------
        dict
        """
        self._op_count += 1
        if experiment_id not in self._experiments:
            return {"status": "error", "reason": "unknown experiment_id"}

        record = {
            "subject_id": subject_id,
            "variant": variant,
            "outcome": outcome,
            "metadata": metadata or {},
            "recorded_at": time.time(),
        }
        self._outcomes[experiment_id].append(record)

        self._fire("record_outcome", {"experiment_id": experiment_id, "variant": variant})
        return {"status": "ok", "op": "record_outcome",
                "experiment_id": experiment_id,
                "total_outcomes": len(self._outcomes[experiment_id])}

    # ------------------------------------------------------------------ #

    def get_results(self, experiment_id: str) -> Dict[str, Any]:
        """Get experiment results with basic significance estimate.

        Returns
        -------
        dict  with per-variant stats, sample sizes, and significance
        """
        self._op_count += 1
        exp = self._experiments.get(experiment_id)
        if exp is None:
            return {"status": "error", "reason": "unknown experiment_id"}

        outcomes = self._outcomes.get(experiment_id, [])
        by_variant: Dict[str, List[float]] = defaultdict(list)
        for o in outcomes:
            by_variant[o["variant"]].append(o["outcome"])

        variant_stats: Dict[str, Dict[str, Any]] = {}
        for v, values in by_variant.items():
            n = len(values)
            mean = sum(values) / n if n > 0 else 0.0
            variance = sum((x - mean) ** 2 for x in values) / n if n > 1 else 0.0
            variant_stats[v] = {
                "n": n,
                "mean": round(mean, 4),
                "variance": round(variance, 6),
                "std": round(math.sqrt(variance), 4),
            }

        # Simple significance estimate: if both variants have n>=30, check overlap
        variants = list(by_variant.keys())
        significant = False
        if len(variants) >= 2:
            a, b = variant_stats.get(variants[0], {}), variant_stats.get(variants[1], {})
            if a.get("n", 0) >= 30 and b.get("n", 0) >= 30:
                diff = abs(a["mean"] - b["mean"])
                pooled_std = math.sqrt((a["variance"] + b["variance"]) / 2)
                if pooled_std > 0 and diff / pooled_std > 1.96:
                    significant = True

        self._fire("get_results", {"experiment_id": experiment_id})
        return {"status": "ok", "op": "get_results",
                "experiment_id": experiment_id,
                "variant_stats": variant_stats,
                "total_outcomes": len(outcomes),
                "statistically_significant": significant}

    # ------------------------------------------------------------------ #

    def list_experiments(self) -> Dict[str, Any]:
        """List all experiments."""
        self._op_count += 1
        exps = []
        for eid, exp in self._experiments.items():
            exps.append({
                **exp,
                "outcome_count": len(self._outcomes.get(eid, [])),
            })
        return {"status": "ok", "op": "list_experiments", "experiments": exps}

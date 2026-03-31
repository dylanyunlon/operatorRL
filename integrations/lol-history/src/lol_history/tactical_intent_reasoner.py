"""
TacticalIntentReasoner — Reasons about optimal tactical intent from game state.

Architecture (拿来主义):
  realtime_inference_chain_builder.py（M653）— inference chain
  DI-star/distar/agent/default/rl_training/as_rl_utils.py — policy gradient reasoning

Location: integrations/lol-history/src/lol_history/tactical_intent_reasoner.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.tactical_intent_reasoner.v1"

_INTENTS = ["push", "farm", "defend", "gank", "objective", "retreat"]

def _safe_div(a, b, d=0.0): return a / b if b else d
def _softmax(scores):
    import math
    max_s = max(scores) if scores else 0
    exps = [math.exp(s - max_s) for s in scores]
    total = sum(exps)
    return [e / total if total > 0 else 1/len(scores) for e in exps]

class TacticalIntentReasoner:
    """Reasons tactical intent: push/farm/defend/gank/objective/retreat.

    Public API: reason, register_rule, get_intent_history, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._rules: Dict[str, List[Callable]] = {i: [] for i in _INTENTS}
        self._history: deque = deque(maxlen=200)
        self._reason_count = 0
        self._intent_counts: Dict[str, int] = {}
        # Default heuristic rules
        self._rules["farm"].append(lambda s: 0.5 if s.get("game_phase") == "early_game" else 0.2)
        self._rules["push"].append(lambda s: 0.6 if s.get("ally_advantage", 0) > 1 else 0.1)
        self._rules["defend"].append(lambda s: 0.7 if s.get("risk_level") in ("danger", "critical") else 0.1)
        self._rules["retreat"].append(lambda s: 0.8 if s.get("health_ratio", 1) < 0.3 else 0.05)
        self._rules["objective"].append(lambda s: 0.6 if s.get("objective_available", False) else 0.1)
        self._rules["gank"].append(lambda s: 0.5 if s.get("game_phase") == "mid_game" and s.get("risk_level") == "safe" else 0.1)

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_rule(self, intent: str, rule_fn: Callable) -> Dict[str, Any]:
        self._op_count += 1
        if intent not in _INTENTS: return {"status": "error", "reason": f"unknown intent: {intent}"}
        self._rules[intent].append(rule_fn)
        return {"status": "ok", "intent": intent, "rules": len(self._rules[intent])}

    def reason(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._reason_count += 1
        if context is None: context = {}
        raw_scores = {}
        for intent in _INTENTS:
            scores = []
            for rule in self._rules[intent]:
                try: scores.append(rule(context))
                except Exception: scores.append(0.0)
            raw_scores[intent] = max(scores) if scores else 0.0
        values = [raw_scores[i] for i in _INTENTS]
        probs = _softmax(values)
        distribution = {intent: round(p, 4) for intent, p in zip(_INTENTS, probs)}
        best_idx = probs.index(max(probs))
        best_intent = _INTENTS[best_idx]
        confidence = round(probs[best_idx], 4)
        self._intent_counts[best_intent] = self._intent_counts.get(best_intent, 0) + 1
        entry = {"intent": best_intent, "confidence": confidence, "distribution": distribution, "timestamp": time.time()}
        self._history.append(entry)
        self._fire("intent_reasoned", {"intent": best_intent, "confidence": confidence})
        return {"status": "ok", **entry}

    def get_intent_history(self, n: int = 20) -> List[Dict]: return list(self._history)[-n:]
    def get_stats(self) -> Dict[str, Any]:
        return {"reason_count": self._reason_count, "intent_distribution": dict(self._intent_counts),
                "total_ops": self._op_count, "registered_rules": {k: len(v) for k, v in self._rules.items()}}


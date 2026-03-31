"""
DecisionQualityScorer — Scores decision quality based on post-action feedback.

Architecture (拿来主义):
  realtime_decision_confidence_scorer.py（M659）— confidence scoring
  coaching_effectiveness_tracker.py（M613）— effectiveness feedback loop

Location: integrations/lol-history/src/lol_history/decision_quality_scorer.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.decision_quality_scorer.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class DecisionQualityScorer:
    """Scores decisions post-hoc and tracks quality trends.

    Public API: score, get_trend, detect_bias, get_stats
    """
    def __init__(self, window_size: int = 100) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._scores: deque = deque(maxlen=window_size)
        self._intent_scores: Dict[str, List[float]] = {}
        self._score_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def score(self, decision_id: str, intent: str, predicted_value: float,
              actual_outcome: float) -> Dict[str, Any]:
        self._op_count += 1
        self._score_count += 1
        quality = 1.0 - min(abs(predicted_value - actual_outcome), 1.0)
        entry = {"decision_id": decision_id, "intent": intent, "predicted": predicted_value,
                 "actual": actual_outcome, "quality": round(quality, 4), "timestamp": time.time()}
        self._scores.append(entry)
        self._intent_scores.setdefault(intent, []).append(quality)
        self._fire("quality_scored", {"intent": intent, "quality": quality})
        return {"status": "ok", **entry}

    def get_trend(self, n: int = 20) -> Dict[str, Any]:
        self._op_count += 1
        recent = list(self._scores)[-n:]
        if len(recent) < 2: return {"status": "ok", "trend": "insufficient_data"}
        scores = [e["quality"] for e in recent]
        first_half = scores[:len(scores)//2]
        second_half = scores[len(scores)//2:]
        avg1 = sum(first_half) / len(first_half)
        avg2 = sum(second_half) / len(second_half)
        if avg2 - avg1 > 0.05: trend = "improving"
        elif avg1 - avg2 > 0.05: trend = "declining"
        else: trend = "stable"
        return {"status": "ok", "trend": trend, "recent_avg": round(avg2, 4), "samples": len(recent)}

    def detect_bias(self) -> Dict[str, Any]:
        self._op_count += 1
        biases = {}
        for intent, scores in self._intent_scores.items():
            if len(scores) < 5: continue
            avg = sum(scores) / len(scores)
            if avg < 0.4: biases[intent] = {"avg_quality": round(avg, 3), "bias": "systematically_poor"}
            elif avg > 0.8: biases[intent] = {"avg_quality": round(avg, 3), "bias": "overconfident_or_good"}
        return {"status": "ok", "biases": biases}

    def get_stats(self) -> Dict[str, Any]:
        all_scores = [e["quality"] for e in self._scores]
        return {"scored": self._score_count,
                "avg_quality": round(sum(all_scores)/len(all_scores), 4) if all_scores else 0,
                "intents_tracked": list(self._intent_scores.keys()), "total_ops": self._op_count}


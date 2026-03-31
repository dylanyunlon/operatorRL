"""
ActionFeedbackCollector — Collects environment feedback after action execution.

Architecture (拿来主义):
  history_feedback_loop_orchestrator.py（M625）— feedback loop
  DI-star/distar/agent/default/agent.py — collect_data(next_obs, reward, done)

Location: integrations/lol-history/src/lol_history/action_feedback_collector.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.action_feedback_collector.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class ActionFeedbackCollector:
    """Collects post-action feedback (state change, reward, opponent reaction).

    Public API: collect, get_effectiveness, get_recent, get_stats
    """
    def __init__(self, window_size: int = 200) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._feedback: deque = deque(maxlen=window_size)
        self._collect_count = 0
        self._total_reward = 0.0
        self._action_scores: Dict[str, List[float]] = {}

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def collect(self, action_id: str, action_type: str, reward: float = 0.0,
                state_delta: Dict[str, Any] = None, meta: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._collect_count += 1
        self._total_reward += reward
        entry = {"action_id": action_id, "action_type": action_type, "reward": reward,
                 "state_delta": state_delta or {}, "meta": meta or {}, "timestamp": time.time()}
        self._feedback.append(entry)
        self._action_scores.setdefault(action_type, []).append(reward)
        self._fire("feedback_collected", {"action_type": action_type, "reward": reward})
        return {"status": "ok", "collected": self._collect_count}

    def get_effectiveness(self, action_type: str = None) -> Dict[str, Any]:
        self._op_count += 1
        if action_type:
            scores = self._action_scores.get(action_type, [])
            if not scores: return {"status": "ok", "action_type": action_type, "effectiveness": 0.0, "samples": 0}
            return {"status": "ok", "action_type": action_type,
                    "effectiveness": round(sum(scores) / len(scores), 4), "samples": len(scores)}
        result = {}
        for at, scores in self._action_scores.items():
            result[at] = {"mean_reward": round(sum(scores) / len(scores), 4), "samples": len(scores)}
        return {"status": "ok", "effectiveness": result}

    def get_recent(self, n: int = 10) -> List[Dict]: return list(self._feedback)[-n:]
    def get_stats(self) -> Dict[str, Any]:
        return {"collected": self._collect_count, "total_reward": round(self._total_reward, 4),
                "avg_reward": round(_safe_div(self._total_reward, self._collect_count), 4),
                "action_types": list(self._action_scores.keys()), "total_ops": self._op_count}


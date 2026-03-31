"""
IntelRewardSignalGenerator — Converts intel prediction accuracy into reward signals.

Architecture (拿来主义):
  reward_shaper.py — compute_reward multi-dimensional scoring
  historical_reward_reshaper.py（M617）— reward reshaping

Location: integrations/lol-history/src/lol_history/intel_reward_signal_generator.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.intel_reward_signal_generator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class IntelRewardSignalGenerator:
    """Converts intel prediction accuracy into reward signals for training.

    Public API: generate_reward, set_module_weight, get_module_rewards,
                get_aggregate_reward, get_stats
    """
    def __init__(self, correct_reward: float = 1.0, incorrect_penalty: float = -0.5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._correct_reward = correct_reward
        self._incorrect_penalty = incorrect_penalty
        self._module_weights: Dict[str, float] = {}
        self._rewards_history: Dict[str, List[Dict[str, Any]]] = {}
        self._generate_count = 0

    def _fire(self, et, data):
        if self.evolution_callback:
            self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_module_weight(self, module_name: str, weight: float) -> Dict[str, Any]:
        self._op_count += 1
        self._module_weights[module_name] = weight
        return {"status": "ok", "module": module_name, "weight": weight}

    def generate_reward(self, module_name: str, predicted: Any, actual: Any,
                         confidence: float = 0.5) -> Dict[str, Any]:
        self._op_count += 1
        self._generate_count += 1
        if isinstance(predicted, bool) and isinstance(actual, bool):
            correct = predicted == actual
            accuracy = 1.0 if correct else 0.0
        elif isinstance(predicted, (int, float)) and isinstance(actual, (int, float)):
            accuracy = 1.0 - min(abs(predicted - actual) / max(abs(actual), 1.0), 1.0)
            correct = accuracy > 0.7
        else:
            correct = str(predicted) == str(actual)
            accuracy = 1.0 if correct else 0.0
        # Reward = (correct_reward or penalty) * confidence * module_weight
        base = self._correct_reward if correct else self._incorrect_penalty
        weight = self._module_weights.get(module_name, 1.0)
        reward = round(base * confidence * weight, 4)
        entry = {"module": module_name, "reward": reward, "correct": correct,
                 "accuracy": round(accuracy, 4), "confidence": confidence, "timestamp": time.time()}
        self._rewards_history.setdefault(module_name, []).append(entry)
        self._fire("reward_generated", {"module": module_name, "reward": reward})
        return {"status": "ok", **entry}

    def get_module_rewards(self, module_name: str, n: int = 50) -> Dict[str, Any]:
        self._op_count += 1
        history = self._rewards_history.get(module_name, [])
        recent = history[-n:]
        if not recent:
            return {"status": "ok", "module": module_name, "rewards": [], "avg": 0.0}
        avg = sum(r["reward"] for r in recent) / len(recent)
        return {"status": "ok", "module": module_name, "rewards": recent,
                "avg_reward": round(avg, 4), "count": len(recent)}

    def get_aggregate_reward(self) -> Dict[str, Any]:
        self._op_count += 1
        all_rewards = []
        per_module = {}
        for module, history in self._rewards_history.items():
            if history:
                avg = sum(r["reward"] for r in history) / len(history)
                per_module[module] = round(avg, 4)
                all_rewards.extend(r["reward"] for r in history)
        overall = sum(all_rewards) / len(all_rewards) if all_rewards else 0.0
        return {"status": "ok", "overall_avg": round(overall, 4),
                "per_module": per_module, "total_signals": len(all_rewards)}

    def get_stats(self) -> Dict[str, Any]:
        total = sum(len(v) for v in self._rewards_history.values())
        return {"generate_count": self._generate_count, "total_signals": total,
                "modules_tracked": len(self._rewards_history), "total_ops": self._op_count}

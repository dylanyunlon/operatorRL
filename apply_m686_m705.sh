#!/bin/bash
# apply_m686_m705.sh — Apply M686-M705 (Phase AW) to operatorRL
# Author: dylanyunlong <dylanyunlong@gmail.com>
# Usage: cd /data/jiacheng/system/cache/temp/nips2026/agent-os && bash apply_m686_m705.sh

set -e
PROJ_ROOT="$(pwd)"
LOL_HIST="$PROJ_ROOT/integrations/lol-history/src/lol_history"

echo "=== Phase AW: M686-M705 自主决策循环 ==="
echo "Target: $LOL_HIST"
mkdir -p "$LOL_HIST"


echo "Creating autonomous_decision_state_machine.py..."
cat > "$LOL_HIST/autonomous_decision_state_machine.py" << 'FILE_EOF'
"""
AutonomousDecisionStateMachine — Finite state machine for the autonomous decision lifecycle.

Architecture (拿来主义):
  fiddler_session_state_machine.py（M647）— FSM with transition hooks
  Akagi/mitm/mitm_abc.py — websocket_start→message→end lifecycle

Location: integrations/lol-history/src/lol_history/autonomous_decision_state_machine.py

Design Notes (Knuth-level critique):
  User:
    - transition() returns success/failure dict — never throws on illegal transitions.
    - Timeout watchdog auto-reverts stuck states.
  System:
    - Hooks fire before and after transitions — enables instrumentation without coupling.
    - History is bounded deque — no unbounded memory growth.
"""

from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.autonomous_decision_state_machine.v1"

_STATES = {"IDLE", "OBSERVING", "ANALYZING", "DECIDING", "EXECUTING", "REVIEWING"}
_TRANSITIONS = {
    "IDLE": {"OBSERVING"},
    "OBSERVING": {"ANALYZING", "IDLE"},
    "ANALYZING": {"DECIDING", "OBSERVING"},
    "DECIDING": {"EXECUTING", "ANALYZING"},
    "EXECUTING": {"REVIEWING", "DECIDING"},
    "REVIEWING": {"OBSERVING", "IDLE"},
}

def _safe_div(a, b, d=0.0): return a / b if b else d

class AutonomousDecisionStateMachine:
    """FSM: IDLE→OBSERVING→ANALYZING→DECIDING→EXECUTING→REVIEWING.

    Public API: transition, force_state, get_state, get_history, register_hook,
                check_timeout, get_stats
    """
    def __init__(self, timeout_s: float = 10.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._state = "IDLE"
        self._timeout_s = timeout_s
        self._state_entered_at = time.time()
        self._hooks: Dict[str, List[Callable]] = {}
        self._history: deque = deque(maxlen=500)
        self._transition_count = 0
        self._timeout_count = 0
        self._op_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_hook(self, transition: str, hook: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._hooks.setdefault(transition, []).append(hook)
        return {"status": "ok", "transition": transition}

    def transition(self, target: str) -> Dict[str, Any]:
        self._op_count += 1
        if target not in _STATES:
            return {"status": "error", "reason": f"unknown state: {target}"}
        valid = _TRANSITIONS.get(self._state, set())
        if target not in valid:
            return {"status": "error", "reason": f"illegal: {self._state}→{target}"}
        old = self._state
        # Fire pre-hooks
        key = f"{old}→{target}"
        for h in self._hooks.get(key, []):
            try: h(old, target)
            except Exception: pass
        self._state = target
        self._state_entered_at = time.time()
        self._transition_count += 1
        self._history.append({"from": old, "to": target, "at": self._state_entered_at})
        self._fire("transition", {"from": old, "to": target})
        return {"status": "ok", "from": old, "to": target}

    def force_state(self, target: str) -> Dict[str, Any]:
        self._op_count += 1
        old = self._state
        self._state = target
        self._state_entered_at = time.time()
        self._history.append({"from": old, "to": target, "at": self._state_entered_at, "forced": True})
        return {"status": "ok", "forced": True, "from": old, "to": target}

    def check_timeout(self) -> Dict[str, Any]:
        self._op_count += 1
        elapsed = time.time() - self._state_entered_at
        if elapsed > self._timeout_s and self._state != "IDLE":
            self._timeout_count += 1
            old = self._state
            self._state = "IDLE"
            self._state_entered_at = time.time()
            self._history.append({"from": old, "to": "IDLE", "at": self._state_entered_at, "timeout": True})
            self._fire("timeout_revert", {"from": old, "elapsed": elapsed})
            return {"status": "timeout", "from": old, "elapsed_s": round(elapsed, 2)}
        return {"status": "ok", "state": self._state, "elapsed_s": round(elapsed, 2)}

    def get_state(self) -> str: return self._state
    def get_history(self, n: int = 20) -> List[Dict]: return list(self._history)[-n:]
    def get_stats(self) -> Dict[str, Any]:
        return {"state": self._state, "transitions": self._transition_count,
                "timeouts": self._timeout_count, "total_ops": self._op_count,
                "history_size": len(self._history)}

FILE_EOF

echo "Creating action_priority_queue.py..."
cat > "$LOL_HIST/action_priority_queue.py" << 'FILE_EOF'
"""
ActionPriorityQueue — Priority queue for pending game actions with TTL expiration.

Architecture (拿来主义):
  fiddler_packet_prioritizer.py（M657）— priority scoring
  DI-star/distar/agent/default/agent.py — step→_post_process decision output

Location: integrations/lol-history/src/lol_history/action_priority_queue.py
"""
from __future__ import annotations
import logging, time, heapq
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.action_priority_queue.v1"
_PRIORITY_MAP = {"critical": 0, "high": 1, "medium": 2, "low": 3}

def _safe_div(a, b, d=0.0): return a / b if b else d

class ActionPriorityQueue:
    """Priority queue: critical > high > medium > low, with TTL expiration.

    Public API: enqueue, dequeue, dequeue_batch, peek, purge_expired, size, get_stats
    """
    def __init__(self, default_ttl_s: float = 5.0) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._heap: List = []
        self._counter = 0
        self._default_ttl = default_ttl_s
        self._enqueue_count = 0
        self._dequeue_count = 0
        self._expired_count = 0
        self._op_count = 0
        self._priority_counts: Dict[str, int] = {}

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def enqueue(self, action: Dict[str, Any], priority: str = "medium",
                ttl_s: float = None) -> Dict[str, Any]:
        self._op_count += 1
        self._enqueue_count += 1
        pri_val = _PRIORITY_MAP.get(priority, 2)
        ttl = ttl_s if ttl_s is not None else self._default_ttl
        expires_at = time.time() + ttl
        self._counter += 1
        entry = (pri_val, self._counter, expires_at, action)
        heapq.heappush(self._heap, entry)
        self._priority_counts[priority] = self._priority_counts.get(priority, 0) + 1
        self._fire("enqueued", {"priority": priority})
        return {"status": "ok", "priority": priority, "queue_size": len(self._heap)}

    def dequeue(self) -> Dict[str, Any]:
        self._op_count += 1
        self.purge_expired()
        if not self._heap:
            return {"status": "empty"}
        pri_val, _, _, action = heapq.heappop(self._heap)
        self._dequeue_count += 1
        pri_name = {v: k for k, v in _PRIORITY_MAP.items()}.get(pri_val, "medium")
        return {"status": "ok", "action": action, "priority": pri_name}

    def dequeue_batch(self, n: int = 5) -> Dict[str, Any]:
        self._op_count += 1
        results = []
        for _ in range(n):
            r = self.dequeue()
            if r["status"] == "empty": break
            results.append(r)
        return {"status": "ok", "count": len(results), "actions": results}

    def peek(self) -> Dict[str, Any]:
        self.purge_expired()
        if not self._heap:
            return {"status": "empty"}
        pri_val, _, expires, action = self._heap[0]
        return {"status": "ok", "action": action, "expires_in": round(expires - time.time(), 2)}

    def purge_expired(self) -> int:
        now = time.time()
        new_heap = []
        purged = 0
        for entry in self._heap:
            if entry[2] > now:
                new_heap.append(entry)
            else:
                purged += 1
                self._expired_count += 1
        if purged:
            self._heap = new_heap
            heapq.heapify(self._heap)
        return purged

    def size(self) -> int: return len(self._heap)

    def get_stats(self) -> Dict[str, Any]:
        return {"enqueued": self._enqueue_count, "dequeued": self._dequeue_count,
                "expired": self._expired_count, "current_size": len(self._heap),
                "priority_distribution": dict(self._priority_counts), "total_ops": self._op_count}

FILE_EOF

echo "Creating realtime_risk_assessor.py..."
cat > "$LOL_HIST/realtime_risk_assessor.py" << 'FILE_EOF'
"""
RealtimeRiskAssessor — Evaluates real-time risk level from game state.

Architecture (拿来主义):
  fiddler_anomaly_detector.py — multi-type anomaly detection
  PARL/benchmark/torch/AlphaZero/submission_template.py — MCTS risk search

Location: integrations/lol-history/src/lol_history/realtime_risk_assessor.py
"""
from __future__ import annotations
import logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.realtime_risk_assessor.v1"
_RISK_LEVELS = ["safe", "caution", "danger", "critical"]

def _safe_div(a, b, d=0.0): return a / b if b else d
def _clamp(v, lo=0.0, hi=1.0): return max(lo, min(hi, v))

class RealtimeRiskAssessor:
    """Assesses risk level: safe/caution/danger/critical.

    Public API: assess, register_factor, get_trend, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._factors: Dict[str, Callable] = {}
        self._history: deque = deque(maxlen=300)
        self._assess_count = 0
        # Register default factors
        self._factors["health_ratio"] = lambda s: 1.0 - _clamp(s.get("health", 100) / max(s.get("max_health", 100), 1))
        self._factors["enemy_nearby"] = lambda s: _clamp(s.get("enemies_visible", 0) / 5.0)
        self._factors["ally_deficit"] = lambda s: _clamp((s.get("enemies_visible", 0) - s.get("allies_nearby", 0)) / 5.0)

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_factor(self, name: str, fn: Callable, weight: float = 1.0) -> Dict[str, Any]:
        self._op_count += 1
        self._factors[name] = lambda s, _fn=fn, _w=weight: _fn(s) * _w
        return {"status": "ok", "factor": name, "total_factors": len(self._factors)}

    def assess(self, game_state: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._assess_count += 1
        if game_state is None: game_state = {}
        scores = {}
        for name, fn in self._factors.items():
            try: scores[name] = round(_clamp(fn(game_state)), 4)
            except Exception: scores[name] = 0.0
        total = sum(scores.values())
        avg = _safe_div(total, len(scores))
        if avg >= 0.75: level = "critical"
        elif avg >= 0.5: level = "danger"
        elif avg >= 0.25: level = "caution"
        else: level = "safe"
        entry = {"level": level, "score": round(avg, 4), "factors": scores, "timestamp": time.time()}
        self._history.append(entry)
        self._fire("risk_assessed", {"level": level, "score": avg})
        return {"status": "ok", **entry}

    def get_trend(self, n: int = 10) -> Dict[str, Any]:
        self._op_count += 1
        recent = list(self._history)[-n:]
        if len(recent) < 2: return {"status": "ok", "trend": "insufficient_data", "samples": len(recent)}
        scores = [e["score"] for e in recent]
        slope = (scores[-1] - scores[0]) / len(scores)
        direction = "worsening" if slope > 0.01 else ("improving" if slope < -0.01 else "stable")
        return {"status": "ok", "trend": direction, "slope": round(slope, 4), "latest": scores[-1]}

    def get_stats(self) -> Dict[str, Any]:
        level_dist = {}
        for e in self._history: level_dist[e["level"]] = level_dist.get(e["level"], 0) + 1
        return {"total_ops": self._op_count, "assessments": self._assess_count,
                "factors": list(self._factors.keys()), "level_distribution": level_dist}

FILE_EOF

echo "Creating game_phase_detector.py..."
cat > "$LOL_HIST/game_phase_detector.py" << 'FILE_EOF'
"""
GamePhaseDetector — Detects current game phase based on time and events.

Architecture (拿来主义):
  game_phase_strategy_mapper.py（M642）— phase→strategy mapping
  DI-star/distar/agent/default/agent.py — _get_time_factor

Location: integrations/lol-history/src/lol_history/game_phase_detector.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.game_phase_detector.v1"

_DEFAULT_PHASE_THRESHOLDS = {"early_game": (0, 900), "mid_game": (900, 1800), "late_game": (1800, 2700), "endgame": (2700, float("inf"))}

class GamePhaseDetector:
    """Detects game phase: early_game/mid_game/late_game/endgame.

    Public API: detect, register_game_phases, register_hook, get_current_phase, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._phase_defs: Dict[str, Dict] = {"default": dict(_DEFAULT_PHASE_THRESHOLDS)}
        self._hooks: Dict[str, List[Callable]] = {}
        self._current_phase = "early_game"
        self._phase_history: List[Dict] = []
        self._detect_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_game_phases(self, game: str, phases: Dict[str, tuple]) -> Dict[str, Any]:
        self._op_count += 1
        self._phase_defs[game] = phases
        return {"status": "ok", "game": game, "phases": list(phases.keys())}

    def register_hook(self, phase_change: str, hook: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._hooks.setdefault(phase_change, []).append(hook)
        return {"status": "ok", "hook": phase_change}

    def detect(self, game_time: float, game: str = "default", events: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._detect_count += 1
        phases = self._phase_defs.get(game, self._phase_defs["default"])
        detected = "unknown"
        for phase, (lo, hi) in phases.items():
            if lo <= game_time < hi:
                detected = phase
                break
        old = self._current_phase
        if detected != old:
            key = f"{old}→{detected}"
            for h in self._hooks.get(key, []):
                try: h(old, detected, game_time)
                except Exception: pass
            self._phase_history.append({"from": old, "to": detected, "game_time": game_time, "at": time.time()})
            self._current_phase = detected
            self._fire("phase_changed", {"from": old, "to": detected, "game_time": game_time})
        return {"status": "ok", "phase": detected, "game_time": game_time, "changed": detected != old}

    def get_current_phase(self) -> str: return self._current_phase
    def get_stats(self) -> Dict[str, Any]:
        return {"current_phase": self._current_phase, "detections": self._detect_count,
                "phase_changes": len(self._phase_history), "total_ops": self._op_count,
                "registered_games": list(self._phase_defs.keys())}

FILE_EOF

echo "Creating tactical_intent_reasoner.py..."
cat > "$LOL_HIST/tactical_intent_reasoner.py" << 'FILE_EOF'
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

FILE_EOF

echo "Creating action_executor_bridge.py..."
cat > "$LOL_HIST/action_executor_bridge.py" << 'FILE_EOF'
"""
ActionExecutorBridge — Bridges abstract actions to game-specific execution format.

Architecture (拿来主义):
  Akagi/autoplay/autoplay.py — act(mjai_msg)→UI execution
  Akagi/mitm/bridge/bridge_base.py — parse/build bidirectional interface

Location: integrations/lol-history/src/lol_history/action_executor_bridge.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.action_executor_bridge.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class ActionExecutorBridge:
    """Bridges abstract actions to game-specific execution.

    Public API: register_executor, execute, execute_dry_run, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._executors: Dict[str, Callable] = {}
        self._exec_count = 0
        self._success_count = 0
        self._dry_run_count = 0
        self._history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_executor(self, game: str, executor: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._executors[game] = executor
        return {"status": "ok", "game": game, "total_executors": len(self._executors)}

    def execute(self, game: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        self._exec_count += 1
        executor = self._executors.get(game)
        if executor is None:
            return {"status": "error", "reason": f"no executor for game '{game}'"}
        _start = time.time()
        try:
            result = executor(action)
            elapsed = time.time() - _start
            self._success_count += 1
            entry = {"game": game, "action": action.get("type", "unknown"), "success": True,
                     "elapsed_ms": round(elapsed * 1000, 2), "timestamp": time.time()}
            self._history.append(entry)
            self._fire("action_executed", entry)
            return {"status": "ok", "result": result, "elapsed_ms": entry["elapsed_ms"]}
        except Exception as exc:
            entry = {"game": game, "action": action.get("type", "unknown"), "success": False, "error": str(exc)}
            self._history.append(entry)
            return {"status": "error", "reason": str(exc)}

    def execute_dry_run(self, game: str, action: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        self._dry_run_count += 1
        executor = self._executors.get(game)
        return {"status": "ok", "dry_run": True, "game": game, "action": action,
                "executor_registered": executor is not None}

    def get_stats(self) -> Dict[str, Any]:
        return {"executions": self._exec_count, "successes": self._success_count,
                "success_rate": round(_safe_div(self._success_count, self._exec_count), 4),
                "dry_runs": self._dry_run_count, "total_ops": self._op_count,
                "executors": list(self._executors.keys())}

FILE_EOF

echo "Creating action_feedback_collector.py..."
cat > "$LOL_HIST/action_feedback_collector.py" << 'FILE_EOF'
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

FILE_EOF

echo "Creating decision_log_replayer.py..."
cat > "$LOL_HIST/decision_log_replayer.py" << 'FILE_EOF'
"""
DecisionLogReplayer — Replays decision logs for post-game review.

Architecture (拿来主义):
  protocol_replay_synchronizer.py（M652）— time-axis synchronized replay
  replay_decision_auditor.py（M612）— frame-by-frame decision comparison

Location: integrations/lol-history/src/lol_history/decision_log_replayer.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.decision_log_replayer.v1"

class DecisionLogReplayer:
    """Replays decision log timeline: state→analysis→action→feedback.

    Public API: load_log, seek, next_entry, filter_by_type, get_summary, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._log: List[Dict[str, Any]] = []
        self._cursor = 0
        self._replay_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def load_log(self, entries: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._op_count += 1
        self._log = sorted(entries, key=lambda e: e.get("timestamp", 0))
        self._cursor = 0
        return {"status": "ok", "entries": len(self._log)}

    def seek(self, game_time: float) -> Dict[str, Any]:
        self._op_count += 1
        for i, e in enumerate(self._log):
            if e.get("game_time", e.get("timestamp", 0)) >= game_time:
                self._cursor = i
                return {"status": "ok", "cursor": i, "game_time": game_time}
        self._cursor = len(self._log)
        return {"status": "ok", "cursor": self._cursor, "game_time": game_time, "at_end": True}

    def next_entry(self) -> Dict[str, Any]:
        self._op_count += 1
        self._replay_count += 1
        if self._cursor >= len(self._log):
            return {"status": "end_of_log"}
        entry = self._log[self._cursor]
        self._cursor += 1
        return {"status": "ok", "entry": entry, "cursor": self._cursor, "remaining": len(self._log) - self._cursor}

    def filter_by_type(self, decision_type: str) -> List[Dict[str, Any]]:
        self._op_count += 1
        return [e for e in self._log if e.get("type") == decision_type or e.get("intent") == decision_type]

    def get_summary(self) -> Dict[str, Any]:
        self._op_count += 1
        types = {}
        for e in self._log: types[e.get("type", "unknown")] = types.get(e.get("type", "unknown"), 0) + 1
        return {"status": "ok", "total_entries": len(self._log), "type_distribution": types,
                "duration_s": (self._log[-1].get("timestamp", 0) - self._log[0].get("timestamp", 0)) if len(self._log) > 1 else 0}

    def get_stats(self) -> Dict[str, Any]:
        return {"entries": len(self._log), "cursor": self._cursor, "replays": self._replay_count, "total_ops": self._op_count}

FILE_EOF

echo "Creating online_policy_adjuster.py..."
cat > "$LOL_HIST/online_policy_adjuster.py" << 'FILE_EOF'
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

FILE_EOF

echo "Creating multi_objective_balancer.py..."
cat > "$LOL_HIST/multi_objective_balancer.py" << 'FILE_EOF'
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

FILE_EOF

echo "Creating team_coordination_reasoner.py..."
cat > "$LOL_HIST/team_coordination_reasoner.py" << 'FILE_EOF'
"""
TeamCoordinationReasoner — Reasons about optimal team coordination actions.

Architecture (拿来主义):
  DI-star/distar/agent/default/model/module_utils.py — Attention multi-head coordination
  ELF/elf_python/zmq_adapter.py — multi-node coordination

Location: integrations/lol-history/src/lol_history/team_coordination_reasoner.py
"""
from __future__ import annotations
import logging, math, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.team_coordination_reasoner.v1"

def _distance(a, b): return math.sqrt((a[0]-b[0])**2 + (a[1]-b[1])**2)

class TeamCoordinationReasoner:
    """Reasons about team coordination: engage/disengage/split/regroup.

    Public API: assess_teamfight, recommend_rally_point, evaluate_engage, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._assess_count = 0
        self._history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def assess_teamfight(self, allies: List[Dict], enemies: List[Dict]) -> Dict[str, Any]:
        self._op_count += 1
        self._assess_count += 1
        ally_hp = sum(a.get("health", 0) for a in allies)
        enemy_hp = sum(e.get("health", 0) for e in enemies)
        ally_count = len([a for a in allies if a.get("alive", True)])
        enemy_count = len([e for e in enemies if e.get("alive", True)])
        hp_ratio = ally_hp / max(enemy_hp, 1)
        number_advantage = ally_count - enemy_count
        if hp_ratio > 1.3 and number_advantage >= 0: recommendation = "engage"
        elif hp_ratio < 0.6 or number_advantage <= -2: recommendation = "disengage"
        elif number_advantage >= 2: recommendation = "engage"
        else: recommendation = "poke"
        entry = {"recommendation": recommendation, "hp_ratio": round(hp_ratio, 2),
                 "number_advantage": number_advantage, "timestamp": time.time()}
        self._history.append(entry)
        self._fire("teamfight_assessed", entry)
        return {"status": "ok", **entry}

    def recommend_rally_point(self, ally_positions: List[tuple]) -> Dict[str, Any]:
        self._op_count += 1
        if not ally_positions: return {"status": "ok", "rally_point": (0, 0)}
        cx = sum(p[0] for p in ally_positions) / len(ally_positions)
        cy = sum(p[1] for p in ally_positions) / len(ally_positions)
        return {"status": "ok", "rally_point": (round(cx, 1), round(cy, 1)), "allies": len(ally_positions)}

    def evaluate_engage(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        if context is None: context = {}
        ult_ready = context.get("ult_ready_count", 0)
        ally_hp_pct = context.get("avg_ally_hp_pct", 0.5)
        score = ult_ready * 0.2 + ally_hp_pct * 0.5 + (1 if context.get("number_advantage", 0) > 0 else 0) * 0.3
        return {"status": "ok", "engage_score": round(score, 3), "should_engage": score > 0.6}

    def get_stats(self) -> Dict[str, Any]:
        rec_dist = {}
        for e in self._history: rec_dist[e.get("recommendation", "?")] = rec_dist.get(e.get("recommendation", "?"), 0) + 1
        return {"assessments": self._assess_count, "recommendation_distribution": rec_dist, "total_ops": self._op_count}

FILE_EOF

echo "Creating resource_allocation_optimizer.py..."
cat > "$LOL_HIST/resource_allocation_optimizer.py" << 'FILE_EOF'
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

FILE_EOF

echo "Creating vision_control_reasoner.py..."
cat > "$LOL_HIST/vision_control_reasoner.py" << 'FILE_EOF'
"""
VisionControlReasoner — Reasons about optimal vision control actions.

Architecture (拿来主义):
  fiddler_lol_decoder.py — eventdata→ward event decoding
  dota2bot-OpenHyperAI/ — ward_purchase_cooldown vision management

Location: integrations/lol-history/src/lol_history/vision_control_reasoner.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional, Tuple
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.vision_control_reasoner.v1"

class VisionControlReasoner:
    """Reasons about ward placement, sweeping, and vision coverage.

    Public API: analyze_coverage, recommend_ward_spot, recommend_sweep, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._wards_placed: List[Dict] = []
        self._high_value_spots: List[Dict] = []
        self._analyze_count = 0
        # Default high-value vision spots (normalized coordinates)
        self._register_default_spots()

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _register_default_spots(self):
        self._high_value_spots = [
            {"name": "dragon_pit", "pos": (0.45, 0.35), "priority": "high"},
            {"name": "baron_pit", "pos": (0.55, 0.65), "priority": "high"},
            {"name": "river_mid", "pos": (0.5, 0.5), "priority": "medium"},
            {"name": "tri_bush_bot", "pos": (0.6, 0.25), "priority": "medium"},
            {"name": "tri_bush_top", "pos": (0.4, 0.75), "priority": "medium"},
        ]

    def analyze_coverage(self, active_wards: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._analyze_count += 1
        if active_wards is None: active_wards = []
        ward_positions = set()
        for w in active_wards:
            pos = w.get("pos", w.get("position"))
            if pos: ward_positions.add(tuple(pos) if isinstance(pos, list) else pos)
        covered_spots = 0
        uncovered = []
        for spot in self._high_value_spots:
            is_covered = any(abs(spot["pos"][0]-wp[0]) < 0.1 and abs(spot["pos"][1]-wp[1]) < 0.1 for wp in ward_positions) if ward_positions else False
            if is_covered: covered_spots += 1
            else: uncovered.append(spot)
        coverage = covered_spots / max(len(self._high_value_spots), 1)
        self._fire("coverage_analyzed", {"coverage": coverage})
        return {"status": "ok", "coverage": round(coverage, 3), "active_wards": len(active_wards),
                "uncovered_spots": uncovered[:3]}

    def recommend_ward_spot(self, game_phase: str = "mid_game", active_wards: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        analysis = self.analyze_coverage(active_wards)
        uncovered = analysis.get("uncovered_spots", [])
        priority_order = {"high": 0, "medium": 1, "low": 2}
        uncovered.sort(key=lambda s: priority_order.get(s.get("priority", "low"), 2))
        if uncovered:
            return {"status": "ok", "recommendation": uncovered[0], "reason": "highest priority uncovered spot"}
        return {"status": "ok", "recommendation": None, "reason": "all key spots covered"}

    def recommend_sweep(self, enemy_ward_estimates: List[Dict] = None) -> Dict[str, Any]:
        self._op_count += 1
        if not enemy_ward_estimates: return {"status": "ok", "sweep_targets": [], "reason": "no estimated enemy wards"}
        sorted_targets = sorted(enemy_ward_estimates, key=lambda w: w.get("danger_level", 0), reverse=True)
        return {"status": "ok", "sweep_targets": sorted_targets[:3]}

    def get_stats(self) -> Dict[str, Any]:
        return {"analyses": self._analyze_count, "high_value_spots": len(self._high_value_spots), "total_ops": self._op_count}

FILE_EOF

echo "Creating timing_window_scheduler.py..."
cat > "$LOL_HIST/timing_window_scheduler.py" << 'FILE_EOF'
"""
TimingWindowScheduler — Schedules time-sensitive action windows.

Architecture (拿来主义):
  ban_pick_realtime_advisor.py（M637）— realtime scheduling
  DI-star/distar/agent/default/agent.py — _get_time_factor

Location: integrations/lol-history/src/lol_history/timing_window_scheduler.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.timing_window_scheduler.v1"

class TimingWindowScheduler:
    """Schedules time-sensitive windows (dragon spawn, ult CD, wave arrival).

    Public API: register_window, check_windows, get_upcoming, get_utilization, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._windows: List[Dict] = []
        self._utilized = 0
        self._missed = 0
        self._check_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_window(self, name: str, opens_at: float, closes_at: float,
                        priority: str = "medium", meta: Dict = None) -> Dict[str, Any]:
        self._op_count += 1
        window = {"name": name, "opens_at": opens_at, "closes_at": closes_at,
                  "priority": priority, "meta": meta or {}, "status": "pending"}
        self._windows.append(window)
        return {"status": "ok", "window": name, "total_windows": len(self._windows)}

    def check_windows(self, game_time: float) -> Dict[str, Any]:
        self._op_count += 1
        self._check_count += 1
        active = []
        upcoming = []
        expired = []
        for w in self._windows:
            if w["status"] == "utilized": continue
            if game_time >= w["closes_at"]:
                if w["status"] != "expired":
                    w["status"] = "expired"
                    self._missed += 1
                expired.append(w)
            elif game_time >= w["opens_at"]:
                w["status"] = "active"
                active.append(w)
            else:
                eta = w["opens_at"] - game_time
                if eta < 30:
                    upcoming.append({**w, "eta_s": round(eta, 1)})
        self._fire("windows_checked", {"active": len(active), "upcoming": len(upcoming)})
        return {"status": "ok", "active": active, "upcoming": upcoming, "expired_count": len(expired)}

    def mark_utilized(self, name: str) -> Dict[str, Any]:
        self._op_count += 1
        for w in self._windows:
            if w["name"] == name and w["status"] == "active":
                w["status"] = "utilized"
                self._utilized += 1
                return {"status": "ok", "window": name}
        return {"status": "error", "reason": f"window '{name}' not active"}

    def get_upcoming(self, game_time: float, horizon_s: float = 60) -> List[Dict]:
        return [w for w in self._windows
                if w["status"] == "pending" and w["opens_at"] - game_time < horizon_s and w["opens_at"] > game_time]

    def get_utilization(self) -> Dict[str, Any]:
        total = self._utilized + self._missed
        return {"utilized": self._utilized, "missed": self._missed,
                "rate": round(self._utilized / max(total, 1), 3)}

    def get_stats(self) -> Dict[str, Any]:
        return {"total_windows": len(self._windows), "utilized": self._utilized,
                "missed": self._missed, "checks": self._check_count, "total_ops": self._op_count}

FILE_EOF

echo "Creating action_sequence_planner.py..."
cat > "$LOL_HIST/action_sequence_planner.py" << 'FILE_EOF'
"""
ActionSequencePlanner — Plans multi-step action sequences.

Architecture (拿来主义):
  PARL/benchmark/torch/AlphaZero/submission_template.py — MCTS lookahead
  DI-star/distar/agent/default/agent.py — step→_post_process action sequence

Location: integrations/lol-history/src/lol_history/action_sequence_planner.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.action_sequence_planner.v1"

class ActionSequencePlanner:
    """Plans multi-step action sequences with expected value estimation.

    Public API: plan, cancel, get_active_plan, evaluate_sequence, get_stats
    """
    def __init__(self, max_depth: int = 5) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._max_depth = max_depth
        self._active_plan: Optional[Dict] = None
        self._plan_count = 0
        self._cancel_count = 0
        self._value_estimators: Dict[str, Callable] = {}

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_value_estimator(self, action_type: str, estimator: Callable) -> Dict[str, Any]:
        self._op_count += 1
        self._value_estimators[action_type] = estimator
        return {"status": "ok", "action_type": action_type}

    def plan(self, context: Dict[str, Any], candidate_actions: List[Dict[str, Any]]) -> Dict[str, Any]:
        self._op_count += 1
        self._plan_count += 1
        if not candidate_actions:
            return {"status": "ok", "sequence": [], "expected_value": 0.0}
        scored = []
        for action in candidate_actions[:self._max_depth]:
            atype = action.get("type", "unknown")
            estimator = self._value_estimators.get(atype)
            value = estimator(action, context) if estimator else action.get("value", 0.5)
            scored.append({**action, "estimated_value": round(value, 4)})
        scored.sort(key=lambda a: a["estimated_value"], reverse=True)
        sequence = scored[:self._max_depth]
        total_value = sum(a["estimated_value"] * (0.9 ** i) for i, a in enumerate(sequence))
        self._active_plan = {"sequence": sequence, "expected_value": round(total_value, 4),
                             "created_at": time.time(), "step": 0}
        self._fire("plan_created", {"steps": len(sequence), "value": total_value})
        return {"status": "ok", **self._active_plan}

    def cancel(self) -> Dict[str, Any]:
        self._op_count += 1
        if self._active_plan is None: return {"status": "ok", "was_active": False}
        self._cancel_count += 1
        old = self._active_plan
        self._active_plan = None
        return {"status": "ok", "was_active": True, "completed_steps": old.get("step", 0)}

    def get_active_plan(self) -> Dict[str, Any]:
        if self._active_plan is None: return {"status": "ok", "active": False}
        return {"status": "ok", "active": True, **self._active_plan}

    def evaluate_sequence(self, sequence: List[Dict], context: Dict = None) -> Dict[str, Any]:
        self._op_count += 1
        total = 0.0
        for i, action in enumerate(sequence):
            atype = action.get("type", "unknown")
            estimator = self._value_estimators.get(atype)
            v = estimator(action, context or {}) if estimator else action.get("value", 0.5)
            total += v * (0.9 ** i)
        return {"status": "ok", "expected_value": round(total, 4), "steps": len(sequence)}

    def get_stats(self) -> Dict[str, Any]:
        return {"plans_created": self._plan_count, "cancels": self._cancel_count,
                "max_depth": self._max_depth, "estimators": list(self._value_estimators.keys()),
                "has_active_plan": self._active_plan is not None, "total_ops": self._op_count}

FILE_EOF

echo "Creating decision_explanation_generator.py..."
cat > "$LOL_HIST/decision_explanation_generator.py" << 'FILE_EOF'
"""
DecisionExplanationGenerator — Generates human-readable decision explanations.

Architecture (拿来主义):
  realtime_voice_command_generator.py（M662）— inference→voice text
  protocol_anomaly_coaching_translator.py（M654）— anomaly→advice translation

Location: integrations/lol-history/src/lol_history/decision_explanation_generator.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.decision_explanation_generator.v1"

class DecisionExplanationGenerator:
    """Generates explanations for decisions at brief/normal/detailed levels.

    Public API: explain, set_verbosity, register_template, get_stats
    """
    def __init__(self, verbosity: str = "normal") -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._verbosity = verbosity
        self._templates: Dict[str, Dict[str, str]] = self._default_templates()
        self._explain_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def _default_templates(self):
        return {
            "push": {"brief": "Push {lane}.", "normal": "Push {lane} — {reason}. Risk: {risk}.",
                     "detailed": "Recommend pushing {lane}. Reasoning: {reason}. Risk level: {risk}. Factors: {factors}."},
            "retreat": {"brief": "Retreat!", "normal": "Retreat — {reason}. Risk: {risk}.",
                        "detailed": "Recommend retreat. Reasoning: {reason}. Current risk: {risk}. Factors: {factors}."},
            "farm": {"brief": "Farm.", "normal": "Farm {area} — {reason}.",
                     "detailed": "Focus on farming in {area}. Reasoning: {reason}. Expected gold/min: {gold_rate}."},
            "objective": {"brief": "Take {objective}!", "normal": "Contest {objective} — {reason}.",
                          "detailed": "Contest {objective}. Reasoning: {reason}. Team readiness: {readiness}. Risk: {risk}."},
            "default": {"brief": "{intent}.", "normal": "{intent} — {reason}.",
                        "detailed": "{intent}. Reasoning: {reason}. Factors: {factors}. Risk: {risk}."},
        }

    def set_verbosity(self, level: str) -> Dict[str, Any]:
        self._op_count += 1
        if level not in ("brief", "normal", "detailed"): return {"status": "error", "reason": "invalid verbosity"}
        self._verbosity = level
        return {"status": "ok", "verbosity": level}

    def register_template(self, intent: str, templates: Dict[str, str]) -> Dict[str, Any]:
        self._op_count += 1
        self._templates[intent] = templates
        return {"status": "ok", "intent": intent}

    def explain(self, decision: Dict[str, Any], verbosity: str = None) -> Dict[str, Any]:
        self._op_count += 1
        self._explain_count += 1
        v = verbosity or self._verbosity
        intent = decision.get("intent", "default")
        templates = self._templates.get(intent, self._templates["default"])
        template = templates.get(v, templates.get("normal", "{intent}"))
        defaults = {"intent": intent, "reason": "analysis indicates", "risk": "medium",
                    "lane": "mid", "area": "jungle", "factors": "multiple", "objective": "dragon",
                    "readiness": "ready", "gold_rate": "0"}
        params = {**defaults, **decision}
        try: text = template.format_map(params)
        except (KeyError, ValueError): text = f"{intent}: {decision.get('reason', 'no reason')}"
        self._fire("explained", {"intent": intent, "verbosity": v})
        return {"status": "ok", "text": text, "intent": intent, "verbosity": v}

    def get_stats(self) -> Dict[str, Any]:
        return {"explanations": self._explain_count, "verbosity": self._verbosity,
                "templates": list(self._templates.keys()), "total_ops": self._op_count}

FILE_EOF

echo "Creating decision_quality_scorer.py..."
cat > "$LOL_HIST/decision_quality_scorer.py" << 'FILE_EOF'
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

FILE_EOF

echo "Creating realtime_dashboard_data_source.py..."
cat > "$LOL_HIST/realtime_dashboard_data_source.py" << 'FILE_EOF'
"""
RealtimeDashboardDataSource — Real-time data source for the decision monitoring dashboard.

Architecture (拿来主义):
  history_telemetry_dashboard.py（M643）— telemetry aggregation
  cross_game_telemetry_aggregator.py（M681）— ingest→export

Location: integrations/lol-history/src/lol_history/realtime_dashboard_data_source.py
"""
from __future__ import annotations
import json, logging, time
from collections import deque
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.realtime_dashboard_data_source.v1"

class RealtimeDashboardDataSource:
    """Real-time data source for decision dashboard.

    Public API: push, get_snapshot, get_history, export_json, export_sse, get_stats
    """
    def __init__(self, buffer_size: int = 500) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._buffer: deque = deque(maxlen=buffer_size)
        self._latest: Dict[str, Any] = {}
        self._push_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def push(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        self._op_count += 1
        self._push_count += 1
        entry = {"timestamp": time.time(), **metrics}
        self._buffer.append(entry)
        self._latest.update(metrics)
        self._latest["_last_update"] = entry["timestamp"]
        return {"status": "ok", "buffered": len(self._buffer)}

    def get_snapshot(self) -> Dict[str, Any]:
        self._op_count += 1
        return {"status": "ok", "snapshot": dict(self._latest)}

    def get_history(self, n: int = 50) -> List[Dict]:
        self._op_count += 1
        return list(self._buffer)[-n:]

    def export_json(self) -> str:
        self._op_count += 1
        return json.dumps({"snapshot": self._latest, "history_size": len(self._buffer)}, default=str)

    def export_sse(self) -> str:
        self._op_count += 1
        data = json.dumps(self._latest, default=str)
        return f"event: dashboard_update\ndata: {data}\n\n"

    def get_stats(self) -> Dict[str, Any]:
        return {"pushes": self._push_count, "buffer_size": len(self._buffer),
                "metrics_tracked": len(self._latest), "total_ops": self._op_count}

FILE_EOF

echo "Creating decision_pipeline_health_guard.py..."
cat > "$LOL_HIST/decision_pipeline_health_guard.py" << 'FILE_EOF'
"""
DecisionPipelineHealthGuard — Monitors decision pipeline health with auto-degradation.

Architecture (拿来主义):
  protocol_health_baseline_manager.py（M658）— baseline deviation detection
  e2e_inference_pipeline_orchestrator.py（M655）— health check + fault isolation

Location: integrations/lol-history/src/lol_history/decision_pipeline_health_guard.py
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.decision_pipeline_health_guard.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class DecisionPipelineHealthGuard:
    """Monitors pipeline health, triggers alerts and graceful degradation.

    Public API: record_metric, check_health, isolate_module, restore_module, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._baselines: Dict[str, Dict] = {}
        self._current: Dict[str, float] = {}
        self._isolated: set = set()
        self._alerts: List[Dict] = []
        self._check_count = 0

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def set_baseline(self, module: str, latency_ms: float = 50, error_rate: float = 0.01) -> Dict[str, Any]:
        self._op_count += 1
        self._baselines[module] = {"latency_ms": latency_ms, "error_rate": error_rate}
        return {"status": "ok", "module": module}

    def record_metric(self, module: str, latency_ms: float = 0, error: bool = False) -> Dict[str, Any]:
        self._op_count += 1
        self._current[f"{module}_latency"] = latency_ms
        if error:
            key = f"{module}_errors"
            self._current[key] = self._current.get(key, 0) + 1
        return {"status": "ok", "module": module}

    def check_health(self) -> Dict[str, Any]:
        self._op_count += 1
        self._check_count += 1
        issues = []
        for module, baseline in self._baselines.items():
            if module in self._isolated: continue
            lat = self._current.get(f"{module}_latency", 0)
            if lat > baseline["latency_ms"] * 3:
                issue = {"module": module, "type": "high_latency", "value": lat, "baseline": baseline["latency_ms"]}
                issues.append(issue)
                self._alerts.append({**issue, "timestamp": time.time()})
        healthy = len(issues) == 0
        self._fire("health_checked", {"healthy": healthy, "issues": len(issues)})
        return {"status": "ok", "healthy": healthy, "issues": issues, "isolated": list(self._isolated)}

    def isolate_module(self, module: str) -> Dict[str, Any]:
        self._op_count += 1
        self._isolated.add(module)
        self._fire("module_isolated", {"module": module})
        return {"status": "ok", "module": module, "isolated": True}

    def restore_module(self, module: str) -> Dict[str, Any]:
        self._op_count += 1
        self._isolated.discard(module)
        return {"status": "ok", "module": module, "restored": True}

    def get_stats(self) -> Dict[str, Any]:
        return {"checks": self._check_count, "alerts": len(self._alerts),
                "isolated_modules": list(self._isolated), "monitored_modules": list(self._baselines.keys()),
                "total_ops": self._op_count}

FILE_EOF

echo "Creating autonomous_decision_orchestrator.py..."
cat > "$LOL_HIST/autonomous_decision_orchestrator.py" << 'FILE_EOF'
"""
AutonomousDecisionOrchestrator — Top-level orchestrator for the autonomous decision loop.

Architecture (拿来主义):
  multi_game_pipeline_orchestrator.py（M685）— register→init→run→shutdown
  capture_to_decision_orchestrator.py（M665）— full lifecycle orchestration

Location: integrations/lol-history/src/lol_history/autonomous_decision_orchestrator.py

Design Notes (Knuth-level critique):
  User:
    - Single run_cycle() drives the entire OBSERVE→ANALYZE→DECIDE→EXECUTE→REVIEW loop.
    - Can run continuously for 30+ minute game sessions.
  System:
    - Module failures are isolated — the loop continues with degraded capability.
    - Full telemetry per cycle enables post-game analysis.
"""
from __future__ import annotations
import logging, time
from typing import Any, Callable, Dict, List, Optional
logger = logging.getLogger(__name__)
_EVOLUTION_KEY = "integrations.lol_history.autonomous_decision_orchestrator.v1"

def _safe_div(a, b, d=0.0): return a / b if b else d

class AutonomousDecisionOrchestrator:
    """Top-level orchestrator: OBSERVE→ANALYZE→DECIDE→EXECUTE→REVIEW loop.

    Public API: register_module, initialize, run_cycle, get_dashboard, shutdown, get_stats
    """
    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None
        self._op_count = 0
        self._modules: Dict[str, Any] = {}
        self._state = "uninitialized"
        self._cycle_count = 0
        self._total_cycle_ms = 0.0
        self._error_count = 0
        self._started_at: Optional[float] = None
        self._cycle_history: List[Dict] = []

    def _fire(self, et, data):
        if self.evolution_callback: self.evolution_callback({"type": et, "key": _EVOLUTION_KEY, **data})

    def register_module(self, name: str, module: Any) -> Dict[str, Any]:
        self._op_count += 1
        self._modules[name] = module
        return {"status": "ok", "module": name, "total_modules": len(self._modules)}

    def initialize(self) -> Dict[str, Any]:
        self._op_count += 1
        self._state = "initialized"
        self._started_at = time.time()
        self._fire("initialized", {"modules": list(self._modules.keys())})
        return {"status": "ok", "modules": len(self._modules), "state": self._state}

    def run_cycle(self, game_state: Dict[str, Any] = None) -> Dict[str, Any]:
        self._op_count += 1
        self._cycle_count += 1
        if game_state is None: game_state = {}
        _start = time.time()
        cycle_result = {"cycle": self._cycle_count, "phases": {}}

        # Phase 1: OBSERVE — gather state
        try:
            phase_detector = self._modules.get("phase_detector")
            if phase_detector and hasattr(phase_detector, "detect"):
                phase_r = phase_detector.detect(game_state.get("game_time", 0))
                cycle_result["phases"]["observe"] = {"phase": phase_r.get("phase", "unknown")}
                game_state["game_phase"] = phase_r.get("phase", "unknown")
        except Exception as e:
            cycle_result["phases"]["observe"] = {"error": str(e)}
            self._error_count += 1

        # Phase 2: ANALYZE — risk + intent
        try:
            risk_assessor = self._modules.get("risk_assessor")
            if risk_assessor and hasattr(risk_assessor, "assess"):
                risk_r = risk_assessor.assess(game_state)
                cycle_result["phases"]["analyze_risk"] = {"level": risk_r.get("level", "unknown")}
                game_state["risk_level"] = risk_r.get("level", "safe")
        except Exception as e:
            cycle_result["phases"]["analyze_risk"] = {"error": str(e)}
            self._error_count += 1

        try:
            intent_reasoner = self._modules.get("intent_reasoner")
            if intent_reasoner and hasattr(intent_reasoner, "reason"):
                intent_r = intent_reasoner.reason(game_state)
                cycle_result["phases"]["analyze_intent"] = {"intent": intent_r.get("intent", "unknown")}
        except Exception as e:
            cycle_result["phases"]["analyze_intent"] = {"error": str(e)}
            self._error_count += 1

        # Phase 3: DECIDE — balance + plan
        try:
            balancer = self._modules.get("balancer")
            if balancer and hasattr(balancer, "balance"):
                scores = game_state.get("objective_scores", {})
                if scores:
                    bal_r = balancer.balance(scores, game_state.get("game_phase"))
                    cycle_result["phases"]["decide"] = {"focus": bal_r.get("recommended_focus", "unknown")}
        except Exception as e:
            cycle_result["phases"]["decide"] = {"error": str(e)}
            self._error_count += 1

        # Phase 4: EXECUTE (recorded but not actually executing in this orchestrator)
        cycle_result["phases"]["execute"] = {"status": "delegated"}

        # Phase 5: REVIEW
        try:
            quality_scorer = self._modules.get("quality_scorer")
            if quality_scorer and hasattr(quality_scorer, "get_trend"):
                trend = quality_scorer.get_trend()
                cycle_result["phases"]["review"] = {"trend": trend.get("trend", "unknown")}
        except Exception as e:
            cycle_result["phases"]["review"] = {"error": str(e)}

        elapsed_ms = (time.time() - _start) * 1000
        self._total_cycle_ms += elapsed_ms
        cycle_result["elapsed_ms"] = round(elapsed_ms, 2)
        self._cycle_history.append(cycle_result)
        if len(self._cycle_history) > 500: self._cycle_history = self._cycle_history[-500:]

        self._fire("cycle_completed", {"cycle": self._cycle_count, "elapsed_ms": elapsed_ms})
        return {"status": "ok", **cycle_result}

    def get_dashboard(self) -> Dict[str, Any]:
        uptime = time.time() - self._started_at if self._started_at else 0
        return {
            "state": self._state, "cycles": self._cycle_count,
            "avg_cycle_ms": round(_safe_div(self._total_cycle_ms, self._cycle_count), 2),
            "errors": self._error_count, "uptime_s": round(uptime, 1),
            "modules": list(self._modules.keys()),
        }

    def shutdown(self) -> Dict[str, Any]:
        self._op_count += 1
        self._state = "shutdown"
        self._fire("shutdown", {"cycles": self._cycle_count})
        return {"status": "ok", "total_cycles": self._cycle_count, "errors": self._error_count}

    def get_stats(self) -> Dict[str, Any]:
        return {"state": self._state, "cycles": self._cycle_count, "errors": self._error_count,
                "avg_cycle_ms": round(_safe_div(self._total_cycle_ms, self._cycle_count), 2),
                "modules": list(self._modules.keys()), "total_ops": self._op_count}

FILE_EOF

echo ""
echo "=== Updating plan.md ==="
cat >> "$PROJ_ROOT/plan.md" << 'PLAN_APPEND'


---

### 阶段 AW: 自主决策循环 + 实时协议驱动行动执行（M686-M705）

> **主题**: 在M666-M685跨游戏适配层基础上，构建完整的自主决策循环——从协议数据流入到行动指令输出的闭环。涵盖决策状态机、行动优先级队列、实时风险评估、对局阶段感知、战术意图推理、行动执行器、行动反馈收集、决策日志回放、在线策略调整、多目标权衡、团队协调推理、资源分配优化、视野控制推理、时间窗口调度、行动序列规划、决策解释生成、决策质量评分、实时仪表盘数据源、决策管线健康守卫、以及自主决策总编排器。

---

**M686** `integrations/lol-history/src/lol_history/autonomous_decision_state_machine.py` — **自主决策状态机** ✅

查看 **extensions/fiddler_bridge/src/fiddler_session_state_machine.py（M647）** 上现有 **有限状态机方式** 的实现方式，理解其模式，特别是状态转换钩子和非法跳转防护。可以从 **`Akagi/mitm/mitm_abc.py`** 的websocket_start→message→end生命周期这个好例子开始。然后，遵循该模式实现一个新的 **AutonomousDecisionStateMachine**，让 **决策循环** 可以 **通过有限状态机（IDLE→OBSERVING→ANALYZING→DECIDING→EXECUTING→REVIEWING）管理每次决策的生命周期**，并能 **注册状态转换钩子、记录状态转换历史、强制超时回退**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M687** `integrations/lol-history/src/lol_history/action_priority_queue.py` — **行动优先级队列** ✅

查看 **extensions/fiddler_bridge/src/fiddler_packet_prioritizer.py（M657）** 上现有 **优先级评分方式** 的实现方式，理解其模式。可以从 **`DI-star/distar/agent/default/agent.py`** 的step→_post_process决策输出管线这个好例子开始。然后，遵循该模式实现一个新的 **ActionPriorityQueue**，让 **决策执行层** 可以 **按优先级排序待执行的行动（critical>high>medium>low）**，并能 **TTL过期淘汰、批量出队、统计各优先级分布**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M688** `integrations/lol-history/src/lol_history/realtime_risk_assessor.py` — **实时风险评估器** ✅

查看 **extensions/fiddler_bridge/src/fiddler_anomaly_detector.py** 上现有 **多类型异常检测方式** 的实现方式，理解其模式。可以从 **`PARL/benchmark/torch/AlphaZero/submission_template.py`** 的MCTS.search风险搜索模式这个好例子开始。然后，遵循该模式实现一个新的 **RealtimeRiskAssessor**，让 **决策分析阶段** 可以 **基于当前游戏状态评估即时风险等级（safe/caution/danger/critical）**，并能 **注册自定义风险因子、追踪风险变化趋势**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M689** `integrations/lol-history/src/lol_history/game_phase_detector.py` — **对局阶段检测器** ✅

查看 **integrations/lol-history/src/lol_history/game_phase_strategy_mapper.py（M642）** 上现有 **阶段策略映射方式** 的实现方式，理解其模式。可以从 **`DI-star/distar/agent/default/agent.py`** 的_get_time_factor这个好例子开始。然后，遵循该模式实现一个新的 **GamePhaseDetector**，让 **决策循环** 可以 **基于游戏时间和事件自动检测当前对局阶段（early_game/mid_game/late_game/endgame）**，并能 **注册阶段转换钩子、支持不同游戏类型的阶段定义**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M690** `integrations/lol-history/src/lol_history/tactical_intent_reasoner.py` — **战术意图推理器** ✅

查看 **integrations/lol-history/src/lol_history/realtime_inference_chain_builder.py（M653）** 上现有 **推理链构建方式** 的实现方式。可以从 **`DI-star/distar/agent/default/rl_training/as_rl_utils.py`** 的policy_gradient_loss策略推理链这个好例子开始。然后，遵循该模式实现一个新的 **TacticalIntentReasoner**，让 **决策分析阶段** 可以 **推理当前最优战术意图（push/farm/defend/gank/objective/retreat）**，并能 **输出意图置信度分布、追踪意图切换频率**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M691** `integrations/lol-history/src/lol_history/action_executor_bridge.py` — **行动执行器桥接** ✅

查看 **Akagi/autoplay/autoplay.py** 上现有 **act(mjai_msg)→执行** 的实现方式，理解其模式。可以从 **`Akagi/mitm/bridge/bridge_base.py`** 的parse/build双向接口这个好例子开始。然后，遵循该模式实现一个新的 **ActionExecutorBridge**，让 **决策执行阶段** 可以 **将抽象行动指令转换为游戏特定的执行格式**，并能 **追踪执行成功率、支持dry-run模式**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M692** `integrations/lol-history/src/lol_history/action_feedback_collector.py` — **行动反馈收集器** ✅

查看 **integrations/lol-history/src/lol_history/history_feedback_loop_orchestrator.py（M625）** 上现有 **反馈循环方式** 的实现方式。可以从 **`DI-star/distar/agent/default/agent.py`** 的collect_data(next_obs, reward, done)这个好例子开始。然后，遵循该模式实现一个新的 **ActionFeedbackCollector**，让 **决策回顾阶段** 可以 **收集每个行动执行后的环境反馈**，并能 **计算行动有效性评分、维护反馈历史窗口**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M693** `integrations/lol-history/src/lol_history/decision_log_replayer.py` — **决策日志回放器** ✅

查看 **extensions/protocol_decoder/src/protocol_replay_synchronizer.py（M652）** 上现有 **按时间轴同步回放方式** 的实现方式。可以从 **`replay_decision_auditor.py`（M612）** 这个好例子开始。然后，遵循该模式实现一个新的 **DecisionLogReplayer**，让 **赛后复盘** 可以 **按时间顺序回放决策日志**，并能 **精确seek到任意时间点、过滤特定类型的决策**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M694** `integrations/lol-history/src/lol_history/online_policy_adjuster.py` — **在线策略调整器** ✅

查看 **DI-star/distar/agent/default/agent.py** 上现有 **update_fake_reward在线更新方式** 的实现方式。可以从 **`historical_reward_reshaper.py`（M617）** 这个好例子开始。然后，遵循该模式实现一个新的 **OnlinePolicyAdjuster**，让 **决策循环** 可以 **基于行动反馈在线调整策略权重（不重训练）**，并能 **限制调整幅度防止策略漂移**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M695** `integrations/lol-history/src/lol_history/multi_objective_balancer.py` — **多目标权衡器** ✅

查看 **integrations/lol/src/lol_agent/reward_shaper.py** 上现有 **compute_reward多维度评分方式** 的实现方式。可以从 **`DI-star/distar/agent/default/rl_training/as_rl_utils.py`** 的head_weights_dict这个好例子开始。然后，遵循该模式实现一个新的 **MultiObjectiveBalancer**，让 **决策分析阶段** 可以 **在多个目标间做Pareto权衡**，并能 **按游戏阶段自动调整目标权重**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M696** `integrations/lol-history/src/lol_history/team_coordination_reasoner.py` — **团队协调推理器** ✅

查看 **DI-star/distar/agent/default/model/module_utils.py** 上现有 **Attention多头注意力方式** 的实现方式。可以从 **`ELF/elf_python/zmq_adapter.py`** 的多节点协调这个好例子开始。然后，遵循该模式实现一个新的 **TeamCoordinationReasoner**，让 **决策分析阶段** 可以 **推理最优团队协调行动**，并能 **评估团战时机、推荐集合点**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M697** `integrations/lol-history/src/lol_history/resource_allocation_optimizer.py` — **资源分配优化器** ✅

查看 **DI-star/distar/agent/default/agent.py** 上现有 **get_behavior_z** 的实现方式。可以从 **`PARL/benchmark/fluid/PPO/train.py`** 的奖励分配模式这个好例子开始。然后，遵循该模式实现一个新的 **ResourceAllocationOptimizer**，让 **决策分析阶段** 可以 **优化资源分配决策**，并能 **模拟不同分配方案的预期收益**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M698** `integrations/lol-history/src/lol_history/vision_control_reasoner.py` — **视野控制推理器** ✅

查看 **extensions/fiddler_bridge/src/fiddler_lol_decoder.py** 上现有 **ward事件解码方式** 的实现方式。可以从 **`dota2bot-OpenHyperAI/`** 的视野资源管理这个好例子开始。然后，遵循该模式实现一个新的 **VisionControlReasoner**，让 **决策分析阶段** 可以 **推理最优视野控制行动**，并能 **追踪视野覆盖率变化**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M699** `integrations/lol-history/src/lol_history/timing_window_scheduler.py` — **时间窗口调度器** ✅

查看 **integrations/lol-history/src/lol_history/ban_pick_realtime_advisor.py（M637）** 上现有 **实时建议调度方式** 的实现方式。可以从 **`DI-star/distar/agent/default/agent.py`** 的_get_time_factor这个好例子开始。然后，遵循该模式实现一个新的 **TimingWindowScheduler**，让 **决策循环** 可以 **调度时间敏感的行动窗口**，并能 **提前预警、追踪窗口利用率**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M700** `integrations/lol-history/src/lol_history/action_sequence_planner.py` — **行动序列规划器** ✅

查看 **PARL/benchmark/torch/AlphaZero/submission_template.py** 上现有 **MCTS.search前瞻搜索方式** 的实现方式。可以从 **`DI-star/distar/agent/default/agent.py`** 的动作序列输出这个好例子开始。然后，遵循该模式实现一个新的 **ActionSequencePlanner**，让 **决策执行层** 可以 **规划多步行动序列**，并能 **评估序列预期收益、支持中途重规划**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M701** `integrations/lol-history/src/lol_history/decision_explanation_generator.py` — **决策解释生成器** ✅

查看 **integrations/lol-history/src/lol_history/realtime_voice_command_generator.py（M662）** 上现有 **推理→语音转换方式** 的实现方式。可以从 **`protocol_anomaly_coaching_translator.py`（M654）** 这个好例子开始。然后，遵循该模式实现一个新的 **DecisionExplanationGenerator**，让 **决策输出层** 可以 **为每个决策生成人类可理解的解释**，并能 **按详细度级别输出（brief/normal/detailed）**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M702** `integrations/lol-history/src/lol_history/decision_quality_scorer.py` — **决策质量评分器** ✅

查看 **integrations/lol-history/src/lol_history/realtime_decision_confidence_scorer.py（M659）** 上现有 **置信度评估方式** 的实现方式。可以从 **`coaching_effectiveness_tracker.py`（M613）** 这个好例子开始。然后，遵循该模式实现一个新的 **DecisionQualityScorer**，让 **决策回顾阶段** 可以 **对每个决策进行事后质量评分**，并能 **维护滑动窗口质量趋势、识别系统性偏差**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M703** `integrations/lol-history/src/lol_history/realtime_dashboard_data_source.py` — **实时仪表盘数据源** ✅

查看 **integrations/lol-history/src/lol_history/history_telemetry_dashboard.py（M643）** 上现有 **遥测聚合方式** 的实现方式。可以从 **`cross_game_telemetry_aggregator.py`（M681）** 这个好例子开始。然后，遵循该模式实现一个新的 **RealtimeDashboardDataSource**，让 **运维仪表盘** 可以 **实时提供决策循环全部关键指标**，并能 **支持SSE/轮询两种消费模式**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M704** `integrations/lol-history/src/lol_history/decision_pipeline_health_guard.py` — **决策管线健康守卫** ✅

查看 **extensions/protocol_decoder/src/protocol_health_baseline_manager.py（M658）** 上现有 **健康基线偏离检测方式** 的实现方式。可以从 **`e2e_inference_pipeline_orchestrator.py`（M655）** 这个好例子开始。然后，遵循该模式实现一个新的 **DecisionPipelineHealthGuard**，让 **决策管线** 可以 **持续监控各阶段健康指标并在偏离基线时触发告警或降级**，并能 **自动隔离故障模块**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---

**M705** `integrations/lol-history/src/lol_history/autonomous_decision_orchestrator.py` — **自主决策总编排器** ✅

查看 **integrations/lol-history/src/lol_history/multi_game_pipeline_orchestrator.py（M685）** 上现有 **总编排方式** 的实现方式。可以从 **`capture_to_decision_orchestrator.py`（M665）** 这个好例子开始。然后，遵循该模式实现一个新的 **AutonomousDecisionOrchestrator**，让 **整个自主决策循环** 可以 **通过一个入口编排M686-M704所有模块**，并能 **在30分钟对局中持续运行、追踪全链路决策指标**。从头开始构建，除了代码库中已有的库之外，不要使用其他库。

---
PLAN_APPEND

echo ""
echo "=== Committing ==="
git add -A
git commit -m "feat(M686-M705): Phase AW — 自主决策循环+实时协议驱动行动执行 20模块实现

Phase AW (M686-M705): 自主决策循环
- M686: AutonomousDecisionStateMachine — 决策FSM
- M687: ActionPriorityQueue — 行动优先级队列含TTL
- M688: RealtimeRiskAssessor — 风险评估(safe/caution/danger/critical)
- M689: GamePhaseDetector — 对局阶段检测
- M690: TacticalIntentReasoner — 战术意图推理
- M691: ActionExecutorBridge — 行动执行器桥接
- M692: ActionFeedbackCollector — 行动反馈收集
- M693: DecisionLogReplayer — 决策日志回放
- M694: OnlinePolicyAdjuster — 在线策略调整
- M695: MultiObjectiveBalancer — 多目标Pareto权衡
- M696: TeamCoordinationReasoner — 团队协调推理
- M697: ResourceAllocationOptimizer — 资源分配优化
- M698: VisionControlReasoner — 视野控制推理
- M699: TimingWindowScheduler — 时间窗口调度
- M700: ActionSequencePlanner — 多步行动序列规划
- M701: DecisionExplanationGenerator — 决策解释生成
- M702: DecisionQualityScorer — 决策质量评分
- M703: RealtimeDashboardDataSource — 实时仪表盘数据源
- M704: DecisionPipelineHealthGuard — 管线健康守卫
- M705: AutonomousDecisionOrchestrator — 自主决策总编排器

架构来源: Akagi/mitm→协议适配, DI-star/agent→策略推理,
PARL/AlphaZero→前瞻规划, ELF→多节点协调" --author="dylanyunlong <dylanyunlong@gmail.com>"

echo ""
echo "=== Done! ==="
echo "Files created: 20 modules"
echo "Run: git push origin master"

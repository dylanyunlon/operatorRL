"""
Unified Decision Framework — cross-game generic decision abstraction.

Provides a game-agnostic decision layer that maps arbitrary game states
to a universal action vocabulary (engage, retreat, farm, defend, etc.).
Supports constraints, decision history, feedback recording, and
self-evolution integration.

Location: agentos/governance/unified_decision_framework.py

Reference (拿来主義):
  - DI-star/distar/agent/default/model/head/action_type_head.py: action classification
  - open_spiel/open_spiel/python/policy.py: game-agnostic policy interface
  - agentos/governance/evolution_orchestrator.py: cross-game orchestration
  - integrations/lol/src/lol_agent/decision_engine.py: LoL-specific decision
  - integrations/dota2/src/dota2_agent/bot_commander.py: Dota2 decision dispatch
  - Akagi/akagi/mjai/bot/bot.py: Mahjong action selection

Design Notes (Knuth-level critique):
  User:
    - register_type() defines the action vocabulary before decisions are made.
    - constraints filter out actions that violate game-specific invariants.
    - record_feedback() closes the RL loop — every decision is traceable.
  System:
    - Decision IDs are monotonic — efficient for sequential replay.
    - History is bounded — configurable via max_history parameter.
    - Constraint evaluation is O(C) per decision where C = constraint count.
"""

from __future__ import annotations

import collections
import logging
import math
import time
import uuid
from typing import Any, Callable, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "agentos.governance.unified_decision_framework.v1"

# ---------------------------------------------------------------------------
# Default action types — common across MOBA + Mahjong
# ---------------------------------------------------------------------------
_DEFAULT_ACTIONS = {
    "engage": {"description": "Initiate combat / confrontation", "aggression": 1.0},
    "retreat": {"description": "Withdraw from danger", "aggression": 0.0},
    "farm":    {"description": "Acquire resources passively", "aggression": 0.2},
    "defend":  {"description": "Protect objective / position", "aggression": 0.4},
    "push":    {"description": "Advance toward enemy objective", "aggression": 0.7},
    "roam":    {"description": "Move across map for information / ganks", "aggression": 0.5},
    "wait":    {"description": "Hold position and observe", "aggression": 0.1},
}

# ---------------------------------------------------------------------------
# Constraint registry
# ---------------------------------------------------------------------------
_BUILTIN_CONSTRAINTS: Dict[str, Callable[[Dict[str, Any], str], bool]] = {}


def _register_constraint(name: str):
    def decorator(fn):
        _BUILTIN_CONSTRAINTS[name] = fn
        return fn
    return decorator


@_register_constraint("no_engage_below_30_health")
def _no_engage_low_hp(state: Dict[str, Any], action: str) -> bool:
    """Block engage when health_pct < 0.3."""
    if action != "engage":
        return True
    hp = state.get("health_pct", 1.0)
    return hp >= 0.3


@_register_constraint("no_push_early_game")
def _no_push_early(state: Dict[str, Any], action: str) -> bool:
    """Block push before 10 minutes."""
    if action != "push":
        return True
    gt = state.get("game_time", 0.0)
    return gt >= 600.0


@_register_constraint("no_roam_without_vision")
def _no_roam_blind(state: Dict[str, Any], action: str) -> bool:
    if action != "roam":
        return True
    vision = state.get("vision_score", 10)
    return vision >= 5


# ---------------------------------------------------------------------------
# Decision record
# ---------------------------------------------------------------------------

class _DecisionRecord:
    __slots__ = (
        "decision_id", "action", "confidence", "state_summary",
        "ts", "feedback",
    )

    def __init__(
        self,
        decision_id: str,
        action: str,
        confidence: float,
        state_summary: Dict[str, Any],
    ) -> None:
        self.decision_id = decision_id
        self.action = action
        self.confidence = confidence
        self.state_summary = state_summary
        self.ts = time.time()
        self.feedback: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "decision_id": self.decision_id,
            "action": self.action,
            "confidence": self.confidence,
            "state_summary": self.state_summary,
            "ts": self.ts,
        }
        if self.feedback is not None:
            d["feedback"] = self.feedback
        return d


# ===========================================================================
# Main class
# ===========================================================================

class UnifiedDecisionFramework:
    """Cross-game universal decision layer.

    Attributes:
        decision_count: Total decisions made.
        decision_types: Set of registered action type names.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, *, max_history: int = 1000) -> None:
        self._types: Dict[str, Dict[str, Any]] = {}
        self._decision_count: int = 0
        self._next_id: int = 0
        self._history: Deque[_DecisionRecord] = collections.deque(maxlen=max_history)
        self._feedback_index: Dict[str, _DecisionRecord] = {}
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def decision_count(self) -> int:
        return self._decision_count

    @property
    def decision_types(self) -> Set[str]:
        return set(self._types.keys())

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_type(self, name: str, description: str = "", **kwargs: Any) -> None:
        """Register an action type."""
        self._types[name] = {
            "description": description,
            **kwargs,
        }
        self._fire_evolution({"action": "register_type", "name": name})

    # ------------------------------------------------------------------
    # Decision engine
    # ------------------------------------------------------------------

    def _score_action(self, action: str, state: Dict[str, Any]) -> float:
        """Heuristic scoring for an action given the game state.

        This is the simplified rule-based scorer — in production it
        would delegate to a learned policy.  The heuristic uses
        gold_lead / game_time / health_pct as signals.

        Reference: integrations/lol decision_engine.py scoring logic.
        """
        meta = self._types.get(action, _DEFAULT_ACTIONS.get(action, {}))
        aggression = meta.get("aggression", 0.5)

        game = state.get("game", "")
        gold_lead = state.get("gold_lead", state.get("team_gold_lead", state.get("net_worth_lead", 0)))
        hp = state.get("health_pct", 1.0)
        gt = state.get("game_time", 0.0)

        # Base confidence from aggression + state signals
        score = 0.5

        # Positive gold lead → favour aggressive actions
        if isinstance(gold_lead, (int, float)) and gold_lead > 0:
            score += aggression * 0.3
        elif isinstance(gold_lead, (int, float)) and gold_lead < 0:
            score += (1.0 - aggression) * 0.2

        # Low health → penalise aggression
        if isinstance(hp, (int, float)) and hp < 0.3:
            score -= aggression * 0.4

        # Late game → boost engage / push
        if isinstance(gt, (int, float)) and gt > 1500:
            if action in ("engage", "push"):
                score += 0.1

        return max(0.01, min(1.0, score))

    def decide(
        self,
        state: Dict[str, Any],
        constraints: List[str] | None = None,
    ) -> Dict[str, Any]:
        """Make a decision for the given game state.

        Args:
            state: Game state dict with game-specific keys.
            constraints: Optional list of constraint names to apply.

        Returns:
            Decision dict with action, confidence, decision_id.
        """
        active_constraints = constraints or []

        # Score all registered types + defaults
        candidates: Dict[str, float] = {}
        all_types = set(self._types.keys()) | set(_DEFAULT_ACTIONS.keys())

        for action in all_types:
            # Check constraints
            blocked = False
            for c_name in active_constraints:
                c_fn = _BUILTIN_CONSTRAINTS.get(c_name)
                if c_fn is not None and not c_fn(state, action):
                    blocked = True
                    break
            if blocked:
                continue

            candidates[action] = self._score_action(action, state)

        if not candidates:
            # All actions blocked — fallback to wait
            candidates["wait"] = 0.1

        # Pick best
        best_action = max(candidates, key=candidates.get)
        best_conf = candidates[best_action]

        # Record
        self._decision_count += 1
        self._next_id += 1
        did = f"D{self._next_id:06d}"

        record = _DecisionRecord(
            decision_id=did,
            action=best_action,
            confidence=best_conf,
            state_summary={k: v for k, v in state.items() if not str(k).startswith("_")},
        )
        self._history.append(record)
        self._feedback_index[did] = record

        self._fire_evolution({
            "action": "decide",
            "decision_action": best_action,
            "confidence": best_conf,
        })

        return {
            "action": best_action,
            "confidence": best_conf,
            "decision_id": did,
            "alternatives": {k: v for k, v in candidates.items() if k != best_action},
        }

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_history(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._history]

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def record_feedback(
        self,
        decision_id: str,
        *,
        outcome: str = "",
        reward: float = 0.0,
        **extra: Any,
    ) -> None:
        """Record outcome feedback for a past decision."""
        record = self._feedback_index.get(decision_id)
        if record is None:
            logger.warning("record_feedback: unknown decision_id=%s", decision_id)
            return
        record.feedback = {"outcome": outcome, "reward": reward, **extra, "ts": time.time()}
        self._fire_evolution({
            "action": "feedback",
            "decision_id": decision_id,
            "outcome": outcome,
            "reward": reward,
        })

    def get_feedback(self, decision_id: str) -> Optional[Dict[str, Any]]:
        record = self._feedback_index.get(decision_id)
        if record is None or record.feedback is None:
            return None
        return record.feedback

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        return {
            "decision_count": self._decision_count,
            "registered_types": len(self._types),
            "history_size": len(self._history),
            "feedback_count": sum(1 for r in self._history if r.feedback is not None),
        }

    # ------------------------------------------------------------------
    # Evolution
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised")

    def __repr__(self) -> str:
        return f"UnifiedDecisionFramework(types={len(self._types)}, decisions={self._decision_count})"


default_framework: UnifiedDecisionFramework = UnifiedDecisionFramework()

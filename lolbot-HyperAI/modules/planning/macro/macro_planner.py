"""
MacroPlanner — Macro-level strategic decision engine (split/group/objective).
=============================================================================
lolbot-HyperAI · Planning Layer

Reads game state and predictions, outputs high-level macro calls:
    SPLIT_PUSH, GROUP, BARON, DRAGON, DEFEND, RESET, VISION_CONTROL

Architecture position:
    modules/planning/macro/macro_planner.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state  (GameSnapshot from perception)
    ├─ Reads: /lol/win_prediction  (WinPrediction from prediction)
    ├─ Reads: /lol/events  (GameEvent list from perception)
    ├─ Publishes: /lol/macro_decision  (MacroDecision)
    └─ Used by: planning_component.py (integrated into planning Proc)

Apollo reference:
    modules/planning/tasks/deciders/decider.cc
    modules/planning/scenarios/scenario_manager.cc

Design notes:
    - Decision tree: game_phase → situation assessment → macro call
    - Desire-weight system: each option gets a score, highest wins
    - Dead-count differential drives urgency
    - Objective spawn windows from prediction layer
    - Cooldown prevents flip-flopping between decisions
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    GameEvent,
    GamePhase,
    GameSnapshot,
    ObjectiveType,
    PlayerState,
    TeamSide,
    WinPrediction,
)

logger = get_logger("planning.macro")

# ─── Constants ───────────────────────────────────────────────────────────────

_DECISION_COOLDOWN_S = 5.0          # Min time between decision changes
_BARON_THRESHOLD_GOLD_LEAD = 2000   # Gold lead needed to consider baron
_DRAGON_CONTEST_WINDOW_S = 60.0     # Start grouping 60s before dragon
_SPLIT_PUSH_MIN_ADVANTAGE = 1       # Kill advantage for split push
_RESET_HP_THRESHOLD = 0.3           # HP ratio to suggest reset
_DEFEND_TOWER_COUNT_DIFF = -2       # Tower deficit to trigger defend
_MOMENTUM_WINDOW_S = 120.0          # Window for momentum analysis
_MAX_DECISION_HISTORY = 100

# Weight system: each desire gets scored 0.0-1.0, multiplied by base weight
_BASE_WEIGHTS = {
    "baron": 1.5,
    "dragon": 1.3,
    "group": 1.0,
    "split_push": 0.9,
    "defend": 1.4,
    "reset": 0.8,
    "vision_control": 0.7,
}


# ─── Data Types ──────────────────────────────────────────────────────────────

class MacroAction(Enum):
    """Available macro-level actions."""
    BARON = "baron"
    DRAGON = "dragon"
    GROUP = "group"
    SPLIT_PUSH = "split_push"
    DEFEND = "defend"
    RESET = "reset"
    VISION_CONTROL = "vision_control"
    IDLE = "idle"


class Urgency(Enum):
    """Decision urgency level."""
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()
    CRITICAL = auto()

    @property
    def numeric(self) -> float:
        return {
            Urgency.LOW: 0.25,
            Urgency.MEDIUM: 0.50,
            Urgency.HIGH: 0.75,
            Urgency.CRITICAL: 1.0,
        }[self]


@dataclass
class MacroDesire:
    """A scored desire for a specific macro action."""
    action: MacroAction
    score: float               # 0.0-1.0 raw desire
    weighted_score: float      # score * base_weight
    urgency: Urgency
    rationale: str
    time_window_s: float = 0.0  # How long this desire is valid


@dataclass
class MacroDecision:
    """Output of the macro planner: a prioritized decision with context."""
    action: MacroAction
    urgency: Urgency
    confidence: float          # 0.0-1.0
    rationale: str
    alternatives: List[MacroDesire] = field(default_factory=list)
    game_time: float = 0.0
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "urgency": self.urgency.name,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale,
            "alternatives_count": len(self.alternatives),
            "game_time": self.game_time,
        }


@dataclass
class _ObjectiveState:
    """Internal tracking of objective availability."""
    dragon_alive: bool = True
    baron_alive: bool = False   # Spawns at 20:00
    herald_alive: bool = True   # Before 20:00
    dragon_respawn_at: float = 0.0
    baron_respawn_at: float = 0.0
    our_dragons: int = 0
    their_dragons: int = 0
    our_barons: int = 0
    their_barons: int = 0


# ─── Situation Assessor ──────────────────────────────────────────────────────

class SituationAssessor:
    """Assesses the current game situation for macro decision-making.

    Extracts high-level situation metrics from raw game state:
    alive counts, gold differentials, tower pressure, objective state.
    """

    def __init__(self) -> None:
        self._kill_events: Deque[Tuple[float, str]] = deque(maxlen=50)

    def assess(
        self,
        snapshot: GameSnapshot,
        events: Optional[List[GameEvent]] = None,
    ) -> Dict[str, Any]:
        """Produce a situation assessment dictionary."""
        blue = snapshot.blue_team
        red = snapshot.red_team
        our_side = snapshot.active_team

        if our_side == TeamSide.BLUE:
            us, them = blue, red
        else:
            us, them = red, blue

        # Alive counts
        our_alive = sum(1 for p in us.players if not p.is_dead)
        their_alive = sum(1 for p in them.players if not p.is_dead)
        alive_diff = our_alive - their_alive

        # Gold
        our_gold = sum(p.current_gold for p in us.players)
        their_gold = sum(p.current_gold for p in them.players)
        gold_diff = our_gold - their_gold

        # Towers
        tower_diff = us.towers_destroyed - them.towers_destroyed

        # Average HP ratio
        our_hp_ratio = 0.0
        alive_count = 0
        for p in us.players:
            if not p.is_dead and p.max_health > 0:
                our_hp_ratio += p.current_health / p.max_health
                alive_count += 1
        if alive_count > 0:
            our_hp_ratio /= alive_count

        # Average level
        our_avg_level = sum(p.level for p in us.players) / max(len(us.players), 1)
        their_avg_level = sum(p.level for p in them.players) / max(len(them.players), 1)

        # Objective state
        obj_state = self._assess_objectives(snapshot, our_side)

        # Momentum: recent kill difference
        momentum = self._assess_momentum(events, snapshot.game_time)

        return {
            "our_alive": our_alive,
            "their_alive": their_alive,
            "alive_diff": alive_diff,
            "gold_diff": gold_diff,
            "tower_diff": tower_diff,
            "our_hp_ratio": our_hp_ratio,
            "our_avg_level": our_avg_level,
            "their_avg_level": their_avg_level,
            "level_diff": our_avg_level - their_avg_level,
            "game_phase": snapshot.phase,
            "game_time": snapshot.game_time,
            "objectives": obj_state,
            "momentum": momentum,
            "our_side": our_side,
        }

    def _assess_objectives(
        self,
        snapshot: GameSnapshot,
        our_side: TeamSide,
    ) -> _ObjectiveState:
        """Build objective availability state."""
        state = _ObjectiveState()
        gt = snapshot.game_time

        blue = snapshot.blue_team
        red = snapshot.red_team

        if our_side == TeamSide.BLUE:
            state.our_dragons = blue.dragons_taken
            state.their_dragons = red.dragons_taken
            state.our_barons = blue.barons_taken
            state.their_barons = red.barons_taken
        else:
            state.our_dragons = red.dragons_taken
            state.their_dragons = blue.dragons_taken
            state.our_barons = red.barons_taken
            state.their_barons = blue.barons_taken

        # Baron spawns at 20:00 (1200s)
        state.baron_alive = gt >= 1200.0
        # Herald before 20:00
        state.herald_alive = gt < 1200.0

        return state

    def _assess_momentum(
        self,
        events: Optional[List[GameEvent]],
        current_time: float,
    ) -> float:
        """Calculate momentum score from recent events.

        Returns:
            Score in [-1.0, 1.0]. Positive = we have momentum.
        """
        if not events:
            return 0.0

        recent_kills_us = 0
        recent_kills_them = 0
        window_start = current_time - _MOMENTUM_WINDOW_S

        for event in events:
            if event.game_time < window_start:
                continue
            if event.event_type.value in ("ChampionKill",):
                # Simplified: even/odd events for team attribution
                # In production, would check killer team from event payload
                recent_kills_us += 1  # placeholder

        diff = recent_kills_us - recent_kills_them
        # Normalize to [-1, 1]
        if diff == 0:
            return 0.0
        return max(-1.0, min(1.0, diff / 5.0))


# ─── Desire Calculators ─────────────────────────────────────────────────────

class _DesireCalculator:
    """Calculates desire scores for each macro action."""

    def baron_desire(self, sit: Dict[str, Any]) -> MacroDesire:
        """Evaluate desire to take Baron."""
        obj = sit["objectives"]
        score = 0.0
        urgency = Urgency.LOW
        rationale_parts = []

        if not obj.baron_alive:
            return MacroDesire(
                MacroAction.BARON, 0.0, 0.0, Urgency.LOW,
                "Baron not available",
            )

        # Gold lead contributes to baron desire
        gold_diff = sit["gold_diff"]
        if gold_diff > _BARON_THRESHOLD_GOLD_LEAD:
            gold_score = min(1.0, gold_diff / 8000.0)
            score += gold_score * 0.4
            rationale_parts.append(f"gold lead +{gold_diff:.0f}")

        # Alive advantage
        alive_diff = sit["alive_diff"]
        if alive_diff >= 2:
            score += 0.35
            urgency = Urgency.HIGH
            rationale_parts.append(f"{alive_diff} man advantage")
        elif alive_diff >= 1:
            score += 0.15
            rationale_parts.append("slight man advantage")

        # Late game increases baron desire
        if sit["game_phase"] == GamePhase.LATE:
            score += 0.15
            rationale_parts.append("late game")

        # Their baron count pressures us to contest
        if obj.their_barons > obj.our_barons:
            score += 0.1
            urgency = Urgency.MEDIUM
            rationale_parts.append("need to contest baron")

        score = min(1.0, score)
        weighted = score * _BASE_WEIGHTS["baron"]
        rationale = "Baron: " + ", ".join(rationale_parts) if rationale_parts else "Baron: low desire"

        return MacroDesire(MacroAction.BARON, score, weighted, urgency, rationale)

    def dragon_desire(self, sit: Dict[str, Any]) -> MacroDesire:
        """Evaluate desire to take Dragon."""
        obj = sit["objectives"]
        score = 0.0
        urgency = Urgency.LOW
        rationale_parts = []

        # Dragon soul threat (4 dragons)
        if obj.their_dragons >= 3:
            score += 0.5
            urgency = Urgency.CRITICAL
            rationale_parts.append("enemy near soul!")
        elif obj.our_dragons >= 3:
            score += 0.4
            urgency = Urgency.HIGH
            rationale_parts.append("we can get soul")

        # Early game: dragon is more valuable relatively
        if sit["game_phase"] == GamePhase.EARLY:
            score += 0.2
            rationale_parts.append("early dragon value")

        # Man advantage
        if sit["alive_diff"] >= 1:
            score += 0.15
            rationale_parts.append("man advantage")

        score = min(1.0, score)
        weighted = score * _BASE_WEIGHTS["dragon"]
        rationale = "Dragon: " + ", ".join(rationale_parts) if rationale_parts else "Dragon: low desire"

        return MacroDesire(MacroAction.DRAGON, score, weighted, urgency, rationale)

    def group_desire(self, sit: Dict[str, Any]) -> MacroDesire:
        """Evaluate desire to group as 5."""
        score = 0.0
        urgency = Urgency.LOW
        rationale_parts = []

        # Mid/late game grouping
        if sit["game_phase"] in (GamePhase.MID, GamePhase.LATE):
            score += 0.3
            rationale_parts.append("mid/late game")

        # Team fight advantage (win probability > 55%)
        momentum = sit.get("momentum", 0.0)
        if momentum > 0.3:
            score += 0.25
            urgency = Urgency.MEDIUM
            rationale_parts.append("positive momentum")

        # Alive advantage
        if sit["alive_diff"] >= 2:
            score += 0.2
            urgency = Urgency.HIGH
            rationale_parts.append("number advantage")

        score = min(1.0, score)
        weighted = score * _BASE_WEIGHTS["group"]
        rationale = "Group: " + ", ".join(rationale_parts) if rationale_parts else "Group: low desire"

        return MacroDesire(MacroAction.GROUP, score, weighted, urgency, rationale)

    def split_push_desire(self, sit: Dict[str, Any]) -> MacroDesire:
        """Evaluate desire for split push."""
        score = 0.0
        urgency = Urgency.LOW
        rationale_parts = []

        # Split push when we have gold lead but not man advantage
        gold_diff = sit["gold_diff"]
        if gold_diff > 1500 and sit["alive_diff"] <= 0:
            score += 0.3
            rationale_parts.append("gold lead, no man adv → split")

        # Level advantage on carry
        if sit["level_diff"] > 1.0:
            score += 0.2
            rationale_parts.append(f"level advantage +{sit['level_diff']:.1f}")

        # Mid game is optimal for split push
        if sit["game_phase"] == GamePhase.MID:
            score += 0.15
            rationale_parts.append("mid game split timing")

        score = min(1.0, score)
        weighted = score * _BASE_WEIGHTS["split_push"]
        rationale = "Split: " + ", ".join(rationale_parts) if rationale_parts else "Split: low desire"

        return MacroDesire(MacroAction.SPLIT_PUSH, score, weighted, urgency, rationale)

    def defend_desire(self, sit: Dict[str, Any]) -> MacroDesire:
        """Evaluate desire to defend."""
        score = 0.0
        urgency = Urgency.LOW
        rationale_parts = []

        # Tower deficit
        tower_diff = sit["tower_diff"]
        if tower_diff <= _DEFEND_TOWER_COUNT_DIFF:
            score += 0.4
            urgency = Urgency.HIGH
            rationale_parts.append(f"tower deficit {tower_diff}")

        # Man disadvantage
        if sit["alive_diff"] <= -2:
            score += 0.35
            urgency = Urgency.CRITICAL
            rationale_parts.append(f"outnumbered by {abs(sit['alive_diff'])}")
        elif sit["alive_diff"] <= -1:
            score += 0.2
            urgency = Urgency.MEDIUM
            rationale_parts.append("slight man disadvantage")

        # Gold deficit
        if sit["gold_diff"] < -3000:
            score += 0.15
            rationale_parts.append(f"gold deficit {sit['gold_diff']:.0f}")

        score = min(1.0, score)
        weighted = score * _BASE_WEIGHTS["defend"]
        rationale = "Defend: " + ", ".join(rationale_parts) if rationale_parts else "Defend: low desire"

        return MacroDesire(MacroAction.DEFEND, score, weighted, urgency, rationale)

    def reset_desire(self, sit: Dict[str, Any]) -> MacroDesire:
        """Evaluate desire to reset (recall to base)."""
        score = 0.0
        urgency = Urgency.LOW
        rationale_parts = []

        if sit["our_hp_ratio"] < _RESET_HP_THRESHOLD:
            score += 0.5
            urgency = Urgency.HIGH
            rationale_parts.append(f"low HP ({sit['our_hp_ratio']:.0%})")

        # No immediate objectives and low resources
        if sit["our_hp_ratio"] < 0.5 and sit["alive_diff"] == 0:
            score += 0.2
            rationale_parts.append("neutral state, conserve")

        score = min(1.0, score)
        weighted = score * _BASE_WEIGHTS["reset"]
        rationale = "Reset: " + ", ".join(rationale_parts) if rationale_parts else "Reset: low desire"

        return MacroDesire(MacroAction.RESET, score, weighted, urgency, rationale)

    def vision_desire(self, sit: Dict[str, Any]) -> MacroDesire:
        """Evaluate desire for vision control."""
        score = 0.0
        urgency = Urgency.LOW
        rationale_parts = []

        # Pre-objective vision is critical
        obj = sit["objectives"]
        if obj.baron_alive and sit["game_time"] > 1140:  # 19 min
            score += 0.3
            urgency = Urgency.MEDIUM
            rationale_parts.append("baron about to spawn, ward pit")

        # Mid game vision rotation
        if sit["game_phase"] == GamePhase.MID:
            score += 0.15
            rationale_parts.append("mid game vision")

        score = min(1.0, score)
        weighted = score * _BASE_WEIGHTS["vision_control"]
        rationale = "Vision: " + ", ".join(rationale_parts) if rationale_parts else "Vision: low desire"

        return MacroDesire(MacroAction.VISION_CONTROL, score, weighted, urgency, rationale)


# ─── MacroPlanner ────────────────────────────────────────────────────────────

class MacroPlanner:
    """Top-level macro decision engine.

    Each call to ``decide()`` evaluates all macro options, scores them
    via the desire-weight system, and returns the highest-scoring
    decision with context and alternatives.

    The planner enforces a cooldown to prevent rapid flip-flopping
    between decisions (e.g. baron→dragon→baron within seconds).
    """

    def __init__(
        self,
        cooldown_s: float = _DECISION_COOLDOWN_S,
        custom_weights: Optional[Dict[str, float]] = None,
    ) -> None:
        self._assessor = SituationAssessor()
        self._calculator = _DesireCalculator()
        self._cooldown_s = cooldown_s
        self._last_decision: Optional[MacroDecision] = None
        self._last_decision_time: float = 0.0
        self._decision_count: int = 0
        self._history: Deque[MacroDecision] = deque(maxlen=_MAX_DECISION_HISTORY)

        # Allow runtime weight tuning (for evolution)
        self._weights = dict(_BASE_WEIGHTS)
        if custom_weights:
            self._weights.update(custom_weights)

    def decide(
        self,
        snapshot: GameSnapshot,
        win_pred: Optional[WinPrediction] = None,
        events: Optional[List[GameEvent]] = None,
    ) -> MacroDecision:
        """Generate a macro decision for the current game state.

        Args:
            snapshot: Current game state from perception.
            win_pred: Current win probability from prediction.
            events: Recent game events from perception.

        Returns:
            MacroDecision with action, urgency, confidence, and rationale.
        """
        now = time.monotonic()

        # Cooldown check: don't flip-flop
        if (
            self._last_decision is not None
            and (now - self._last_decision_time) < self._cooldown_s
            and self._last_decision.action != MacroAction.IDLE
        ):
            return self._last_decision

        # Assess situation
        sit = self._assessor.assess(snapshot, events)

        # Calculate all desires
        desires = [
            self._calculator.baron_desire(sit),
            self._calculator.dragon_desire(sit),
            self._calculator.group_desire(sit),
            self._calculator.split_push_desire(sit),
            self._calculator.defend_desire(sit),
            self._calculator.reset_desire(sit),
            self._calculator.vision_desire(sit),
        ]

        # Sort by weighted score descending
        desires.sort(key=lambda d: d.weighted_score, reverse=True)

        # Pick the best
        best = desires[0]
        if best.weighted_score < 0.1:
            # Nothing worth doing — idle
            decision = MacroDecision(
                action=MacroAction.IDLE,
                urgency=Urgency.LOW,
                confidence=0.5,
                rationale="No strong macro call — hold position",
                alternatives=desires[1:4],
                game_time=snapshot.game_time,
            )
        else:
            # Confidence based on score gap between #1 and #2
            gap = best.weighted_score - desires[1].weighted_score if len(desires) > 1 else best.weighted_score
            confidence = min(1.0, 0.5 + gap)

            decision = MacroDecision(
                action=best.action,
                urgency=best.urgency,
                confidence=confidence,
                rationale=best.rationale,
                alternatives=desires[1:4],
                game_time=snapshot.game_time,
            )

        # Record
        self._last_decision = decision
        self._last_decision_time = now
        self._decision_count += 1
        self._history.append(decision)

        logger.debug(
            "Macro decision #%d: %s (%.2f) — %s",
            self._decision_count,
            decision.action.value,
            decision.confidence,
            decision.rationale,
        )

        return decision

    # ── Configuration API (for evolution) ────────────────────────────────

    def set_weight(self, action: str, weight: float) -> None:
        """Update a desire base weight at runtime."""
        if action in self._weights:
            self._weights[action] = max(0.0, weight)

    def set_cooldown(self, cooldown_s: float) -> None:
        """Update decision cooldown."""
        self._cooldown_s = max(1.0, cooldown_s)

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return planner statistics."""
        action_counts: Dict[str, int] = {}
        for d in self._history:
            key = d.action.value
            action_counts[key] = action_counts.get(key, 0) + 1

        return {
            "decision_count": self._decision_count,
            "history_size": len(self._history),
            "action_distribution": action_counts,
            "last_action": self._last_decision.action.value
            if self._last_decision else "none",
            "weights": dict(self._weights),
        }

    def reset(self) -> None:
        """Reset planner state (e.g. between games)."""
        self._last_decision = None
        self._last_decision_time = 0.0
        self._decision_count = 0
        self._history.clear()

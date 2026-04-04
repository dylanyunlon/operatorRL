"""
PlanningComponent — Strategy recommendations at 2Hz.
======================================================

Reads ``GameSnapshot`` from ``/lol/game_state`` and win predictions from
``/lol/win_prediction`` to generate tactical strategy recommendations
on ``/lol/strategy``.

Phase 4 additions (Claude#6):
    - MacroPlanner integration: macro decisions → /lol/macro_decision
    - LaneAdvisor integration: lane advice → /lol/lane_advice
    - Reads /lol/teamfight_assessment for teamfight-aware macro

Architecture position:
    modules/planning/planning_component.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (GameSnapshot from perception)
    ├─ Reads: /lol/win_prediction (WinPrediction from prediction)
    ├─ Reads: /lol/teamfight_assessment (TeamfightAssessment)  [Phase 4]
    ├─ Publishes: /lol/strategy (StrategyAdvice)
    ├─ Publishes: /lol/macro_decision (MacroDecision)          [Phase 4]
    ├─ Publishes: /lol/lane_advice (LaneAdvice list)           [Phase 4]
    └─ Delegates to: macro/macro_planner.py, strategy/lane_advisor.py

Apollo reference:
    modules/planning/planning_component.cc  — ``Proc(prediction_msg)``
    modules/planning/tasks/deciders/decider.cc — decision dispatch

Design notes:
    - 500ms interval (2Hz) — same as prediction
    - MacroPlanner: desire-weighted decisions with cooldown anti-flicker
    - LaneAdvisor: only active during EARLY/MID phases (laning)
    - Internal MacroDecisionEngine kept for backward compat, but
      MacroPlanner.decide() is now the primary decision source
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.component_base import (
    ComponentDependency,
    LifecycleState,
    ManagedComponent,
)
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    GameEvent,
    GamePhase,
    GameSnapshot,
    StrategyAdvice,
    TeamSide,
    WinPrediction,
)
from modules.planning.macro.macro_planner import (
    MacroPlanner,
    MacroDecision,
    MacroAction,
)
from modules.planning.strategy.lane_advisor import (
    LaneAdvisor,
    LaneAdvice,
)

logger = get_logger("planning")

# ─── Constants ───────────────────────────────────────────────────────────────

_PLANNING_INTERVAL_MS = 500.0   # 2Hz
_WARN_THRESHOLD_MS = 400.0
_MAX_ADVICE_PER_TICK = 3
_MIN_ADVICE_CONFIDENCE = 0.3
_LANE_ADVICE_MAX_PHASES = {GamePhase.EARLY, GamePhase.MID}

# MacroPlanner runs every tick. LaneAdvisor every 4th tick (1Hz at 2Hz base).
_LANE_ADVISOR_TICK_DIVISOR = 4


# ─── Legacy MacroDecisionEngine (backward compat) ───────────────────────────

class MacroDecisionEngine:
    """Simple strategy engine kept for backward compatibility.

    Phase 4 replaces this with MacroPlanner as the primary decision source.
    This class is still importable by external code but PlanningComponent
    no longer uses it as primary.
    """

    def __init__(self) -> None:
        self._last_advice_time: float = 0.0
        self._cooldown_sec: float = 5.0

    def decide(
        self,
        snapshot: GameSnapshot,
        win_pred: Optional[WinPrediction] = None,
    ) -> Optional[StrategyAdvice]:
        """Generate a strategy recommendation based on game state."""
        now = time.monotonic()
        if now - self._last_advice_time < self._cooldown_sec:
            return None

        phase = snapshot.phase

        if phase == GamePhase.EARLY:
            advice = self._early_game_strategy(snapshot, win_pred)
        elif phase == GamePhase.MID:
            advice = self._mid_game_strategy(snapshot, win_pred)
        elif phase in (GamePhase.LATE, GamePhase.ENDING):
            advice = self._late_game_strategy(snapshot, win_pred)
        else:
            return None

        if advice is not None:
            self._last_advice_time = now
        return advice

    def _early_game_strategy(
        self,
        snapshot: GameSnapshot,
        win_pred: Optional[WinPrediction],
    ) -> Optional[StrategyAdvice]:
        """Early game: focus on laning fundamentals."""
        our_team = (
            snapshot.blue_team
            if snapshot.active_team == TeamSide.BLUE
            else snapshot.red_team
        )
        their_team = (
            snapshot.red_team
            if snapshot.active_team == TeamSide.BLUE
            else snapshot.blue_team
        )

        kill_diff = our_team.total_kills - their_team.total_kills
        if kill_diff <= -3:
            return self._make_advice(
                "play_safe",
                "We're behind in kills. Focus on safe farming and vision.",
                0.7, snapshot.game_time,
            )
        if kill_diff >= 3:
            return self._make_advice(
                "press_advantage",
                "Kill lead — look for aggressive plays and invades.",
                0.6, snapshot.game_time,
            )
        return None

    def _mid_game_strategy(
        self,
        snapshot: GameSnapshot,
        win_pred: Optional[WinPrediction],
    ) -> Optional[StrategyAdvice]:
        """Mid game: objectives and grouping."""
        if win_pred and win_pred.blue_win_prob is not None:
            prob = win_pred.blue_win_prob
            if snapshot.active_team == TeamSide.RED:
                prob = 1.0 - prob

            if prob < 0.35:
                return self._make_advice(
                    "defend_and_scale",
                    "We're behind. Avoid fights, farm safely, wait for power spikes.",
                    0.8, snapshot.game_time,
                )
            if prob > 0.65:
                return self._make_advice(
                    "force_objectives",
                    "We're ahead. Group for dragon/baron and force fights.",
                    0.7, snapshot.game_time,
                )
        return None

    def _late_game_strategy(
        self,
        snapshot: GameSnapshot,
        win_pred: Optional[WinPrediction],
    ) -> Optional[StrategyAdvice]:
        """Late game: decisive plays."""
        our_team = (
            snapshot.blue_team
            if snapshot.active_team == TeamSide.BLUE
            else snapshot.red_team
        )
        their_team = (
            snapshot.red_team
            if snapshot.active_team == TeamSide.BLUE
            else snapshot.blue_team
        )

        their_dead = sum(1 for p in their_team.players if p.is_dead)
        if their_dead >= 2:
            return self._make_advice(
                "push_advantage",
                f"{their_dead} enemies dead — take baron or push for inhibitor!",
                0.9, snapshot.game_time,
            )
        return None

    def _make_advice(
        self,
        rec_type: str,
        text: str,
        confidence: float,
        game_time: float,
    ) -> StrategyAdvice:
        return StrategyAdvice(
            rec_type=rec_type,
            text=text,
            confidence=confidence,
            priority=2 if confidence > 0.7 else 1,
            game_time=game_time,
        )


# ─── PlanningComponent ──────────────────────────────────────────────────────

class PlanningComponent(TimerComponent, ManagedComponent):
    """Planning component: 2Hz strategy recommendation.

    Each Proc() cycle:
    1. Reads latest GameSnapshot from /lol/game_state
    2. Reads latest WinPrediction from /lol/win_prediction
    3. Runs MacroPlanner.decide() → publishes MacroDecision      [Phase 4]
    4. Runs LaneAdvisor.advise() (laning phases) → publishes     [Phase 4]
    5. Runs legacy MacroDecisionEngine → publishes StrategyAdvice
    6. Publishes all results to respective channels

    Apollo equivalent: ``PlanningComponent::Proc(prediction_msg)``

    Claude11: Added ManagedComponent mixin for lifecycle + circuit breaker.
    """

    COMPONENT_NAME = "planning"
    DEPENDENCIES = [
        ComponentDependency("perception", required=True),
        ComponentDependency("prediction", required=False),
    ]
    VERSION = "2.0.0"

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="planning",
                interval_ms=_PLANNING_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        # Readers
        self._game_state_reader: Optional[Reader[GameSnapshot]] = None
        self._win_pred_reader: Optional[Reader[WinPrediction]] = None
        self._events_reader: Optional[Reader[List[GameEvent]]] = None

        # Writers
        self._strategy_writer: Optional[Writer[StrategyAdvice]] = None
        self._status_writer: Optional[Writer[StatusMessage]] = None
        self._macro_decision_writer: Optional[Writer[MacroDecision]] = None
        self._lane_advice_writer: Optional[Writer[List[LaneAdvice]]] = None

        # Engines
        self._legacy_engine: Optional[MacroDecisionEngine] = None
        self._macro_planner: Optional[MacroPlanner] = None
        self._lane_advisor: Optional[LaneAdvisor] = None

        # State
        self._plan_count: int = 0
        self._advice_count: int = 0
        self._lane_tick_counter: int = 0
        self._last_macro_decision: Optional[MacroDecision] = None
        self._recent_events: List[GameEvent] = []

    def Init(self) -> bool:
        self._managed_init()
        logger.info("Initializing PlanningComponent...")

        self._node = CyberNode("planning")

        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", GameSnapshot, pending_queue_size=8,
        )
        self._win_pred_reader = self._node.CreateReader(
            "/lol/win_prediction", WinPrediction, pending_queue_size=4,
        )
        self._events_reader = self._node.CreateReader(
            "/lol/events", list, pending_queue_size=16,
        )

        self._strategy_writer = self._node.CreateWriter(
            "/lol/strategy", StrategyAdvice,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/planning_status", StatusMessage,
        )
        # Phase 4 channels
        self._macro_decision_writer = self._node.CreateWriter(
            "/lol/macro_decision", MacroDecision,
        )
        self._lane_advice_writer = self._node.CreateWriter(
            "/lol/lane_advice", list,
        )

        self._legacy_engine = MacroDecisionEngine()

        # Phase 4: instantiate sub-planners
        self._macro_planner = MacroPlanner()
        self._lane_advisor = LaneAdvisor()

        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info("PlanningComponent initialized (with MacroPlanner + LaneAdvisor)")
        return True

    def Proc(self) -> bool:
        """One planning cycle.

        Apollo equivalent: ``PlanningComponent::Proc()``
        """
        # Read game state
        self._game_state_reader.Observe()
        snapshot: Optional[GameSnapshot] = (
            self._game_state_reader.GetLatestObserved()
        )
        if snapshot is None:
            return True

        # Read win prediction
        self._win_pred_reader.Observe()
        win_pred: Optional[WinPrediction] = (
            self._win_pred_reader.GetLatestObserved()
        )

        # Collect events
        if self._events_reader:
            self._events_reader.Observe()
            evts = self._events_reader.GetLatestObserved()
            if evts:
                self._recent_events = list(evts)

        self._plan_count += 1

        # ── Phase 4: MacroPlanner ────────────────────────────────────
        # Runs every tick (2Hz). Has internal cooldown to prevent flicker.
        if self._macro_planner is not None:
            try:
                macro = self._macro_planner.decide(
                    snapshot, win_pred, self._recent_events or None,
                )
                self._last_macro_decision = macro
                if self._macro_decision_writer:
                    self._macro_decision_writer.Write(macro)

                # If macro decision is urgent, also emit as legacy StrategyAdvice
                # so voice announcer (which reads /lol/strategy) can pick it up.
                if macro.action != MacroAction.IDLE and macro.confidence >= _MIN_ADVICE_CONFIDENCE:
                    legacy = StrategyAdvice(
                        rec_type=f"macro_{macro.action.value}",
                        text=macro.rationale,
                        confidence=macro.confidence,
                        priority=2 if macro.urgency.name in ("HIGH", "CRITICAL") else 1,
                        game_time=snapshot.game_time,
                    )
                    if self._strategy_writer:
                        self._strategy_writer.Write(legacy)
                        self._advice_count += 1

            except Exception as exc:
                logger.warning(
                    "MacroPlanner error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Phase 4: LaneAdvisor ─────────────────────────────────────
        # Runs at reduced frequency, only during laning phases.
        self._lane_tick_counter += 1
        if (
            self._lane_tick_counter >= _LANE_ADVISOR_TICK_DIVISOR
            and self._lane_advisor is not None
            and snapshot.phase in _LANE_ADVICE_MAX_PHASES
        ):
            self._lane_tick_counter = 0
            try:
                advices = self._lane_advisor.advise(snapshot)
                if advices:
                    # Publish all lane advice
                    if self._lane_advice_writer:
                        self._lane_advice_writer.Write(advices)

                    # Convert top advice to legacy StrategyAdvice for voice
                    top = advices[0]
                    if top.confidence >= _MIN_ADVICE_CONFIDENCE:
                        legacy = StrategyAdvice(
                            rec_type=f"lane_{top.advice_type.value}",
                            text=top.text,
                            confidence=top.confidence,
                            priority=1,
                            game_time=snapshot.game_time,
                        )
                        if self._strategy_writer:
                            self._strategy_writer.Write(legacy)
                            self._advice_count += 1

            except Exception as exc:
                logger.warning(
                    "LaneAdvisor error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Legacy engine (fallback when sub-planners produce nothing) ─
        if self._last_macro_decision is None or self._last_macro_decision.action == MacroAction.IDLE:
            advice = self._legacy_engine.decide(snapshot, win_pred)
            if advice is not None and self._strategy_writer:
                self._strategy_writer.Write(advice)
                self._advice_count += 1

        # ── Status ───────────────────────────────────────────────────
        self._publish_status(Status.ok())

        # Periodic log
        if self._plan_count % 40 == 0:
            macro_str = (
                self._last_macro_decision.action.value
                if self._last_macro_decision else "N/A"
            )
            logger.info(
                "Planning tick=%d advice=%d macro=%s phase=%s",
                self._plan_count, self._advice_count, macro_str,
                snapshot.phase.name,
            )

        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    def _publish_status(self, status: Status) -> None:
        if self._status_writer:
            self._status_writer.Write(StatusMessage(
                status=status,
                sequence=self._plan_count,
                source_component="planning",
            ))

    def planning_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "plan_count": self._plan_count,
            "advice_count": self._advice_count,
            "macro_planner_active": self._macro_planner is not None,
            "lane_advisor_active": self._lane_advisor is not None,
            "last_macro_action": (
                self._last_macro_decision.action.value
                if self._last_macro_decision else "N/A"
            ),
        })
        return base

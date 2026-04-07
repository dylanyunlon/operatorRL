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
# Claude19: Wire Claude18 PowerSpikeDetector + ObjectiveWindowAdvisor + new modules
from modules.planning.strategy.power_spike_detector import (
    PowerSpikeDetector,
    PowerSpike,
    SpikeImpact,
)
from modules.prediction.objective.objective_window_advisor import (
    ObjectiveWindowAdvisor,
    ObjectiveWindowAdvice,
)
from modules.planning.objective.objective_timer import (
    ObjectiveTimer,
)
from modules.planning.tempo.recall_advisor import (
    RecallAdvisor,
    RecallUrgency,
)
from modules.planning.summoner.spell_tracker import (
    SummonerSpellTracker,
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
# Claude19: PowerSpikeDetector and ObjectiveWindowAdvisor run every 2nd tick (1Hz)
_SPIKE_ADVISOR_TICK_DIVISOR = 2


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
            primary_action=rec_type,
            reasoning=text,
            confidence=confidence,
            urgency=0.8 if confidence > 0.7 else 0.4,
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

        # Claude19: PowerSpikeDetector + ObjectiveWindowAdvisor + RecallAdvisor + SpellTracker
        self._power_spike_detector: Optional[PowerSpikeDetector] = None
        self._objective_window_advisor: Optional[ObjectiveWindowAdvisor] = None
        self._objective_timer: Optional[ObjectiveTimer] = None
        self._recall_advisor: Optional[RecallAdvisor] = None
        self._spell_tracker: Optional[SummonerSpellTracker] = None
        self._spike_tick_counter: int = 0

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

        # Claude19: Instantiate Claude18 PowerSpikeDetector + ObjectiveWindowAdvisor + new modules
        self._power_spike_detector = PowerSpikeDetector()
        self._objective_window_advisor = ObjectiveWindowAdvisor()
        self._objective_timer = ObjectiveTimer()
        self._recall_advisor = RecallAdvisor()
        self._spell_tracker = SummonerSpellTracker()

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
                        primary_action=f"macro_{macro.action.value}",
                        reasoning=macro.rationale,
                        confidence=macro.confidence,
                        urgency=0.9 if macro.urgency.name in ("HIGH", "CRITICAL") else 0.5,
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
                            primary_action=f"lane_{top.advice_type.value}",
                            reasoning=top.text,
                            confidence=top.confidence,
                            urgency=0.4,
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

        # ── Claude19: ObjectiveTimer tick ────────────────────────────
        # Updates spawn/respawn state from recent events (every tick).
        if self._objective_timer is not None:
            try:
                for evt in self._recent_events:
                    etype = (
                        evt.event_type.value
                        if hasattr(evt.event_type, "value")
                        else str(evt.event_type)
                    )
                    self._objective_timer.process_event(
                        etype, "BLUE", evt.game_time,
                    )
                self._objective_timer.tick(snapshot.game_time)
            except Exception as exc:
                logger.warning(
                    "ObjectiveTimer error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Claude19: PowerSpikeDetector ─────────────────────────────
        # Runs every 2nd tick (1Hz). Detects level/item power spikes
        # and emits strategy advice when a significant spike occurs.
        self._spike_tick_counter += 1
        if (
            self._spike_tick_counter >= _SPIKE_ADVISOR_TICK_DIVISOR
            and self._power_spike_detector is not None
        ):
            self._spike_tick_counter = 0
            try:
                players = (
                    list(snapshot.all_players)
                    if hasattr(snapshot, "all_players") else []
                )
                active_team = snapshot.active_team if hasattr(snapshot, "active_team") else "BLUE"
                spikes = self._power_spike_detector.detect(
                    players, active_team, snapshot.game_time,
                )
                for spike in spikes:
                    if spike.impact.value >= SpikeImpact.MODERATE.value:
                        advice = StrategyAdvice(
                            primary_action=f"spike_{spike.spike_type.name.lower()}",
                            reasoning=f"Power spike: {spike.champion_name} hit {spike.description}",
                            confidence=0.6,
                            urgency=0.7 if spike.is_ally else 0.5,
                            game_time=snapshot.game_time,
                        )
                        if self._strategy_writer:
                            self._strategy_writer.Write(advice)
                            self._advice_count += 1
            except Exception as exc:
                logger.warning(
                    "PowerSpikeDetector error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Claude19: ObjectiveWindowAdvisor ──────────────────────────
        # Runs every 2nd tick (1Hz). Evaluates objective windows
        # using ObjectiveTimer state and publishes strategic advice.
        if (
            self._plan_count % 2 == 0
            and self._objective_window_advisor is not None
            and self._objective_timer is not None
        ):
            try:
                obj_states = self._objective_timer.stats().get("objectives", {})
                win_prob = (
                    win_pred.blue_win_prob if win_pred else 0.5
                )
                windows = self._objective_window_advisor.evaluate(
                    game_time=snapshot.game_time,
                    objective_states=obj_states,
                    team_alive_count=5,
                    enemy_alive_count=5,
                    win_probability=win_prob,
                )
                for w in windows:
                    if w.strategic_priority > 0.6:
                        advice = StrategyAdvice(
                            primary_action=f"objective_{w.objective_name}",
                            reasoning=w.advice_text,
                            confidence=w.strategic_priority,
                            urgency=0.8,
                            game_time=snapshot.game_time,
                        )
                        if self._strategy_writer:
                            self._strategy_writer.Write(advice)
                            self._advice_count += 1
            except Exception as exc:
                logger.warning(
                    "ObjectiveWindowAdvisor error (non-fatal): %s: %s",
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

    # ─── Claude17: Advice History & Strategy Coherence ───────────────────

    def get_advice_history(
        self, last_n: int = 20
    ) -> List[Dict[str, Any]]:
        """Return the last N strategy advice records.

        Claude17: Enables analysis of advice quality, frequency,
        and whether the advice was acted upon.
        """
        if not hasattr(self, '_advice_history'):
            self._advice_history: List[Dict[str, Any]] = []
        return self._advice_history[-last_n:]

    def _record_advice(self, advice: Any) -> None:
        """Record an advice for history tracking.

        Claude17: Called internally after each planning cycle
        produces advice.
        """
        if not hasattr(self, '_advice_history'):
            self._advice_history = []

        record = {
            "ts": time.time(),
            "plan_count": self._plan_count,
        }

        if hasattr(advice, 'primary_action'):
            action = advice.primary_action
            record["action"] = action.value if hasattr(
                action, 'value') else str(action)
        if hasattr(advice, 'reasoning'):
            record["reasoning"] = str(advice.reasoning)[:200]
        if hasattr(advice, 'urgency'):
            record["urgency"] = advice.urgency

        self._advice_history.append(record)
        # Bound the history
        if len(self._advice_history) > 500:
            self._advice_history = self._advice_history[-250:]

    def compute_strategy_coherence(
        self, window: int = 10
    ) -> float:
        """Measure how consistent recent advice has been.

        Claude17: A coherence score of 1.0 means all recent advice
        recommended the same action. Low coherence (<0.3) suggests
        the planner is thrashing between strategies.

        Used by evolution to penalize unstable planning.

        Returns:
            Float in [0.0, 1.0] where 1.0 = perfectly consistent.
        """
        history = self.get_advice_history(window)
        if len(history) < 2:
            return 1.0

        actions = [h.get("action", "") for h in history if h.get("action")]
        if not actions:
            return 1.0

        # Count most frequent action
        from collections import Counter
        counts = Counter(actions)
        most_common_count = counts.most_common(1)[0][1]

        return round(most_common_count / len(actions), 4)

    def deduplicate_advice(
        self, advice_list: List[Any], cooldown_s: float = 30.0
    ) -> List[Any]:
        """Remove duplicate advice within a cooldown window.

        Claude17: Prevents spamming the same recommendation.
        Two advices are "duplicate" if they have the same primary_action
        within cooldown_s of each other.

        Args:
            advice_list: List of StrategyAdvice objects.
            cooldown_s: Minimum seconds between same-action advice.

        Returns:
            Filtered list with duplicates removed.
        """
        if not advice_list:
            return []

        seen: Dict[str, float] = {}
        result = []
        now = time.time()

        for advice in advice_list:
            action = ""
            if hasattr(advice, 'primary_action'):
                action = str(advice.primary_action)
            elif hasattr(advice, 'action'):
                action = str(advice.action)

            last_seen = seen.get(action, 0)
            if now - last_seen >= cooldown_s:
                result.append(advice)
                seen[action] = now

        return result

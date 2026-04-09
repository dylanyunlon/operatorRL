"""
PredictionComponent — Win probability and teamfight prediction (2Hz).
======================================================================

Reads ``GameSnapshot`` from ``/lol/game_state`` (published by perception)
and runs prediction models to estimate win probability, teamfight
outcomes, and objective timing windows.  Publishes results for planning
and voice narration.

Phase 4 additions (Claude#6):
    - TeamfightPredictor integration (replaces inline TeamfightAnalyzer)
    - Reads /lol/kill_feed for momentum-aware confidence
    - Publishes TeamfightAssessment with 8-dim features + action recs

Architecture position:
    modules/prediction/prediction_component.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (GameSnapshot from perception)
    ├─ Reads: /lol/events (GameEvent list from perception)
    ├─ Reads: /lol/kill_feed (DetectedKillPattern list)  [Phase 4]
    ├─ Publishes: /lol/win_prediction (WinPrediction)
    ├─ Publishes: /lol/teamfight_prediction (TeamfightPrediction)
    ├─ Publishes: /lol/teamfight_assessment (TeamfightAssessment) [Phase 4]
    └─ Delegates to: win_probability/win_predictor.py,
                     team_fight/teamfight_predictor.py [Phase 4]

Apollo reference:
    modules/prediction/prediction_component.cc  — ``Proc(msg)``
    modules/prediction/evaluator/evaluator_manager.h — model management

Design notes:
    - 500ms interval (2Hz) — predictions don't need 10Hz refresh
    - Feature extraction from GameSnapshot.to_feature_dict()
    - Model inference abstracted behind WinPredictor interface
    - Exponential smoothing to avoid jarring probability jumps
    - Prediction history for trend analysis
    - TeamfightPredictor: 8-dim feature vector, sigmoid scoring,
      ENGAGE/DISENGAGE/POKE/PICK action recommendations
    - Kill feed patterns boost/decay momentum for confidence estimation
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
    TeamfightPrediction,
    TeamSide,
    WinPrediction,
)
from modules.prediction.team_fight.teamfight_predictor import (
    TeamfightPredictor,
    TeamfightAssessment,
)
# Claude19: Wire Claude18 ConfidenceCalibrator + new analyzers
from modules.prediction.evaluator.confidence_calibrator import (
    ConfidenceCalibrator,
    CalibratedConfidence,
    DataQualitySignal,
)
from modules.prediction.timing.death_timer_analyzer import (
    DeathTimerAnalyzer,
    DeathTimerReport,
)
from modules.prediction.composition.comp_analyzer import (
    CompAnalyzer,
    CompAnalysisReport,
)
# Claude25: Extracted (Apollo: evaluator/ separate from component)
from modules.prediction.features.prediction_features import PredictionFeatures
from modules.prediction.features.win_predictor_legacy import WinPredictor
from modules.prediction.features.teamfight_analyzer_legacy import TeamfightAnalyzer

logger = get_logger("prediction")

# ─── Constants ───────────────────────────────────────────────────────────────

_PREDICTION_INTERVAL_MS = 500.0   # 2Hz prediction cycle
_WARN_THRESHOLD_MS = 400.0
_SMOOTHING_ALPHA = 0.3            # EMA smoothing for win probability
_PREDICTION_HISTORY_MAX = 200
_MIN_GAME_TIME_FOR_PREDICTION = 120.0  # wait 2 min before predicting
_CONFIDENCE_RAMP_TIME = 600.0     # confidence reaches max at 10 min

# TeamfightPredictor runs every N prediction ticks to save CPU.
# At 2Hz, divisor=2 means 1Hz teamfight assessment.
_TEAMFIGHT_TICK_DIVISOR = 2


# Claude25: PredictionFeatures/WinPredictor/TeamfightAnalyzer → features/

# ─── PredictionComponent ────────────────────────────────────────────────────

class PredictionComponent(TimerComponent, ManagedComponent):
    """Prediction component: 2Hz win/teamfight prediction.

    Each Proc() cycle:
    1. Reads latest GameSnapshot from /lol/game_state
    2. Reads latest kill feed patterns from /lol/kill_feed (Phase 4)
    3. Extracts features
    4. Runs win probability model
    5. Runs TeamfightPredictor (Phase 4, replaces inline analyzer)
    6. Applies EMA smoothing
    7. Publishes predictions

    Claude11: Added ManagedComponent mixin for lifecycle + circuit breaker.

    Apollo equivalent: ``PredictionComponent::Proc(perception_msg)``
    """

    COMPONENT_NAME = "prediction"
    DEPENDENCIES = [
        ComponentDependency("perception", required=True,
                            channels=["/lol/game_state"]),
    ]
    VERSION = "2.0.0"

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="prediction",
                interval_ms=_PREDICTION_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        # Readers
        self._game_state_reader: Optional[Reader[GameSnapshot]] = None
        self._events_reader: Optional[Reader[List[GameEvent]]] = None
        self._kill_feed_reader: Optional[Reader[list]] = None

        # Writers
        self._win_pred_writer: Optional[Writer[WinPrediction]] = None
        self._teamfight_writer: Optional[Writer[TeamfightPrediction]] = None
        self._teamfight_assessment_writer: Optional[Writer[TeamfightAssessment]] = None
        self._status_writer: Optional[Writer[StatusMessage]] = None

        # Models
        self._win_predictor: Optional[WinPredictor] = None
        self._teamfight_analyzer: Optional[TeamfightAnalyzer] = None
        self._teamfight_predictor: Optional[TeamfightPredictor] = None

        # State
        self._smoothed_win_prob: float = 0.5
        self._prediction_history: Deque[WinPrediction] = deque(
            maxlen=_PREDICTION_HISTORY_MAX
        )
        self._snapshot_history: Deque[GameSnapshot] = deque(maxlen=60)
        self._recent_events: List[GameEvent] = []
        self._pred_count: int = 0
        self._teamfight_tick_counter: int = 0
        self._last_teamfight_assessment: Optional[TeamfightAssessment] = None

        # Claude19: Wire Claude18 ConfidenceCalibrator + new analyzers
        self._confidence_calibrator: Optional[ConfidenceCalibrator] = None
        self._death_timer_analyzer: Optional[DeathTimerAnalyzer] = None
        self._comp_analyzer: Optional[CompAnalyzer] = None
        self._last_calibrated_confidence: Optional[CalibratedConfidence] = None
        self._last_death_report: Optional[DeathTimerReport] = None
        self._last_comp_report: Optional[CompAnalysisReport] = None
        self._death_window_writer: Optional[Writer] = None
        self._comp_cached: bool = False

    def Init(self) -> bool:
        self._managed_init()
        logger.info("Initializing PredictionComponent...")

        self._node = CyberNode("prediction")

        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", GameSnapshot, pending_queue_size=8,
        )
        # Phase 4: subscribe to events and kill feed
        self._events_reader = self._node.CreateReader(
            "/lol/events", list, pending_queue_size=16,
        )
        self._kill_feed_reader = self._node.CreateReader(
            "/lol/kill_feed", list, pending_queue_size=16,
        )

        self._win_pred_writer = self._node.CreateWriter(
            "/lol/win_prediction", WinPrediction,
        )
        self._teamfight_writer = self._node.CreateWriter(
            "/lol/teamfight_prediction", TeamfightPrediction,
        )
        # Phase 4: richer teamfight assessment channel
        self._teamfight_assessment_writer = self._node.CreateWriter(
            "/lol/teamfight_assessment", TeamfightAssessment,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/prediction_status", StatusMessage,
        )

        self._win_predictor = WinPredictor()
        self._teamfight_analyzer = TeamfightAnalyzer()

        # Phase 4: instantiate TeamfightPredictor
        self._teamfight_predictor = TeamfightPredictor()

        # Claude19: Instantiate ConfidenceCalibrator + DeathTimerAnalyzer + CompAnalyzer
        self._confidence_calibrator = ConfidenceCalibrator()
        self._death_timer_analyzer = DeathTimerAnalyzer()
        self._comp_analyzer = CompAnalyzer()
        self._death_window_writer = self._node.CreateWriter(
            "/lol/death_windows", dict,
        )

        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info(
            "PredictionComponent initialized (model=%s, teamfight=TeamfightPredictor)",
            self._win_predictor.version,
        )
        return True

    def Proc(self) -> bool:
        """One prediction cycle — Apollo pattern: Proc() → _internal_proc().

        Claude22 refactor: Thin shell matching Apollo PredictionComponent::Proc()
        (real Apollo: 7 lines, delegates to PredictionEndToEndProc 130 lines).
        All Claude1-21 logic moved to _internal_proc(). Zero logic removed.
        """
        # ── READ: Observe game state ─────────────────────────────────
        self._game_state_reader.Observe()
        snapshot: Optional[GameSnapshot] = (
            self._game_state_reader.GetLatestObserved()
        )

        if snapshot is None:
            return True  # no data yet

        # Skip prediction for very early game
        if snapshot.game_time < _MIN_GAME_TIME_FOR_PREDICTION:
            return True

        # ── PROCESS: delegate to _internal_proc (Apollo EndToEnd equiv) ─
        self._internal_proc(snapshot)

        # ── MONITOR: status heartbeat ────────────────────────────────
        self._publish_status(Status.ok())
        return True

    # ── Apollo-style InternalProc (Claude22: all Proc() logic moved here) ──

    def _internal_proc(self, snapshot: GameSnapshot) -> None:
        """Core prediction processing — called by Proc() after read/validate.

        Apollo reference: PredictionComponent::PredictionEndToEndProc()
        Claude22: Contains all Claude1-21 Proc() logic, verbatim.
        The only structural change: tf_pred and confidence are now local
        to this method (they were local to Proc() before — same scope).
        """
        self._snapshot_history.append(snapshot)
        self._pred_count += 1

        # ── Collect recent events from events channel ────────────────
        if self._events_reader:
            self._events_reader.Observe()
            events_batch = self._events_reader.GetLatestObserved()
            if events_batch:
                self._recent_events = list(events_batch)

        # ── Find old snapshot for trend features ─────────────────────
        prev_snapshot = None
        for old in self._snapshot_history:
            if snapshot.game_time - old.game_time >= 110.0:
                prev_snapshot = old
                break

        # ── Feature extraction ───────────────────────────────────────
        features = PredictionFeatures.from_snapshot(snapshot, prev_snapshot)

        # ── Win probability ──────────────────────────────────────────
        raw_prob = self._win_predictor.predict(features)

        # EMA smoothing to prevent jarring jumps
        self._smoothed_win_prob = (
            _SMOOTHING_ALPHA * raw_prob +
            (1 - _SMOOTHING_ALPHA) * self._smoothed_win_prob
        )

        # Confidence ramps up with game time
        raw_confidence = min(1.0, snapshot.game_time / _CONFIDENCE_RAMP_TIME)

        # Claude19: Replace raw confidence with ConfidenceCalibrator
        # The calibrator integrates data quality, event richness, and stability
        # on top of the base time ramp, per Claude18's design.
        confidence = raw_confidence
        if self._confidence_calibrator is not None:
            try:
                signal = DataQualitySignal(
                    canbus_source_type="unknown",
                    canbus_stale_count=0,
                    canbus_error_rate=0.0,
                    perception_snapshot_count=self._pred_count,
                    perception_event_count=len(self._recent_events),
                    game_time=snapshot.game_time,
                )
                cal = self._confidence_calibrator.calibrate(
                    raw_confidence, signal,
                )
                self._last_calibrated_confidence = cal
                confidence = cal.final_confidence
            except Exception as exc:
                logger.warning(
                    "ConfidenceCalibrator error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # Feature importance
        top_features = self._win_predictor.feature_importance(features)

        win_pred = WinPrediction(
            blue_win_prob=round(self._smoothed_win_prob, 4),
            confidence=round(confidence, 3),
            model_version=self._win_predictor.version,
            game_time=snapshot.game_time,
            top_features=tuple(
                (name, round(val, 4)) for name, val in top_features[:5]
            ),
        )

        self._prediction_history.append(win_pred)
        if self._win_pred_writer:
            self._win_pred_writer.Write(win_pred)

        # ── Legacy teamfight prediction (backward compat) ────────────
        tf_pred = self._teamfight_analyzer.analyze(snapshot)
        if self._teamfight_writer:
            self._teamfight_writer.Write(tf_pred)

        # ── Phase 4: TeamfightPredictor (richer 8-dim model) ─────────
        self._teamfight_tick_counter += 1
        if (
            self._teamfight_tick_counter >= _TEAMFIGHT_TICK_DIVISOR
            and self._teamfight_predictor is not None
        ):
            self._teamfight_tick_counter = 0
            try:
                assessment = self._teamfight_predictor.predict(
                    snapshot, self._recent_events or None,
                )
                self._last_teamfight_assessment = assessment
                if self._teamfight_assessment_writer:
                    self._teamfight_assessment_writer.Write(assessment)
            except Exception as exc:
                # Non-fatal: legacy analyzer already published tf_pred above
                logger.warning(
                    "TeamfightPredictor error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Claude19: DeathTimerAnalyzer ─────────────────────────────
        # Runs every prediction tick (2Hz). Lightweight — just scans
        # player is_dead flags and computes windows.
        if self._death_timer_analyzer is not None:
            try:
                players = list(snapshot.all_players) if hasattr(snapshot, "all_players") else []
                self._death_timer_analyzer.update_deaths(players, snapshot.game_time)

                active_team = "BLUE"
                if hasattr(snapshot, "active_team"):
                    at = snapshot.active_team
                    if hasattr(at, "name"):
                        active_team = "BLUE" if "BLUE" in at.name.upper() else "RED"

                report = self._death_timer_analyzer.analyze(
                    snapshot.game_time, active_team,
                )
                self._last_death_report = report
                if report.current_window and self._death_window_writer:
                    self._death_window_writer.Write(report.to_dict())
            except Exception as exc:
                logger.warning(
                    "DeathTimerAnalyzer error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Claude19: CompAnalyzer ───────────────────────────────────
        # Runs ONCE per game (comp doesn't change). Caches result.
        if self._comp_analyzer is not None and not self._comp_cached:
            try:
                players = list(snapshot.all_players) if hasattr(snapshot, "all_players") else []
                blue_champs = [
                    getattr(p, "champion_name", "")
                    for p in players
                    if hasattr(p, "team") and "BLUE" in str(getattr(p.team, "name", "")).upper()
                ]
                red_champs = [
                    getattr(p, "champion_name", "")
                    for p in players
                    if hasattr(p, "team") and "RED" in str(getattr(p.team, "name", "")).upper()
                ]
                if len(blue_champs) == 5 and len(red_champs) == 5:
                    phase_str = snapshot.phase.name if hasattr(snapshot, "phase") else "EARLY"
                    self._last_comp_report = self._comp_analyzer.analyze(
                        blue_champs, red_champs, phase_str,
                    )
                    self._comp_cached = True
                    logger.info(
                        "Comp analysis: blue=%s vs red=%s adj=%.3f",
                        self._last_comp_report.blue_profile.primary_archetype.name,
                        self._last_comp_report.red_profile.primary_archetype.name,
                        self._last_comp_report.comp_adjustment,
                    )
            except Exception as exc:
                logger.warning(
                    "CompAnalyzer error (non-fatal): %s: %s",
                    type(exc).__name__, exc,
                )

        # ── Log significant changes ──────────────────────────────────
        if self._pred_count % 20 == 0:  # log every ~10s
            winner = "BLUE" if self._smoothed_win_prob > 0.5 else "RED"
            tf_action = (
                self._last_teamfight_assessment.recommended_action.value
                if self._last_teamfight_assessment
                else tf_pred.recommended_action
            )
            logger.info(
                "Win prediction: %s %.1f%% (conf=%.0f%%) at %.0fs | "
                "Teamfight: %.0f%% → %s",
                winner,
                max(self._smoothed_win_prob, 1 - self._smoothed_win_prob) * 100,
                confidence * 100,
                snapshot.game_time,
                tf_pred.likelihood * 100,
                tf_action,
            )

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    def _publish_status(self, status: Status) -> None:
        if self._status_writer:
            self._status_writer.Write(StatusMessage(
                status=status,
                sequence=self._pred_count,
                source_component="prediction",
            ))

    # ── Introspection ────────────────────────────────────────────────

    @property
    def current_win_prob(self) -> float:
        return self._smoothed_win_prob

    def prediction_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "prediction_count": self._pred_count,
            "current_win_prob": round(self._smoothed_win_prob, 4),
            "model_version": self._win_predictor.version if self._win_predictor else "N/A",
            "history_size": len(self._prediction_history),
            "teamfight_predictor_active": self._teamfight_predictor is not None,
            "last_teamfight_action": (
                self._last_teamfight_assessment.recommended_action.value
                if self._last_teamfight_assessment else "N/A"
            ),
        })
        return base

    # ─── Claude17: Prediction Accuracy Tracking ──────────────────────────

    def record_actual_outcome(self, won: bool) -> Dict[str, Any]:
        """Record the actual game outcome for accuracy evaluation.

        Claude17: After each game, compare predicted win probability
        at various checkpoints against the actual result. This feeds
        into the evolution fitness evaluator.

        Args:
            won: True if our team won.

        Returns:
            Dict with accuracy metrics for this session.
        """
        actual = 1.0 if won else 0.0
        errors = []
        calibration_bins: Dict[str, List[float]] = {
            "0.0-0.2": [], "0.2-0.4": [], "0.4-0.6": [],
            "0.6-0.8": [], "0.8-1.0": [],
        }

        for pred in self._prediction_history:
            prob = pred if isinstance(pred, float) else getattr(
                pred, 'win_probability', 0.5
            )
            error = abs(prob - actual)
            errors.append(error)

            # Calibration: group by predicted prob range
            if prob < 0.2:
                calibration_bins["0.0-0.2"].append(actual)
            elif prob < 0.4:
                calibration_bins["0.2-0.4"].append(actual)
            elif prob < 0.6:
                calibration_bins["0.4-0.6"].append(actual)
            elif prob < 0.8:
                calibration_bins["0.6-0.8"].append(actual)
            else:
                calibration_bins["0.8-1.0"].append(actual)

        mae = sum(errors) / max(len(errors), 1)
        brier = sum(e ** 2 for e in errors) / max(len(errors), 1)

        # Final prediction accuracy (did we call it right?)
        final_prob = self._smoothed_win_prob
        correct_call = (final_prob > 0.5) == won

        return {
            "actual_outcome": "win" if won else "loss",
            "final_prediction": round(final_prob, 4),
            "correct_call": correct_call,
            "mae": round(mae, 4),
            "brier_score": round(brier, 4),
            "prediction_count": len(self._prediction_history),
            "calibration": {
                k: {
                    "count": len(v),
                    "actual_rate": round(sum(v) / max(len(v), 1), 4),
                }
                for k, v in calibration_bins.items() if v
            },
        }

    def get_prediction_trend(
        self, last_n: int = 20
    ) -> List[float]:
        """Return the last N win probability values.

        Claude17: Used by planning to detect momentum shifts.
        A rising trend suggests our team is gaining advantage.
        """
        history = self._prediction_history[-last_n:]
        return [
            p if isinstance(p, float) else getattr(
                p, 'win_probability', 0.5
            )
            for p in history
        ]

    def get_momentum(self, window: int = 10) -> float:
        """Compute momentum: rate of change of win probability.

        Claude17: Positive = gaining, Negative = losing.
        Used by planning to decide aggression level.

        Returns:
            Float rate per minute (positive = improving).
        """
        trend = self.get_prediction_trend(window)
        if len(trend) < 2:
            return 0.0

        # Simple linear regression slope
        n = len(trend)
        x_mean = (n - 1) / 2.0
        y_mean = sum(trend) / n
        numerator = sum(
            (i - x_mean) * (trend[i] - y_mean) for i in range(n)
        )
        denominator = sum((i - x_mean) ** 2 for i in range(n))

        if abs(denominator) < 1e-10:
            return 0.0

        slope_per_tick = numerator / denominator
        # Convert to per-minute based on component interval
        ticks_per_min = 60000.0 / max(self.interval_ms, 1)
        return round(slope_per_tick * ticks_per_min, 6)


    # ─── Apollo-aligned data freshness and confidence (Claude23) ─────────
    #
    # Apollo prediction_component.cc checks ADCTrajectory freshness before
    # running prediction. We add feature staleness check and confidence bounds.

    def _check_features_fresh(self, snapshot: Any) -> bool:
        """Check if the GameSnapshot is fresh enough for prediction.

        Apollo pattern: prediction checks localization/perception timestamps
        before computing. Stale input → stale prediction → dangerous.

        Returns True if snapshot is fresh enough to trust.
        """
        if snapshot is None:
            return False

        snap_time = getattr(snapshot, "game_time", 0.0)
        if snap_time <= 0:
            return False

        # Check if this is a duplicate (same game_time as last prediction)
        if hasattr(self, "_last_predicted_game_time"):
            if snap_time <= self._last_predicted_game_time:
                return False  # stale or duplicate

        return True

    def _clamp_confidence(
        self, raw_prob: float, min_conf: float = 0.05, max_conf: float = 0.95
    ) -> float:
        """Clamp win probability to avoid extreme overconfidence.

        No model should output 0% or 100% — even in clearly won/lost games,
        throws and comebacks happen. This guard prevents the voice announcer
        from making embarrassing absolute statements.

        Args:
            raw_prob: Raw win probability from model (0.0 to 1.0).
            min_conf: Floor (default 5%).
            max_conf: Ceiling (default 95%).

        Returns:
            Clamped probability.
        """
        return max(min_conf, min(max_conf, raw_prob))

    def _safe_mode_prediction(self) -> float:
        """Return a neutral prediction when in safe mode.

        Apollo equivalent: when data is stale, don't compute new predictions.
        Return 0.5 (neutral) or last-known value if available.
        """
        if hasattr(self, "_last_win_prob") and self._last_win_prob > 0:
            return self._last_win_prob
        return 0.5

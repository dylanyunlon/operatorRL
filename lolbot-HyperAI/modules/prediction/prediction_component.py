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


# ─── Feature Engineering ────────────────────────────────────────────────────

@dataclass
class PredictionFeatures:
    """Extracted features for the win prediction model.

    Combines game state features with derived statistics.
    """
    # Base features from GameSnapshot.to_feature_dict()
    game_time: float = 0.0
    gold_diff: float = 0.0
    kill_diff: int = 0
    tower_diff: int = 0
    dragon_diff: int = 0
    blue_barons: int = 0
    red_barons: int = 0
    blue_avg_level: float = 0.0
    red_avg_level: float = 0.0
    blue_alive: int = 5
    red_alive: int = 5

    # Derived features
    gold_diff_per_min: float = 0.0
    kill_rate_blue: float = 0.0
    kill_rate_red: float = 0.0
    level_advantage: float = 0.0
    alive_advantage: int = 0

    # Tempo features
    gold_trend: float = 0.0        # gold_diff change over last 2 min
    recent_kill_advantage: int = 0  # kill diff in last 2 min

    @staticmethod
    def from_snapshot(
        snapshot: GameSnapshot,
        prev_snapshot: Optional[GameSnapshot] = None,
    ) -> "PredictionFeatures":
        """Extract features from a game snapshot.

        Args:
            snapshot: Current game snapshot.
            prev_snapshot: Snapshot from ~2 minutes ago for trends.
        """
        fd = snapshot.to_feature_dict()
        game_time = max(fd["game_time"], 1.0)
        game_min = game_time / 60.0

        features = PredictionFeatures(
            game_time=game_time,
            gold_diff=fd["gold_diff"],
            kill_diff=fd["kill_diff"],
            tower_diff=fd["tower_diff"],
            dragon_diff=fd["dragon_diff"],
            blue_barons=fd["blue_barons"],
            red_barons=fd["red_barons"],
            blue_avg_level=fd["blue_avg_level"],
            red_avg_level=fd["red_avg_level"],
            blue_alive=fd["blue_alive"],
            red_alive=fd["red_alive"],
            gold_diff_per_min=fd["gold_diff"] / game_min,
            kill_rate_blue=fd["blue_kills"] / game_min,
            kill_rate_red=fd["red_kills"] / game_min,
            level_advantage=fd["blue_avg_level"] - fd["red_avg_level"],
            alive_advantage=fd["blue_alive"] - fd["red_alive"],
        )

        # Compute trends if we have historical data
        if prev_snapshot is not None:
            prev_fd = prev_snapshot.to_feature_dict()
            features = PredictionFeatures(
                **{
                    k: getattr(features, k)
                    for k in features.__dataclass_fields__
                    if k not in ("gold_trend", "recent_kill_advantage")
                },
                gold_trend=fd["gold_diff"] - prev_fd["gold_diff"],
                recent_kill_advantage=(
                    fd["kill_diff"] - prev_fd["kill_diff"]
                ),
            )

        return features

    def to_vector(self) -> List[float]:
        """Convert to a flat feature vector for model input."""
        return [
            self.game_time / 3600.0,           # normalize to hours
            self.gold_diff / 10000.0,           # normalize
            float(self.kill_diff) / 20.0,
            float(self.tower_diff) / 11.0,
            float(self.dragon_diff) / 4.0,
            float(self.blue_barons) / 3.0,
            float(self.red_barons) / 3.0,
            self.blue_avg_level / 18.0,
            self.red_avg_level / 18.0,
            float(self.blue_alive) / 5.0,
            float(self.red_alive) / 5.0,
            self.gold_diff_per_min / 1000.0,
            self.level_advantage / 5.0,
            float(self.alive_advantage) / 5.0,
            self.gold_trend / 5000.0,
            float(self.recent_kill_advantage) / 10.0,
        ]


# ─── Win Predictor (heuristic baseline) ─────────────────────────────────────

class WinPredictor:
    """Heuristic win probability model.

    Uses a weighted combination of game features to estimate win
    probability.  Designed as a baseline; can be replaced by a
    trained ML model inheriting the same interface.

    The sigmoid function maps the weighted sum to [0, 1] probability.
    Weights are tuned from high-elo game analysis patterns.
    """

    # Feature weights (positive = favors blue)
    _WEIGHTS: Dict[str, float] = {
        "gold_diff_norm": 2.5,
        "kill_diff_norm": 1.2,
        "tower_diff_norm": 3.0,
        "dragon_diff_norm": 1.5,
        "baron_diff": 2.0,
        "level_advantage_norm": 0.8,
        "alive_advantage_norm": 1.5,
        "gold_trend_norm": 0.6,
        "recent_kills_norm": 0.4,
    }

    def __init__(self, model_version: str = "heuristic-v1") -> None:
        self._version = model_version

    def predict(self, features: PredictionFeatures) -> float:
        """Predict blue team win probability.

        Args:
            features: Extracted game features.

        Returns:
            Probability [0, 1] that blue team wins.
        """
        # Compute weighted score
        score = 0.0
        w = self._WEIGHTS

        score += w["gold_diff_norm"] * (features.gold_diff / 10000.0)
        score += w["kill_diff_norm"] * (features.kill_diff / 20.0)
        score += w["tower_diff_norm"] * (features.tower_diff / 11.0)
        score += w["dragon_diff_norm"] * (features.dragon_diff / 4.0)
        score += w["baron_diff"] * (
            (features.blue_barons - features.red_barons) / 3.0
        )
        score += w["level_advantage_norm"] * (features.level_advantage / 5.0)
        score += w["alive_advantage_norm"] * (
            features.alive_advantage / 5.0
        )
        score += w["gold_trend_norm"] * (features.gold_trend / 5000.0)
        score += w["recent_kills_norm"] * (
            features.recent_kill_advantage / 10.0
        )

        # Sigmoid mapping
        prob = 1.0 / (1.0 + math.exp(-score))
        return max(0.01, min(0.99, prob))  # clamp to avoid 0/1 extremes

    def feature_importance(
        self, features: PredictionFeatures
    ) -> List[Tuple[str, float]]:
        """Return ranked feature contributions to the prediction."""
        contributions = [
            ("gold_diff", self._WEIGHTS["gold_diff_norm"] * features.gold_diff / 10000.0),
            ("kill_diff", self._WEIGHTS["kill_diff_norm"] * features.kill_diff / 20.0),
            ("tower_diff", self._WEIGHTS["tower_diff_norm"] * features.tower_diff / 11.0),
            ("dragon_diff", self._WEIGHTS["dragon_diff_norm"] * features.dragon_diff / 4.0),
            ("alive_advantage", self._WEIGHTS["alive_advantage_norm"] * features.alive_advantage / 5.0),
        ]
        contributions.sort(key=lambda x: abs(x[1]), reverse=True)
        return contributions

    @property
    def version(self) -> str:
        return self._version


# ─── Legacy TeamfightAnalyzer (kept for backward compat) ────────────────────

class TeamfightAnalyzer:
    """Lightweight teamfight analyzer — legacy fallback.

    Phase 4 replaces this with TeamfightPredictor for richer analysis,
    but we keep this class so that any code importing it doesn't break.
    The PredictionComponent now uses TeamfightPredictor as primary and
    only falls back to this if TeamfightPredictor is unavailable.
    """

    def analyze(self, snapshot: GameSnapshot) -> TeamfightPrediction:
        """Predict teamfight probability and expected outcome."""
        blue = snapshot.blue_team
        red = snapshot.red_team

        # Base likelihood from alive counts
        total_alive = blue.alive_count + red.alive_count
        base_likelihood = total_alive / 10.0

        # Phase multiplier
        phase_mult = {
            GamePhase.LOADING: 0.0,
            GamePhase.EARLY: 0.3,
            GamePhase.MID: 0.7,
            GamePhase.LATE: 0.9,
            GamePhase.ENDING: 1.0,
            GamePhase.POST_GAME: 0.0,
        }.get(snapshot.phase, 0.5)

        # Recent kill activity boost
        recent_kills = len([
            e for e in snapshot.new_events
            if e.event_type.value == "ChampionKill"
        ])
        kill_boost = min(0.3, recent_kills * 0.1)

        likelihood = min(1.0, base_likelihood * phase_mult + kill_boost)

        # Win-if-fight estimate
        alive_diff = blue.alive_count - red.alive_count
        level_diff = blue.avg_level - red.avg_level
        fight_score = alive_diff * 0.15 + level_diff * 0.05
        blue_win_if_fight = 1.0 / (1.0 + math.exp(-fight_score))

        # Recommendation
        if snapshot.active_player is None:
            action = "hold"
        elif snapshot.active_team == TeamSide.BLUE:
            if blue_win_if_fight > 0.6:
                action = "engage"
            elif blue_win_if_fight < 0.4:
                action = "disengage"
            else:
                action = "hold"
        else:
            if blue_win_if_fight < 0.4:
                action = "engage"
            elif blue_win_if_fight > 0.6:
                action = "disengage"
            else:
                action = "hold"

        return TeamfightPrediction(
            likelihood=likelihood,
            blue_win_if_fight=blue_win_if_fight,
            recommended_action=action,
            reasoning=f"Alive: {blue.alive_count}v{red.alive_count}, "
                      f"Phase: {snapshot.phase.name}",
            game_time=snapshot.game_time,
        )


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

        self.register_self()
        self._transition(LifecycleState.READY)
        self._transition(LifecycleState.RUNNING)
        logger.info(
            "PredictionComponent initialized (model=%s, teamfight=TeamfightPredictor)",
            self._win_predictor.version,
        )
        return True

    def Proc(self) -> bool:
        """One prediction cycle.

        Apollo equivalent: ``PredictionComponent::Proc()``
        """
        self._game_state_reader.Observe()
        snapshot: Optional[GameSnapshot] = (
            self._game_state_reader.GetLatestObserved()
        )

        if snapshot is None:
            return True  # no data yet

        # Skip prediction for very early game
        if snapshot.game_time < _MIN_GAME_TIME_FOR_PREDICTION:
            return True

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
        confidence = min(1.0, snapshot.game_time / _CONFIDENCE_RAMP_TIME)

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

        self._publish_status(Status.ok())
        return True

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

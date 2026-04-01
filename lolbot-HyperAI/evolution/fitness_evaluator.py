#!/usr/bin/env python3
"""
evolution/fitness_evaluator.py — Generation Fitness Scoring
=============================================================
lolbot-HyperAI · Evolution Layer

In the self-evolution loop (plan.md §二):
    程序A → 运行，撞墙，记录日志
    LLM（修复酶）→ 看日志，建议修改
    程序A'（新一代）→ 替换 A

The Fitness Evaluator is the "看日志" step: it reads all logged data
from a session and produces a single fitness score that determines
whether generation A' outperforms generation A.

Fitness dimensions:
    1. Prediction accuracy: Was our win probability calibrated?
       (Did 60% predictions win 60% of the time?)
    2. Recommendation relevance: Were recommendations timely and
       contextually appropriate? (Approximated by timing analysis)
    3. System health: Uptime, error rate, latency percentiles
    4. Coverage: Did we generate recommendations across all game phases?
    5. User engagement: Announcement frequency (not too many, not too few)

The fitness score is a weighted sum normalized to [0, 1].
The evolution controller compares generation A and A' fitness scores
to decide whether to commit or rollback.

Subscribes to: (reads from transport history / recording files)
Publishes to: CH_EVOLUTION_FITNESS
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_EVOLUTION_FITNESS,
    CH_LIVE_GAME_STATE,
    CH_STRATEGY_RECOMMENDATION,
    CH_SYSTEM_ERROR,
    CH_SYSTEM_HEARTBEAT,
    CH_VOICE_ANNOUNCEMENT,
    CH_WIN_PROBABILITY,
    ChannelMessage,
    MessageFactory,
)
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# Fitness metrics
# ---------------------------------------------------------------------------
@dataclass
class PredictionAccuracyMetrics:
    """Measures how well-calibrated win predictions were."""
    total_predictions: int = 0
    # Bucket predictions by predicted probability
    # bucket_key = rounded win_pct (0.1, 0.2, ..., 0.9)
    # value = list of booleans (actual outcomes)
    calibration_buckets: Dict[float, List[bool]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # Brier score: mean squared error of probabilistic predictions
    brier_sum: float = 0.0
    brier_count: int = 0
    # Trend accuracy: did trend direction match actual outcome change?
    trend_correct: int = 0
    trend_total: int = 0

    def brier_score(self) -> float:
        """
        Brier score: lower is better. 0 = perfect, 0.25 = random.
        """
        if self.brier_count == 0:
            return 0.25  # Default to random
        return self.brier_sum / self.brier_count

    def calibration_error(self) -> float:
        """
        Expected Calibration Error (ECE).

        For each bucket: |actual_win_rate - predicted_probability|
        Weighted by bucket size.
        """
        if not self.calibration_buckets:
            return 0.5
        total_n = sum(len(v) for v in self.calibration_buckets.values())
        if total_n == 0:
            return 0.5
        ece = 0.0
        for predicted_pct, outcomes in self.calibration_buckets.items():
            actual_rate = sum(outcomes) / len(outcomes)
            weight = len(outcomes) / total_n
            ece += weight * abs(actual_rate - predicted_pct)
        return ece

    def trend_accuracy(self) -> float:
        if self.trend_total == 0:
            return 0.5
        return self.trend_correct / self.trend_total

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_predictions": self.total_predictions,
            "brier_score": round(self.brier_score(), 4),
            "calibration_error": round(self.calibration_error(), 4),
            "trend_accuracy": round(self.trend_accuracy(), 3),
            "bucket_sizes": {
                str(k): len(v)
                for k, v in self.calibration_buckets.items()
            },
        }


@dataclass
class RecommendationMetrics:
    """Measures recommendation quality."""
    total_generated: int = 0
    total_published: int = 0
    by_type: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_phase: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_priority: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    timing_scores: List[float] = field(default_factory=list)

    def type_coverage(self) -> float:
        """Fraction of recommendation types that were used."""
        total_types = 15  # Number of RecType values
        return min(len(self.by_type) / total_types, 1.0)

    def phase_coverage(self) -> float:
        """Fraction of game phases that received recommendations."""
        total_phases = 5  # early_laning, laning, mid_game, late_game, champ_select
        return min(len(self.by_phase) / total_phases, 1.0)

    def avg_timing_score(self) -> float:
        """Average timing appropriateness (0 = bad, 1 = perfect)."""
        if not self.timing_scores:
            return 0.5
        return sum(self.timing_scores) / len(self.timing_scores)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_generated": self.total_generated,
            "total_published": self.total_published,
            "type_coverage": round(self.type_coverage(), 3),
            "phase_coverage": round(self.phase_coverage(), 3),
            "avg_timing_score": round(self.avg_timing_score(), 3),
            "by_type": dict(self.by_type),
            "by_phase": dict(self.by_phase),
        }


@dataclass
class SystemHealthMetrics:
    """Measures system operational health."""
    uptime_ms: int = 0
    total_errors: int = 0
    total_messages: int = 0
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    components_healthy: int = 0
    components_total: int = 0
    dropped_messages: int = 0

    def error_rate(self) -> float:
        if self.total_messages == 0:
            return 0.0
        return self.total_errors / self.total_messages

    def availability(self) -> float:
        if self.components_total == 0:
            return 1.0
        return self.components_healthy / self.components_total

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uptime_ms": self.uptime_ms,
            "error_rate": round(self.error_rate(), 4),
            "availability": round(self.availability(), 3),
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "dropped_messages": self.dropped_messages,
        }


@dataclass
class AnnouncementMetrics:
    """Measures voice announcement quality."""
    total_announced: int = 0
    total_dropped: int = 0
    total_deduped: int = 0
    announcements_per_minute: float = 0.0

    def drop_rate(self) -> float:
        total = self.total_announced + self.total_dropped
        if total == 0:
            return 0.0
        return self.total_dropped / total

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_announced": self.total_announced,
            "drop_rate": round(self.drop_rate(), 3),
            "announcements_per_minute": round(self.announcements_per_minute, 2),
        }


# ---------------------------------------------------------------------------
# Fitness score computation
# ---------------------------------------------------------------------------
@dataclass
class FitnessScore:
    """
    Aggregated fitness score for one generation/session.

    The total fitness is a weighted sum of sub-scores, each in [0, 1].
    """
    generation_id: str = ""
    session_id: str = ""

    # Sub-scores (all 0-1, higher is better)
    prediction_score: float = 0.0
    recommendation_score: float = 0.0
    health_score: float = 0.0
    coverage_score: float = 0.0
    engagement_score: float = 0.0

    # Weights
    prediction_weight: float = 0.30
    recommendation_weight: float = 0.25
    health_weight: float = 0.20
    coverage_weight: float = 0.15
    engagement_weight: float = 0.10

    # Raw metrics (for detailed analysis)
    prediction_metrics: Optional[Dict] = None
    recommendation_metrics: Optional[Dict] = None
    health_metrics: Optional[Dict] = None
    announcement_metrics: Optional[Dict] = None

    @property
    def total(self) -> float:
        """Weighted total fitness score in [0, 1]."""
        return (
            self.prediction_score * self.prediction_weight
            + self.recommendation_score * self.recommendation_weight
            + self.health_score * self.health_weight
            + self.coverage_score * self.coverage_weight
            + self.engagement_score * self.engagement_weight
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "session_id": self.session_id,
            "total_fitness": round(self.total, 4),
            "sub_scores": {
                "prediction": round(self.prediction_score, 4),
                "recommendation": round(self.recommendation_score, 4),
                "health": round(self.health_score, 4),
                "coverage": round(self.coverage_score, 4),
                "engagement": round(self.engagement_score, 4),
            },
            "weights": {
                "prediction": self.prediction_weight,
                "recommendation": self.recommendation_weight,
                "health": self.health_weight,
                "coverage": self.coverage_weight,
                "engagement": self.engagement_weight,
            },
            "raw_metrics": {
                "prediction": self.prediction_metrics,
                "recommendation": self.recommendation_metrics,
                "health": self.health_metrics,
                "announcement": self.announcement_metrics,
            },
        }


# ---------------------------------------------------------------------------
# Fitness Evaluator Component
# ---------------------------------------------------------------------------
class FitnessEvaluator:
    """
    Evaluates fitness of a session/generation from logged data.

    Can evaluate:
        1. Live session (from transport history)
        2. Recorded session (from JSONL file)
        3. Replay session (from replayed recording)

    The evaluator collects metrics from all channels and computes
    a FitnessScore that the evolution controller uses to decide
    whether to keep or rollback a mutation.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._factory = MessageFactory("evolution.fitness_evaluator")

        # Collected metrics
        self._pred_metrics = PredictionAccuracyMetrics()
        self._rec_metrics = RecommendationMetrics()
        self._health_metrics = SystemHealthMetrics()
        self._announce_metrics = AnnouncementMetrics()

        # Collection state
        self._collecting = False
        self._collection_start_ms = 0
        self._unsubs: List[Callable] = []

    def start_collection(self) -> None:
        """Begin collecting metrics from the bus."""
        self._collecting = True
        self._collection_start_ms = int(time.monotonic() * 1000)

        # Reset metrics
        self._pred_metrics = PredictionAccuracyMetrics()
        self._rec_metrics = RecommendationMetrics()
        self._health_metrics = SystemHealthMetrics()
        self._announce_metrics = AnnouncementMetrics()

        # Subscribe to all relevant channels
        self._unsubs.append(
            self._transport.subscribe(
                CH_WIN_PROBABILITY, self._on_prediction,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_STRATEGY_RECOMMENDATION, self._on_recommendation,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_SYSTEM_HEARTBEAT, self._on_heartbeat,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_SYSTEM_ERROR, self._on_error,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_VOICE_ANNOUNCEMENT, self._on_announcement,
            )
        )

    def stop_collection(self) -> None:
        """Stop collecting and unsubscribe."""
        self._collecting = False
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()

    def evaluate(
        self,
        generation_id: str = "",
        session_id: str = "",
        game_won: Optional[bool] = None,
    ) -> FitnessScore:
        """
        Compute the fitness score from collected metrics.

        Args:
            generation_id: Identifier for this generation.
            session_id: Identifier for this session.
            game_won: Actual game outcome (if known). Used for
                      prediction calibration.

        Returns:
            FitnessScore with all sub-scores computed.
        """
        # If we know the outcome, update calibration
        if game_won is not None:
            self._update_calibration(game_won)

        score = FitnessScore(
            generation_id=generation_id,
            session_id=session_id,
        )

        # 1. Prediction score
        score.prediction_score = self._score_predictions()
        score.prediction_metrics = self._pred_metrics.to_dict()

        # 2. Recommendation score
        score.recommendation_score = self._score_recommendations()
        score.recommendation_metrics = self._rec_metrics.to_dict()

        # 3. Health score
        score.health_score = self._score_health()
        score.health_metrics = self._health_metrics.to_dict()

        # 4. Coverage score
        score.coverage_score = self._score_coverage()

        # 5. Engagement score
        score.engagement_score = self._score_engagement()
        score.announcement_metrics = self._announce_metrics.to_dict()

        # Publish fitness score to bus
        msg = self._factory.create(
            CH_EVOLUTION_FITNESS,
            {
                "generation_id": generation_id,
                "fitness_score": round(score.total, 4),
                "metrics": score.to_dict(),
            },
        )
        self._transport.publish(msg)

        return score

    def evaluate_from_history(
        self,
        game_won: Optional[bool] = None,
        generation_id: str = "",
    ) -> FitnessScore:
        """
        Evaluate fitness from transport history (no live collection).

        Useful for evaluating a replayed session.
        """
        # Process prediction history
        for msg in self._transport.history(CH_WIN_PROBABILITY):
            self._on_prediction(msg)

        for msg in self._transport.history(CH_STRATEGY_RECOMMENDATION):
            self._on_recommendation(msg)

        for msg in self._transport.history(CH_VOICE_ANNOUNCEMENT):
            self._on_announcement(msg)

        return self.evaluate(
            generation_id=generation_id,
            game_won=game_won,
        )

    # -- Subscription handlers ------------------------------------------

    def _on_prediction(self, msg: ChannelMessage) -> None:
        p = msg.payload
        self._pred_metrics.total_predictions += 1
        win_pct = p.get("win_pct", 0.5)
        # Bucket by rounded probability
        bucket = round(win_pct, 1)
        # (We'll fill in actual outcomes in evaluate())

    def _on_recommendation(self, msg: ChannelMessage) -> None:
        p = msg.payload
        self._rec_metrics.total_published += 1
        rec_type = p.get("rec_type", "unknown")
        phase = p.get("game_phase", "unknown")
        priority = p.get("priority", 2)
        self._rec_metrics.by_type[rec_type] += 1
        self._rec_metrics.by_phase[phase] += 1
        self._rec_metrics.by_priority[priority] += 1

    def _on_heartbeat(self, msg: ChannelMessage) -> None:
        p = msg.payload
        self._health_metrics.total_messages += 1
        status = p.get("status", "ok")
        if status == "ok":
            self._health_metrics.components_healthy += 1
        self._health_metrics.components_total += 1
        uptime = p.get("uptime_ms", 0)
        if uptime > self._health_metrics.uptime_ms:
            self._health_metrics.uptime_ms = uptime

    def _on_error(self, msg: ChannelMessage) -> None:
        self._health_metrics.total_errors += 1

    def _on_announcement(self, msg: ChannelMessage) -> None:
        self._announce_metrics.total_announced += 1

    # -- Calibration ----------------------------------------------------

    def _update_calibration(self, game_won: bool) -> None:
        """
        Update prediction calibration with actual game outcome.

        Retroactively fills in calibration buckets from prediction history.
        """
        predictions = self._transport.history(CH_WIN_PROBABILITY)
        for msg in predictions:
            win_pct = msg.payload.get("win_pct", 0.5)
            bucket = round(win_pct, 1)
            self._pred_metrics.calibration_buckets[bucket].append(game_won)

            # Brier score
            error = (win_pct - (1.0 if game_won else 0.0)) ** 2
            self._pred_metrics.brier_sum += error
            self._pred_metrics.brier_count += 1

    # -- Scoring functions ----------------------------------------------

    def _score_predictions(self) -> float:
        """Score prediction quality (0-1)."""
        brier = self._pred_metrics.brier_score()
        # Brier: 0 = perfect, 0.25 = random. Invert to 0-1 scale.
        brier_score = max(0, 1.0 - brier / 0.25)

        ece = self._pred_metrics.calibration_error()
        calibration_score = max(0, 1.0 - ece * 2)

        trend_score = self._pred_metrics.trend_accuracy()

        # Weighted combination
        return 0.4 * brier_score + 0.4 * calibration_score + 0.2 * trend_score

    def _score_recommendations(self) -> float:
        """Score recommendation quality (0-1)."""
        coverage = self._rec_metrics.type_coverage()
        phase_cov = self._rec_metrics.phase_coverage()
        timing = self._rec_metrics.avg_timing_score()

        # Recommendation volume: too few is bad, too many is bad
        # Sweet spot: 1-3 per minute during active game
        total = self._rec_metrics.total_published
        game_duration_min = self._health_metrics.uptime_ms / 60000
        if game_duration_min > 0:
            rec_per_min = total / game_duration_min
        else:
            rec_per_min = 0

        # Optimal: 1.5 rec/min. Score drops off either side.
        volume_score = max(0, 1.0 - abs(rec_per_min - 1.5) / 3.0)

        return 0.3 * coverage + 0.2 * phase_cov + 0.2 * timing + 0.3 * volume_score

    def _score_health(self) -> float:
        """Score system health (0-1)."""
        error_rate = self._health_metrics.error_rate()
        error_score = max(0, 1.0 - error_rate * 10)  # Penalize heavily

        availability = self._health_metrics.availability()

        # Latency: <10ms = perfect, >100ms = bad
        lat = self._health_metrics.latency_p95_ms
        latency_score = max(0, 1.0 - lat / 100.0) if lat > 0 else 1.0

        return 0.4 * error_score + 0.3 * availability + 0.3 * latency_score

    def _score_coverage(self) -> float:
        """Score how well we covered the game session (0-1)."""
        # Did we generate predictions at all?
        has_predictions = 1.0 if self._pred_metrics.total_predictions > 10 else 0.3
        has_recs = 1.0 if self._rec_metrics.total_published > 5 else 0.3
        phase_cov = self._rec_metrics.phase_coverage()

        return 0.3 * has_predictions + 0.3 * has_recs + 0.4 * phase_cov

    def _score_engagement(self) -> float:
        """Score announcement engagement (0-1)."""
        total = self._announce_metrics.total_announced
        dropped = self._announce_metrics.total_dropped

        # Not too few, not too many
        game_min = max(self._health_metrics.uptime_ms / 60000, 0.1)
        per_min = total / game_min

        # Optimal: 0.5-2 per minute
        if per_min < 0.3:
            volume_score = per_min / 0.3
        elif per_min > 3.0:
            volume_score = max(0, 1.0 - (per_min - 3.0) / 5.0)
        else:
            volume_score = 1.0

        # Low drop rate is good
        drop_rate = self._announce_metrics.drop_rate()
        drop_score = max(0, 1.0 - drop_rate * 2)

        return 0.6 * volume_score + 0.4 * drop_score

    # -- Stats ----------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        return {
            "collecting": self._collecting,
            "predictions_tracked": self._pred_metrics.total_predictions,
            "recommendations_tracked": self._rec_metrics.total_published,
            "announcements_tracked": self._announce_metrics.total_announced,
            "errors_tracked": self._health_metrics.total_errors,
        }

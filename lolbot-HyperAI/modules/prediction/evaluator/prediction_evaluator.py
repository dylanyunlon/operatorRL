"""
PredictionEvaluator — Post-game accuracy evaluation for evolution.
====================================================================
modules/prediction/evaluator/prediction_evaluator.py

Claude17: After each game, evaluates how accurate the prediction
model was. This feeds directly into the evolution fitness function.

Architecture position:
    modules/prediction/evaluator/prediction_evaluator.py  ← YOU ARE HERE
    ├─ Reads: prediction history from PredictionComponent
    ├─ Reads: actual game outcome
    ├─ Computes: Brier score, calibration, accuracy metrics
    ├─ Publishes: /lol/prediction_eval (evaluation report)
    └─ Consumed by: evolution/fitness_evaluator.py

Apollo reference:
    modules/prediction/evaluator/evaluator_manager.h

Design notes:
    - Brier score: proper scoring rule for probabilistic predictions
    - Calibration: do 70% predictions actually win 70% of the time?
    - Log loss: information-theoretic measure of prediction quality
    - Time-weighted: early predictions matter less than late ones
    - Game-phase breakdown: accuracy per phase (early/mid/late)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class PredictionCheckpoint:
    """A single prediction recorded during the game."""
    game_time: float
    win_probability: float
    confidence: float = 1.0
    phase: str = ""  # early/mid/late


@dataclass
class EvaluationReport:
    """Complete evaluation of predictions for one game."""
    session_id: str
    actual_outcome: bool  # True = win
    prediction_count: int
    brier_score: float
    log_loss: float
    correct_call: bool
    final_prediction: float
    calibration: Dict[str, Dict[str, float]]
    phase_accuracy: Dict[str, float]
    time_weighted_brier: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "outcome": "win" if self.actual_outcome else "loss",
            "prediction_count": self.prediction_count,
            "brier_score": round(self.brier_score, 6),
            "log_loss": round(self.log_loss, 6),
            "correct_call": self.correct_call,
            "final_prediction": round(self.final_prediction, 4),
            "calibration": self.calibration,
            "phase_accuracy": {
                k: round(v, 4) for k, v in self.phase_accuracy.items()
            },
            "time_weighted_brier": round(self.time_weighted_brier, 6),
        }

    @property
    def fitness_score(self) -> float:
        """Convert evaluation into a single fitness value for evolution.

        Lower Brier score = better. We invert so higher = better.
        Scale to [0, 1] range.
        """
        # Brier score is in [0, 1] where 0 = perfect
        brier_fitness = 1.0 - self.brier_score
        # Bonus for correct final call
        call_bonus = 0.1 if self.correct_call else 0.0
        return min(1.0, brier_fitness + call_bonus)


class PredictionEvaluator:
    """Evaluates prediction accuracy after a game completes.

    Usage::

        evaluator = PredictionEvaluator()

        # During game: record checkpoints
        evaluator.record(game_time=120, win_prob=0.55, phase="early")
        evaluator.record(game_time=600, win_prob=0.62, phase="mid")

        # After game: evaluate
        report = evaluator.evaluate(
            session_id="session_123",
            won=True,
        )
        print(report.brier_score)

        # Reset for next game
        evaluator.reset()
    """

    def __init__(self) -> None:
        self._checkpoints: List[PredictionCheckpoint] = []
        self._evaluation_history: List[EvaluationReport] = []

    def record(
        self,
        game_time: float,
        win_prob: float,
        confidence: float = 1.0,
        phase: str = "",
    ) -> None:
        """Record a prediction checkpoint during the game."""
        self._checkpoints.append(PredictionCheckpoint(
            game_time=game_time,
            win_probability=max(0.001, min(0.999, win_prob)),
            confidence=confidence,
            phase=phase or self._classify_phase(game_time),
        ))

    @staticmethod
    def _classify_phase(game_time: float) -> str:
        if game_time < 840:
            return "early"
        elif game_time < 1500:
            return "mid"
        else:
            return "late"

    def evaluate(
        self,
        session_id: str,
        won: bool,
    ) -> EvaluationReport:
        """Evaluate all recorded predictions against actual outcome.

        Args:
            session_id: The game session identifier.
            won: Whether our team won.

        Returns:
            Complete EvaluationReport.
        """
        actual = 1.0 if won else 0.0
        n = len(self._checkpoints)

        if n == 0:
            return EvaluationReport(
                session_id=session_id,
                actual_outcome=won,
                prediction_count=0,
                brier_score=0.25,
                log_loss=0.693,
                correct_call=False,
                final_prediction=0.5,
                calibration={},
                phase_accuracy={},
                time_weighted_brier=0.25,
            )

        # ── Brier Score ──────────────────────────────────────────────
        brier = sum(
            (cp.win_probability - actual) ** 2
            for cp in self._checkpoints
        ) / n

        # ── Log Loss ─────────────────────────────────────────────────
        eps = 1e-15
        log_loss = -sum(
            actual * math.log(max(cp.win_probability, eps))
            + (1 - actual) * math.log(max(1 - cp.win_probability, eps))
            for cp in self._checkpoints
        ) / n

        # ── Time-weighted Brier ──────────────────────────────────────
        # Later predictions weighted more heavily
        max_time = max(cp.game_time for cp in self._checkpoints)
        if max_time > 0:
            weights = [
                (cp.game_time / max_time) ** 0.5
                for cp in self._checkpoints
            ]
            total_weight = sum(weights)
            tw_brier = sum(
                w * (cp.win_probability - actual) ** 2
                for w, cp in zip(weights, self._checkpoints)
            ) / max(total_weight, eps)
        else:
            tw_brier = brier

        # ── Final prediction ─────────────────────────────────────────
        final = self._checkpoints[-1].win_probability
        correct_call = (final > 0.5) == won

        # ── Calibration bins ─────────────────────────────────────────
        bins = {
            "0.0-0.2": [], "0.2-0.4": [], "0.4-0.6": [],
            "0.6-0.8": [], "0.8-1.0": [],
        }
        for cp in self._checkpoints:
            p = cp.win_probability
            if p < 0.2:
                bins["0.0-0.2"].append(actual)
            elif p < 0.4:
                bins["0.2-0.4"].append(actual)
            elif p < 0.6:
                bins["0.4-0.6"].append(actual)
            elif p < 0.8:
                bins["0.6-0.8"].append(actual)
            else:
                bins["0.8-1.0"].append(actual)

        calibration = {}
        for bin_name, outcomes in bins.items():
            if outcomes:
                calibration[bin_name] = {
                    "count": len(outcomes),
                    "actual_rate": round(
                        sum(outcomes) / len(outcomes), 4
                    ),
                }

        # ── Phase accuracy ───────────────────────────────────────────
        phase_errors: Dict[str, List[float]] = {}
        for cp in self._checkpoints:
            phase = cp.phase or "unknown"
            if phase not in phase_errors:
                phase_errors[phase] = []
            phase_errors[phase].append(
                abs(cp.win_probability - actual)
            )

        phase_accuracy = {
            phase: 1.0 - (sum(errs) / len(errs))
            for phase, errs in phase_errors.items()
        }

        report = EvaluationReport(
            session_id=session_id,
            actual_outcome=won,
            prediction_count=n,
            brier_score=brier,
            log_loss=log_loss,
            correct_call=correct_call,
            final_prediction=final,
            calibration=calibration,
            phase_accuracy=phase_accuracy,
            time_weighted_brier=tw_brier,
        )

        self._evaluation_history.append(report)
        return report

    def reset(self) -> None:
        """Clear checkpoints for next game."""
        self._checkpoints.clear()

    def get_history(self, last_n: int = 20) -> List[Dict[str, Any]]:
        """Return evaluation history across games."""
        return [r.to_dict() for r in self._evaluation_history[-last_n:]]

    def aggregate_stats(self) -> Dict[str, Any]:
        """Aggregate accuracy stats across all evaluated games."""
        if not self._evaluation_history:
            return {"games_evaluated": 0}

        briers = [r.brier_score for r in self._evaluation_history]
        correct = sum(1 for r in self._evaluation_history if r.correct_call)

        return {
            "games_evaluated": len(self._evaluation_history),
            "avg_brier_score": round(
                sum(briers) / len(briers), 6
            ),
            "best_brier": round(min(briers), 6),
            "worst_brier": round(max(briers), 6),
            "correct_call_rate": round(
                correct / len(self._evaluation_history), 4
            ),
            "avg_fitness": round(
                sum(r.fitness_score for r in self._evaluation_history)
                / len(self._evaluation_history), 4
            ),
        }

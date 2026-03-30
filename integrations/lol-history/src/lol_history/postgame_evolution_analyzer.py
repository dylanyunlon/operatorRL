"""
PostGameEvolutionAnalyzer — Analyses completed games to extract evolution signals.

After each game ends, this module compares the assistant's predictions and
recommendations against actual outcomes, computing accuracy metrics and
generating training signal updates for the agentic self-evolution loop.

Architecture (拿来主义):
  - Seraphine/app/lol/tools.py: parseGameDetailData — post-game stats extraction
  - Seraphine/app/lol/connector.py: getGameDetailByGameId — full game detail
  - integrations/lol/src/lol_agent/postgame_evolution_pipeline.py: evolution pattern
  - DI-star reinforcement learning feedback loop

Location: integrations/lol-history/src/lol_history/postgame_evolution_analyzer.py
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol_history.postgame_evolution_analyzer.v1"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _kda(k: int, d: int, a: int) -> float:
    return (k + a) / max(d, 1)


class PredictionRecord:
    """A single prediction made during the game with its outcome."""

    def __init__(
        self,
        prediction_type: str,
        predicted_value: Any,
        actual_value: Any,
        timestamp: float,
        confidence: float = 0.5,
    ) -> None:
        self.prediction_type = prediction_type
        self.predicted_value = predicted_value
        self.actual_value = actual_value
        self.timestamp = timestamp
        self.confidence = confidence

    @property
    def is_correct(self) -> bool:
        """Whether the prediction was correct."""
        if isinstance(self.predicted_value, (int, float)) and isinstance(self.actual_value, (int, float)):
            # Numeric: within 20% tolerance
            if self.actual_value == 0:
                return abs(self.predicted_value) < 0.1
            return abs(self.predicted_value - self.actual_value) / abs(self.actual_value) < 0.2
        return self.predicted_value == self.actual_value

    @property
    def error_magnitude(self) -> float:
        """Magnitude of prediction error (0 = perfect, 1 = complete miss)."""
        if isinstance(self.predicted_value, (int, float)) and isinstance(self.actual_value, (int, float)):
            if self.actual_value == 0:
                return min(abs(self.predicted_value), 1.0)
            return min(abs(self.predicted_value - self.actual_value) / max(abs(self.actual_value), 1), 1.0)
        return 0.0 if self.is_correct else 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prediction_type": self.prediction_type,
            "predicted_value": self.predicted_value,
            "actual_value": self.actual_value,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "is_correct": self.is_correct,
            "error_magnitude": self.error_magnitude,
        }


class PostGameEvolutionAnalyzer:
    """Analyses completed games for self-evolution learning signals.

    After each game, this class:
    1. Compares pre-game predictions to actual outcomes
    2. Evaluates recommendation quality
    3. Generates evolution reward signals
    4. Identifies areas for model improvement

    Public API
    ----------
    register_prediction(prediction_type, predicted, confidence)
    record_outcome(prediction_type, actual)
    analyze_game_outcome(game_detail) -> dict
    compute_prediction_accuracy() -> dict
    generate_evolution_signals(game_detail) -> dict
    identify_improvement_areas(game_history) -> dict
    compute_reward_signal(game_detail, predictions) -> dict
    run_full_postgame_analysis(game_detail) -> dict
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._analysis_count: int = 0
        self._predictions: List[PredictionRecord] = []
        self._pending_predictions: Dict[str, Tuple[Any, float, float]] = {}
        self._cumulative_accuracy: List[float] = []

    # ------------------------------------------------------------------ #
    #  1. Prediction Registration                                         #
    # ------------------------------------------------------------------ #

    def register_prediction(
        self,
        prediction_type: str,
        predicted_value: Any,
        confidence: float = 0.5,
    ) -> None:
        """Register a prediction made during or before the game.

        Parameters
        ----------
        prediction_type : str
            Category: "win", "kill_count", "death_count", "cs_at_15",
            "first_blood", "tower_first", etc.
        predicted_value : Any
            The predicted value.
        confidence : float
            Model's confidence in [0, 1].
        """
        self._pending_predictions[prediction_type] = (
            predicted_value, confidence, time.time()
        )

    def record_outcome(
        self,
        prediction_type: str,
        actual_value: Any,
    ) -> Optional[PredictionRecord]:
        """Record the actual outcome for a registered prediction.

        Parameters
        ----------
        prediction_type : str
            Must match a previously registered prediction.
        actual_value : Any
            The actual outcome.

        Returns
        -------
        PredictionRecord or None if no matching prediction found.
        """
        if prediction_type not in self._pending_predictions:
            return None

        predicted, confidence, ts = self._pending_predictions.pop(prediction_type)
        record = PredictionRecord(
            prediction_type=prediction_type,
            predicted_value=predicted,
            actual_value=actual_value,
            timestamp=ts,
            confidence=confidence,
        )
        self._predictions.append(record)
        return record

    # ------------------------------------------------------------------ #
    #  2. Analyze Game Outcome                                            #
    # ------------------------------------------------------------------ #

    def analyze_game_outcome(
        self,
        game_detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyse a completed game's outcome for learning signals.

        Parameters
        ----------
        game_detail : dict
            Full game detail with: win (bool), kills, deaths, assists, cs,
            game_duration, gold_earned, damage_dealt, damage_taken,
            vision_score, objectives_taken.

        Returns
        -------
        dict with performance_grade, key_metrics, performance_breakdown,
        improvement_suggestions.
        """
        win = game_detail.get("win", False)
        kills = game_detail.get("kills", 0)
        deaths = game_detail.get("deaths", 0)
        assists = game_detail.get("assists", 0)
        cs = game_detail.get("cs", 0)
        duration_min = game_detail.get("game_duration", 1) / 60.0
        gold = game_detail.get("gold_earned", 0)
        dmg_dealt = game_detail.get("damage_dealt", 0)
        dmg_taken = game_detail.get("damage_taken", 0)
        vision = game_detail.get("vision_score", 0)
        objectives = game_detail.get("objectives_taken", 0)

        kda = _kda(kills, deaths, assists)
        cs_per_min = _safe_div(cs, duration_min)
        gold_per_min = _safe_div(gold, duration_min)
        dmg_per_min = _safe_div(dmg_dealt, duration_min)

        # Performance breakdown (each in [0, 1])
        kda_score = min(kda / 8.0, 1.0)
        cs_score = min(cs_per_min / 8.0, 1.0)
        vision_score_norm = min(vision / 40.0, 1.0)
        objective_score = min(objectives / 4.0, 1.0)
        damage_score = min(dmg_per_min / 800.0, 1.0)
        survivability = max(0, 1.0 - deaths / max(kills + assists + 1, 1))

        breakdown = {
            "kda_score": round(kda_score, 4),
            "cs_score": round(cs_score, 4),
            "vision_score": round(vision_score_norm, 4),
            "objective_score": round(objective_score, 4),
            "damage_score": round(damage_score, 4),
            "survivability_score": round(survivability, 4),
        }

        # Overall grade
        overall = (
            kda_score * 0.25
            + cs_score * 0.20
            + vision_score_norm * 0.10
            + objective_score * 0.15
            + damage_score * 0.15
            + survivability * 0.15
        )

        # Letter grade
        if overall >= 0.85:
            grade = "S"
        elif overall >= 0.70:
            grade = "A"
        elif overall >= 0.55:
            grade = "B"
        elif overall >= 0.40:
            grade = "C"
        else:
            grade = "D"

        # Improvement suggestions
        suggestions: List[str] = []
        if cs_score < 0.5:
            suggestions.append("Improve CS/min — aim for 7+ CS/min consistently.")
        if vision_score_norm < 0.3:
            suggestions.append("Place more wards — vision wins games.")
        if survivability < 0.4:
            suggestions.append("Reduce deaths — positioning needs improvement.")
        if damage_score < 0.3:
            suggestions.append("Increase damage output — look for more fights.")

        key_metrics = {
            "kda": round(kda, 2),
            "cs_per_min": round(cs_per_min, 1),
            "gold_per_min": round(gold_per_min, 1),
            "damage_per_min": round(dmg_per_min, 1),
            "vision_score": vision,
        }

        return {
            "win": win,
            "performance_grade": grade,
            "overall_score": round(overall, 4),
            "key_metrics": key_metrics,
            "performance_breakdown": breakdown,
            "improvement_suggestions": suggestions,
        }

    # ------------------------------------------------------------------ #
    #  3. Compute Prediction Accuracy                                     #
    # ------------------------------------------------------------------ #

    def compute_prediction_accuracy(self) -> Dict[str, Any]:
        """Compute accuracy across all recorded predictions.

        Returns
        -------
        dict with overall_accuracy, accuracy_by_type, total_predictions,
        avg_confidence, calibration_error.
        """
        if not self._predictions:
            return {
                "overall_accuracy": 0.0,
                "accuracy_by_type": {},
                "total_predictions": 0,
                "avg_confidence": 0.0,
                "calibration_error": 0.0,
            }

        correct = sum(1 for p in self._predictions if p.is_correct)
        total = len(self._predictions)
        overall = _safe_div(correct, total)

        # By type
        type_buckets: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {"correct": 0, "total": 0}
        )
        for p in self._predictions:
            bucket = type_buckets[p.prediction_type]
            bucket["total"] += 1
            if p.is_correct:
                bucket["correct"] += 1

        accuracy_by_type = {
            t: round(_safe_div(b["correct"], b["total"]), 4)
            for t, b in type_buckets.items()
        }

        avg_conf = sum(p.confidence for p in self._predictions) / total
        # Calibration error: |avg_confidence - accuracy|
        cal_error = abs(avg_conf - overall)

        return {
            "overall_accuracy": round(overall, 4),
            "accuracy_by_type": accuracy_by_type,
            "total_predictions": total,
            "avg_confidence": round(avg_conf, 4),
            "calibration_error": round(cal_error, 4),
        }

    # ------------------------------------------------------------------ #
    #  4. Generate Evolution Signals                                      #
    # ------------------------------------------------------------------ #

    def generate_evolution_signals(
        self,
        game_detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Generate structured evolution signals for the training pipeline.

        Parameters
        ----------
        game_detail : dict
            Complete game outcome data.

        Returns
        -------
        dict with reward_signal, prediction_feedback, model_update_hints,
        evolution_priority.
        """
        outcome = self.analyze_game_outcome(game_detail)
        accuracy = self.compute_prediction_accuracy()

        # Reward signal: positive for good performance, negative for bad
        win_reward = 1.0 if game_detail.get("win") else -0.5
        perf_reward = (outcome["overall_score"] - 0.5) * 2  # [-1, 1]
        prediction_reward = (accuracy["overall_accuracy"] - 0.5) * 2 if accuracy["total_predictions"] > 0 else 0.0

        combined_reward = (
            win_reward * 0.4
            + perf_reward * 0.3
            + prediction_reward * 0.3
        )

        # Model update hints: which components need improvement
        hints: List[Dict[str, Any]] = []
        for suggestion in outcome.get("improvement_suggestions", []):
            hints.append({"area": suggestion, "priority": "high" if "deaths" in suggestion.lower() else "medium"})

        if accuracy.get("calibration_error", 0) > 0.2:
            hints.append({"area": "Confidence calibration needs improvement", "priority": "high"})

        # Evolution priority
        if combined_reward < -0.3:
            priority = "critical"
        elif combined_reward < 0.1:
            priority = "high"
        elif combined_reward < 0.5:
            priority = "medium"
        else:
            priority = "low"

        result = {
            "reward_signal": round(combined_reward, 4),
            "prediction_feedback": accuracy,
            "model_update_hints": hints,
            "evolution_priority": priority,
            "performance_outcome": outcome,
        }

        self._fire("evolution_signals", {
            "reward": combined_reward,
            "priority": priority,
            "prediction_accuracy": accuracy.get("overall_accuracy", 0),
        })
        return result

    # ------------------------------------------------------------------ #
    #  5. Identify Improvement Areas                                      #
    # ------------------------------------------------------------------ #

    def identify_improvement_areas(
        self,
        game_history: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Identify recurring weaknesses across multiple games.

        Parameters
        ----------
        game_history : list[dict]
            List of game outcome dicts.

        Returns
        -------
        dict with recurring_weaknesses, trend_direction, focus_areas,
        strengths.
        """
        if not game_history:
            return {
                "recurring_weaknesses": [],
                "trend_direction": "unknown",
                "focus_areas": [],
                "strengths": [],
            }

        # Collect all outcomes
        outcomes = [self.analyze_game_outcome(g) for g in game_history]

        # Aggregate breakdown scores
        score_sums: Dict[str, float] = defaultdict(float)
        for o in outcomes:
            for key, val in o.get("performance_breakdown", {}).items():
                score_sums[key] += val

        n = len(outcomes)
        score_avgs = {k: v / n for k, v in score_sums.items()}

        # Weaknesses: below 0.4 average
        weaknesses = [k for k, v in score_avgs.items() if v < 0.4]
        # Strengths: above 0.7
        strengths = [k for k, v in score_avgs.items() if v >= 0.7]

        # Trend: compare first half to second half
        if n >= 4:
            first_half = outcomes[:n // 2]
            second_half = outcomes[n // 2:]
            avg_first = sum(o["overall_score"] for o in first_half) / len(first_half)
            avg_second = sum(o["overall_score"] for o in second_half) / len(second_half)
            if avg_second > avg_first + 0.05:
                trend = "improving"
            elif avg_second < avg_first - 0.05:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        # Focus areas: top 3 weaknesses sorted by impact
        focus = sorted(weaknesses, key=lambda k: score_avgs.get(k, 0))[:3]

        return {
            "recurring_weaknesses": weaknesses,
            "trend_direction": trend,
            "focus_areas": focus,
            "strengths": strengths,
            "avg_scores": {k: round(v, 4) for k, v in score_avgs.items()},
        }

    # ------------------------------------------------------------------ #
    #  6. Compute Reward Signal                                           #
    # ------------------------------------------------------------------ #

    def compute_reward_signal(
        self,
        game_detail: Dict[str, Any],
        predictions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Compute a detailed reward signal for RL training.

        Parameters
        ----------
        game_detail : dict
            Game outcome.
        predictions : list[dict], optional
            List of prediction records for this game.

        Returns
        -------
        dict with reward, components, rl_compatible_reward.
        """
        outcome = self.analyze_game_outcome(game_detail)
        win = game_detail.get("win", False)

        # Reward components
        win_r = 1.0 if win else -1.0
        perf_r = outcome["overall_score"] * 2 - 1  # [-1, 1]
        kda_r = min(outcome["key_metrics"].get("kda", 0) / 5.0, 1.0)

        # Prediction component
        pred_r = 0.0
        if predictions:
            correct = sum(1 for p in predictions if p.get("is_correct"))
            pred_r = _safe_div(correct, len(predictions)) * 2 - 1

        combined = win_r * 0.35 + perf_r * 0.30 + kda_r * 0.20 + pred_r * 0.15

        return {
            "reward": round(combined, 4),
            "components": {
                "win_reward": round(win_r, 4),
                "performance_reward": round(perf_r, 4),
                "kda_reward": round(kda_r, 4),
                "prediction_reward": round(pred_r, 4),
            },
            "rl_compatible_reward": round(combined, 4),
        }

    # ------------------------------------------------------------------ #
    #  7. Full Postgame Analysis                                          #
    # ------------------------------------------------------------------ #

    def run_full_postgame_analysis(
        self,
        game_detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run the complete postgame analysis pipeline.

        Parameters
        ----------
        game_detail : dict
            Complete game detail.

        Returns
        -------
        dict with game_outcome, prediction_accuracy, evolution_signals,
        reward_signal, analysis_summary.
        """
        self._analysis_count += 1

        outcome = self.analyze_game_outcome(game_detail)
        accuracy = self.compute_prediction_accuracy()
        evolution = self.generate_evolution_signals(game_detail)
        reward = self.compute_reward_signal(game_detail)

        summary_parts: List[str] = []
        summary_parts.append(f"Game {'Won' if game_detail.get('win') else 'Lost'} — Grade: {outcome['performance_grade']}.")
        if accuracy["total_predictions"] > 0:
            summary_parts.append(f"Prediction accuracy: {accuracy['overall_accuracy']:.0%}.")
        summary_parts.append(f"Evolution priority: {evolution['evolution_priority']}.")

        result = {
            "game_outcome": outcome,
            "prediction_accuracy": accuracy,
            "evolution_signals": evolution,
            "reward_signal": reward,
            "analysis_summary": " ".join(summary_parts),
        }

        self._fire("full_postgame", {
            "grade": outcome["performance_grade"],
            "reward": reward["reward"],
            "priority": evolution["evolution_priority"],
        })
        return result

    # ------------------------------------------------------------------ #
    #  Internal                                                           #
    # ------------------------------------------------------------------ #

    def reset_predictions(self) -> None:
        """Clear all pending and recorded predictions."""
        self._predictions.clear()
        self._pending_predictions.clear()

    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback({
                "source": _EVOLUTION_KEY,
                "type": event_type,
                "timestamp": time.time(),
                "payload": payload,
            })

    def get_diagnostics(self) -> Dict[str, Any]:
        return {
            "analysis_count": self._analysis_count,
            "pending_predictions": len(self._pending_predictions),
            "recorded_predictions": len(self._predictions),
            "evolution_key": _EVOLUTION_KEY,
        }

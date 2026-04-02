"""
ABTestManager — A/B testing for evolution generations.
=======================================================

Manages controlled experiments between generation variants by
alternating which generation's parameters are active across games,
tracking per-generation fitness, and applying statistical tests
to determine if a new generation is significantly better.

Architecture position:
    modules/calibration/ab_test_manager.py   ← YOU ARE HERE
    ├─ Used by: evolution/generation_manager.py
    ├─ Uses: calibration/model_calibrator.py (for probability checks)
    └─ Writes: data/ab_tests/ (experiment results)

Apollo reference:
    modules/calibration/ — A/B calibration experiments
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MIN_GAMES_PER_ARM: int = 5
_DEFAULT_CONFIDENCE: float = 0.95  # 95% confidence


@dataclass
class ExperimentArm:
    """One arm of an A/B experiment."""
    arm_id: str
    generation_id: str
    games_played: int = 0
    total_fitness: float = 0.0
    fitness_values: List[float] = field(default_factory=list)
    wins: int = 0
    losses: int = 0

    @property
    def mean_fitness(self) -> float:
        if not self.fitness_values:
            return 0.0
        return sum(self.fitness_values) / len(self.fitness_values)

    @property
    def variance(self) -> float:
        if len(self.fitness_values) < 2:
            return 0.0
        mean = self.mean_fitness
        return sum((v - mean) ** 2 for v in self.fitness_values) / (
            len(self.fitness_values) - 1
        )

    @property
    def std_dev(self) -> float:
        return math.sqrt(self.variance)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "generation_id": self.generation_id,
            "games_played": self.games_played,
            "mean_fitness": round(self.mean_fitness, 4),
            "std_dev": round(self.std_dev, 4),
            "win_rate": round(self.win_rate, 3),
            "wins": self.wins,
            "losses": self.losses,
        }


@dataclass
class ExperimentResult:
    """Statistical result of an A/B experiment."""
    winner: Optional[str] = None  # arm_id of winner, None if inconclusive
    p_value: float = 1.0
    effect_size: float = 0.0
    confidence_met: bool = False
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "winner": self.winner,
            "p_value": round(self.p_value, 6),
            "effect_size": round(self.effect_size, 4),
            "confidence_met": self.confidence_met,
            "reason": self.reason,
        }


@dataclass
class Experiment:
    """Complete A/B experiment tracking."""
    experiment_id: str = ""
    created_at: float = 0.0
    control: ExperimentArm = field(default_factory=lambda: ExperimentArm("", ""))
    treatment: ExperimentArm = field(default_factory=lambda: ExperimentArm("", ""))
    min_games: int = _MIN_GAMES_PER_ARM
    confidence_level: float = _DEFAULT_CONFIDENCE
    result: Optional[ExperimentResult] = None
    is_active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "created_at": self.created_at,
            "is_active": self.is_active,
            "control": self.control.to_dict(),
            "treatment": self.treatment.to_dict(),
            "result": self.result.to_dict() if self.result else None,
        }


class ABTestManager:
    """Manages A/B testing between evolution generations.

    Usage::

        manager = ABTestManager()
        exp_id = manager.create_experiment(
            control_gen="gen_001",
            treatment_gen="gen_002",
        )
        # After each game:
        arm = manager.get_active_arm(exp_id)
        # ... play game with arm's generation params ...
        manager.record_game(exp_id, arm, fitness=0.72, won=True)
        # Check if we have a winner:
        result = manager.evaluate(exp_id)
    """

    def __init__(
        self,
        data_dir: str = "data/ab_tests",
        min_games: int = _MIN_GAMES_PER_ARM,
        confidence: float = _DEFAULT_CONFIDENCE,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._experiments: Dict[str, Experiment] = {}
        self._min_games = min_games
        self._confidence = confidence
        self._completed_count: int = 0

    def create_experiment(
        self,
        control_gen: str,
        treatment_gen: str,
    ) -> str:
        """Create a new A/B experiment between two generations."""
        exp_id = f"exp_{uuid.uuid4().hex[:8]}"

        exp = Experiment(
            experiment_id=exp_id,
            created_at=time.time(),
            control=ExperimentArm(
                arm_id="control", generation_id=control_gen
            ),
            treatment=ExperimentArm(
                arm_id="treatment", generation_id=treatment_gen
            ),
            min_games=self._min_games,
            confidence_level=self._confidence,
        )
        self._experiments[exp_id] = exp
        logger.info(
            "Created experiment %s: %s vs %s",
            exp_id, control_gen, treatment_gen,
        )
        return exp_id

    def get_active_arm(self, experiment_id: str) -> str:
        """Get which arm should be used for the next game.

        Uses simple alternation to ensure balanced assignment.
        """
        exp = self._experiments.get(experiment_id)
        if not exp or not exp.is_active:
            return "control"

        # Assign to the arm with fewer games
        if exp.control.games_played <= exp.treatment.games_played:
            return "control"
        return "treatment"

    def get_generation_for_arm(
        self, experiment_id: str, arm_id: str
    ) -> Optional[str]:
        exp = self._experiments.get(experiment_id)
        if not exp:
            return None
        if arm_id == "control":
            return exp.control.generation_id
        return exp.treatment.generation_id

    def record_game(
        self,
        experiment_id: str,
        arm_id: str,
        fitness: float,
        won: bool,
    ) -> None:
        """Record the result of a game for an experiment arm."""
        exp = self._experiments.get(experiment_id)
        if not exp or not exp.is_active:
            return

        arm = exp.control if arm_id == "control" else exp.treatment
        arm.games_played += 1
        arm.fitness_values.append(fitness)
        arm.total_fitness += fitness
        if won:
            arm.wins += 1
        else:
            arm.losses += 1

        logger.debug(
            "Experiment %s arm %s: game #%d, fitness=%.4f, won=%s",
            experiment_id, arm_id, arm.games_played, fitness, won,
        )

    def evaluate(self, experiment_id: str) -> ExperimentResult:
        """Evaluate the experiment using Welch's t-test."""
        exp = self._experiments.get(experiment_id)
        if not exp:
            return ExperimentResult(reason="experiment not found")

        c = exp.control
        t = exp.treatment

        if c.games_played < exp.min_games or t.games_played < exp.min_games:
            return ExperimentResult(
                reason=f"insufficient games: control={c.games_played}, "
                       f"treatment={t.games_played}, min={exp.min_games}"
            )

        # Welch's t-test
        mean_diff = t.mean_fitness - c.mean_fitness
        se = math.sqrt(
            (c.variance / c.games_played) + (t.variance / t.games_played)
        ) if (c.variance + t.variance) > 0 else 1e-10

        t_stat = mean_diff / se if se > 0 else 0.0

        # Approximate p-value using normal distribution (large sample approx)
        p_value = 2.0 * (1.0 - self._normal_cdf(abs(t_stat)))

        # Cohen's d effect size
        pooled_std = math.sqrt(
            (c.variance + t.variance) / 2
        ) if (c.variance + t.variance) > 0 else 1.0
        effect_size = mean_diff / pooled_std

        confidence_met = p_value < (1.0 - exp.confidence_level)

        winner = None
        if confidence_met:
            winner = "treatment" if mean_diff > 0 else "control"

        result = ExperimentResult(
            winner=winner,
            p_value=p_value,
            effect_size=effect_size,
            confidence_met=confidence_met,
            reason="significant" if confidence_met else "not significant",
        )

        exp.result = result
        if confidence_met:
            exp.is_active = False
            self._completed_count += 1
            self._save_experiment(exp)

        return result

    def _normal_cdf(self, x: float) -> float:
        """Approximate standard normal CDF via Abramowitz & Stegun."""
        sign = 1 if x >= 0 else -1
        x = abs(x)
        t = 1.0 / (1.0 + 0.2316419 * x)
        poly = t * (0.319381530 + t * (-0.356563782 + t * (
            1.781477937 + t * (-1.821255978 + t * 1.330274429)
        )))
        return 0.5 * (1.0 + sign * (
            1.0 - poly * math.exp(-0.5 * x * x)
        ))

    def _save_experiment(self, exp: Experiment) -> None:
        path = self._data_dir / f"{exp.experiment_id}.json"
        try:
            with open(path, "w") as f:
                json.dump(exp.to_dict(), f, indent=2)
        except OSError as exc:
            logger.error("Failed to save experiment: %s", exc)

    def list_experiments(self) -> List[Dict[str, Any]]:
        return [exp.to_dict() for exp in self._experiments.values()]

    def get_experiment(self, exp_id: str) -> Optional[Dict[str, Any]]:
        exp = self._experiments.get(exp_id)
        return exp.to_dict() if exp else None

    def stats(self) -> Dict[str, Any]:
        active = sum(1 for e in self._experiments.values() if e.is_active)
        return {
            "total_experiments": len(self._experiments),
            "active": active,
            "completed": self._completed_count,
        }

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


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: Multi-armed bandit auto-graduation + Thompson sampling
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 ABTestManager 的 Welch t-test 双臂实验 这个好例子开始。
#   然后，遵循该模式实现 MultiArmedBandit，让 evolution层 可以 同时评估多个变体，
#   并能 自动将流量分配给表现更好的臂。
#   接着 ThompsonSampler 引入 贝叶斯采样策略，使 系统 能够 在 exploitation 和
#   exploration 之间自动平衡，同时 AutoGraduator 优化 毕业判定逻辑。
#   最终 ABTestManagerV3 完善 multi-arm API，确保 向后兼容 双臂实验接口。


# ─── Thompson sampling for multi-armed bandit ───────────────────────────────

class ThompsonSampler:
    """Thompson sampling for Beta-Bernoulli bandit.

    Each arm maintains Beta(alpha, beta) parameters.
    Sampling from the posterior gives natural exploration/exploitation trade-off.

    This is used for binary outcomes (win/loss). For continuous fitness,
    we convert to win-rate via thresholding.

    Apollo parallel: calibration parameter selection under uncertainty.
    """

    def __init__(self, prior_alpha: float = 1.0, prior_beta: float = 1.0):
        self._prior_alpha = prior_alpha
        self._prior_beta = prior_beta
        self._arms: Dict[str, Tuple[float, float]] = {}  # arm_id → (α, β)
        self._rng = random.Random(int(time.time()))

    def add_arm(self, arm_id: str) -> None:
        """Add a new arm with the prior."""
        self._arms[arm_id] = (self._prior_alpha, self._prior_beta)

    def update(self, arm_id: str, reward: bool) -> None:
        """Update arm posterior based on binary reward."""
        if arm_id not in self._arms:
            self.add_arm(arm_id)
        alpha, beta = self._arms[arm_id]
        if reward:
            self._arms[arm_id] = (alpha + 1, beta)
        else:
            self._arms[arm_id] = (alpha, beta + 1)

    def sample(self) -> str:
        """Sample from each arm's posterior, return arm with highest sample.

        This naturally balances exploration (uncertain arms get wide samples)
        and exploitation (good arms get high samples).
        """
        if not self._arms:
            return ""
        best_arm = ""
        best_sample = -1.0
        for arm_id, (alpha, beta) in self._arms.items():
            sample = self._rng.betavariate(alpha, beta)
            if sample > best_sample:
                best_sample = sample
                best_arm = arm_id
        return best_arm

    def expected_rewards(self) -> Dict[str, float]:
        """Get expected reward (posterior mean) for each arm."""
        result = {}
        for arm_id, (alpha, beta) in self._arms.items():
            result[arm_id] = alpha / (alpha + beta)
        return result

    def arm_stats(self) -> Dict[str, Dict[str, Any]]:
        result = {}
        for arm_id, (alpha, beta) in self._arms.items():
            result[arm_id] = {
                "alpha": alpha,
                "beta": beta,
                "expected_reward": round(alpha / (alpha + beta), 4),
                "total_trials": int(alpha + beta - 2 * self._prior_alpha),
            }
        return result

    def remove_arm(self, arm_id: str) -> None:
        self._arms.pop(arm_id, None)

    def reset(self) -> None:
        self._arms.clear()


# ─── Multi-armed bandit experiment ───────────────────────────────────────────

@dataclass
class BanditArm:
    """Extended arm for multi-armed bandit experiments."""
    arm_id: str = ""
    generation_id: str = ""
    games_played: int = 0
    fitness_values: List[float] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    allocation_count: int = 0  # times this arm was selected by bandit

    @property
    def mean_fitness(self) -> float:
        return sum(self.fitness_values) / max(1, len(self.fitness_values))

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
            "win_rate": round(self.win_rate, 3),
            "allocation_count": self.allocation_count,
        }


@dataclass
class GraduationResult:
    """Result of auto-graduation check."""
    should_graduate: bool = False
    winner_arm_id: str = ""
    winner_generation_id: str = ""
    confidence: float = 0.0
    reason: str = ""
    arms_evaluated: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "should_graduate": self.should_graduate,
            "winner_arm_id": self.winner_arm_id,
            "winner_generation_id": self.winner_generation_id,
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
        }


class AutoGraduator:
    """Auto-graduation logic for multi-armed bandit experiments.

    Decides when an experiment has enough data to declare a winner
    and "graduate" the best arm to production.

    Graduation criteria (all must be met):
    1. Minimum games per arm
    2. Statistical significance (p < threshold)
    3. Practical significance (effect size > threshold)
    4. Stability (no arm trending downward in recent games)
    """

    def __init__(
        self,
        min_games_per_arm: int = 10,
        p_threshold: float = 0.05,
        effect_size_threshold: float = 0.1,
        stability_window: int = 5,
    ) -> None:
        self._min_games = min_games_per_arm
        self._p_threshold = p_threshold
        self._effect_threshold = effect_size_threshold
        self._stability_window = stability_window

    def check(self, arms: List[BanditArm]) -> GraduationResult:
        """Check if the experiment should graduate."""
        if len(arms) < 2:
            return GraduationResult(reason="need at least 2 arms")

        # Check minimum games
        for arm in arms:
            if arm.games_played < self._min_games:
                return GraduationResult(
                    reason=f"arm '{arm.arm_id}' has only "
                           f"{arm.games_played}/{self._min_games} games"
                )

        # Find best arm by mean fitness
        sorted_arms = sorted(arms, key=lambda a: a.mean_fitness, reverse=True)
        best = sorted_arms[0]
        runner_up = sorted_arms[1]

        # Effect size
        pooled_var = self._pooled_variance(best, runner_up)
        pooled_std = math.sqrt(pooled_var) if pooled_var > 0 else 1e-10
        effect_size = (best.mean_fitness - runner_up.mean_fitness) / pooled_std

        if abs(effect_size) < self._effect_threshold:
            return GraduationResult(
                reason=f"effect size {effect_size:.3f} below "
                       f"threshold {self._effect_threshold}",
                arms_evaluated=len(arms),
            )

        # Statistical significance (Welch's t-test)
        p_value = self._welch_p_value(best, runner_up)
        if p_value >= self._p_threshold:
            return GraduationResult(
                reason=f"p-value {p_value:.4f} above threshold "
                       f"{self._p_threshold}",
                arms_evaluated=len(arms),
            )

        # Stability check: best arm should not be declining
        if not self._is_stable(best):
            return GraduationResult(
                reason=f"best arm '{best.arm_id}' fitness declining",
                arms_evaluated=len(arms),
            )

        return GraduationResult(
            should_graduate=True,
            winner_arm_id=best.arm_id,
            winner_generation_id=best.generation_id,
            confidence=1.0 - p_value,
            reason="all graduation criteria met",
            arms_evaluated=len(arms),
        )

    def _pooled_variance(self, a: BanditArm, b: BanditArm) -> float:
        if len(a.fitness_values) < 2 or len(b.fitness_values) < 2:
            return 0.0
        va = sum((x - a.mean_fitness) ** 2 for x in a.fitness_values) / (
            len(a.fitness_values) - 1)
        vb = sum((x - b.mean_fitness) ** 2 for x in b.fitness_values) / (
            len(b.fitness_values) - 1)
        return (va + vb) / 2

    def _welch_p_value(self, a: BanditArm, b: BanditArm) -> float:
        na, nb = len(a.fitness_values), len(b.fitness_values)
        if na < 2 or nb < 2:
            return 1.0
        va = sum((x - a.mean_fitness) ** 2 for x in a.fitness_values) / (na - 1)
        vb = sum((x - b.mean_fitness) ** 2 for x in b.fitness_values) / (nb - 1)
        se = math.sqrt(va / na + vb / nb) if (va + vb) > 0 else 1e-10
        t_stat = (a.mean_fitness - b.mean_fitness) / se
        # Normal approximation for p-value
        return 2.0 * (1.0 - ABTestManager._normal_cdf(
            ABTestManager(data_dir="/tmp"), abs(t_stat)))

    def _is_stable(self, arm: BanditArm) -> bool:
        if len(arm.fitness_values) < self._stability_window:
            return True
        recent = arm.fitness_values[-self._stability_window:]
        # Check if recent trend is non-declining
        if len(recent) < 2:
            return True
        slope = (recent[-1] - recent[0]) / len(recent)
        return slope >= -0.05  # allow small noise


# ─── Multi-armed bandit manager ─────────────────────────────────────────────

@dataclass
class MultiArmExperiment:
    """Multi-arm experiment with Thompson sampling and auto-graduation."""
    experiment_id: str = ""
    created_at: float = 0.0
    arms: Dict[str, BanditArm] = field(default_factory=dict)
    is_active: bool = True
    graduation_result: Optional[GraduationResult] = None
    fitness_threshold: float = 0.5  # fitness > threshold counts as "win"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "is_active": self.is_active,
            "arm_count": len(self.arms),
            "arms": {k: v.to_dict() for k, v in self.arms.items()},
            "graduation": (self.graduation_result.to_dict()
                           if self.graduation_result else None),
        }


class ABTestManagerV3(ABTestManager):
    """V3 A/B test manager with multi-armed bandit and auto-graduation.

    Fully backward-compatible with V1 ABTestManager (2-arm experiments).
    Adds multi-arm experiments with Thompson sampling and auto-graduation.

    Usage::
        manager = ABTestManagerV3()

        # Create multi-arm experiment
        exp_id = manager.create_multi_arm(
            generation_ids=["gen_001", "gen_002", "gen_003"],
        )

        # Before each game: get the arm to use
        arm_id = manager.select_arm(exp_id)
        gen_id = manager.get_generation_for_multi_arm(exp_id, arm_id)

        # After game: record result
        manager.record_multi_arm(exp_id, arm_id, fitness=0.72, won=True)

        # Check graduation
        grad = manager.check_graduation(exp_id)
        if grad.should_graduate:
            print(f"Winner: {grad.winner_generation_id}")
    """

    def __init__(
        self,
        data_dir: str = "data/ab_tests",
        min_games: int = _MIN_GAMES_PER_ARM,
        confidence: float = _DEFAULT_CONFIDENCE,
    ) -> None:
        super().__init__(data_dir=data_dir, min_games=min_games,
                         confidence=confidence)
        self._multi_experiments: Dict[str, MultiArmExperiment] = {}
        self._samplers: Dict[str, ThompsonSampler] = {}
        self._graduator = AutoGraduator(min_games_per_arm=min_games)

    def create_multi_arm(
        self,
        generation_ids: List[str],
        fitness_threshold: float = 0.5,
    ) -> str:
        """Create a multi-armed bandit experiment."""
        exp_id = f"mab_{uuid.uuid4().hex[:8]}"

        arms = {}
        sampler = ThompsonSampler()
        for i, gen_id in enumerate(generation_ids):
            arm_id = f"arm_{i}"
            arms[arm_id] = BanditArm(arm_id=arm_id, generation_id=gen_id)
            sampler.add_arm(arm_id)

        exp = MultiArmExperiment(
            experiment_id=exp_id,
            created_at=time.time(),
            arms=arms,
            fitness_threshold=fitness_threshold,
        )
        self._multi_experiments[exp_id] = exp
        self._samplers[exp_id] = sampler

        logger.info(
            "Created multi-arm experiment %s with %d arms: %s",
            exp_id, len(generation_ids), generation_ids,
        )
        return exp_id

    def select_arm(self, experiment_id: str) -> str:
        """Select an arm using Thompson sampling."""
        sampler = self._samplers.get(experiment_id)
        exp = self._multi_experiments.get(experiment_id)
        if not sampler or not exp or not exp.is_active:
            return ""

        arm_id = sampler.sample()
        if arm_id in exp.arms:
            exp.arms[arm_id].allocation_count += 1
        return arm_id

    def get_generation_for_multi_arm(
        self, experiment_id: str, arm_id: str
    ) -> Optional[str]:
        exp = self._multi_experiments.get(experiment_id)
        if not exp:
            return None
        arm = exp.arms.get(arm_id)
        return arm.generation_id if arm else None

    def record_multi_arm(
        self,
        experiment_id: str,
        arm_id: str,
        fitness: float,
        won: bool,
    ) -> None:
        """Record a game result for a multi-arm experiment."""
        exp = self._multi_experiments.get(experiment_id)
        sampler = self._samplers.get(experiment_id)
        if not exp or not exp.is_active:
            return

        arm = exp.arms.get(arm_id)
        if not arm:
            return

        arm.games_played += 1
        arm.fitness_values.append(fitness)
        if won:
            arm.wins += 1
        else:
            arm.losses += 1

        # Update Thompson sampler with binary reward
        reward = fitness >= exp.fitness_threshold
        if sampler:
            sampler.update(arm_id, reward)

    def check_graduation(self, experiment_id: str) -> GraduationResult:
        """Check if a multi-arm experiment should graduate."""
        exp = self._multi_experiments.get(experiment_id)
        if not exp or not exp.is_active:
            return GraduationResult(reason="experiment not active")

        result = self._graduator.check(list(exp.arms.values()))
        if result.should_graduate:
            exp.is_active = False
            exp.graduation_result = result
            self._save_multi_experiment(exp)
            logger.info(
                "Experiment %s graduated: winner=%s (gen=%s, conf=%.3f)",
                experiment_id, result.winner_arm_id,
                result.winner_generation_id, result.confidence,
            )

        return result

    def _save_multi_experiment(self, exp: MultiArmExperiment) -> None:
        path = self._data_dir / f"{exp.experiment_id}.json"
        try:
            with open(path, "w") as f:
                json.dump(exp.to_dict(), f, indent=2)
        except OSError as exc:
            logger.error("Failed to save multi-arm experiment: %s", exc)

    def list_multi_experiments(self) -> List[Dict[str, Any]]:
        return [exp.to_dict() for exp in self._multi_experiments.values()]

    def multi_arm_stats(self) -> Dict[str, Any]:
        active = sum(1 for e in self._multi_experiments.values() if e.is_active)
        graduated = sum(1 for e in self._multi_experiments.values()
                        if e.graduation_result and e.graduation_result.should_graduate)
        return {
            "total_multi_arm": len(self._multi_experiments),
            "active": active,
            "graduated": graduated,
        }

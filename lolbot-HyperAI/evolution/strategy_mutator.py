#!/usr/bin/env python3
"""
evolution/strategy_mutator.py — Mutation Proposal Generator (LLM修复酶)
=========================================================================
lolbot-HyperAI · Evolution Layer

From plan.md §二:
    LLM（修复酶）→ 看日志，建议修改

The Strategy Mutator is the "repair enzyme" — it looks at fitness
evaluation results and generates concrete mutation proposals to
improve the system. In the biological analogy:
    - DNA = GenerationSnapshot (all tunable parameters)
    - Repair enzyme = StrategyMutator (proposes targeted changes)
    - Natural selection = FitnessEvaluator (decides if changes help)

Mutation strategies:
    1. GRADIENT: Nudge weights in the direction that improves fitness
       (like SGD but for system config, not model weights)
    2. CALIBRATION: Fix prediction calibration by adjusting weights
       that contributed to miscalibrated buckets
    3. COOLDOWN_TUNE: Adjust recommendation timing based on engagement
    4. EXPLORATION: Random perturbation for escaping local optima
    5. PATTERN: Apply known-good patterns from fitness analysis

In production, this module could delegate to an actual LLM for
sophisticated reasoning about what to change. The current implementation
uses rule-based heuristics as a bootstrap — good enough for the first
few generations until we have enough data for LLM-guided evolution.

The mutator never applies changes directly. It produces MutationProposals
that the GenerationManager applies, evaluates, and commits/rollbacks.
"""

from __future__ import annotations

import hashlib
import math
import random
import sys
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from evolution.fitness_evaluator import FitnessScore
from evolution.generation_manager import GenerationSnapshot, MutationProposal


# ---------------------------------------------------------------------------
# Mutation strategy types
# ---------------------------------------------------------------------------
class MutationStrategy(Enum):
    GRADIENT = "gradient"           # Nudge weights toward better fitness
    CALIBRATION = "calibration"     # Fix prediction calibration
    COOLDOWN_TUNE = "cooldown_tune" # Adjust recommendation timing
    EXPLORATION = "exploration"     # Random perturbation
    PATTERN = "pattern"             # Known-good pattern application


# ---------------------------------------------------------------------------
# Mutation analysis helpers
# ---------------------------------------------------------------------------
@dataclass
class FitnessDiagnosis:
    """Analysis of what's wrong with current fitness."""
    weakest_dimension: str = ""
    weakest_score: float = 0.0
    prediction_issues: List[str] = field(default_factory=list)
    recommendation_issues: List[str] = field(default_factory=list)
    engagement_issues: List[str] = field(default_factory=list)
    suggested_strategies: List[MutationStrategy] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "weakest_dimension": self.weakest_dimension,
            "weakest_score": round(self.weakest_score, 4),
            "prediction_issues": self.prediction_issues,
            "recommendation_issues": self.recommendation_issues,
            "engagement_issues": self.engagement_issues,
            "suggested_strategies": [s.value for s in self.suggested_strategies],
        }


# ---------------------------------------------------------------------------
# Strategy Mutator
# ---------------------------------------------------------------------------
class StrategyMutator:
    """
    Generates mutation proposals from fitness analysis.

    Usage:
        mutator = StrategyMutator(seed=42)
        diagnosis = mutator.diagnose(fitness_score)
        proposals = mutator.propose(current_snapshot, fitness_score)
        # Pass proposals to GenerationManager.apply_mutations()
    """

    # Mutation magnitude limits
    MAX_WEIGHT_DELTA = 0.3       # Max change to a single weight per generation
    MAX_COOLDOWN_DELTA = 10.0    # Max seconds change to a cooldown
    MAX_THRESHOLD_DELTA = 0.1    # Max change to a threshold
    MAX_PROPOSALS_PER_GEN = 5    # Don't change too many things at once

    # Exploration probability (random mutation even if things look ok)
    EXPLORATION_PROB = 0.15

    def __init__(self, seed: Optional[int] = None) -> None:
        self._rng = random.Random(seed)
        self._mutation_count = 0

    def diagnose(self, fitness: FitnessScore) -> FitnessDiagnosis:
        """
        Analyze a fitness score to identify areas for improvement.

        Returns a diagnosis with specific issues and suggested strategies.
        """
        diag = FitnessDiagnosis()

        # Find weakest dimension
        dimensions = {
            "prediction": fitness.prediction_score,
            "recommendation": fitness.recommendation_score,
            "health": fitness.health_score,
            "coverage": fitness.coverage_score,
            "engagement": fitness.engagement_score,
        }
        weakest = min(dimensions.items(), key=lambda x: x[1])
        diag.weakest_dimension = weakest[0]
        diag.weakest_score = weakest[1]

        # Prediction issues
        if fitness.prediction_score < 0.6:
            pred_metrics = fitness.prediction_metrics or {}
            brier = pred_metrics.get("brier_score", 0.25)
            ece = pred_metrics.get("calibration_error", 0.5)
            if brier > 0.15:
                diag.prediction_issues.append(
                    f"High Brier score ({brier:.3f}) — predictions are inaccurate"
                )
                diag.suggested_strategies.append(MutationStrategy.GRADIENT)
            if ece > 0.1:
                diag.prediction_issues.append(
                    f"Poor calibration (ECE={ece:.3f}) — probabilities don't match reality"
                )
                diag.suggested_strategies.append(MutationStrategy.CALIBRATION)

        # Recommendation issues
        if fitness.recommendation_score < 0.5:
            rec_metrics = fitness.recommendation_metrics or {}
            type_cov = rec_metrics.get("type_coverage", 0)
            phase_cov = rec_metrics.get("phase_coverage", 0)
            if type_cov < 0.3:
                diag.recommendation_issues.append(
                    "Low recommendation type coverage — using too few types"
                )
                diag.suggested_strategies.append(MutationStrategy.COOLDOWN_TUNE)
            if phase_cov < 0.4:
                diag.recommendation_issues.append(
                    "Low phase coverage — missing recommendations in some phases"
                )
                diag.suggested_strategies.append(MutationStrategy.COOLDOWN_TUNE)

        # Engagement issues
        if fitness.engagement_score < 0.5:
            ann_metrics = fitness.announcement_metrics or {}
            apm = ann_metrics.get("announcements_per_minute", 0)
            if apm < 0.3:
                diag.engagement_issues.append(
                    "Too few announcements — player may not be getting enough info"
                )
            elif apm > 3.0:
                diag.engagement_issues.append(
                    "Too many announcements — player may feel spammed"
                )
            drop_rate = ann_metrics.get("drop_rate", 0)
            if drop_rate > 0.3:
                diag.engagement_issues.append(
                    f"High drop rate ({drop_rate:.1%}) — announcements expiring before delivery"
                )

        # Always consider some exploration
        if self._rng.random() < self.EXPLORATION_PROB:
            diag.suggested_strategies.append(MutationStrategy.EXPLORATION)

        # Deduplicate strategies
        diag.suggested_strategies = list(set(diag.suggested_strategies))
        return diag

    def propose(
        self,
        snapshot: GenerationSnapshot,
        fitness: FitnessScore,
        *,
        max_proposals: Optional[int] = None,
    ) -> List[MutationProposal]:
        """
        Generate mutation proposals based on fitness analysis.

        Args:
            snapshot: Current generation parameters.
            fitness: Fitness evaluation results.
            max_proposals: Override max proposals per generation.

        Returns:
            List of MutationProposals (up to MAX_PROPOSALS_PER_GEN).
        """
        limit = max_proposals or self.MAX_PROPOSALS_PER_GEN
        diagnosis = self.diagnose(fitness)
        proposals: List[MutationProposal] = []

        for strategy in diagnosis.suggested_strategies:
            if len(proposals) >= limit:
                break

            if strategy == MutationStrategy.GRADIENT:
                proposals.extend(
                    self._gradient_proposals(snapshot, fitness, diagnosis)
                )
            elif strategy == MutationStrategy.CALIBRATION:
                proposals.extend(
                    self._calibration_proposals(snapshot, fitness)
                )
            elif strategy == MutationStrategy.COOLDOWN_TUNE:
                proposals.extend(
                    self._cooldown_proposals(snapshot, fitness, diagnosis)
                )
            elif strategy == MutationStrategy.EXPLORATION:
                proposals.extend(
                    self._exploration_proposals(snapshot)
                )

        # Trim to limit
        proposals = proposals[:limit]

        # Assign IDs
        for p in proposals:
            if not p.proposal_id:
                p.proposal_id = self._make_id()

        return proposals

    # -- Gradient-based mutations ---------------------------------------

    def _gradient_proposals(
        self,
        snapshot: GenerationSnapshot,
        fitness: FitnessScore,
        diagnosis: FitnessDiagnosis,
    ) -> List[MutationProposal]:
        """
        Propose weight adjustments based on prediction performance.

        Strategy: If predictions are too high (overconfident), reduce
        weights of features that were contributing most. If too low,
        increase them.
        """
        proposals = []
        pred_metrics = fitness.prediction_metrics or {}
        buckets = pred_metrics.get("bucket_sizes", {})

        # Identify direction of miscalibration
        # If we don't have detailed bucket data, make small adjustments
        # to the most impactful weights
        weights = snapshot.prediction_weights
        if not weights:
            return proposals

        # Pick top 2 weights by magnitude to adjust
        sorted_weights = sorted(
            weights.items(), key=lambda x: abs(x[1]), reverse=True,
        )

        for feature, current_weight in sorted_weights[:2]:
            # Slight reduction of extreme weights (regularization)
            if abs(current_weight) > 1.5:
                delta = -0.1 * (1 if current_weight > 0 else -1)
                new_weight = current_weight + delta
                proposals.append(MutationProposal(
                    proposal_id="",
                    category="weight_adjustment",
                    target_param=f"prediction_weights.{feature}",
                    old_value=round(current_weight, 4),
                    new_value=round(new_weight, 4),
                    rationale=(
                        f"Regularize extreme weight on '{feature}' "
                        f"({current_weight:.3f} → {new_weight:.3f}) "
                        f"to improve prediction accuracy"
                    ),
                    confidence=0.6,
                    estimated_impact="medium",
                ))

        return proposals

    # -- Calibration mutations ------------------------------------------

    def _calibration_proposals(
        self,
        snapshot: GenerationSnapshot,
        fitness: FitnessScore,
    ) -> List[MutationProposal]:
        """
        Fix calibration by adjusting the prediction bias.

        If we're systematically overconfident (predicting 60% but
        winning only 45%), reduce bias. Vice versa for underconfident.
        """
        proposals = []
        pred_metrics = fitness.prediction_metrics or {}
        ece = pred_metrics.get("calibration_error", 0)

        if ece > 0.1:
            # Adjust bias (simple approach)
            current_bias = snapshot.prediction_bias
            # Heuristic: move bias toward 0 if miscalibrated
            delta = -0.05 * (1 if current_bias > 0 else -1)
            if abs(current_bias) < 0.05:
                # Bias is already near zero — problem is in weights
                delta = 0.0

            if delta != 0:
                new_bias = current_bias + delta
                proposals.append(MutationProposal(
                    proposal_id="",
                    category="calibration",
                    target_param="prediction_bias",
                    old_value=round(current_bias, 4),
                    new_value=round(new_bias, 4),
                    rationale=(
                        f"Adjust prediction bias to improve calibration "
                        f"(ECE={ece:.3f})"
                    ),
                    confidence=0.5,
                    estimated_impact="medium",
                ))

        # Also adjust confidence threshold if we're publishing
        # too many low-confidence recommendations
        current_conf = snapshot.min_recommendation_confidence
        rec_metrics = fitness.recommendation_metrics or {}
        total_pub = rec_metrics.get("total_published", 0)
        total_gen = rec_metrics.get("total_generated", 0)

        if total_gen > 0 and total_pub / total_gen > 0.8:
            # Publishing almost everything — raise threshold
            new_conf = min(current_conf + 0.05, 0.8)
            if new_conf != current_conf:
                proposals.append(MutationProposal(
                    proposal_id="",
                    category="threshold_change",
                    target_param="min_recommendation_confidence",
                    old_value=current_conf,
                    new_value=new_conf,
                    rationale=(
                        f"Raise confidence threshold from {current_conf} "
                        f"to {new_conf} — publishing {total_pub}/{total_gen} "
                        f"({total_pub/total_gen:.0%}) of generated recs"
                    ),
                    confidence=0.55,
                    estimated_impact="low",
                ))

        return proposals

    # -- Cooldown tuning mutations --------------------------------------

    def _cooldown_proposals(
        self,
        snapshot: GenerationSnapshot,
        fitness: FitnessScore,
        diagnosis: FitnessDiagnosis,
    ) -> List[MutationProposal]:
        """Adjust recommendation cooldowns based on engagement."""
        proposals = []
        cooldowns = snapshot.recommendation_cooldowns
        if not cooldowns:
            return proposals

        ann_metrics = fitness.announcement_metrics or {}
        apm = ann_metrics.get("announcements_per_minute", 1.0)

        if apm < 0.5:
            # Too few announcements — reduce cooldowns
            for rec_type, current_cd in list(cooldowns.items())[:2]:
                new_cd = max(5.0, current_cd - 5.0)
                if new_cd != current_cd:
                    proposals.append(MutationProposal(
                        proposal_id="",
                        category="cooldown_tuning",
                        target_param=f"recommendation_cooldowns.{rec_type}",
                        old_value=current_cd,
                        new_value=new_cd,
                        rationale=(
                            f"Reduce cooldown for '{rec_type}' "
                            f"({current_cd}s → {new_cd}s) — "
                            f"only {apm:.1f} announcements/min"
                        ),
                        confidence=0.5,
                        estimated_impact="low",
                    ))
        elif apm > 3.0:
            # Too many — increase cooldowns
            for rec_type, current_cd in list(cooldowns.items())[:2]:
                new_cd = min(120.0, current_cd + 10.0)
                if new_cd != current_cd:
                    proposals.append(MutationProposal(
                        proposal_id="",
                        category="cooldown_tuning",
                        target_param=f"recommendation_cooldowns.{rec_type}",
                        old_value=current_cd,
                        new_value=new_cd,
                        rationale=(
                            f"Increase cooldown for '{rec_type}' "
                            f"({current_cd}s → {new_cd}s) — "
                            f"{apm:.1f} announcements/min is too high"
                        ),
                        confidence=0.5,
                        estimated_impact="low",
                    ))

        # Also adjust announcement interval
        current_interval = snapshot.min_announce_interval_sec
        if apm > 3.0 and current_interval < 8.0:
            new_interval = current_interval + 2.0
            proposals.append(MutationProposal(
                proposal_id="",
                category="interval_tuning",
                target_param="min_announce_interval_sec",
                old_value=current_interval,
                new_value=new_interval,
                rationale=(
                    f"Increase min announcement interval "
                    f"({current_interval}s → {new_interval}s)"
                ),
                confidence=0.6,
                estimated_impact="medium",
            ))

        return proposals

    # -- Exploration mutations ------------------------------------------

    def _exploration_proposals(
        self,
        snapshot: GenerationSnapshot,
    ) -> List[MutationProposal]:
        """
        Random perturbation for escaping local optima.

        Pick a random weight and nudge it by a small random amount.
        """
        proposals = []
        weights = snapshot.prediction_weights
        if not weights:
            return proposals

        # Pick 1-2 random weights to perturb
        weight_names = list(weights.keys())
        num_to_perturb = self._rng.randint(1, min(2, len(weight_names)))
        targets = self._rng.sample(weight_names, num_to_perturb)

        for feature in targets:
            current = weights[feature]
            delta = self._rng.gauss(0, 0.1)
            delta = max(-self.MAX_WEIGHT_DELTA,
                       min(self.MAX_WEIGHT_DELTA, delta))
            new_val = current + delta

            proposals.append(MutationProposal(
                proposal_id="",
                category="exploration",
                target_param=f"prediction_weights.{feature}",
                old_value=round(current, 4),
                new_value=round(new_val, 4),
                rationale=(
                    f"Exploration: perturb '{feature}' by {delta:+.4f} "
                    f"to discover potentially better configurations"
                ),
                confidence=0.3,
                estimated_impact="low",
            ))

        return proposals

    # -- Helpers --------------------------------------------------------

    def _make_id(self) -> str:
        self._mutation_count += 1
        raw = f"mut_{self._mutation_count}_{int(time.time())}"
        return hashlib.md5(raw.encode()).hexdigest()[:10]

    def stats(self) -> Dict[str, Any]:
        return {
            "mutations_generated": self._mutation_count,
            "exploration_prob": self.EXPLORATION_PROB,
            "max_weight_delta": self.MAX_WEIGHT_DELTA,
            "max_proposals_per_gen": self.MAX_PROPOSALS_PER_GEN,
        }

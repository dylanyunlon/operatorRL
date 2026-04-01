#!/usr/bin/env python3
"""
M1056: Evolution Controller — Self-Evolution Loop Driver
=========================================================
OperatorRL M1046-M1065 · 自部署 自环境反馈 自演化

Implements the core self-evolution loop from plan.md §二:
    程序A（Agent）→ 运行，撞墙，记录日志
    LLM（修复酶）→ 看日志，建议修改
    程序A'（新一代）→ 替换 A

This controller orchestrates the cycle:
1. Run the game assistant system (程序A)
2. Collect structured logs from all modules
3. Analyze logs for patterns, errors, and performance metrics
4. Generate evolution proposals (what to improve)
5. Apply proposals to produce next generation (程序A')
6. Evaluate if A' outperforms A
7. If yes: commit. If no: rollback.

Maps to OperatorRL architecture:
    GovernedRunner.step() → 程序A运行 + 日志收集
    PolicyReward.__call__() → success/error → 奖励信号
    AgentLightningTrainer.fit() → LLM修复酶（更新权重/代码）
    verl/daemon.py 热替换 → A → A' 自演化
"""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from evo_logging.evolution_logger import (
        get_logger, get_analyzer, LogCategory, LogAnalyzer)
except ImportError:
    pass


@dataclass
class EvolutionProposal:
    """A proposed modification to the system."""
    proposal_id: str
    category: str           # "strategy_tuning", "threshold_adjustment",
                            # "new_pattern", "bug_fix", "performance"
    target_module: str      # Which module to modify
    description: str
    rationale: str          # Why this change is needed (from log analysis)
    confidence: float       # 0-1 how confident we are this helps
    estimated_impact: str   # "low", "medium", "high"
    parameters: Dict[str, Any] = field(default_factory=dict)
    applied: bool = False
    rollback_data: Optional[Dict] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class EvolutionGeneration:
    """One generation in the evolution history."""
    generation_id: int
    created_at: str
    proposals_applied: List[str]
    metrics_before: Dict[str, float]
    metrics_after: Optional[Dict[str, float]] = None
    improvement: Optional[float] = None
    committed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


class EvolutionController:
    """
    Orchestrates the self-evolution cycle.

    Each cycle:
        analyze_logs() → generate_proposals() → apply_proposals()
        → evaluate() → commit_or_rollback()

    The controller maintains a generation counter and history.
    Each generation is a snapshot of the system's configuration
    parameters that can be tuned without code changes:
        - Strategy engine thresholds
        - Threat score weights
        - Recommendation timing intervals
        - Voice output priorities
        - Cache TTLs
        - Rate limit values
    """
    def __init__(self, log_dir: str = "logs/m1046_m1065"):
        self._logger = get_logger()
        self._analyzer = get_analyzer(log_dir)
        self._generation = 0
        self._history: List[EvolutionGeneration] = []
        self._current_config: Dict[str, Any] = self._default_config()
        self._proposals: List[EvolutionProposal] = []
        self._proposal_counter = 0

    def _default_config(self) -> Dict[str, Any]:
        """Default tunable parameters for the system."""
        return {
            'strategy.min_rec_interval_sec': 10.0,
            'strategy.max_active_recs': 5,
            'strategy.ban_threshold': 3.0,
            'strategy.threat_critical_score': 75.0,
            'strategy.threat_high_score': 55.0,
            'history.cache_ttl_sec': 300,
            'history.max_games_fetch': 20,
            'history.fetch_timeout_sec': 3.0,
            'capture.poll_interval_sec': 1.0,
            'capture.lcu_rate_limit_rps': 18,
            'voice.cooldown_sec': 2.0,
            'voice.max_queue_size': 20,
            'voice.speech_rate': 1.2,
            'analyzer.threat_mmr_weight': 0.4,
            'analyzer.threat_form_weight': 0.2,
            'analyzer.threat_mastery_weight': 0.2,
            'analyzer.threat_consistency_weight': 0.1,
            'analyzer.threat_special_weight': 0.1,
            'monitor.tick_interval_sec': 30.0,
        }

    def analyze_and_propose(self) -> List[EvolutionProposal]:
        """
        Analyze logs and generate evolution proposals.

        This is the "LLM repair enzyme" step. Reads logs, identifies
        patterns, and proposes parameter adjustments.
        """
        report = self._analyzer.generate_evolution_report()
        proposals = []

        # Proposal 1: Adjust recommendation interval based on acceptance rate
        strategy_logs = [
            e for e in report.get('level_distribution', {}).items()
        ]
        reward_count = report.get('level_distribution', {}).get('REWARD', 0)
        total_strategy = report.get('category_distribution', {}).get('strategy_engine', 0)
        if total_strategy > 10:
            acceptance_rate = reward_count / total_strategy
            current_interval = self._current_config['strategy.min_rec_interval_sec']
            if acceptance_rate < 0.3:
                # Low acceptance → recommend less frequently
                new_interval = min(current_interval * 1.5, 30.0)
                proposals.append(self._make_proposal(
                    "strategy_tuning", "strategy_engine",
                    f"Increase recommendation interval from {current_interval}s to {new_interval}s",
                    f"Acceptance rate is {acceptance_rate:.1%}, below 30% threshold. "
                    f"Recommending less often may reduce noise.",
                    confidence=0.7,
                    parameters={'strategy.min_rec_interval_sec': new_interval}))
            elif acceptance_rate > 0.7:
                # High acceptance → can recommend more frequently
                new_interval = max(current_interval * 0.75, 5.0)
                proposals.append(self._make_proposal(
                    "strategy_tuning", "strategy_engine",
                    f"Decrease recommendation interval from {current_interval}s to {new_interval}s",
                    f"Acceptance rate is {acceptance_rate:.1%}, above 70%. "
                    f"User values recommendations — provide more.",
                    confidence=0.6,
                    parameters={'strategy.min_rec_interval_sec': new_interval}))

        # Proposal 2: Adjust based on error patterns
        error_clusters = report.get('error_clusters', {})
        for cluster_key, errors in error_clusters.items():
            if len(errors) > 5:
                if 'riot_api' in cluster_key:
                    proposals.append(self._make_proposal(
                        "threshold_adjustment", "capture",
                        f"Reduce LCU polling rate due to {len(errors)} API errors",
                        f"Cluster '{cluster_key}' has {len(errors)} errors. "
                        f"Rate limiting may be needed.",
                        confidence=0.8,
                        parameters={'capture.lcu_rate_limit_rps':
                                    max(self._current_config['capture.lcu_rate_limit_rps'] - 3, 5)}))

        # Proposal 3: Adjust based on latency
        latency_data = report.get('latency_percentiles', {})
        for category, percentiles in latency_data.items():
            p99 = percentiles.get('p99', 0)
            if p99 > 100:  # p99 > 100ms
                if 'history' in category:
                    proposals.append(self._make_proposal(
                        "performance", "history",
                        f"Increase fetch timeout: p99 latency is {p99:.0f}ms",
                        f"Category '{category}' p99={p99:.0f}ms exceeds 100ms. "
                        f"Increase timeout to prevent premature failures.",
                        confidence=0.6,
                        parameters={'history.fetch_timeout_sec':
                                    max(p99 / 1000 * 3, 3.0)}))

        self._proposals = proposals
        self._logger.evolution(
            f"Generated {len(proposals)} evolution proposals",
            data={'proposals': [p.to_dict() for p in proposals]})
        return proposals

    def apply_proposals(
        self, proposals: Optional[List[EvolutionProposal]] = None
    ) -> EvolutionGeneration:
        """Apply selected proposals and create new generation."""
        if proposals is None:
            proposals = [p for p in self._proposals if p.confidence >= 0.5]

        metrics_before = self._collect_metrics()
        applied_ids = []

        for proposal in proposals:
            # Save rollback data
            proposal.rollback_data = {
                k: self._current_config.get(k)
                for k in proposal.parameters
            }
            # Apply parameter changes
            for key, value in proposal.parameters.items():
                self._current_config[key] = value
            proposal.applied = True
            applied_ids.append(proposal.proposal_id)
            self._logger.evolution(
                f"Applied proposal: {proposal.description}",
                data={'proposal_id': proposal.proposal_id,
                      'parameters': proposal.parameters})

        self._generation += 1
        gen = EvolutionGeneration(
            generation_id=self._generation,
            created_at=datetime.now(timezone.utc).isoformat(),
            proposals_applied=applied_ids,
            metrics_before=metrics_before,
        )
        self._history.append(gen)
        return gen

    def evaluate_generation(self, gen: EvolutionGeneration) -> float:
        """
        Evaluate current generation's performance.

        Returns improvement score: >0 = better, <0 = worse.
        """
        metrics_after = self._collect_metrics()
        gen.metrics_after = metrics_after

        # Compute improvement score
        improvement = 0.0
        for key in gen.metrics_before:
            metrics_before = gen.metrics_before
            before = metrics_before.get(key, 0)
            after = metrics_after.get(key, 0)
            if before != 0:
                pct_change = (after - before) / abs(before)
                # Positive metrics: acceptance_rate, reward_rate
                if key in ('acceptance_rate', 'reward_rate', 'cache_hit_rate'):
                    improvement += pct_change
                # Negative metrics: error_rate, p99_latency
                elif key in ('error_rate', 'p99_latency'):
                    improvement -= pct_change

        gen.improvement = round(improvement, 4)
        return improvement

    def commit_or_rollback(
        self, gen: EvolutionGeneration, improvement: float
    ) -> bool:
        """Commit generation if improved, rollback otherwise."""
        if improvement > 0:
            gen.committed = True
            self._logger.evolution(
                f"Generation {gen.generation_id} committed: "
                f"improvement={improvement:.4f}",
                data=gen.to_dict())
            return True
        else:
            # Rollback all proposals
            for proposal in self._proposals:
                if proposal.applied and proposal.rollback_data:
                    for key, value in proposal.rollback_data.items():
                        if value is not None:
                            self._current_config[key] = value
                    proposal.applied = False
            self._logger.evolution(
                f"Generation {gen.generation_id} rolled back: "
                f"improvement={improvement:.4f}",
                data=gen.to_dict())
            return False

    def get_config(self, key: str) -> Any:
        """Get a tunable configuration value."""
        return self._current_config.get(key)

    def get_all_config(self) -> Dict[str, Any]:
        return dict(self._current_config)

    def _collect_metrics(self) -> Dict[str, float]:
        """Collect current performance metrics from all modules."""
        report = self._analyzer.generate_evolution_report()
        total = report.get('total_entries', 1)
        errors = report.get('level_distribution', {}).get('ERROR', 0)
        rewards = report.get('level_distribution', {}).get('REWARD', 0)
        strategy = report.get('category_distribution', {}).get('strategy_engine', 1)
        return {
            'error_rate': errors / max(total, 1),
            'reward_rate': rewards / max(total, 1),
            'acceptance_rate': rewards / max(strategy, 1),
            'total_events': total,
        }

    def _make_proposal(
        self, category: str, target: str, desc: str, rationale: str,
        confidence: float, parameters: Dict, impact: str = "medium"
    ) -> EvolutionProposal:
        self._proposal_counter += 1
        return EvolutionProposal(
            proposal_id=f"prop_{self._generation}_{self._proposal_counter:04d}",
            category=category,
            target_module=target,
            description=desc,
            rationale=rationale,
            confidence=confidence,
            estimated_impact=impact,
            parameters=parameters,
        )

    def get_evolution_history(self) -> List[Dict]:
        return [g.to_dict() for g in self._history]


class MutationStrategy:
    """
    Defines how the system mutates between generations.

    The evolution controller uses mutation strategies to determine
    what changes to apply when transitioning from Agent A to Agent A'.

    Mutation types:
        - PARAMETER: Adjust hyperparameters (poll interval, thresholds)
        - STRUCTURAL: Add/remove processing pipeline stages
        - BEHAVIORAL: Change strategy weighting or decision boundaries

    Production critique:
        1. User: Mutations are logged with full before/after state,
           enabling rollback if a mutation degrades performance.
        2. System: Only one mutation is applied per evolution cycle
           to maintain clear causality between change and outcome.
    """
    def __init__(self):
        self._mutation_log: List[Dict] = []
        self._current_generation: int = 0
        self._parameter_ranges: Dict[str, Tuple[float, float]] = {
            'poll_interval_sec': (0.5, 5.0),
            'strategy_confidence_threshold': (0.3, 0.9),
            'voice_output_delay_ms': (100, 2000),
            'history_fetch_depth': (5, 50),
            'opponent_threat_threshold': (0.2, 0.8),
            'reward_decay_factor': (0.8, 0.99),
            'cache_ttl_sec': (30, 600),
        }
        self._current_params: Dict[str, float] = {
            k: (v[0] + v[1]) / 2 for k, v in self._parameter_ranges.items()
        }

    def propose_mutation(
        self, reward_trend: str, error_rate: float
    ) -> Optional[Dict]:
        """
        Propose a parameter mutation based on current performance.

        Returns mutation specification or None if no mutation needed.
        """
        import random
        if reward_trend == 'improving' and error_rate < 0.05:
            return None  # Don't fix what isn't broken

        # Select parameter to mutate
        param = random.choice(list(self._parameter_ranges.keys()))
        lo, hi = self._parameter_ranges[param]
        current = self._current_params[param]

        # Determine mutation direction from performance signals
        if error_rate > 0.1:
            # High error rate → increase safety margins
            if param in ('poll_interval_sec', 'cache_ttl_sec'):
                new_val = min(hi, current * 1.2)
            else:
                new_val = current  # No change for non-safety params
        elif reward_trend == 'declining':
            # Declining performance → explore more aggressively
            delta = (hi - lo) * random.uniform(0.05, 0.15)
            direction = random.choice([-1, 1])
            new_val = max(lo, min(hi, current + direction * delta))
        else:
            # Stable → small random perturbation
            delta = (hi - lo) * random.uniform(0.01, 0.05)
            direction = random.choice([-1, 1])
            new_val = max(lo, min(hi, current + direction * delta))

        mutation = {
            'generation': self._current_generation,
            'parameter': param,
            'old_value': round(current, 4),
            'new_value': round(new_val, 4),
            'reason': f"reward_trend={reward_trend}, error_rate={error_rate:.3f}",
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        return mutation

    def apply_mutation(self, mutation: Dict) -> None:
        """Apply a proposed mutation."""
        param = mutation['parameter']
        self._current_params[param] = mutation['new_value']
        self._mutation_log.append(mutation)
        self._current_generation += 1

    def rollback_last(self) -> Optional[Dict]:
        """Rollback the last mutation."""
        if not self._mutation_log:
            return None
        last = self._mutation_log.pop()
        self._current_params[last['parameter']] = last['old_value']
        self._current_generation = max(0, self._current_generation - 1)
        return last

    def get_params(self) -> Dict[str, float]:
        return dict(self._current_params)

    def get_mutation_history(self) -> List[Dict]:
        return list(self._mutation_log)

    def get_generation(self) -> int:
        return self._current_generation


class EvolutionCheckpointer:
    """
    Manages evolution checkpoints for rollback and analysis.

    Each checkpoint captures the complete system state at a point
    in the evolution timeline. If a new generation underperforms,
    we can restore the previous checkpoint.
    """
    def __init__(self, checkpoint_dir: str = "checkpoints"):
        self._dir = Path(checkpoint_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._checkpoints: List[Dict] = []

    def save_checkpoint(
        self, generation: int, params: Dict, metrics: Dict,
        mutation: Optional[Dict] = None
    ) -> str:
        """Save a checkpoint and return its ID."""
        checkpoint_id = f"gen_{generation}_{int(time.time())}"
        checkpoint = {
            'id': checkpoint_id,
            'generation': generation,
            'params': params,
            'metrics': metrics,
            'mutation': mutation,
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        self._checkpoints.append(checkpoint)
        checkpoint_path = self._dir / f"{checkpoint_id}.json"
        checkpoint_path.write_text(
            json.dumps(checkpoint, indent=2, ensure_ascii=False))
        return checkpoint_id

    def load_checkpoint(self, checkpoint_id: str) -> Optional[Dict]:
        """Load a checkpoint by ID."""
        path = self._dir / f"{checkpoint_id}.json"
        if path.exists():
            return json.loads(path.read_text())
        for cp in self._checkpoints:
            if cp['id'] == checkpoint_id:
                return cp
        return None

    def get_best_checkpoint(self, metric_key: str = 'reward_mean') -> Optional[Dict]:
        """Find the checkpoint with the best value for a metric."""
        if not self._checkpoints:
            return None
        return max(
            self._checkpoints,
            key=lambda cp: cp.get('metrics', {}).get(metric_key, 0))

    def list_checkpoints(self) -> List[Dict]:
        return [
            {'id': cp['id'], 'generation': cp['generation'],
             'timestamp': cp['timestamp']}
            for cp in self._checkpoints
        ]

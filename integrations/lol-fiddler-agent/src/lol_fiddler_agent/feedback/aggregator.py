"""
Feedback Aggregator - Aggregates compliance records across game sessions.

Computes aggregate statistics for strategy evaluation, identifies
which advice types are most/least followed and most/least effective,
and provides data for strategy model improvement.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lol_fiddler_agent.agents.strategy_agent import ActionType
from lol_fiddler_agent.feedback.tracker import ComplianceRecord

logger = logging.getLogger(__name__)


@dataclass
class ActionStats:
    """Aggregate statistics for a single action type."""
    action: ActionType
    total_given: int = 0
    total_followed: int = 0
    total_ignored: int = 0
    positive_outcomes: int = 0
    negative_outcomes: int = 0
    neutral_outcomes: int = 0
    avg_gold_change: float = 0.0
    avg_reward: float = 0.0

    @property
    def compliance_rate(self) -> float:
        if self.total_given == 0:
            return 0.0
        return self.total_followed / self.total_given

    @property
    def effectiveness(self) -> float:
        """Rate of positive outcomes when advice was followed."""
        if self.total_followed == 0:
            return 0.0
        followed_positive = 0
        # This is approximate; exact tracking requires per-record data
        return self.positive_outcomes / max(self.total_given, 1)

    @property
    def is_valuable(self) -> bool:
        """Whether this advice type provides value."""
        return self.compliance_rate > 0.3 and self.effectiveness > 0.4


@dataclass
class SessionSummary:
    """Summary of a single game session."""
    session_id: str
    start_time: float
    end_time: float
    total_advice: int = 0
    total_followed: int = 0
    compliance_rate: float = 0.0
    avg_reward: float = 0.0
    won: Optional[bool] = None
    champion: str = ""
    game_duration_minutes: float = 0.0


class FeedbackAggregator:
    """Aggregates feedback data across sessions for analysis.

    Example::

        aggregator = FeedbackAggregator()
        aggregator.add_records(session_records)
        stats = aggregator.get_action_stats()
        best_advice = aggregator.most_effective_actions(top_n=3)
    """

    def __init__(self) -> None:
        self._action_stats: dict[ActionType, ActionStats] = {}
        self._sessions: list[SessionSummary] = []
        self._all_records: list[ComplianceRecord] = []
        self._gold_by_action: dict[ActionType, list[int]] = defaultdict(list)
        self._reward_by_action: dict[ActionType, list[float]] = defaultdict(list)

    def add_records(
        self,
        records: list[ComplianceRecord],
        session_id: str = "",
        won: Optional[bool] = None,
        champion: str = "",
    ) -> None:
        """Add compliance records from a game session."""
        self._all_records.extend(records)

        for record in records:
            action = record.advice_action
            if action not in self._action_stats:
                self._action_stats[action] = ActionStats(action=action)

            stats = self._action_stats[action]
            stats.total_given += 1

            if record.followed:
                stats.total_followed += 1
            else:
                stats.total_ignored += 1

            if record.outcome == "positive":
                stats.positive_outcomes += 1
            elif record.outcome == "negative":
                stats.negative_outcomes += 1
            else:
                stats.neutral_outcomes += 1

            self._gold_by_action[action].append(record.gold_change)
            self._reward_by_action[action].append(record.reward_signal)

        # Update running averages
        for action, stats in self._action_stats.items():
            golds = self._gold_by_action[action]
            rewards = self._reward_by_action[action]
            if golds:
                stats.avg_gold_change = sum(golds) / len(golds)
            if rewards:
                stats.avg_reward = sum(rewards) / len(rewards)

        # Record session
        if records:
            followed = sum(1 for r in records if r.followed)
            session = SessionSummary(
                session_id=session_id or f"session_{int(time.time())}",
                start_time=min(r.advice_given_at for r in records),
                end_time=max(r.evaluated_at for r in records),
                total_advice=len(records),
                total_followed=followed,
                compliance_rate=followed / len(records) if records else 0,
                avg_reward=sum(r.reward_signal for r in records) / len(records),
                won=won,
                champion=champion,
            )
            self._sessions.append(session)

    def get_action_stats(self) -> dict[ActionType, ActionStats]:
        return dict(self._action_stats)

    def most_effective_actions(self, top_n: int = 5) -> list[ActionStats]:
        """Get the most effective advice types."""
        sorted_stats = sorted(
            self._action_stats.values(),
            key=lambda s: s.avg_reward,
            reverse=True,
        )
        return sorted_stats[:top_n]

    def least_followed_actions(self, top_n: int = 5) -> list[ActionStats]:
        """Get advice types with lowest compliance."""
        sorted_stats = sorted(
            self._action_stats.values(),
            key=lambda s: s.compliance_rate,
        )
        return sorted_stats[:top_n]

    def get_overall_compliance(self) -> float:
        total = sum(s.total_given for s in self._action_stats.values())
        followed = sum(s.total_followed for s in self._action_stats.values())
        if total == 0:
            return 0.0
        return followed / total

    def get_overall_effectiveness(self) -> float:
        total = sum(s.total_given for s in self._action_stats.values())
        positive = sum(s.positive_outcomes for s in self._action_stats.values())
        if total == 0:
            return 0.0
        return positive / total

    def get_session_history(self) -> list[SessionSummary]:
        return list(self._sessions)

    def get_win_rate_by_compliance(self) -> dict[str, float]:
        """Correlate compliance rate with win rate."""
        high_compliance = [s for s in self._sessions if s.compliance_rate >= 0.6 and s.won is not None]
        low_compliance = [s for s in self._sessions if s.compliance_rate < 0.6 and s.won is not None]

        high_wr = sum(1 for s in high_compliance if s.won) / max(len(high_compliance), 1)
        low_wr = sum(1 for s in low_compliance if s.won) / max(len(low_compliance), 1)

        return {
            "high_compliance_win_rate": high_wr,
            "low_compliance_win_rate": low_wr,
            "high_compliance_games": len(high_compliance),
            "low_compliance_games": len(low_compliance),
        }

    def export_report(self) -> dict[str, Any]:
        """Export full aggregated report."""
        return {
            "overall_compliance": self.get_overall_compliance(),
            "overall_effectiveness": self.get_overall_effectiveness(),
            "total_sessions": len(self._sessions),
            "total_records": len(self._all_records),
            "action_stats": {
                action.value: {
                    "given": stats.total_given,
                    "followed": stats.total_followed,
                    "compliance": stats.compliance_rate,
                    "avg_reward": stats.avg_reward,
                    "positive_rate": stats.positive_outcomes / max(stats.total_given, 1),
                }
                for action, stats in self._action_stats.items()
            },
            "compliance_vs_winrate": self.get_win_rate_by_compliance(),
        }

    def save(self, path: str) -> None:
        """Save aggregated data to JSON."""
        with open(path, "w") as f:
            json.dump(self.export_report(), f, indent=2)

    def reset(self) -> None:
        self._action_stats.clear()
        self._sessions.clear()
        self._all_records.clear()
        self._gold_by_action.clear()
        self._reward_by_action.clear()


# ── Evolution Integration (M280 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'feedback_aggregator'


class EvolvableFeedbackAggregator(FeedbackAggregator):
    """FeedbackAggregator with multi-dimensional reward computation.

    Extends the base aggregator to compute composite reward signals
    from compliance, effectiveness, and gold delta dimensions,
    feeding directly into AgentLightning's PolicyReward.
    """

    def __init__(self) -> None:
        super().__init__()
        self._evolution_callback = None

    @property
    def evolution_callback(self):
        return self._evolution_callback

    @evolution_callback.setter
    def evolution_callback(self, cb):
        self._evolution_callback = cb

    def _fire_evolution(self, data: dict) -> None:
        import time as _time
        data.setdefault('module', _EVOLUTION_KEY)
        data.setdefault('timestamp', _time.time())
        if self._evolution_callback:
            try:
                self._evolution_callback(data)
            except Exception:
                pass

    def compute_multidim_reward(
        self, compliance: float = 0.0, effectiveness: float = 0.0,
        gold_delta: float = 0.0, weights: dict = None,
    ) -> dict:
        """Compute multi-dimensional reward signal.

        Returns composite reward from weighted dimensions:
        - compliance: how often advice was followed (0-1)
        - effectiveness: positive outcome rate (0-1)
        - gold_delta: gold change normalized to [-1, 1]
        """
        w = weights or {'compliance': 0.3, 'effectiveness': 0.5, 'gold': 0.2}
        gold_norm = max(min(gold_delta / 5000.0, 1.0), -1.0)
        composite = (
            w.get('compliance', 0.3) * compliance
            + w.get('effectiveness', 0.5) * effectiveness
            + w.get('gold', 0.2) * gold_norm
        )
        return {
            'compliance': compliance,
            'effectiveness': effectiveness,
            'gold_delta': gold_delta,
            'gold_normalized': gold_norm,
            'composite': composite,
            'weights': w,
        }

    def to_training_annotation(self, **kwargs) -> dict:
        annotation = {'module': _EVOLUTION_KEY}
        annotation.update(kwargs)
        return annotation

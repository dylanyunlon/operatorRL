"""
Feedback Tracker - Monitors player actions relative to given advice.

Detects whether the player followed strategic advice by comparing
successive game states, and records compliance/outcome pairs for
reinforcement learning.

Architecture:
  StrategicAdvice → FeedbackTracker → (state_diff) → ComplianceRecord
                                                         ↓
                                                   RewardCalculator
                                                         ↓
                                                   TrainingDataStore
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.agents.strategy_agent import (
    ActionType,
    PerformanceFeedback,
    StrategicAdvice,
)
from lol_fiddler_agent.models.game_snapshot import GameSnapshot

logger = logging.getLogger(__name__)


class ComplianceOutcome(str):
    FOLLOWED_POSITIVE = "followed_positive"
    FOLLOWED_NEGATIVE = "followed_negative"
    FOLLOWED_NEUTRAL = "followed_neutral"
    IGNORED_POSITIVE = "ignored_positive"
    IGNORED_NEGATIVE = "ignored_negative"
    IGNORED_NEUTRAL = "ignored_neutral"
    UNKNOWN = "unknown"


@dataclass
class ComplianceRecord:
    """Records whether advice was followed and the outcome."""
    advice_id: str
    advice_action: ActionType
    advice_given_at: float  # game time
    evaluated_at: float  # game time
    followed: bool
    outcome: str  # positive, negative, neutral
    gold_change: int = 0
    kill_change: int = 0
    death_change: int = 0
    health_change: float = 0.0
    objective_gained: bool = False

    @property
    def compliance_outcome(self) -> str:
        prefix = "followed" if self.followed else "ignored"
        return f"{prefix}_{self.outcome}"

    @property
    def reward_signal(self) -> float:
        """Convert to a reward signal for RL."""
        base = 0.0
        if self.outcome == "positive":
            base = 1.0 if self.followed else -0.5
        elif self.outcome == "negative":
            base = -1.0 if self.followed else 0.5
        else:
            base = 0.1 if self.followed else -0.1

        # Bonus modifiers
        base += self.gold_change * 0.0005
        base += self.kill_change * 0.3
        base -= self.death_change * 0.5
        if self.objective_gained:
            base += 1.0

        return base


@dataclass
class PendingAdvice:
    """Advice waiting for compliance evaluation."""
    advice: StrategicAdvice
    snapshot_at_advice: GameSnapshot
    given_at: float  # wall clock time
    evaluation_window: float = 30.0  # seconds to wait before evaluating

    @property
    def is_expired(self) -> bool:
        return time.time() - self.given_at > self.evaluation_window


# Detection rules for each action type
_COMPLIANCE_DETECTORS: dict[ActionType, str] = {
    ActionType.FARM: "cs_increased",
    ActionType.TRADE: "damage_dealt_or_taken",
    ActionType.ALL_IN: "kill_or_death",
    ActionType.ROAM: "position_changed",
    ActionType.RECALL: "gold_spent",
    ActionType.OBJECTIVE: "objective_event",
    ActionType.DEFEND: "stayed_near_tower",
    ActionType.GROUP: "near_teammates",
    ActionType.SPLIT: "alone_in_sidelane",
    ActionType.DISENGAGE: "avoided_fight",
}


class FeedbackTracker:
    """Tracks advice compliance and generates feedback records.

    Usage::

        tracker = FeedbackTracker()
        tracker.record_advice(advice, current_snapshot)
        # ... time passes ...
        records = tracker.evaluate(new_snapshot)
        for record in records:
            print(f"Advice {record.advice_id}: {record.compliance_outcome}")
    """

    def __init__(
        self,
        evaluation_delay: float = 15.0,
        max_pending: int = 50,
    ) -> None:
        self._evaluation_delay = evaluation_delay
        self._pending: deque[PendingAdvice] = deque(maxlen=max_pending)
        self._records: list[ComplianceRecord] = []
        self._total_followed = 0
        self._total_ignored = 0

    def record_advice(
        self,
        advice: StrategicAdvice,
        snapshot: GameSnapshot,
    ) -> None:
        """Record that advice was given at this game state."""
        pending = PendingAdvice(
            advice=advice,
            snapshot_at_advice=snapshot,
            given_at=time.time(),
            evaluation_window=advice.time_window_seconds or self._evaluation_delay,
        )
        self._pending.append(pending)

    def evaluate(self, current_snapshot: GameSnapshot) -> list[ComplianceRecord]:
        """Evaluate pending advice against current state.

        Returns newly generated compliance records.
        """
        new_records: list[ComplianceRecord] = []
        still_pending: list[PendingAdvice] = []

        for pending in self._pending:
            if pending.is_expired:
                record = self._evaluate_single(pending, current_snapshot)
                new_records.append(record)
                self._records.append(record)
                if record.followed:
                    self._total_followed += 1
                else:
                    self._total_ignored += 1
            else:
                still_pending.append(pending)

        self._pending = deque(still_pending, maxlen=self._pending.maxlen)
        return new_records

    def _evaluate_single(
        self,
        pending: PendingAdvice,
        current: GameSnapshot,
    ) -> ComplianceRecord:
        """Evaluate whether a single piece of advice was followed."""
        old = pending.snapshot_at_advice
        advice = pending.advice

        # Compute state differences
        gold_change = current.gold_difference - old.gold_difference
        kill_change = current.kill_difference - old.kill_difference

        # Compute player-specific changes
        death_change = 0
        health_change = current.my_health_pct - old.my_health_pct
        for cp, op in zip(current.players, old.players):
            if cp.champion_name == current.my_champion:
                death_change = cp.deaths - op.deaths
                break

        # Determine if advice was followed based on action type
        followed = self._detect_compliance(advice.action, old, current)

        # Determine outcome
        outcome = self._determine_outcome(gold_change, kill_change, death_change)

        # Check for objective events
        objective_gained = len(current.recent_events) > len(old.recent_events)

        return ComplianceRecord(
            advice_id=advice.advice_id,
            advice_action=advice.action,
            advice_given_at=old.game_time,
            evaluated_at=current.game_time,
            followed=followed,
            outcome=outcome,
            gold_change=gold_change,
            kill_change=kill_change,
            death_change=death_change,
            health_change=health_change,
            objective_gained=objective_gained,
        )

    def _detect_compliance(
        self,
        action: ActionType,
        old: GameSnapshot,
        current: GameSnapshot,
    ) -> bool:
        """Heuristically detect if the player complied with advice."""
        if action == ActionType.FARM:
            # Check if CS increased meaningfully
            old_cs = self._get_my_cs(old)
            new_cs = self._get_my_cs(current)
            time_delta = max(current.game_time - old.game_time, 1)
            cs_rate = (new_cs - old_cs) / (time_delta / 60)
            return cs_rate >= 5.0  # At least 5 CS/min

        elif action == ActionType.RECALL:
            # Health went up significantly (healed at base)
            return current.my_health_pct > old.my_health_pct + 30

        elif action == ActionType.DEFEND:
            # Didn't die, stayed alive
            old_deaths = sum(p.deaths for p in old.players if p.champion_name == old.my_champion)
            new_deaths = sum(p.deaths for p in current.players if p.champion_name == current.my_champion)
            return new_deaths <= old_deaths

        elif action == ActionType.TRADE:
            # Engaged in combat (kills or assists changed)
            old_ka = self._get_my_ka(old)
            new_ka = self._get_my_ka(current)
            return new_ka > old_ka

        elif action == ActionType.ALL_IN:
            # Got a kill
            old_kills = self._get_my_kills(old)
            new_kills = self._get_my_kills(current)
            return new_kills > old_kills

        elif action == ActionType.OBJECTIVE:
            # Any objective event occurred
            return len(current.recent_events) > len(old.recent_events)

        elif action == ActionType.GROUP:
            # Hard to detect without position data; assume followed
            return True

        elif action == ActionType.DISENGAGE:
            # Didn't die
            old_deaths = sum(p.deaths for p in old.players if p.champion_name == old.my_champion)
            new_deaths = sum(p.deaths for p in current.players if p.champion_name == current.my_champion)
            return new_deaths <= old_deaths

        return True  # Default: assume followed

    @staticmethod
    def _determine_outcome(gold_change: int, kill_change: int, death_change: int) -> str:
        score = gold_change * 0.001 + kill_change * 0.5 - death_change * 0.8
        if score > 0.3:
            return "positive"
        elif score < -0.3:
            return "negative"
        return "neutral"

    @staticmethod
    def _get_my_cs(snap: GameSnapshot) -> int:
        for p in snap.players:
            if p.champion_name == snap.my_champion:
                return p.creep_score
        return 0

    @staticmethod
    def _get_my_ka(snap: GameSnapshot) -> int:
        for p in snap.players:
            if p.champion_name == snap.my_champion:
                return p.kills + p.assists
        return 0

    @staticmethod
    def _get_my_kills(snap: GameSnapshot) -> int:
        for p in snap.players:
            if p.champion_name == snap.my_champion:
                return p.kills
        return 0

    # ── Statistics ─────────────────────────────────────────────────────────

    @property
    def compliance_rate(self) -> float:
        total = self._total_followed + self._total_ignored
        if total == 0:
            return 0.0
        return self._total_followed / total

    @property
    def total_records(self) -> int:
        return len(self._records)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def get_records(self) -> list[ComplianceRecord]:
        return list(self._records)

    def get_summary(self) -> dict[str, Any]:
        return {
            "total_records": len(self._records),
            "pending": len(self._pending),
            "compliance_rate": self.compliance_rate,
            "followed": self._total_followed,
            "ignored": self._total_ignored,
        }

    def export_for_training(self) -> list[dict[str, Any]]:
        """Export records in format suitable for RL training."""
        return [
            {
                "advice_action": r.advice_action.value,
                "followed": r.followed,
                "outcome": r.outcome,
                "reward": r.reward_signal,
                "gold_change": r.gold_change,
                "kill_change": r.kill_change,
                "game_time": r.advice_given_at,
            }
            for r in self._records
        ]


# ── Evolution Integration (M281 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'feedback_tracker'


class EvolvableFeedbackTracker(FeedbackTracker):
    """FeedbackTracker with self-evolution callback hooks."""

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

    def to_training_annotation(self, **kwargs) -> dict:
        annotation = {'module': _EVOLUTION_KEY}
        annotation.update(kwargs)
        return annotation

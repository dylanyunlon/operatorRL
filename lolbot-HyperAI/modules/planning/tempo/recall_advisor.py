"""
modules/planning/tempo/recall_advisor.py — Optimal recall timing advisor.
==========================================================================
Claude19 · Wires into PlanningComponent.Proc()

Analyzes gold state, health/mana, wave position, and upcoming objectives
to advise on optimal recall timing. A mistimed recall can lose tempo;
a well-timed one sets up item spikes.

Apollo analogy: planning/tasks/deciders/pull_over_decider.cc decides
when the vehicle should pull over (our equivalent: pull back to base).

File location: lolbot-HyperAI/modules/planning/tempo/recall_advisor.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RecallUrgency(Enum):
    """How urgently the player should recall."""
    NOW = auto()         # Recall immediately
    SOON = auto()        # Recall within 30s
    WHEN_SAFE = auto()   # Recall when wave is pushed
    NOT_YET = auto()     # Stay on map


# Common item breakpoints (gold thresholds that unlock key items)
_ITEM_BREAKPOINTS = [
    (1300, "Needlessly Large Rod / B.F. Sword"),
    (1100, "Blasting Wand / Pickaxe + boots"),
    (900, "Component + Control Ward"),
    (700, "Boots upgrade"),
    (500, "Long Sword + Refillable"),
    (350, "Boots + potions"),
]

# Health thresholds for recall recommendation
_HEALTH_CRITICAL = 0.25    # Below 25% = recall now
_HEALTH_LOW = 0.40         # Below 40% = recall soon
_MANA_LOW = 0.15           # Below 15% = recall soon


@dataclass
class RecallAdvice:
    """A recall timing recommendation."""
    urgency: RecallUrgency
    reason: str
    gold_available: float
    best_buy: str  # What you can buy if you recall now
    game_time: float
    health_pct: float = 1.0
    mana_pct: float = 1.0
    objective_window_s: float = 0.0  # Seconds until next objective

    def to_dict(self) -> Dict[str, Any]:
        return {
            "urgency": self.urgency.name,
            "reason": self.reason,
            "gold": round(self.gold_available, 0),
            "best_buy": self.best_buy,
            "game_time": round(self.game_time, 1),
            "health_pct": round(self.health_pct, 2),
            "mana_pct": round(self.mana_pct, 2),
            "objective_window_s": round(self.objective_window_s, 0),
        }


class RecallAdvisor:
    """Advises on optimal recall timing.

    Usage::
        advisor = RecallAdvisor()
        advice = advisor.evaluate(
            current_gold=1350.0,
            health_pct=0.35,
            mana_pct=0.20,
            game_time=600.0,
            next_objective_time=900.0,
        )
        if advice.urgency in (RecallUrgency.NOW, RecallUrgency.SOON):
            announce(advice.reason)
    """

    _COOLDOWN_S = 20.0  # Don't spam recall advice

    def __init__(self) -> None:
        self._last_advice_time: float = 0.0
        self._advice_count: int = 0

    def evaluate(
        self,
        current_gold: float = 0.0,
        health_pct: float = 1.0,
        mana_pct: float = 1.0,
        game_time: float = 0.0,
        next_objective_time: float = 0.0,
        is_dead: bool = False,
        enemy_dead_count: int = 0,
    ) -> RecallAdvice:
        """Evaluate whether the player should recall.

        Args:
            current_gold: Unspent gold.
            health_pct: Current health as fraction (0-1).
            mana_pct: Current mana as fraction (0-1).
            game_time: Current game time.
            next_objective_time: When next objective spawns.
            is_dead: Whether active player is currently dead.
            enemy_dead_count: How many enemies are dead.

        Returns:
            RecallAdvice with urgency and reasoning.
        """
        self._advice_count += 1

        # Dead players don't need recall advice
        if is_dead:
            return RecallAdvice(
                urgency=RecallUrgency.NOT_YET,
                reason="Currently dead — items purchased on respawn",
                gold_available=current_gold,
                best_buy=self._best_buy(current_gold),
                game_time=game_time,
                health_pct=health_pct,
                mana_pct=mana_pct,
            )

        objective_window = (
            next_objective_time - game_time if next_objective_time > game_time
            else float("inf")
        )

        reasons: List[str] = []
        urgency = RecallUrgency.NOT_YET

        # Health check
        if health_pct < _HEALTH_CRITICAL:
            urgency = RecallUrgency.NOW
            reasons.append(f"Health critical ({health_pct*100:.0f}%)")
        elif health_pct < _HEALTH_LOW:
            if urgency.value > RecallUrgency.SOON.value:
                urgency = RecallUrgency.SOON
            reasons.append(f"Health low ({health_pct*100:.0f}%)")

        # Mana check
        if mana_pct < _MANA_LOW and mana_pct >= 0:
            if urgency.value > RecallUrgency.SOON.value:
                urgency = RecallUrgency.SOON
            reasons.append(f"Mana low ({mana_pct*100:.0f}%)")

        # Gold check — enough for a power spike item
        best = self._best_buy(current_gold)
        for threshold, item_name in _ITEM_BREAKPOINTS:
            if current_gold >= threshold:
                if urgency == RecallUrgency.NOT_YET:
                    urgency = RecallUrgency.WHEN_SAFE
                reasons.append(
                    f"Can buy {item_name} ({current_gold:.0f}g)")
                break

        # Objective window — don't recall if objective imminent
        if objective_window < 60.0 and urgency != RecallUrgency.NOW:
            if health_pct > _HEALTH_CRITICAL:
                urgency = RecallUrgency.NOT_YET
                reasons = [f"Objective spawns in {objective_window:.0f}s — stay"]

        # Don't recall during a numerical advantage window
        if enemy_dead_count >= 2 and urgency != RecallUrgency.NOW:
            urgency = RecallUrgency.NOT_YET
            reasons = [f"{enemy_dead_count} enemies dead — press advantage"]

        reason = "; ".join(reasons) if reasons else "No recall needed"

        return RecallAdvice(
            urgency=urgency,
            reason=reason,
            gold_available=current_gold,
            best_buy=best,
            game_time=game_time,
            health_pct=health_pct,
            mana_pct=mana_pct,
            objective_window_s=objective_window,
        )

    def _best_buy(self, gold: float) -> str:
        """Find the best item purchasable with current gold."""
        for threshold, item_name in _ITEM_BREAKPOINTS:
            if gold >= threshold:
                return item_name
        if gold >= 150:
            return "Potions / Components"
        return "Nothing significant"

    def stats(self) -> Dict[str, Any]:
        return {"advice_count": self._advice_count}


# ═══════════════════════════════════════════════════════════════════════════
# Claude20: Extended recall advisor with wave state, history, and templates
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class RecallHistoryEntry:
    """Record of a recall recommendation for post-game analysis."""
    game_time: float
    urgency: RecallUrgency
    reason: str
    gold_available: float
    health_pct: float
    was_followed: bool = False  # Did the player actually recall?

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": round(self.game_time, 1),
            "urgency": self.urgency.name,
            "reason": self.reason,
            "gold": round(self.gold_available, 0),
            "followed": self.was_followed,
        }


class RecallAdvisorV2(RecallAdvisor):
    """Extended recall advisor with wave awareness and recommendation history.

    Claude20: Adds wave state awareness, item completion tracking,
    recommendation history for evolution fitness evaluation, and
    voice-friendly text generation.

    All existing RecallAdvisor logic preserved.
    """

    _WAVE_PUSH_BONUS_S = 10.0  # Extra safe time when wave is pushing

    def __init__(self) -> None:
        super().__init__()
        self._history: List[RecallHistoryEntry] = []
        self._wave_state: str = "unknown"  # pushing, frozen, slow_push, pushed_in
        self._completed_items: int = 0
        self._last_recall_time: float = 0.0
        self._good_recall_count: int = 0
        self._bad_recall_count: int = 0

    def evaluate_with_context(
        self,
        current_gold: float = 0.0,
        health_pct: float = 1.0,
        mana_pct: float = 1.0,
        game_time: float = 0.0,
        next_objective_time: float = 0.0,
        is_dead: bool = False,
        enemy_dead_count: int = 0,
        wave_state: str = "unknown",
        completed_items: int = 0,
        active_player_level: int = 1,
    ) -> RecallAdvice:
        """Enhanced evaluate with wave state and item context.

        Claude20: Wraps base evaluate() with additional signals.

        Args:
            wave_state: Current lane wave state (pushing/frozen/etc).
            completed_items: Number of completed items in inventory.
            active_player_level: Current level of active player.
        """
        self._wave_state = wave_state
        self._completed_items = completed_items

        # Get base recommendation
        advice = self.evaluate(
            current_gold=current_gold,
            health_pct=health_pct,
            mana_pct=mana_pct,
            game_time=game_time,
            next_objective_time=next_objective_time,
            is_dead=is_dead,
            enemy_dead_count=enemy_dead_count,
        )

        # Wave state modulation
        if wave_state == "pushing" and advice.urgency == RecallUrgency.WHEN_SAFE:
            # Good time to recall — wave is pushing away from us
            advice = RecallAdvice(
                urgency=RecallUrgency.SOON,
                reason=advice.reason + " (wave pushing — safe window)",
                gold_available=advice.gold_available,
                best_buy=advice.best_buy,
                game_time=advice.game_time,
                health_pct=advice.health_pct,
                mana_pct=advice.mana_pct,
                objective_window_s=advice.objective_window_s,
            )
        elif wave_state == "pushed_in" and advice.urgency == RecallUrgency.SOON:
            # Don't recall when wave is crashing into our tower
            if health_pct > _HEALTH_CRITICAL:
                advice = RecallAdvice(
                    urgency=RecallUrgency.WHEN_SAFE,
                    reason="Wave at tower — catch the wave first, then recall",
                    gold_available=advice.gold_available,
                    best_buy=advice.best_buy,
                    game_time=advice.game_time,
                    health_pct=advice.health_pct,
                    mana_pct=advice.mana_pct,
                    objective_window_s=advice.objective_window_s,
                )

        # Item completion spike: if we can complete a major item, bump urgency
        if current_gold >= 1300 and completed_items < 3:
            if advice.urgency == RecallUrgency.NOT_YET:
                advice = RecallAdvice(
                    urgency=RecallUrgency.WHEN_SAFE,
                    reason=f"Can complete item #{completed_items + 1} — {advice.best_buy}",
                    gold_available=advice.gold_available,
                    best_buy=advice.best_buy,
                    game_time=advice.game_time,
                    health_pct=advice.health_pct,
                    mana_pct=advice.mana_pct,
                    objective_window_s=advice.objective_window_s,
                )

        # Record in history
        self._history.append(RecallHistoryEntry(
            game_time=game_time,
            urgency=advice.urgency,
            reason=advice.reason,
            gold_available=current_gold,
            health_pct=health_pct,
        ))

        return advice

    def generate_voice_text(self, advice: RecallAdvice) -> str:
        """Generate TTS-friendly text from recall advice.

        Claude20: Converts structured advice into natural speech.
        """
        if advice.urgency == RecallUrgency.NOW:
            return f"Recall now! {advice.reason}."
        elif advice.urgency == RecallUrgency.SOON:
            return f"Look for a recall soon. {advice.reason}."
        elif advice.urgency == RecallUrgency.WHEN_SAFE:
            return f"Consider recalling when safe. You can buy {advice.best_buy}."
        else:
            return ""

    def record_actual_recall(self, game_time: float) -> None:
        """Record that the player actually recalled.

        Used for tracking recommendation accuracy in fitness evaluation.
        """
        self._last_recall_time = game_time
        # Mark recent recommendation as followed if within window
        for entry in reversed(self._history):
            if game_time - entry.game_time < 15.0:
                entry.was_followed = True
                if entry.urgency in (RecallUrgency.NOW, RecallUrgency.SOON):
                    self._good_recall_count += 1
                break

    def record_death_without_recall(self, game_time: float) -> None:
        """Record a death where recall was recommended but not followed.

        Claude20: Tracks advice quality for evolution fitness.
        """
        for entry in reversed(self._history):
            if game_time - entry.game_time < 30.0:
                if entry.urgency in (RecallUrgency.NOW, RecallUrgency.SOON):
                    if not entry.was_followed:
                        self._bad_recall_count += 1
                break

    def recommendation_accuracy(self) -> float:
        """Compute how often recall recommendations were followed.

        Returns fraction (0-1). Used in evolution fitness scoring.
        """
        total = self._good_recall_count + self._bad_recall_count
        if total == 0:
            return 0.5  # No data
        return self._good_recall_count / total

    def recent_history(self, count: int = 10) -> List[Dict[str, Any]]:
        """Get recent recommendation history."""
        return [h.to_dict() for h in self._history[-count:]]

    def extended_stats(self) -> Dict[str, Any]:
        base = self.stats()
        base.update({
            "history_size": len(self._history),
            "good_recalls": self._good_recall_count,
            "bad_recalls": self._bad_recall_count,
            "accuracy": round(self.recommendation_accuracy(), 3),
            "last_recall_time": self._last_recall_time,
            "wave_state": self._wave_state,
        })
        return base

    def reset(self) -> None:
        """Reset state for new game."""
        self._history.clear()
        self._wave_state = "unknown"
        self._completed_items = 0
        self._last_recall_time = 0.0
        self._good_recall_count = 0
        self._bad_recall_count = 0

"""
BackTimingAdvisor — Optimal recall timing based on gold and wave state.
=========================================================================
lolbot-HyperAI · Planning Layer

Calculates the best time to recall (B) based on current gold, key item
breakpoints, enemy positions, and wave state.

Architecture position:
    modules/planning/strategy/back_timing_advisor.py   ← YOU ARE HERE
    ├─ Called by: PlanningComponent.Proc() during EARLY/MID phase
    ├─ Input: GameSnapshot (gold, items, positions)
    ├─ Output: Optional VoiceCommand ("Good time to back for X")
    └─ Publishes: via PlanningComponent voice_writer

Design notes:
    - Item breakpoint table: common first-buy thresholds
    - Gold buffer: recommend back when gold >= breakpoint + 75 (control ward)
    - Safety check: don't recommend back during teamfight/objective contest
    - Cooldown: 60s between back recommendations
"""

from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from modules.common.adapters.game_messages import (
    GamePhase, GameSnapshot, PlayerState, TeamSide, VoiceCommand,
)
from cyber.logger.cyber_logger import get_logger

logger = get_logger("planning.back_timing")

_COOLDOWN_S = 60.0
_GOLD_BUFFER = 75  # extra gold for control ward

# Common first-buy gold breakpoints
_ITEM_BREAKPOINTS: List[Tuple[int, str, str]] = [
    (875, "Pickaxe", "AD"),
    (900, "Blasting Wand", "AP"),
    (1100, "Serrated Dirk", "Lethality"),
    (1300, "B.F. Sword", "AD"),
    (1300, "Lost Chapter", "AP Mana"),
    (1100, "Noonquiver", "ADC"),
    (1600, "Ironspike Whip", "Fighter"),
    (2600, "Mythic Component", "Core"),
    (3200, "Full Mythic", "Powerspike"),
    (800, "Boots Tier 2 Upgrade", "Mobility"),
]


@dataclass
class BackRecommendation:
    """A recall recommendation with reasoning."""
    should_back: bool = False
    item_name: str = ""
    gold_needed: int = 0
    current_gold: float = 0.0
    confidence: float = 0.0
    reasoning: str = ""


class BackTimingAdvisor:
    """Advises when to recall based on gold and game context.

    Not a TimerComponent — called by PlanningComponent as a sub-module.

    Usage::
        advisor = BackTimingAdvisor()
        rec = advisor.evaluate(snapshot)
        if rec.should_back:
            voice_writer.Write(VoiceCommand(text=rec.reasoning, ...))
    """

    def __init__(self) -> None:
        self._last_recommendation_time: float = 0.0
        self._recommendation_count: int = 0
        self._breakpoints = sorted(_ITEM_BREAKPOINTS, key=lambda x: x[0])

    def evaluate(self, snapshot: GameSnapshot) -> Optional[BackRecommendation]:
        """Evaluate whether the active player should recall now."""
        now = time.time()

        # Cooldown check
        if now - self._last_recommendation_time < _COOLDOWN_S:
            return None

        # Only during laning / mid game
        if snapshot.phase not in (GamePhase.EARLY, GamePhase.MID):
            return None

        player = snapshot.active_player
        if player is None:
            return None

        # Don't suggest back if player is dead
        if player.is_dead:
            return None

        gold = player.current_gold

        # Find the best item we can afford
        best_item: Optional[Tuple[int, str, str]] = None
        for cost, name, category in reversed(self._breakpoints):
            if gold >= cost + _GOLD_BUFFER:
                best_item = (cost, name, category)
                break

        if best_item is None:
            return None

        cost, name, category = best_item

        # Safety: check if enemies are missing or if objective is contested
        # (simplified: suppress if game_time suggests imminent objective)
        game_time = snapshot.game_time
        if self._is_objective_window(game_time):
            return None

        # Check health: low health = more reason to back
        health_ratio = 1.0
        if player.max_health > 0:
            health_ratio = player.current_health / player.max_health

        confidence = 0.6
        reasoning_parts = [f"You have {int(gold)}g"]

        if health_ratio < 0.4:
            confidence += 0.2
            reasoning_parts.append("low health")
        if gold >= cost + 300:
            confidence += 0.1
            reasoning_parts.append(f"enough for {name} plus extras")
        else:
            reasoning_parts.append(f"enough for {name}")

        confidence = min(1.0, confidence)

        text = f"Good time to back. {'. '.join(reasoning_parts)}."

        self._last_recommendation_time = now
        self._recommendation_count += 1

        return BackRecommendation(
            should_back=True,
            item_name=name,
            gold_needed=cost,
            current_gold=gold,
            confidence=confidence,
            reasoning=text,
        )

    @staticmethod
    def _is_objective_window(game_time: float) -> bool:
        """Check if an objective spawn is imminent."""
        # Dragon spawns at 5:00 and every 5:00 after
        # Baron spawns at 20:00 and every 6:00 after
        drake_windows = [300, 600, 900, 1200, 1500, 1800, 2100]
        baron_windows = [1200, 1560, 1920, 2280]
        for w in drake_windows + baron_windows:
            if abs(game_time - w) < 45:
                return True
        return False

    def stats(self) -> Dict[str, Any]:
        return {"recommendations": self._recommendation_count}

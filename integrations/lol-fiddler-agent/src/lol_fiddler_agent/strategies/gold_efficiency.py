"""
Gold Efficiency Calculator - Tracks gold pacing and spending efficiency.

Compares gold income rate against benchmarks and evaluates
item purchase timing to detect gold-sinking opportunities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.network.live_client_data import GamePhase, LiveGameState
from lol_fiddler_agent.agents.strategy_agent import ActionType, StrategicAdvice, StrategyEvaluator, Urgency

logger = logging.getLogger(__name__)

# Gold income benchmarks (gold/min) by role
_GOLD_BENCHMARKS: dict[str, dict[str, float]] = {
    "TOP": {"excellent": 420, "good": 380, "average": 340, "poor": 300},
    "JUNGLE": {"excellent": 380, "good": 340, "average": 300, "poor": 260},
    "MIDDLE": {"excellent": 430, "good": 390, "average": 350, "poor": 310},
    "BOTTOM": {"excellent": 440, "good": 400, "average": 360, "poor": 320},
    "UTILITY": {"excellent": 320, "good": 280, "average": 240, "poor": 200},
}


@dataclass
class GoldAnalysis:
    """Gold efficiency analysis result."""
    gold_per_minute: float = 0.0
    gold_grade: str = "C"
    unspent_gold: float = 0.0
    recall_recommended: bool = False
    purchase_suggestion: str = ""
    benchmarks: dict[str, float] = field(default_factory=dict)


class GoldEfficiencyEvaluator(StrategyEvaluator):
    """Evaluates gold income efficiency and spending patterns."""

    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []
        if not state.active_player or not state.game_data:
            return advice

        game_minutes = max(state.game_data.game_time / 60, 1)
        gold = state.active_player.current_gold

        # Find position
        my_name = state.active_player.summoner_name or state.active_player.riot_id
        my_position = "MIDDLE"  # default
        my_total_gold = 0
        for p in state.all_players:
            if p.summoner_name == my_name or p.riot_id == my_name:
                my_position = p.position if p.position != "UNKNOWN" else "MIDDLE"
                my_total_gold = p.get_total_gold_estimate() + gold
                break

        gold_per_min = my_total_gold / game_minutes
        benchmarks = _GOLD_BENCHMARKS.get(my_position, _GOLD_BENCHMARKS["MIDDLE"])

        # Grade
        if gold_per_min >= benchmarks["excellent"]:
            grade = "S"
        elif gold_per_min >= benchmarks["good"]:
            grade = "A"
        elif gold_per_min >= benchmarks["average"]:
            grade = "B"
        elif gold_per_min >= benchmarks["poor"]:
            grade = "C"
        else:
            grade = "D"

        # Unspent gold advice
        if gold >= 2000 and state.game_data.game_phase != GamePhase.LATE:
            advice.append(StrategicAdvice(
                action=ActionType.RECALL,
                urgency=Urgency.HIGH,
                reason=f"Sitting on {int(gold)}g unspent - recall and buy items",
                confidence=0.80,
            ))
        elif gold >= 1300:
            advice.append(StrategicAdvice(
                action=ActionType.RECALL,
                urgency=Urgency.MEDIUM,
                reason=f"{int(gold)}g available - look for safe recall timing",
                confidence=0.65,
            ))

        # Gold income pace
        if grade in ("D",) and game_minutes > 5:
            advice.append(StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.HIGH,
                reason=f"Gold income low ({gold_per_min:.0f}/min, grade {grade}). Prioritize CS",
                confidence=0.75,
            ))

        return advice

    def analyze(self, state: LiveGameState) -> GoldAnalysis:
        """Get detailed gold analysis."""
        if not state.active_player or not state.game_data:
            return GoldAnalysis()

        game_minutes = max(state.game_data.game_time / 60, 1)
        gold = state.active_player.current_gold

        my_name = state.active_player.summoner_name or state.active_player.riot_id
        my_position = "MIDDLE"
        total_gold = 0
        for p in state.all_players:
            if p.summoner_name == my_name or p.riot_id == my_name:
                my_position = p.position if p.position != "UNKNOWN" else "MIDDLE"
                total_gold = p.get_total_gold_estimate() + gold
                break

        gpm = total_gold / game_minutes
        benchmarks = _GOLD_BENCHMARKS.get(my_position, _GOLD_BENCHMARKS["MIDDLE"])

        if gpm >= benchmarks["excellent"]:
            grade = "S"
        elif gpm >= benchmarks["good"]:
            grade = "A"
        elif gpm >= benchmarks["average"]:
            grade = "B"
        elif gpm >= benchmarks["poor"]:
            grade = "C"
        else:
            grade = "D"

        return GoldAnalysis(
            gold_per_minute=gpm,
            gold_grade=grade,
            unspent_gold=gold,
            recall_recommended=gold >= 1300,
            benchmarks=benchmarks,
        )

"""
Wave Management Evaluator - Minion wave analysis and CS strategy.

Tracks creep score pacing, identifies freeze/push/slow-push opportunities,
and recommends wave manipulation based on game phase and lane state.
"""

from __future__ import annotations

import logging
from typing import Optional

from lol_fiddler_agent.network.live_client_data import (
    GamePhase,
    LiveGameState,
    Position,
    Team,
)
from lol_fiddler_agent.agents.strategy_agent import (
    ActionType,
    StrategicAdvice,
    StrategyEvaluator,
    Urgency,
)

logger = logging.getLogger(__name__)

# Ideal CS benchmarks per minute
_CS_BENCHMARKS = {
    "perfect": 10.0,     # Theoretical max (~12.6 with jungle)
    "excellent": 8.5,
    "good": 7.0,
    "average": 5.5,
    "poor": 4.0,
}


class WaveManagementEvaluator(StrategyEvaluator):
    """Evaluates CS pacing and wave management opportunities.

    Recommendations:
    - Slow push before roaming/recalling
    - Fast push to deny enemy roams
    - Freeze when ahead to zone enemy
    - Match opponent push speed under tower
    """

    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []

        if not state.game_data or not state.active_player:
            return advice

        phase = state.game_data.game_phase
        game_time = state.game_data.game_time
        game_minutes = max(game_time / 60, 0.5)

        # Get my CS
        my_name = state.active_player.summoner_name or state.active_player.riot_id
        my_cs = 0
        my_position = ""
        for p in state.all_players:
            if p.summoner_name == my_name or p.riot_id == my_name:
                my_cs = p.scores.creep_score
                my_position = p.position
                break

        # Junglers have different CS patterns
        if my_position == Position.JUNGLE.value:
            return self._evaluate_jungle_cs(my_cs, game_minutes, state)

        cs_per_min = my_cs / game_minutes

        # CS pacing advice
        cs_advice = self._evaluate_cs_pacing(cs_per_min, game_minutes, phase)
        if cs_advice:
            advice.append(cs_advice)

        # Lane opponent CS comparison
        opponent_advice = self._evaluate_cs_diff(state, my_cs, my_position)
        if opponent_advice:
            advice.append(opponent_advice)

        # Wave state recommendations (phase dependent)
        wave_advice = self._evaluate_wave_state(state, phase, my_position)
        if wave_advice:
            advice.extend(wave_advice)

        return advice

    def _evaluate_cs_pacing(
        self, cs_per_min: float, game_minutes: float, phase: GamePhase,
    ) -> Optional[StrategicAdvice]:
        if game_minutes < 3:
            return None  # Too early for meaningful CS evaluation

        if cs_per_min < _CS_BENCHMARKS["poor"]:
            return StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.HIGH,
                reason=f"CS rate critically low ({cs_per_min:.1f}/min). Focus on last-hitting",
                confidence=0.85,
            )
        elif cs_per_min < _CS_BENCHMARKS["average"]:
            return StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.MEDIUM,
                reason=f"CS rate below average ({cs_per_min:.1f}/min). Prioritize farm over trades",
                confidence=0.70,
            )
        elif cs_per_min >= _CS_BENCHMARKS["excellent"]:
            return StrategicAdvice(
                action=ActionType.TRADE,
                urgency=Urgency.LOW,
                reason=f"Excellent CS ({cs_per_min:.1f}/min). Farm lead enables aggressive plays",
                confidence=0.60,
            )
        return None

    def _evaluate_cs_diff(
        self, state: LiveGameState, my_cs: int, my_position: str,
    ) -> Optional[StrategicAdvice]:
        """Compare CS to lane opponent."""
        my_team = state.get_my_team()
        enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER

        # Find lane opponent
        for p in state.all_players:
            if p.team_enum == enemy_team and p.position == my_position:
                cs_diff = my_cs - p.scores.creep_score
                if cs_diff < -20:
                    return StrategicAdvice(
                        action=ActionType.FARM,
                        urgency=Urgency.HIGH,
                        reason=f"Down {abs(cs_diff)} CS to {p.champion_name}. Focus on catching up",
                        confidence=0.75,
                    )
                elif cs_diff > 30:
                    return StrategicAdvice(
                        action=ActionType.TRADE,
                        urgency=Urgency.LOW,
                        reason=f"Up {cs_diff} CS over {p.champion_name}. Item advantage likely",
                        confidence=0.65,
                    )
                break
        return None

    def _evaluate_wave_state(
        self, state: LiveGameState, phase: GamePhase, position: str,
    ) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []

        if not state.active_player or not state.active_player.champion_stats:
            return advice

        gold = state.active_player.current_gold
        health_pct = state.active_player.champion_stats.health_percent

        # Pre-recall wave management
        if gold >= 1300 and health_pct < 60 and phase == GamePhase.EARLY:
            advice.append(StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.MEDIUM,
                reason="Push wave into enemy tower, then recall with item gold",
                confidence=0.70,
                time_window_seconds=15,
            ))

        # Post-teleport opportunities
        if phase == GamePhase.MID:
            # Check for slow push opportunities toward objectives
            game_time = state.game_data.game_time if state.game_data else 0
            dragon_timer_approx = 5 * 60 - (game_time % (5 * 60))
            if dragon_timer_approx < 60:
                advice.append(StrategicAdvice(
                    action=ActionType.FARM,
                    urgency=Urgency.HIGH,
                    reason="Dragon soon - push bot wave to create pressure",
                    confidence=0.75,
                    target_position="bot_lane",
                    time_window_seconds=30,
                ))

        return advice

    def _evaluate_jungle_cs(
        self, cs: int, game_minutes: float, state: LiveGameState,
    ) -> list[StrategicAdvice]:
        """Evaluate jungler's farm pacing."""
        advice: list[StrategicAdvice] = []
        cs_per_min = cs / game_minutes

        if game_minutes < 3:
            return advice

        # Junglers typically have lower CS/min but should be efficient
        if cs_per_min < 3.5:
            advice.append(StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.MEDIUM,
                reason=f"Jungle CS low ({cs_per_min:.1f}/min). Clear camps between ganks",
                confidence=0.65,
            ))
        elif cs_per_min < 2.5:
            advice.append(StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.HIGH,
                reason=f"Jungle CS critically low ({cs_per_min:.1f}/min). Prioritize clearing",
                confidence=0.80,
            ))

        return advice

    @staticmethod
    def get_cs_grade(cs_per_min: float) -> str:
        """Get a letter grade for CS rate."""
        if cs_per_min >= _CS_BENCHMARKS["excellent"]:
            return "S"
        elif cs_per_min >= _CS_BENCHMARKS["good"]:
            return "A"
        elif cs_per_min >= _CS_BENCHMARKS["average"]:
            return "B"
        elif cs_per_min >= _CS_BENCHMARKS["poor"]:
            return "C"
        return "D"


# ── Evolution Integration (M271 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'wave_management'


class EvolvableWaveManagementEvaluator(WaveManagementEvaluator):
    """WaveManagementEvaluator with self-evolution callback hooks."""

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

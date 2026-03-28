"""
Power Spike Detector - Identifies item and level power spikes.

Tracks when a player hits key power milestones (level 6/11/16,
item completions) and generates strategic advice based on
relative power curves between teams.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.network.live_client_data import LiveGameState, Player, Team
from lol_fiddler_agent.agents.strategy_agent import ActionType, StrategicAdvice, StrategyEvaluator, Urgency

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PowerSpike:
    """A detected power spike event."""
    player_name: str
    champion_name: str
    spike_type: str  # "level", "item", "ability"
    description: str
    significance: float  # 0.0 to 1.0


# Key level spikes
_LEVEL_SPIKES = {
    2: 0.3,   # Level 2 all-in potential
    3: 0.3,   # All basic abilities
    6: 0.8,   # Ultimate
    11: 0.5,  # Rank 2 ult
    16: 0.4,  # Rank 3 ult
}


class PowerSpikeDetector(StrategyEvaluator):
    """Detects power spikes and recommends engagement windows."""

    def __init__(self) -> None:
        self._last_levels: dict[str, int] = {}
        self._last_items: dict[str, int] = {}
        self._recent_spikes: list[PowerSpike] = []

    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []

        if not state.active_player or not state.game_data:
            return advice

        my_team = state.get_my_team()
        new_spikes = self._detect_spikes(state)
        self._recent_spikes.extend(new_spikes)

        # Analyze our spikes
        my_name = state.active_player.summoner_name or state.active_player.riot_id
        for spike in new_spikes:
            if self._is_ally_spike(spike, state, my_team):
                if spike.significance >= 0.7:
                    advice.append(StrategicAdvice(
                        action=ActionType.TRADE,
                        urgency=Urgency.HIGH,
                        reason=f"Power spike: {spike.description} - look to fight!",
                        confidence=spike.significance,
                        time_window_seconds=60,
                    ))
                elif spike.significance >= 0.3:
                    advice.append(StrategicAdvice(
                        action=ActionType.TRADE,
                        urgency=Urgency.MEDIUM,
                        reason=f"Minor spike: {spike.description}",
                        confidence=spike.significance,
                    ))
            else:
                # Enemy spike - play cautious
                if spike.significance >= 0.7:
                    advice.append(StrategicAdvice(
                        action=ActionType.DEFEND,
                        urgency=Urgency.HIGH,
                        reason=f"Enemy {spike.champion_name} power spike: {spike.description} - play safe",
                        confidence=spike.significance,
                        time_window_seconds=60,
                    ))

        # Level advantage analysis
        level_advice = self._evaluate_level_advantage(state)
        if level_advice:
            advice.append(level_advice)

        return advice

    def _detect_spikes(self, state: LiveGameState) -> list[PowerSpike]:
        """Detect new power spikes since last check."""
        spikes: list[PowerSpike] = []

        for player in state.all_players:
            name = player.summoner_name or player.riot_id

            # Level spikes
            old_level = self._last_levels.get(name, 0)
            if player.level > old_level:
                self._last_levels[name] = player.level
                sig = _LEVEL_SPIKES.get(player.level, 0.0)
                if sig > 0:
                    spikes.append(PowerSpike(
                        player_name=name,
                        champion_name=player.champion_name,
                        spike_type="level",
                        description=f"{player.champion_name} hit level {player.level}",
                        significance=sig,
                    ))

            # Item spikes
            completed = player.get_completed_items_count()
            old_items = self._last_items.get(name, 0)
            if completed > old_items:
                self._last_items[name] = completed
                # First and second completed items are biggest spikes
                sig = 0.6 if completed <= 2 else 0.3
                spikes.append(PowerSpike(
                    player_name=name,
                    champion_name=player.champion_name,
                    spike_type="item",
                    description=f"{player.champion_name} completed item #{completed}",
                    significance=sig,
                ))

        return spikes

    def _is_ally_spike(self, spike: PowerSpike, state: LiveGameState, my_team: Team) -> bool:
        for p in state.all_players:
            if (p.summoner_name == spike.player_name or p.riot_id == spike.player_name):
                return p.team_enum == my_team
        return False

    def _evaluate_level_advantage(self, state: LiveGameState) -> Optional[StrategicAdvice]:
        """Check for team-wide level advantage."""
        if not state.active_player:
            return None

        my_team = state.get_my_team()
        enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER

        ally_levels = [p.level for p in state.all_players if p.team_enum == my_team]
        enemy_levels = [p.level for p in state.all_players if p.team_enum == enemy_team]

        if not ally_levels or not enemy_levels:
            return None

        avg_diff = (sum(ally_levels) / len(ally_levels)) - (sum(enemy_levels) / len(enemy_levels))

        if avg_diff >= 2.0:
            return StrategicAdvice(
                action=ActionType.ALL_IN,
                urgency=Urgency.MEDIUM,
                reason=f"Team level advantage (+{avg_diff:.1f} avg) - force fights",
                confidence=0.70,
            )
        elif avg_diff <= -2.0:
            return StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.MEDIUM,
                reason=f"Team level deficit ({avg_diff:.1f} avg) - avoid fights, farm up",
                confidence=0.70,
            )
        return None

    def get_recent_spikes(self, limit: int = 10) -> list[PowerSpike]:
        return self._recent_spikes[-limit:]

    def reset(self) -> None:
        self._last_levels.clear()
        self._last_items.clear()
        self._recent_spikes.clear()


# ── Evolution Integration (M266 — appended, 不增不删原有函数) ─────────────
_EVOLUTION_KEY = 'power_spike'


class EvolvablePowerSpikeDetector(PowerSpikeDetector):
    """PowerSpikeDetector with self-evolution callback hooks.

    Wraps the original detector to emit training annotations
    on every evaluation cycle, feeding the AgentLightning loop.
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

    def to_training_annotation(self, **kwargs) -> dict:
        annotation = {'module': _EVOLUTION_KEY}
        annotation.update(kwargs)
        return annotation

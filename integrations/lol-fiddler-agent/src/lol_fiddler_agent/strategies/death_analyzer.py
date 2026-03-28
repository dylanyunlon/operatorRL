"""
Death Analyzer - Analyzes death patterns for post-game coaching.

Tracks when, where, and why deaths occurred to provide
constructive feedback on positioning and decision-making.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from lol_fiddler_agent.network.live_client_data import GameEvent, GamePhase, LiveGameState

logger = logging.getLogger(__name__)


@dataclass
class DeathRecord:
    """Record of a player death with context."""
    game_time: float
    game_phase: str
    killer_champion: str = ""
    assisters: list[str] = field(default_factory=list)
    my_health_before: float = 100.0
    my_level: int = 1
    enemy_count_nearby: int = 1
    gold_at_death: float = 0.0
    was_overextended: bool = False
    near_objective: bool = False
    context: str = ""

    @property
    def was_avoidable(self) -> bool:
        """Heuristic: death was likely avoidable if outnumbered or low health."""
        return self.enemy_count_nearby >= 2 or self.my_health_before < 30

    @property
    def death_type(self) -> str:
        if self.enemy_count_nearby >= 3:
            return "caught_out"
        elif self.was_overextended:
            return "overextended"
        elif len(self.assisters) >= 2:
            return "ganked"
        elif self.near_objective:
            return "objective_contest"
        return "1v1"


@dataclass
class DeathAnalysis:
    """Aggregated death analysis for a game session."""
    total_deaths: int = 0
    avoidable_deaths: int = 0
    death_types: dict[str, int] = field(default_factory=dict)
    deaths_by_phase: dict[str, int] = field(default_factory=dict)
    avg_gold_at_death: float = 0.0
    worst_death_streak: int = 0
    coaching_tips: list[str] = field(default_factory=list)

    @property
    def avoidable_rate(self) -> float:
        if self.total_deaths == 0:
            return 0.0
        return self.avoidable_deaths / self.total_deaths


class DeathAnalyzerEngine:
    """Analyzes death patterns to generate coaching advice.

    Example::

        analyzer = DeathAnalyzerEngine()
        analyzer.record_death(death_record)
        analysis = analyzer.analyze()
        for tip in analysis.coaching_tips:
            print(tip)
    """

    def __init__(self) -> None:
        self._deaths: list[DeathRecord] = []
        self._current_streak = 0
        self._max_streak = 0

    def record_death(self, record: DeathRecord) -> None:
        self._deaths.append(record)
        self._current_streak += 1
        self._max_streak = max(self._max_streak, self._current_streak)

    def record_kill_or_assist(self) -> None:
        """Reset death streak on kill/assist."""
        self._current_streak = 0

    def record_from_event(
        self, event: GameEvent, state: LiveGameState,
    ) -> Optional[DeathRecord]:
        """Create a death record from a game event."""
        if not event.is_kill_event():
            return None

        my_name = ""
        if state.active_player:
            my_name = state.active_player.summoner_name or state.active_player.riot_id

        if event.victim_name != my_name:
            return None

        phase = state.game_data.game_phase.value if state.game_data else "early"
        health = 0.0
        if state.active_player and state.active_player.champion_stats:
            health = state.active_player.champion_stats.health_percent
        gold = state.active_player.current_gold if state.active_player else 0

        record = DeathRecord(
            game_time=event.event_time,
            game_phase=phase,
            killer_champion=event.killer_name or "",
            assisters=list(event.assisters),
            my_health_before=health,
            my_level=state.active_player.level if state.active_player else 1,
            enemy_count_nearby=1 + len(event.assisters),
            gold_at_death=gold,
        )
        self.record_death(record)
        return record

    def analyze(self) -> DeathAnalysis:
        """Generate comprehensive death analysis."""
        if not self._deaths:
            return DeathAnalysis(coaching_tips=["No deaths recorded - excellent!"])

        analysis = DeathAnalysis(
            total_deaths=len(self._deaths),
            worst_death_streak=self._max_streak,
        )

        # Classify deaths
        for death in self._deaths:
            dtype = death.death_type
            analysis.death_types[dtype] = analysis.death_types.get(dtype, 0) + 1
            analysis.deaths_by_phase[death.game_phase] = (
                analysis.deaths_by_phase.get(death.game_phase, 0) + 1
            )
            if death.was_avoidable:
                analysis.avoidable_deaths += 1

        # Average gold lost
        analysis.avg_gold_at_death = sum(d.gold_at_death for d in self._deaths) / len(self._deaths)

        # Generate coaching tips
        tips = self._generate_tips(analysis)
        analysis.coaching_tips = tips

        return analysis

    def _generate_tips(self, analysis: DeathAnalysis) -> list[str]:
        tips: list[str] = []

        if analysis.avoidable_rate > 0.5:
            tips.append(
                f"{analysis.avoidable_deaths}/{analysis.total_deaths} deaths were avoidable. "
                "Focus on map awareness and backing off when outnumbered."
            )

        caught = analysis.death_types.get("caught_out", 0)
        if caught >= 2:
            tips.append(
                f"Caught out {caught} times. Use wards and stay near teammates in mid-late game."
            )

        ganked = analysis.death_types.get("ganked", 0)
        if ganked >= 2:
            tips.append(
                f"Ganked {ganked} times. Track enemy jungler and ward river bush more frequently."
            )

        overextended = analysis.death_types.get("overextended", 0)
        if overextended >= 2:
            tips.append(
                f"Overextended {overextended} times. Respect enemy fog of war."
            )

        early_deaths = analysis.deaths_by_phase.get("early", 0)
        if early_deaths >= 3:
            tips.append(
                f"{early_deaths} deaths in early game. Play safer in lane phase."
            )

        if analysis.avg_gold_at_death > 1000:
            tips.append(
                f"Average gold at death: {analysis.avg_gold_at_death:.0f}g. "
                "Spend gold before risky plays."
            )

        if analysis.worst_death_streak >= 3:
            tips.append(
                f"Worst death streak: {analysis.worst_death_streak}. "
                "After 2 deaths, play ultra-safe and focus only on CS."
            )

        if not tips:
            tips.append("Deaths were mostly unavoidable. Good job overall!")

        return tips

    @property
    def death_count(self) -> int:
        return len(self._deaths)

    def reset(self) -> None:
        self._deaths.clear()
        self._current_streak = 0
        self._max_streak = 0

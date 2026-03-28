"""
Map Awareness Evaluator - Vision control and fog-of-war strategy.

Tracks ward placement opportunities, enemy jungler predictions,
and safe zones based on available vision information from the
Live Client API events feed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

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


class MapRegion(str, Enum):
    TOP_LANE = "top_lane"
    MID_LANE = "mid_lane"
    BOT_LANE = "bot_lane"
    TOP_RIVER = "top_river"
    BOT_RIVER = "bot_river"
    TOP_JUNGLE_ALLY = "top_jungle_ally"
    BOT_JUNGLE_ALLY = "bot_jungle_ally"
    TOP_JUNGLE_ENEMY = "top_jungle_enemy"
    BOT_JUNGLE_ENEMY = "bot_jungle_enemy"
    DRAGON_PIT = "dragon_pit"
    BARON_PIT = "baron_pit"
    BASE_ALLY = "base_ally"
    BASE_ENEMY = "base_enemy"


@dataclass
class WardSuggestion:
    """Recommended ward placement."""
    region: MapRegion
    reason: str
    priority: int = 5  # 1-10, higher = more important
    game_phase: GamePhase = GamePhase.EARLY


@dataclass
class JunglerPrediction:
    """Predicted enemy jungler location based on game state."""
    most_likely_region: MapRegion
    confidence: float = 0.5
    reasoning: str = ""
    last_seen_time: float = 0.0


class MapAwarenessEvaluator(StrategyEvaluator):
    """Evaluates map awareness and vision strategy.

    Uses event data to track:
    - Ward placement opportunities
    - Enemy jungler pathing predictions
    - Safe/danger zones per game phase
    - Roaming windows
    """

    # Standard jungle clear times (seconds)
    FIRST_CLEAR_TIME = 3 * 60 + 15  # ~3:15
    CAMP_RESPAWN = 2 * 60  # 2 minutes
    SCUTTLE_SPAWN = 3 * 60 + 30  # 3:30

    def __init__(self) -> None:
        self._last_ward_reminder: float = 0.0
        self._ward_reminder_cooldown: float = 60.0  # 1 min between ward reminders

    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []

        if not state.game_data:
            return advice

        game_time = state.game_data.game_time
        phase = state.game_data.game_phase

        # Ward suggestions based on game phase
        ward_advice = self._evaluate_warding(state, game_time, phase)
        if ward_advice:
            advice.extend(ward_advice)

        # Jungler tracking
        jungler_advice = self._evaluate_jungler_threat(state, game_time)
        if jungler_advice:
            advice.extend(jungler_advice)

        # Roaming opportunities
        roam_advice = self._evaluate_roam_opportunity(state, game_time, phase)
        if roam_advice:
            advice.extend(roam_advice)

        return advice

    def _evaluate_warding(
        self, state: LiveGameState, game_time: float, phase: GamePhase,
    ) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []
        now = time.time()

        # Cooldown check
        if now - self._last_ward_reminder < self._ward_reminder_cooldown:
            return advice

        # Find active player's ward score
        my_name = ""
        if state.active_player:
            my_name = state.active_player.summoner_name or state.active_player.riot_id

        ward_score = 0.0
        for p in state.all_players:
            if p.summoner_name == my_name or p.riot_id == my_name:
                ward_score = p.scores.ward_score
                break

        # Expected ward score per minute
        game_minutes = max(game_time / 60, 1)
        expected_wards_per_min = 0.5 if phase == GamePhase.EARLY else 0.8
        expected = expected_wards_per_min * game_minutes

        if ward_score < expected * 0.5:
            advice.append(StrategicAdvice(
                action=ActionType.DEFEND,
                urgency=Urgency.MEDIUM,
                reason=f"Ward score ({ward_score:.0f}) is low for {game_minutes:.0f}min - place more wards",
                confidence=0.70,
            ))
            self._last_ward_reminder = now

        # Key ward timings
        if abs(game_time - self.SCUTTLE_SPAWN) < 30:
            advice.append(StrategicAdvice(
                action=ActionType.OBJECTIVE,
                urgency=Urgency.HIGH,
                reason="Scuttle crab spawning - ward river and contest",
                confidence=0.75,
                target_position="river",
                time_window_seconds=30,
            ))

        return advice

    def _evaluate_jungler_threat(
        self, state: LiveGameState, game_time: float,
    ) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []
        my_team = state.get_my_team()
        enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER

        # Find enemy jungler
        enemy_jg = state.get_player_by_position(Position.JUNGLE, enemy_team)
        if not enemy_jg:
            return advice

        # Check if enemy jungler is dead
        if enemy_jg.is_dead:
            advice.append(StrategicAdvice(
                action=ActionType.TRADE,
                urgency=Urgency.MEDIUM,
                reason=f"Enemy jungler ({enemy_jg.champion_name}) is dead - safe to play aggressive",
                confidence=0.80,
                time_window_seconds=enemy_jg.respawn_timer,
            ))
            return advice

        # Predict jungler location based on game time
        prediction = self._predict_jungler_location(game_time, enemy_jg.level)

        if prediction.confidence > 0.6:
            # Warn if jungler likely near your lane
            my_position = ""
            for p in state.all_players:
                if p.summoner_name == (state.active_player.summoner_name if state.active_player else ""):
                    my_position = p.position
                    break

            if self._regions_are_close(prediction.most_likely_region, my_position):
                advice.append(StrategicAdvice(
                    action=ActionType.DEFEND,
                    urgency=Urgency.HIGH,
                    reason=f"Enemy jungler likely near {prediction.most_likely_region.value} - play safe",
                    confidence=prediction.confidence,
                    time_window_seconds=20,
                ))

        return advice

    def _evaluate_roam_opportunity(
        self, state: LiveGameState, game_time: float, phase: GamePhase,
    ) -> list[StrategicAdvice]:
        advice: list[StrategicAdvice] = []

        if phase != GamePhase.EARLY:
            return advice

        # Only mid laners should get roam advice
        if not state.active_player:
            return advice

        my_name = state.active_player.summoner_name or state.active_player.riot_id
        my_position = ""
        for p in state.all_players:
            if p.summoner_name == my_name or p.riot_id == my_name:
                my_position = p.position
                break

        if my_position != Position.MIDDLE.value:
            return advice

        # Check if we have health/mana for roaming
        stats = state.active_player.champion_stats
        if not stats:
            return advice

        if stats.health_percent > 60 and stats.resource_percent > 40:
            # Check for low-health enemies in side lanes
            my_team = state.get_my_team()
            enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER
            enemies = state.get_enemies()

            for enemy in enemies:
                if enemy.position in (Position.TOP.value, Position.BOTTOM.value):
                    # We can't see enemy health directly, but if they have deaths
                    if enemy.scores.deaths >= 2 and enemy.level < state.active_player.level:
                        advice.append(StrategicAdvice(
                            action=ActionType.ROAM,
                            urgency=Urgency.MEDIUM,
                            reason=f"Good roam window to {enemy.position} - enemy {enemy.champion_name} is behind",
                            confidence=0.65,
                            target_position=enemy.position.lower(),
                        ))
                        break

        return advice

    def _predict_jungler_location(
        self, game_time: float, jungler_level: int,
    ) -> JunglerPrediction:
        """Predict enemy jungler location based on game time."""
        minutes = game_time / 60

        if minutes < 1.5:
            return JunglerPrediction(
                most_likely_region=MapRegion.BOT_JUNGLE_ENEMY,
                confidence=0.7,
                reasoning="Start of game, likely clearing bot-side",
            )
        elif minutes < 3.5:
            return JunglerPrediction(
                most_likely_region=MapRegion.TOP_JUNGLE_ENEMY,
                confidence=0.5,
                reasoning="Mid clear, transitioning to topside",
            )
        elif minutes < 4.0:
            return JunglerPrediction(
                most_likely_region=MapRegion.TOP_RIVER,
                confidence=0.6,
                reasoning="Scuttle/gank timing, likely river or ganking",
            )
        else:
            # After first clear, prediction becomes harder
            return JunglerPrediction(
                most_likely_region=MapRegion.BOT_RIVER,
                confidence=0.3,
                reasoning="Post-first-clear, location uncertain",
            )

    @staticmethod
    def _regions_are_close(region: MapRegion, position: str) -> bool:
        """Check if a map region is close to a lane position."""
        proximity: dict[str, set[MapRegion]] = {
            "TOP": {MapRegion.TOP_LANE, MapRegion.TOP_RIVER, MapRegion.TOP_JUNGLE_ENEMY},
            "MIDDLE": {MapRegion.TOP_RIVER, MapRegion.BOT_RIVER, MapRegion.MID_LANE},
            "BOTTOM": {MapRegion.BOT_LANE, MapRegion.BOT_RIVER, MapRegion.BOT_JUNGLE_ENEMY, MapRegion.DRAGON_PIT},
            "JUNGLE": set(),
            "UTILITY": {MapRegion.BOT_LANE, MapRegion.BOT_RIVER, MapRegion.DRAGON_PIT},
        }
        return region in proximity.get(position, set())

    def get_ward_suggestions(self, phase: GamePhase) -> list[WardSuggestion]:
        """Get generic ward placement suggestions per phase."""
        if phase == GamePhase.EARLY:
            return [
                WardSuggestion(MapRegion.BOT_RIVER, "River bush for dragon/jungle vision", 8),
                WardSuggestion(MapRegion.TOP_RIVER, "Pixel bush for mid/top junction", 7),
            ]
        elif phase == GamePhase.MID:
            return [
                WardSuggestion(MapRegion.BARON_PIT, "Baron pit control", 10, GamePhase.MID),
                WardSuggestion(MapRegion.DRAGON_PIT, "Dragon pit control", 9, GamePhase.MID),
                WardSuggestion(MapRegion.TOP_JUNGLE_ENEMY, "Deep ward for enemy pathing", 6, GamePhase.MID),
            ]
        else:
            return [
                WardSuggestion(MapRegion.BARON_PIT, "Baron pit - critical objective", 10, GamePhase.LATE),
                WardSuggestion(MapRegion.BASE_ENEMY, "Enemy base approach", 8, GamePhase.LATE),
            ]

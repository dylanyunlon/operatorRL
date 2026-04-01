"""
EventDetector — Advanced in-game event detection and classification.
=====================================================================

Analyzes sequences of GameEvents and GameSnapshots to detect
higher-order patterns: teamfights, power spikes, objective contests,
and momentum shifts.  These patterns feed the prediction and planning
modules with richer context than raw kill/death events.

Architecture position:
    modules/perception/events/event_detector.py   ← YOU ARE HERE
    ├─ Consumed by: perception_component.py (integrated into Proc)
    ├─ Input: GameSnapshot sequence
    ├─ Output: DetectedPattern objects
    └─ Referenced by: prediction (teamfight prediction), planning

Apollo reference:
    modules/perception/traffic_light_detection/ — pattern recognition
    modules/perception/multi_sensor_fusion/     — temporal fusion

Design notes:
    - Sliding window analysis over recent events
    - Teamfight detection: 3+ kills within 15s in proximity
    - Power spike: level 6/11/16, item completion
    - Objective contest: multiple teams near objective within window
    - Momentum: kill streak, gold swing detection
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Tuple

from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    GameSnapshot,
    PlayerState,
    TeamSide,
)
from cyber.logger.cyber_logger import get_logger

logger = get_logger("perception.events")

# ─── Constants ───────────────────────────────────────────────────────────────

_TEAMFIGHT_WINDOW_S = 15.0       # kills within 15s = potential teamfight
_TEAMFIGHT_MIN_PARTICIPANTS = 3  # at least 3 kills to classify as teamfight
_MOMENTUM_WINDOW_S = 60.0        # 1 minute window for momentum analysis
_POWER_SPIKE_LEVELS = {6, 11, 16}  # Ultimate ranks
_GOLD_SWING_THRESHOLD = 2000.0   # Gold swing > 2k in window = momentum shift
_EVENT_HISTORY_MAX = 500
_SNAPSHOT_HISTORY_MAX = 100


class PatternType(Enum):
    """Types of detected patterns."""
    TEAMFIGHT = auto()
    SKIRMISH = auto()          # 2-3 person fight
    PICK = auto()              # solo kill / caught out
    POWER_SPIKE = auto()       # level/item spike
    OBJECTIVE_CONTEST = auto() # fight near objective
    MOMENTUM_SHIFT = auto()    # significant gold/kill swing
    ACE = auto()               # team wipe
    BARON_POWER_PLAY = auto()  # baron kill → push
    DRAGON_SOUL_POINT = auto() # 3rd dragon (soul point)
    DEATH_TIMER_WINDOW = auto() # enemy death timer > 30s


@dataclass
class DetectedPattern:
    """A detected high-level game pattern.

    Attributes:
        pattern_type: Classification of the pattern.
        game_time: When the pattern was detected.
        confidence: 0-1 confidence score.
        description: Human-readable description.
        participants: Players involved.
        team_advantage: Which team benefits (BLUE/RED/UNKNOWN).
        details: Additional pattern-specific data.
    """
    pattern_type: PatternType
    game_time: float
    confidence: float = 0.0
    description: str = ""
    participants: Tuple[str, ...] = ()
    team_advantage: TeamSide = TeamSide.UNKNOWN
    details: Dict[str, Any] = field(default_factory=dict)
    detected_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.pattern_type.name,
            "game_time": self.game_time,
            "confidence": round(self.confidence, 3),
            "description": self.description,
            "participants": list(self.participants),
            "team_advantage": self.team_advantage.name,
            "details": self.details,
        }


class EventDetector:
    """Detects high-level game patterns from event streams.

    Maintains a sliding window of recent events and snapshots to
    identify teamfights, power spikes, objective contests, and
    momentum shifts.

    Usage::

        detector = EventDetector()
        # Called each perception tick:
        patterns = detector.analyze(snapshot, new_events)
        for p in patterns:
            logger.info("Detected: %s", p.description)
    """

    def __init__(self) -> None:
        self._event_history: Deque[GameEvent] = deque(maxlen=_EVENT_HISTORY_MAX)
        self._snapshot_history: Deque[GameSnapshot] = deque(
            maxlen=_SNAPSHOT_HISTORY_MAX
        )
        self._detected_patterns: List[DetectedPattern] = []
        self._last_teamfight_time: float = -999.0
        self._last_momentum_check_time: float = 0.0
        self._player_levels: Dict[str, int] = {}
        self._dragon_counts: Dict[str, int] = {
            TeamSide.BLUE.name: 0,
            TeamSide.RED.name: 0,
        }

    def analyze(
        self,
        snapshot: GameSnapshot,
        new_events: List[GameEvent],
    ) -> List[DetectedPattern]:
        """Analyze the current game state and new events for patterns.

        Args:
            snapshot: Current game snapshot.
            new_events: Events detected this tick.

        Returns:
            List of newly detected patterns.
        """
        # Update history
        self._snapshot_history.append(snapshot)
        for evt in new_events:
            self._event_history.append(evt)

        patterns: List[DetectedPattern] = []

        # ── Detect teamfights ────────────────────────────────────────
        tf_pattern = self._detect_teamfight(snapshot)
        if tf_pattern is not None:
            patterns.append(tf_pattern)

        # ── Detect power spikes ──────────────────────────────────────
        spike_patterns = self._detect_power_spikes(snapshot, new_events)
        patterns.extend(spike_patterns)

        # ── Detect objective events ──────────────────────────────────
        obj_patterns = self._detect_objective_events(new_events, snapshot)
        patterns.extend(obj_patterns)

        # ── Detect momentum shifts ───────────────────────────────────
        momentum = self._detect_momentum_shift(snapshot)
        if momentum is not None:
            patterns.append(momentum)

        # ── Detect death timer windows ───────────────────────────────
        dt_pattern = self._detect_death_timer_window(snapshot)
        if dt_pattern is not None:
            patterns.append(dt_pattern)

        # ── Detect ace ───────────────────────────────────────────────
        for evt in new_events:
            if evt.event_type == EventType.ACE:
                patterns.append(DetectedPattern(
                    pattern_type=PatternType.ACE,
                    game_time=evt.game_time,
                    confidence=1.0,
                    description=f"ACE by {evt.killer}'s team!",
                    team_advantage=TeamSide.UNKNOWN,
                ))

        self._detected_patterns.extend(patterns)
        return patterns

    # ─── Teamfight Detection ─────────────────────────────────────────

    def _detect_teamfight(
        self, snapshot: GameSnapshot
    ) -> Optional[DetectedPattern]:
        """Detect teamfights from clustered kill events.

        A teamfight is defined as >= 3 champion kills within a 15s window.
        Skirmishes are 2 kills in the same window.
        """
        current_time = snapshot.game_time
        if current_time - self._last_teamfight_time < 10.0:
            return None  # debounce

        # Get recent kills within window
        recent_kills = [
            evt for evt in self._event_history
            if (evt.event_type == EventType.CHAMPION_KILL
                and current_time - evt.game_time <= _TEAMFIGHT_WINDOW_S)
        ]

        if len(recent_kills) < 2:
            return None

        participants = set()
        for kill in recent_kills:
            participants.add(kill.killer)
            participants.add(kill.victim)
            participants.update(kill.assisters)
        participants.discard("")

        # Count kills per side
        blue_kills = sum(
            1 for k in recent_kills
            if self._player_team(k.killer, snapshot) == TeamSide.BLUE
        )
        red_kills = sum(
            1 for k in recent_kills
            if self._player_team(k.killer, snapshot) == TeamSide.RED
        )

        if len(recent_kills) >= _TEAMFIGHT_MIN_PARTICIPANTS:
            self._last_teamfight_time = current_time
            advantage = TeamSide.BLUE if blue_kills > red_kills else (
                TeamSide.RED if red_kills > blue_kills else TeamSide.UNKNOWN
            )
            return DetectedPattern(
                pattern_type=PatternType.TEAMFIGHT,
                game_time=current_time,
                confidence=min(1.0, len(recent_kills) / 5.0),
                description=(
                    f"Teamfight detected: {len(recent_kills)} kills in "
                    f"{_TEAMFIGHT_WINDOW_S}s ({blue_kills}B vs {red_kills}R)"
                ),
                participants=tuple(sorted(participants)),
                team_advantage=advantage,
                details={
                    "kill_count": len(recent_kills),
                    "blue_kills": blue_kills,
                    "red_kills": red_kills,
                    "participant_count": len(participants),
                },
            )
        elif len(recent_kills) == 2:
            return DetectedPattern(
                pattern_type=PatternType.SKIRMISH,
                game_time=current_time,
                confidence=0.5,
                description=f"Skirmish: 2 kills in {_TEAMFIGHT_WINDOW_S}s",
                participants=tuple(sorted(participants)),
                team_advantage=TeamSide.BLUE if blue_kills > red_kills
                               else TeamSide.RED,
            )

        return None

    # ─── Power Spike Detection ───────────────────────────────────────

    def _detect_power_spikes(
        self,
        snapshot: GameSnapshot,
        new_events: List[GameEvent],
    ) -> List[DetectedPattern]:
        """Detect level-up power spikes (6/11/16 for ultimate ranks)."""
        patterns: List[DetectedPattern] = []

        for player in snapshot.all_players:
            prev_level = self._player_levels.get(player.summoner_name, 1)
            curr_level = player.level

            if curr_level != prev_level:
                self._player_levels[player.summoner_name] = curr_level

                # Check for key level milestones
                crossed_spikes = _POWER_SPIKE_LEVELS & set(
                    range(prev_level + 1, curr_level + 1)
                )
                if crossed_spikes:
                    spike_level = max(crossed_spikes)
                    is_active = player.is_active_player
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.POWER_SPIKE,
                        game_time=snapshot.game_time,
                        confidence=0.9 if spike_level == 6 else 0.7,
                        description=(
                            f"{'YOU' if is_active else player.champion_name} "
                            f"hit level {spike_level} "
                            f"({'ultimate upgrade!' if spike_level in (6, 11, 16) else 'power spike'})"
                        ),
                        participants=(player.summoner_name,),
                        team_advantage=player.team,
                        details={
                            "champion": player.champion_name,
                            "level": spike_level,
                            "is_active_player": is_active,
                        },
                    ))

        return patterns

    # ─── Objective Detection ─────────────────────────────────────────

    def _detect_objective_events(
        self,
        new_events: List[GameEvent],
        snapshot: GameSnapshot,
    ) -> List[DetectedPattern]:
        """Detect objective kills and derived patterns."""
        patterns: List[DetectedPattern] = []

        for evt in new_events:
            if evt.event_type == EventType.DRAGON_KILL:
                killer_team = self._player_team(evt.killer, snapshot)
                team_key = killer_team.name

                self._dragon_counts[team_key] = self._dragon_counts.get(
                    team_key, 0
                ) + 1
                dragon_count = self._dragon_counts[team_key]

                if dragon_count == 3:
                    patterns.append(DetectedPattern(
                        pattern_type=PatternType.DRAGON_SOUL_POINT,
                        game_time=evt.game_time,
                        confidence=1.0,
                        description=f"{team_key} team at SOUL POINT (3 dragons)!",
                        team_advantage=killer_team,
                        details={"dragon_count": dragon_count},
                    ))

            elif evt.event_type == EventType.BARON_KILL:
                killer_team = self._player_team(evt.killer, snapshot)
                patterns.append(DetectedPattern(
                    pattern_type=PatternType.BARON_POWER_PLAY,
                    game_time=evt.game_time,
                    confidence=1.0,
                    description=f"BARON taken by {killer_team.name}! Power play window.",
                    team_advantage=killer_team,
                    details={"baron_buff_duration": 180.0},
                ))

        return patterns

    # ─── Momentum Shift Detection ────────────────────────────────────

    def _detect_momentum_shift(
        self, snapshot: GameSnapshot
    ) -> Optional[DetectedPattern]:
        """Detect momentum shifts via gold swing analysis."""
        if snapshot.game_time - self._last_momentum_check_time < 30.0:
            return None
        self._last_momentum_check_time = snapshot.game_time

        if len(self._snapshot_history) < 10:
            return None

        # Compare gold diff now vs 60s ago
        old_snapshots = [
            s for s in self._snapshot_history
            if snapshot.game_time - s.game_time >= 50.0
            and snapshot.game_time - s.game_time <= 70.0
        ]
        if not old_snapshots:
            return None

        old = old_snapshots[-1]
        gold_swing = snapshot.gold_diff - old.gold_diff

        if abs(gold_swing) >= _GOLD_SWING_THRESHOLD:
            swinging_to = TeamSide.BLUE if gold_swing > 0 else TeamSide.RED
            return DetectedPattern(
                pattern_type=PatternType.MOMENTUM_SHIFT,
                game_time=snapshot.game_time,
                confidence=min(1.0, abs(gold_swing) / 5000.0),
                description=(
                    f"Momentum shift! {swinging_to.name} gained "
                    f"{abs(gold_swing):.0f}g in last 60s"
                ),
                team_advantage=swinging_to,
                details={
                    "gold_swing": gold_swing,
                    "old_gold_diff": old.gold_diff,
                    "new_gold_diff": snapshot.gold_diff,
                },
            )

        return None

    # ─── Death Timer Window ──────────────────────────────────────────

    def _detect_death_timer_window(
        self, snapshot: GameSnapshot
    ) -> Optional[DetectedPattern]:
        """Detect when enemy has long death timers (opportunity window)."""
        if snapshot.active_player is None:
            return None

        enemy = snapshot.enemy_team
        long_deaths = [
            p for p in enemy.players
            if p.is_dead and p.respawn_timer > 30.0
        ]

        if len(long_deaths) >= 2:
            return DetectedPattern(
                pattern_type=PatternType.DEATH_TIMER_WINDOW,
                game_time=snapshot.game_time,
                confidence=min(1.0, len(long_deaths) / 3.0),
                description=(
                    f"{len(long_deaths)} enemies dead with >30s timers. "
                    f"Objective window!"
                ),
                team_advantage=snapshot.active_team,
                details={
                    "dead_enemies": [
                        {
                            "champion": p.champion_name,
                            "respawn_timer": round(p.respawn_timer, 1),
                        }
                        for p in long_deaths
                    ],
                },
            )

        return None

    # ─── Helpers ─────────────────────────────────────────────────────

    def _player_team(self, name: str, snapshot: GameSnapshot) -> TeamSide:
        """Resolve a player name to their team side."""
        for p in snapshot.all_players:
            if p.summoner_name == name:
                return p.team
        return TeamSide.UNKNOWN

    # ─── Introspection ───────────────────────────────────────────────

    @property
    def pattern_count(self) -> int:
        return len(self._detected_patterns)

    @property
    def recent_patterns(self) -> List[DetectedPattern]:
        return self._detected_patterns[-20:]

    def summary(self) -> Dict[str, Any]:
        by_type: Dict[str, int] = {}
        for p in self._detected_patterns:
            key = p.pattern_type.name
            by_type[key] = by_type.get(key, 0) + 1
        return {
            "total_patterns": len(self._detected_patterns),
            "event_history_size": len(self._event_history),
            "by_type": by_type,
        }

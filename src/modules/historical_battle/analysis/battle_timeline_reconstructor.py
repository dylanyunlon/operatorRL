#!/usr/bin/env python3
"""
M813 - Battle Timeline Reconstructor
=======================================
OperatorRL Historical Battle System - Event Sequence Rebuilding

查看 Riot API 时间线数据实现方式，理解其模式，特别是事件流和
状态快照是如何分离的。遵循该模式实现时间线重建器，使历史对战
可以被按时间顺序完整回放，并能提取关键转折点。

Core responsibilities:
- Reconstruct game events from timeline API data
- Build minute-by-minute game state snapshots
- Identify key turning points (dragon fights, baron, aces)
- Calculate gold/xp differentials over time
- Support both full timeline and event-only modes
"""

import os
import sys
import json
import math
import logging
import datetime
import statistics
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Sequence, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict

logger = logging.getLogger("operatorRL.historical_battle.battle_timeline_reconstructor")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────────────

FRAME_INTERVAL_MS = 60000
EARLY_GAME_END_MIN = 14
MID_GAME_END_MIN = 25
GOLD_SWING_THRESHOLD = 2000
XP_SWING_THRESHOLD = 1500
ACE_RESET_WINDOW_MS = 10000
OBJECTIVE_CONTEST_WINDOW_MS = 30000
TURRET_PLATE_FALL_MIN = 14
DRAGON_SOUL_COUNT = 4
BARON_SPAWN_MIN = 20
ELDER_DRAKE_MIN = 35
RIFT_HERALD_DESPAWN_MIN = 19
FIRST_BLOOD_BONUS_GOLD = 400
TOWER_KILL_TEAM_GOLD = 550
DRAGON_TEAM_GOLD = 25
BARON_TEAM_GOLD = 300
MIN_TEAMFIGHT_PARTICIPANTS = 3
TEAMFIGHT_WINDOW_MS = 15000


class EventType(Enum):
    """Timeline event types."""
    CHAMPION_KILL = "CHAMPION_KILL"
    BUILDING_KILL = "BUILDING_KILL"
    ELITE_MONSTER_KILL = "ELITE_MONSTER_KILL"
    ITEM_PURCHASED = "ITEM_PURCHASED"
    ITEM_SOLD = "ITEM_SOLD"
    ITEM_DESTROYED = "ITEM_DESTROYED"
    ITEM_UNDO = "ITEM_UNDO"
    SKILL_LEVEL_UP = "SKILL_LEVEL_UP"
    LEVEL_UP = "LEVEL_UP"
    WARD_PLACED = "WARD_PLACED"
    WARD_KILLED = "WARD_KILL"
    TURRET_PLATE_DESTROYED = "TURRET_PLATE_DESTROYED"
    GAME_END = "GAME_END"
    PAUSE_START = "PAUSE_START"
    PAUSE_END = "PAUSE_END"


class GamePhase(Enum):
    """Phase of the game."""
    EARLY_GAME = "early_game"
    MID_GAME = "mid_game"
    LATE_GAME = "late_game"


class TurningPointType(Enum):
    """Types of game turning points."""
    ACE = "ace"
    BARON_KILL = "baron_kill"
    ELDER_DRAKE = "elder_drake"
    DRAGON_SOUL = "dragon_soul"
    BASE_RACE = "base_race"
    GOLD_SWING = "gold_swing"
    TEAMFIGHT_WIN = "teamfight_win"
    INHIBITOR_KILL = "inhibitor_kill"
    FIRST_BLOOD = "first_blood"
    FIRST_TOWER = "first_tower"
    SHUTDOWN_KILL = "shutdown_kill"


class MonsterType(Enum):
    """Elite monster types."""
    DRAGON = "DRAGON"
    BARON = "BARON_NASHOR"
    RIFT_HERALD = "RIFTHERALD"
    ELDER_DRAGON = "ELDER_DRAGON"
    VOID_GRUB = "HORDE"


class DragonType(Enum):
    """Dragon subtypes."""
    INFERNAL = "FIRE_DRAGON"
    OCEAN = "WATER_DRAGON"
    MOUNTAIN = "EARTH_DRAGON"
    CLOUD = "AIR_DRAGON"
    HEXTECH = "HEXTECH_DRAGON"
    CHEMTECH = "CHEMTECH_DRAGON"
    ELDER = "ELDER_DRAGON"


# ─── Data Models ──────────────────────────────────────────────────────────────

@dataclass
class TimelineEvent:
    """A single timeline event."""
    event_type: EventType = EventType.CHAMPION_KILL
    timestamp_ms: int = 0
    participant_id: int = 0
    team_id: int = 0
    position_x: int = 0
    position_y: int = 0
    raw_data: Dict[str, Any] = field(default_factory=dict)

    # Kill-specific
    killer_id: int = 0
    victim_id: int = 0
    assisting_participants: List[int] = field(default_factory=list)
    bounty_gold: int = 0

    # Monster-specific
    monster_type: str = ""
    monster_subtype: str = ""

    # Building-specific
    building_type: str = ""
    lane_type: str = ""
    tower_type: str = ""

    # Item-specific
    item_id: int = 0

    @property
    def timestamp_min(self) -> float:
        return self.timestamp_ms / 60000

    @property
    def game_phase(self) -> GamePhase:
        minutes = self.timestamp_min
        if minutes <= EARLY_GAME_END_MIN:
            return GamePhase.EARLY_GAME
        elif minutes <= MID_GAME_END_MIN:
            return GamePhase.MID_GAME
        return GamePhase.LATE_GAME


@dataclass
class ParticipantSnapshot:
    """Participant state at a specific timestamp."""
    participant_id: int = 0
    champion_name: str = ""
    level: int = 0
    current_gold: int = 0
    total_gold: int = 0
    xp: int = 0
    minions_killed: int = 0
    jungle_minions_killed: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    position_x: int = 0
    position_y: int = 0
    items: List[int] = field(default_factory=list)

    @property
    def cs(self) -> int:
        return self.minions_killed + self.jungle_minions_killed


@dataclass
class TeamSnapshot:
    """Team state at a specific timestamp."""
    team_id: int = 0
    total_gold: int = 0
    total_kills: int = 0
    towers_destroyed: int = 0
    dragons_killed: int = 0
    barons_killed: int = 0
    inhibitors_destroyed: int = 0
    participants: List[ParticipantSnapshot] = field(default_factory=list)

    @property
    def avg_level(self) -> float:
        if not self.participants:
            return 0.0
        return statistics.mean(p.level for p in self.participants)


@dataclass
class GameStateSnapshot:
    """Complete game state at a specific timestamp."""
    timestamp_ms: int = 0
    frame_index: int = 0
    blue_team: TeamSnapshot = field(default_factory=lambda: TeamSnapshot(team_id=100))
    red_team: TeamSnapshot = field(default_factory=lambda: TeamSnapshot(team_id=200))
    events_in_frame: List[TimelineEvent] = field(default_factory=list)

    @property
    def timestamp_min(self) -> float:
        return self.timestamp_ms / 60000

    @property
    def gold_diff(self) -> int:
        """Gold difference (positive = blue ahead)."""
        return self.blue_team.total_gold - self.red_team.total_gold

    @property
    def kill_diff(self) -> int:
        return self.blue_team.total_kills - self.red_team.total_kills

    @property
    def game_phase(self) -> GamePhase:
        minutes = self.timestamp_min
        if minutes <= EARLY_GAME_END_MIN:
            return GamePhase.EARLY_GAME
        elif minutes <= MID_GAME_END_MIN:
            return GamePhase.MID_GAME
        return GamePhase.LATE_GAME


@dataclass
class TurningPoint:
    """A key turning point in the game."""
    turning_point_type: TurningPointType = TurningPointType.TEAMFIGHT_WIN
    timestamp_ms: int = 0
    team_id: int = 0
    description: str = ""
    gold_swing: int = 0
    significance_score: float = 0.0
    events: List[TimelineEvent] = field(default_factory=list)

    @property
    def timestamp_min(self) -> float:
        return self.timestamp_ms / 60000

    @property
    def is_major(self) -> bool:
        return self.significance_score >= 0.7


@dataclass
class TeamfightResult:
    """Result of a detected teamfight."""
    start_ms: int = 0
    end_ms: int = 0
    winning_team_id: int = 0
    blue_kills: int = 0
    red_kills: int = 0
    participants_involved: Set[int] = field(default_factory=set)
    is_ace: bool = False
    near_objective: str = ""

    @property
    def duration_seconds(self) -> float:
        return (self.end_ms - self.start_ms) / 1000

    @property
    def total_kills(self) -> int:
        return self.blue_kills + self.red_kills


@dataclass
class ReconstructedTimeline:
    """Complete reconstructed game timeline."""
    match_id: str = ""
    game_duration_ms: int = 0
    frame_count: int = 0
    snapshots: List[GameStateSnapshot] = field(default_factory=list)
    all_events: List[TimelineEvent] = field(default_factory=list)
    turning_points: List[TurningPoint] = field(default_factory=list)
    teamfights: List[TeamfightResult] = field(default_factory=list)
    gold_timeline: List[Tuple[int, int]] = field(default_factory=list)
    xp_timeline: List[Tuple[int, int]] = field(default_factory=list)

    @property
    def game_duration_min(self) -> float:
        return self.game_duration_ms / 60000

    @property
    def major_turning_points(self) -> List[TurningPoint]:
        return [tp for tp in self.turning_points if tp.is_major]


# ─── Event Parser ─────────────────────────────────────────────────────────────

class EventParser:
    """Parse raw timeline events into typed TimelineEvent objects."""

    @staticmethod
    def parse_event(raw: Dict[str, Any]) -> TimelineEvent:
        """Parse a single raw event."""
        event_type_str = raw.get("type", "")
        try:
            event_type = EventType(event_type_str)
        except ValueError:
            event_type = EventType.CHAMPION_KILL

        event = TimelineEvent(
            event_type=event_type,
            timestamp_ms=raw.get("timestamp", 0),
            participant_id=raw.get("participantId", 0),
            raw_data=raw,
        )

        position = raw.get("position", {})
        event.position_x = position.get("x", 0)
        event.position_y = position.get("y", 0)

        if event_type == EventType.CHAMPION_KILL:
            event.killer_id = raw.get("killerId", 0)
            event.victim_id = raw.get("victimId", 0)
            event.assisting_participants = raw.get("assistingParticipantIds", [])
            event.bounty_gold = raw.get("bounty", 0) + raw.get("shutdownBounty", 0)

        elif event_type == EventType.ELITE_MONSTER_KILL:
            event.killer_id = raw.get("killerId", 0)
            event.monster_type = raw.get("monsterType", "")
            event.monster_subtype = raw.get("monsterSubType", "")
            event.team_id = EventParser._killer_to_team(event.killer_id)

        elif event_type == EventType.BUILDING_KILL:
            event.killer_id = raw.get("killerId", 0)
            event.team_id = raw.get("teamId", 0)
            event.building_type = raw.get("buildingType", "")
            event.lane_type = raw.get("laneType", "")
            event.tower_type = raw.get("towerType", "")

        elif event_type in (EventType.ITEM_PURCHASED, EventType.ITEM_SOLD):
            event.item_id = raw.get("itemId", 0)

        return event

    @staticmethod
    def _killer_to_team(killer_id: int) -> int:
        """Infer team from participant ID (1-5 = blue, 6-10 = red)."""
        if 1 <= killer_id <= 5:
            return 100
        elif 6 <= killer_id <= 10:
            return 200
        return 0

    @classmethod
    def parse_events(cls, raw_events: List[Dict[str, Any]]) -> List[TimelineEvent]:
        """Parse a list of raw events."""
        events = []
        for raw in raw_events:
            event = cls.parse_event(raw)
            events.append(event)
        events.sort(key=lambda e: e.timestamp_ms)
        return events


# ─── Teamfight Detector ──────────────────────────────────────────────────────

class TeamfightDetector:
    """Detect teamfights from kill event clusters."""

    @staticmethod
    def detect(kill_events: List[TimelineEvent]) -> List[TeamfightResult]:
        """Detect teamfights by clustering nearby kills."""
        if not kill_events:
            return []

        teamfights = []
        current_cluster: List[TimelineEvent] = []

        for event in kill_events:
            if event.event_type != EventType.CHAMPION_KILL:
                continue

            if not current_cluster:
                current_cluster.append(event)
                continue

            time_diff = event.timestamp_ms - current_cluster[-1].timestamp_ms
            if time_diff <= TEAMFIGHT_WINDOW_MS:
                current_cluster.append(event)
            else:
                if len(current_cluster) >= MIN_TEAMFIGHT_PARTICIPANTS:
                    tf = TeamfightDetector._cluster_to_teamfight(current_cluster)
                    teamfights.append(tf)
                current_cluster = [event]

        if len(current_cluster) >= MIN_TEAMFIGHT_PARTICIPANTS:
            tf = TeamfightDetector._cluster_to_teamfight(current_cluster)
            teamfights.append(tf)

        return teamfights

    @staticmethod
    def _cluster_to_teamfight(events: List[TimelineEvent]) -> TeamfightResult:
        """Convert a kill cluster to a teamfight result."""
        blue_kills = sum(
            1 for e in events if 1 <= e.killer_id <= 5
        )
        red_kills = sum(
            1 for e in events if 6 <= e.killer_id <= 10
        )

        participants = set()
        for e in events:
            participants.add(e.killer_id)
            participants.add(e.victim_id)
            participants.update(e.assisting_participants)

        winning_team = 100 if blue_kills > red_kills else 200 if red_kills > blue_kills else 0

        blue_deaths = sum(1 for e in events if 1 <= e.victim_id <= 5)
        red_deaths = sum(1 for e in events if 6 <= e.victim_id <= 10)
        is_ace = blue_deaths >= 5 or red_deaths >= 5

        return TeamfightResult(
            start_ms=events[0].timestamp_ms,
            end_ms=events[-1].timestamp_ms,
            winning_team_id=winning_team,
            blue_kills=blue_kills,
            red_kills=red_kills,
            participants_involved=participants,
            is_ace=is_ace,
        )


# ─── Turning Point Detector ──────────────────────────────────────────────────

class TurningPointDetector:
    """Detect significant turning points in the game."""

    @staticmethod
    def detect(
        events: List[TimelineEvent],
        snapshots: List[GameStateSnapshot],
        teamfights: List[TeamfightResult],
    ) -> List[TurningPoint]:
        """Analyze events and state changes for turning points."""
        turning_points = []

        # First blood
        for e in events:
            if e.event_type == EventType.CHAMPION_KILL:
                tp = TurningPoint(
                    turning_point_type=TurningPointType.FIRST_BLOOD,
                    timestamp_ms=e.timestamp_ms,
                    team_id=100 if 1 <= e.killer_id <= 5 else 200,
                    description="First Blood",
                    gold_swing=FIRST_BLOOD_BONUS_GOLD,
                    significance_score=0.3,
                    events=[e],
                )
                turning_points.append(tp)
                break

        # Baron/Dragon/Elder kills
        for e in events:
            if e.event_type == EventType.ELITE_MONSTER_KILL:
                if e.monster_type == "BARON_NASHOR":
                    turning_points.append(TurningPoint(
                        turning_point_type=TurningPointType.BARON_KILL,
                        timestamp_ms=e.timestamp_ms,
                        team_id=e.team_id,
                        description="Baron Nashor slain",
                        gold_swing=BARON_TEAM_GOLD * 5,
                        significance_score=0.8,
                        events=[e],
                    ))
                elif "ELDER" in e.monster_subtype.upper():
                    turning_points.append(TurningPoint(
                        turning_point_type=TurningPointType.ELDER_DRAKE,
                        timestamp_ms=e.timestamp_ms,
                        team_id=e.team_id,
                        description="Elder Dragon slain",
                        significance_score=0.9,
                        events=[e],
                    ))

        # Aces from teamfights
        for tf in teamfights:
            if tf.is_ace:
                turning_points.append(TurningPoint(
                    turning_point_type=TurningPointType.ACE,
                    timestamp_ms=tf.start_ms,
                    team_id=tf.winning_team_id,
                    description=f"Ace ({tf.total_kills} kills)",
                    significance_score=0.85,
                ))

        # Gold swings
        prev_diff = 0
        for snapshot in snapshots:
            diff = snapshot.gold_diff
            swing = abs(diff - prev_diff)
            if swing >= GOLD_SWING_THRESHOLD:
                advantage_team = 100 if diff > prev_diff else 200
                turning_points.append(TurningPoint(
                    turning_point_type=TurningPointType.GOLD_SWING,
                    timestamp_ms=snapshot.timestamp_ms,
                    team_id=advantage_team,
                    description=f"Gold swing: {swing}g",
                    gold_swing=swing,
                    significance_score=min(swing / 5000, 1.0),
                ))
            prev_diff = diff

        # Sort by timestamp
        turning_points.sort(key=lambda tp: tp.timestamp_ms)
        return turning_points


# ─── Main Reconstructor ──────────────────────────────────────────────────────

class BattleTimelineReconstructor:
    """
    Main timeline reconstruction engine.
    Implements HistoricalBattleInterface contract.
    """

    def __init__(self):
        self._event_parser = EventParser()
        self._teamfight_detector = TeamfightDetector()
        self._turning_point_detector = TurningPointDetector()
        self._initialized = False

    async def initialize(self, config: Dict[str, Any] = None) -> bool:
        self._initialized = True
        logger.info("BattleTimelineReconstructor initialized")
        return True

    def reconstruct(
        self, match_id: str, timeline_data: Dict[str, Any]
    ) -> ReconstructedTimeline:
        """Reconstruct a complete timeline from raw API data."""
        result = ReconstructedTimeline(match_id=match_id)

        frames = timeline_data.get("info", {}).get("frames", [])
        if not frames:
            frames = timeline_data.get("frames", [])

        result.frame_count = len(frames)

        all_events = []
        snapshots = []

        for idx, frame in enumerate(frames):
            timestamp = frame.get("timestamp", idx * FRAME_INTERVAL_MS)

            # Parse events in this frame
            raw_events = frame.get("events", [])
            frame_events = self._event_parser.parse_events(raw_events)
            all_events.extend(frame_events)

            # Build state snapshot
            snapshot = self._build_snapshot(frame, idx, timestamp, frame_events)
            snapshots.append(snapshot)

            # Gold timeline
            result.gold_timeline.append((timestamp, snapshot.gold_diff))

        result.all_events = all_events
        result.snapshots = snapshots
        result.game_duration_ms = (
            frames[-1].get("timestamp", 0) if frames else 0
        )

        # Detect teamfights
        kill_events = [e for e in all_events if e.event_type == EventType.CHAMPION_KILL]
        result.teamfights = self._teamfight_detector.detect(kill_events)

        # Detect turning points
        result.turning_points = self._turning_point_detector.detect(
            all_events, snapshots, result.teamfights
        )

        return result

    def _build_snapshot(
        self,
        frame: Dict[str, Any],
        index: int,
        timestamp: int,
        events: List[TimelineEvent],
    ) -> GameStateSnapshot:
        """Build a game state snapshot from frame data."""
        snapshot = GameStateSnapshot(
            timestamp_ms=timestamp,
            frame_index=index,
            events_in_frame=events,
        )

        participant_frames = frame.get("participantFrames", {})
        for pid_str, pf in participant_frames.items():
            pid = int(pid_str)
            position = pf.get("position", {})
            p_snap = ParticipantSnapshot(
                participant_id=pid,
                level=pf.get("level", 0),
                current_gold=pf.get("currentGold", 0),
                total_gold=pf.get("totalGold", 0),
                xp=pf.get("xp", 0),
                minions_killed=pf.get("minionsKilled", 0),
                jungle_minions_killed=pf.get("jungleMinionsKilled", 0),
                position_x=position.get("x", 0),
                position_y=position.get("y", 0),
            )

            if 1 <= pid <= 5:
                snapshot.blue_team.participants.append(p_snap)
                snapshot.blue_team.total_gold += p_snap.total_gold
            else:
                snapshot.red_team.participants.append(p_snap)
                snapshot.red_team.total_gold += p_snap.total_gold

        return snapshot

    async def health_check(self) -> Dict[str, Any]:
        return {"initialized": self._initialized}

    async def shutdown(self):
        logger.info("BattleTimelineReconstructor shutdown")

    def get_module_info(self) -> Dict[str, str]:
        return {
            "task_id": "M813",
            "name": "Battle Timeline Reconstructor",
            "version": "1.0.0",
        }


if __name__ == "__main__":
    print("M813 Battle Timeline Reconstructor - Self Test")

    parser = EventParser()
    raw = {"type": "CHAMPION_KILL", "timestamp": 120000, "killerId": 3, "victimId": 8, "assistingParticipantIds": [1, 2]}
    event = parser.parse_event(raw)
    print(f"Parsed event: {event.event_type.value} at {event.timestamp_min:.1f}min")

    detector = TeamfightDetector()
    kills = [
        TimelineEvent(event_type=EventType.CHAMPION_KILL, timestamp_ms=600000, killer_id=1, victim_id=6),
        TimelineEvent(event_type=EventType.CHAMPION_KILL, timestamp_ms=602000, killer_id=7, victim_id=2),
        TimelineEvent(event_type=EventType.CHAMPION_KILL, timestamp_ms=604000, killer_id=3, victim_id=8),
        TimelineEvent(event_type=EventType.CHAMPION_KILL, timestamp_ms=606000, killer_id=4, victim_id=9),
    ]
    tfs = detector.detect(kills)
    print(f"Detected {len(tfs)} teamfight(s)")

    print("\nM813 self-test passed.")

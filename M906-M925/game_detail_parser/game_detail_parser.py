#!/usr/bin/env python3
"""
M908: GameDetailParser
=======================

Parse getGameDetailByGameId responses — extract participants, items,
runes, timeline events, and compute per-game analytics.

Architecture Pattern:
  查看 Seraphine connector.py 上现有 getGameDetailByGameId 的实现方式,
  理解其模式, 特别是 participants 数组和 stats 对象是如何嵌套的。
  从 getGameDetailByGameId 返回的 JSON 结构开始。
  然后, 遵循该模式实现一个新的 GameDetailParser,
  让 operatorRL 可以提取结构化对局数据, 并能做跨对局分析。
  接着引入时间线事件解析, 使对局内关键事件(击杀/龙/塔)能被索引,
  同时优化出装路径还原逻辑。
  随后整合符文配置解析, 令英雄构建分析支持完整符文页,
  进而增强参与者角色识别精度。
  最终完善大型对象安全解析, 确保畸形数据不会crash,
  全面升级对局详情解析以达成数据仓库级别质量。

Author: dylanyunlong <dylanyunlong@gmail.com>
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import datetime
import enum
import functools
import hashlib
import json
import logging
import math
import os
import pathlib
import statistics
import time
import traceback
import typing
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ITEM_SLOT_COUNT = 7
RUNE_SLOT_COUNT = 6
MAX_PARTICIPANTS = 10
TIMELINE_EVENT_TYPES = {
    "CHAMPION_KILL", "BUILDING_KILL", "ELITE_MONSTER_KILL",
    "WARD_PLACED", "WARD_KILL", "ITEM_PURCHASED", "ITEM_SOLD",
    "ITEM_DESTROYED", "ITEM_UNDO", "TURRET_PLATE_DESTROYED",
    "LEVEL_UP", "SKILL_LEVEL_UP",
}
DRAGON_TYPES = {"FIRE_DRAGON", "WATER_DRAGON", "EARTH_DRAGON", "AIR_DRAGON", "ELDER_DRAGON", "HEXTECH_DRAGON", "CHEMTECH_DRAGON"}
OBJECTIVE_TYPES = {"BARON_NASHOR", "RIFTHERALD"} | DRAGON_TYPES


class TeamSide(enum.Enum):
    BLUE = 100
    RED = 200


class GameMode(enum.Enum):
    CLASSIC = "CLASSIC"
    ARAM = "ARAM"
    URF = "URF"
    ONEFORALL = "ONEFORALL"
    NEXUSBLITZ = "NEXUSBLITZ"
    UNKNOWN = "UNKNOWN"


@dataclasses.dataclass
class ParticipantStats:
    """Parsed stats for a single participant."""
    puuid: str = ""
    summoner_name: str = ""
    champion_id: int = 0
    champion_name: str = ""
    team_id: int = 100
    role: str = ""
    lane: str = ""
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    gold_earned: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    vision_score: int = 0
    wards_placed: int = 0
    wards_killed: int = 0
    items: List[int] = dataclasses.field(default_factory=list)
    runes: List[int] = dataclasses.field(default_factory=list)
    rune_primary_tree: int = 0
    rune_secondary_tree: int = 0
    spell1_id: int = 0
    spell2_id: int = 0
    level: int = 18
    win: bool = False
    first_blood: bool = False
    turrets_killed: int = 0
    inhibitors_killed: int = 0
    double_kills: int = 0
    triple_kills: int = 0
    quadra_kills: int = 0
    penta_kills: int = 0
    time_ccing: int = 0
    cs_per_min: float = 0.0
    gold_per_min: float = 0.0
    damage_per_min: float = 0.0
    kda: float = 0.0
    kill_participation: float = 0.0

    def compute_derived(self, game_duration_minutes: float, team_kills: int) -> None:
        """Compute derived stats."""
        if game_duration_minutes > 0:
            self.cs_per_min = round(self.cs / game_duration_minutes, 1)
            self.gold_per_min = round(self.gold_earned / game_duration_minutes, 1)
            self.damage_per_min = round(self.damage_dealt / game_duration_minutes, 1)
        self.kda = round((self.kills + self.assists) / max(1, self.deaths), 2)
        if team_kills > 0:
            self.kill_participation = round((self.kills + self.assists) / team_kills, 3)

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        return d

    @classmethod
    def from_riot_json(cls, participant: Dict, identity: Dict = None) -> "ParticipantStats":
        """Parse from Riot game detail participant JSON."""
        stats = participant.get("stats", {})
        timeline = participant.get("timeline", {})
        puuid = ""
        name = ""
        if identity:
            player = identity.get("player", {})
            puuid = player.get("puuid", "")
            name = player.get("summonerName", player.get("gameName", ""))
        items = []
        for i in range(ITEM_SLOT_COUNT):
            item_id = stats.get(f"item{i}", 0)
            if item_id > 0:
                items.append(item_id)
        runes = []
        for i in range(RUNE_SLOT_COUNT):
            rune_id = stats.get(f"perk{i}", 0)
            if rune_id > 0:
                runes.append(rune_id)
        return cls(
            puuid=puuid,
            summoner_name=name,
            champion_id=participant.get("championId", 0),
            team_id=participant.get("teamId", 100),
            role=timeline.get("role", ""),
            lane=timeline.get("lane", ""),
            kills=stats.get("kills", 0),
            deaths=stats.get("deaths", 0),
            assists=stats.get("assists", 0),
            cs=stats.get("totalMinionsKilled", 0) + stats.get("neutralMinionsKilled", 0),
            gold_earned=stats.get("goldEarned", 0),
            damage_dealt=stats.get("totalDamageDealtToChampions", 0),
            damage_taken=stats.get("totalDamageTaken", 0),
            vision_score=stats.get("visionScore", 0),
            wards_placed=stats.get("wardsPlaced", 0),
            wards_killed=stats.get("wardsKilled", 0),
            items=items,
            runes=runes,
            rune_primary_tree=stats.get("perkPrimaryStyle", 0),
            rune_secondary_tree=stats.get("perkSubStyle", 0),
            spell1_id=participant.get("spell1Id", 0),
            spell2_id=participant.get("spell2Id", 0),
            level=stats.get("champLevel", 18),
            win=stats.get("win", False),
            first_blood=stats.get("firstBloodKill", False),
            turrets_killed=stats.get("turretKills", 0),
            inhibitors_killed=stats.get("inhibitorKills", 0),
            double_kills=stats.get("doubleKills", 0),
            triple_kills=stats.get("tripleKills", 0),
            quadra_kills=stats.get("quadraKills", 0),
            penta_kills=stats.get("pentaKills", 0),
            time_ccing=stats.get("timeCCingOthers", 0),
        )


@dataclasses.dataclass
class TimelineEvent:
    """Parsed timeline event."""
    timestamp_ms: int
    event_type: str
    killer_id: int = 0
    victim_id: int = 0
    assisting_ids: List[int] = dataclasses.field(default_factory=list)
    position_x: int = 0
    position_y: int = 0
    monster_type: str = ""
    building_type: str = ""
    item_id: int = 0
    ward_type: str = ""
    skill_slot: int = 0
    level_up_type: str = ""

    @property
    def timestamp_minutes(self) -> float:
        return self.timestamp_ms / 60000.0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_riot_json(cls, event: Dict) -> Optional["TimelineEvent"]:
        etype = event.get("type", "")
        if etype not in TIMELINE_EVENT_TYPES:
            return None
        pos = event.get("position", {})
        return cls(
            timestamp_ms=event.get("timestamp", 0),
            event_type=etype,
            killer_id=event.get("killerId", event.get("creatorId", 0)),
            victim_id=event.get("victimId", 0),
            assisting_ids=event.get("assistingParticipantIds", []),
            position_x=pos.get("x", 0),
            position_y=pos.get("y", 0),
            monster_type=event.get("monsterType", event.get("monsterSubType", "")),
            building_type=event.get("buildingType", ""),
            item_id=event.get("itemId", 0),
            ward_type=event.get("wardType", ""),
            skill_slot=event.get("skillSlot", 0),
            level_up_type=event.get("levelUpType", ""),
        )


@dataclasses.dataclass
class TeamStats:
    """Parsed team-level stats."""
    team_id: int
    win: bool
    first_blood: bool = False
    first_tower: bool = False
    first_dragon: bool = False
    first_baron: bool = False
    first_rift_herald: bool = False
    tower_kills: int = 0
    inhibitor_kills: int = 0
    dragon_kills: int = 0
    baron_kills: int = 0
    rift_herald_kills: int = 0
    bans: List[int] = dataclasses.field(default_factory=list)
    total_kills: int = 0
    total_deaths: int = 0
    total_gold: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_riot_json(cls, team: Dict) -> "TeamStats":
        bans = [b.get("championId", 0) for b in team.get("bans", [])]
        return cls(
            team_id=team.get("teamId", 100),
            win=team.get("win", "") == "Win" or team.get("win", False) is True,
            first_blood=team.get("firstBlood", False),
            first_tower=team.get("firstTower", False),
            first_dragon=team.get("firstDragon", False),
            first_baron=team.get("firstBaron", False),
            first_rift_herald=team.get("firstRiftHerald", False),
            tower_kills=team.get("towerKills", 0),
            inhibitor_kills=team.get("inhibitorKills", 0),
            dragon_kills=team.get("dragonKills", 0),
            baron_kills=team.get("baronKills", 0),
            rift_herald_kills=team.get("riftHeraldKills", 0),
            bans=bans,
        )


@dataclasses.dataclass
class ParsedGameDetail:
    """Complete parsed game detail."""
    game_id: int
    game_creation: int
    game_duration: int
    game_mode: str
    game_version: str
    map_id: int
    queue_id: int
    teams: List[TeamStats] = dataclasses.field(default_factory=list)
    participants: List[ParticipantStats] = dataclasses.field(default_factory=list)
    timeline_events: List[TimelineEvent] = dataclasses.field(default_factory=list)
    parse_errors: List[str] = dataclasses.field(default_factory=list)

    @property
    def duration_minutes(self) -> float:
        return self.game_duration / 60.0

    @property
    def blue_team(self) -> List[ParticipantStats]:
        return [p for p in self.participants if p.team_id == TeamSide.BLUE.value]

    @property
    def red_team(self) -> List[ParticipantStats]:
        return [p for p in self.participants if p.team_id == TeamSide.RED.value]

    @property
    def winner(self) -> TeamSide:
        for t in self.teams:
            if t.win:
                return TeamSide(t.team_id)
        return TeamSide.BLUE

    def get_participant_by_puuid(self, puuid: str) -> Optional[ParticipantStats]:
        for p in self.participants:
            if p.puuid == puuid:
                return p
        return None

    def get_kills_at_time(self, minutes: float) -> List[TimelineEvent]:
        threshold_ms = int(minutes * 60000)
        return [e for e in self.timeline_events
                if e.event_type == "CHAMPION_KILL" and e.timestamp_ms <= threshold_ms]

    def get_objective_events(self) -> List[TimelineEvent]:
        return [e for e in self.timeline_events
                if e.monster_type in OBJECTIVE_TYPES or e.building_type]

    def get_dragon_sequence(self) -> List[Tuple[float, str, int]]:
        dragons = []
        for e in self.timeline_events:
            if e.monster_type in DRAGON_TYPES:
                dragons.append((e.timestamp_minutes, e.monster_type, e.killer_id))
        return sorted(dragons, key=lambda x: x[0])

    def compute_gold_diff_at(self, minutes: float) -> int:
        """Estimate gold difference (Blue - Red) at given time."""
        blue_gold = sum(p.gold_earned for p in self.blue_team)
        red_gold = sum(p.gold_earned for p in self.red_team)
        ratio = min(1.0, minutes / self.duration_minutes) if self.duration_minutes > 0 else 1.0
        return int((blue_gold - red_gold) * ratio)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_id": self.game_id,
            "creation": self.game_creation,
            "duration": self.game_duration,
            "mode": self.game_mode,
            "version": self.game_version,
            "map_id": self.map_id,
            "queue_id": self.queue_id,
            "teams": [t.to_dict() for t in self.teams],
            "participants": [p.to_dict() for p in self.participants],
            "timeline_event_count": len(self.timeline_events),
            "errors": self.parse_errors,
        }


class GameDetailParser:
    """
    Production-grade game detail parser.

    Features:
    - Safe parsing of deeply nested Riot JSON structures
    - Participant stats extraction with derived metrics
    - Timeline event parsing and indexing
    - Team-level stat aggregation
    - Cross-participant analytics (gold diff, KP, etc.)
    - Error collection without crashing
    """

    def __init__(self, connector=None):
        self._connector = connector
        self._cache: Dict[int, ParsedGameDetail] = {}
        self._parse_count = 0
        self._error_count = 0
        logger.info("GameDetailParser initialized")

    async def fetch_and_parse(self, game_id: int) -> Optional[ParsedGameDetail]:
        """Fetch game detail from LCU and parse it."""
        if game_id in self._cache:
            return self._cache[game_id]
        if self._connector is None:
            raw = self._generate_stub_detail(game_id)
        else:
            try:
                raw = await self._connector.lcu_get(f"/lol-match-history/v1/games/{game_id}")
            except Exception as exc:
                logger.error("Failed to fetch game %d: %s", game_id, exc)
                return None
        if raw is None:
            return None
        parsed = self.parse(raw)
        if parsed:
            self._cache[game_id] = parsed
        return parsed

    def parse(self, raw: Dict[str, Any]) -> Optional[ParsedGameDetail]:
        """Parse raw game detail JSON into structured ParsedGameDetail."""
        errors: List[str] = []
        try:
            game_id = raw.get("gameId", 0)
            game_creation = raw.get("gameCreation", 0)
            game_duration = raw.get("gameDuration", 0)
            game_mode = raw.get("gameMode", "UNKNOWN")
            game_version = raw.get("gameVersion", "")
            map_id = raw.get("mapId", 11)
            queue_id = raw.get("queueId", 0)
            # Parse teams
            teams = []
            for team_data in raw.get("teams", []):
                try:
                    teams.append(TeamStats.from_riot_json(team_data))
                except Exception as exc:
                    errors.append(f"team_parse: {exc}")
            # Parse participants
            participants = []
            raw_participants = raw.get("participants", [])
            identities = raw.get("participantIdentities", [])
            identity_map = {}
            for ident in identities:
                pid = ident.get("participantId", 0)
                identity_map[pid] = ident
            for p_data in raw_participants:
                try:
                    pid = p_data.get("participantId", 0)
                    identity = identity_map.get(pid, {})
                    ps = ParticipantStats.from_riot_json(p_data, identity)
                    duration_min = game_duration / 60.0 if game_duration > 0 else 1.0
                    team_kills = sum(
                        pp.get("stats", {}).get("kills", 0)
                        for pp in raw_participants
                        if pp.get("teamId") == p_data.get("teamId")
                    )
                    ps.compute_derived(duration_min, team_kills)
                    participants.append(ps)
                except Exception as exc:
                    errors.append(f"participant_parse: {exc}")
            # Aggregate team kills into TeamStats
            for team in teams:
                team_members = [p for p in participants if p.team_id == team.team_id]
                team.total_kills = sum(p.kills for p in team_members)
                team.total_deaths = sum(p.deaths for p in team_members)
                team.total_gold = sum(p.gold_earned for p in team_members)
            # Parse timeline
            timeline_events = []
            timeline_data = raw.get("timeline", raw.get("frames", []))
            if isinstance(timeline_data, dict):
                frames = timeline_data.get("frames", [])
            elif isinstance(timeline_data, list):
                frames = timeline_data
            else:
                frames = []
            for frame in frames:
                events = frame.get("events", []) if isinstance(frame, dict) else []
                for event in events:
                    try:
                        te = TimelineEvent.from_riot_json(event)
                        if te:
                            timeline_events.append(te)
                    except Exception as exc:
                        errors.append(f"timeline_parse: {exc}")
            parsed = ParsedGameDetail(
                game_id=game_id,
                game_creation=game_creation,
                game_duration=game_duration,
                game_mode=game_mode,
                game_version=game_version,
                map_id=map_id,
                queue_id=queue_id,
                teams=teams,
                participants=participants,
                timeline_events=sorted(timeline_events, key=lambda e: e.timestamp_ms),
                parse_errors=errors,
            )
            self._parse_count += 1
            if errors:
                self._error_count += len(errors)
                logger.warning("Parsed game %d with %d errors", game_id, len(errors))
            return parsed
        except Exception as exc:
            self._error_count += 1
            logger.error("Critical parse failure: %s", exc)
            return None

    def _generate_stub_detail(self, game_id: int) -> Dict[str, Any]:
        """Generate stub game detail for testing."""
        import random
        participants = []
        identities = []
        for i in range(1, 11):
            team = 100 if i <= 5 else 200
            participants.append({
                "participantId": i,
                "championId": random.randint(1, 150),
                "teamId": team,
                "spell1Id": 4,
                "spell2Id": random.choice([7, 11, 12, 14, 21]),
                "stats": {
                    "kills": random.randint(0, 12),
                    "deaths": random.randint(0, 8),
                    "assists": random.randint(0, 18),
                    "totalMinionsKilled": random.randint(50, 250),
                    "neutralMinionsKilled": random.randint(0, 60),
                    "goldEarned": random.randint(8000, 18000),
                    "totalDamageDealtToChampions": random.randint(5000, 40000),
                    "totalDamageTaken": random.randint(10000, 35000),
                    "visionScore": random.randint(5, 60),
                    "wardsPlaced": random.randint(2, 25),
                    "wardsKilled": random.randint(0, 10),
                    "champLevel": random.randint(12, 18),
                    "win": team == 100,
                    "item0": random.randint(1000, 7000),
                    "item1": random.randint(1000, 7000),
                    "item2": random.randint(1000, 7000),
                    "perk0": random.randint(8000, 8500),
                    "perkPrimaryStyle": 8100,
                    "perkSubStyle": 8300,
                },
                "timeline": {
                    "role": "SOLO",
                    "lane": ["TOP", "JUNGLE", "MID", "BOTTOM", "BOTTOM"][i % 5],
                },
            })
            identities.append({
                "participantId": i,
                "player": {
                    "puuid": hashlib.md5(f"player{i}".encode()).hexdigest(),
                    "summonerName": f"Player{i}",
                },
            })
        return {
            "gameId": game_id,
            "gameCreation": int(time.time() * 1000) - random.randint(0, 86400000),
            "gameDuration": random.randint(1200, 2400),
            "gameMode": "CLASSIC",
            "gameVersion": "14.10.1",
            "mapId": 11,
            "queueId": 420,
            "teams": [
                {"teamId": 100, "win": "Win", "firstBlood": True, "towerKills": 8,
                 "dragonKills": 3, "baronKills": 1, "bans": [{"championId": c} for c in [10, 20, 30, 40, 50]]},
                {"teamId": 200, "win": "Fail", "firstBlood": False, "towerKills": 3,
                 "dragonKills": 1, "baronKills": 0, "bans": [{"championId": c} for c in [11, 21, 31, 41, 51]]},
            ],
            "participants": participants,
            "participantIdentities": identities,
        }

    async def batch_parse(self, game_ids: List[int]) -> List[ParsedGameDetail]:
        """Parse multiple games."""
        results = []
        for gid in game_ids:
            parsed = await self.fetch_and_parse(gid)
            if parsed:
                results.append(parsed)
        return results

    def get_stats(self) -> Dict[str, Any]:
        return {
            "parsed_games": self._parse_count,
            "total_errors": self._error_count,
            "cached_games": len(self._cache),
        }

    def clear_cache(self) -> None:
        self._cache.clear()


__all__ = [
    "GameDetailParser",
    "ParsedGameDetail",
    "ParticipantStats",
    "TimelineEvent",
    "TeamStats",
    "TeamSide",
    "GameMode",
]

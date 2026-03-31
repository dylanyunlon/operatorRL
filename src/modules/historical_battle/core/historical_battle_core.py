#!/usr/bin/env python3
"""
M806 - Historical Battle Data Core
====================================
OperatorRL Historical Battle System - Core Data Models & Schemas

查看 Seraphine 项目上现有的战斗数据模型实现方式，理解其模式，
特别是数据结构和接口是如何分离的。从 LCU API 的数据结构开始，
遵循该模式实现完整的历史战斗数据核心层，使所有子模块可以共享
统一的数据模型，并能进行严格的数据验证。

Core responsibilities:
- Define all data models for match history, player stats, champion data
- Provide validation, serialization, and transformation utilities
- Establish the canonical data schema for the entire battle system
- Support both Riot API and LCU (League Client Update) data formats
"""

import os
import sys
import json
import time
import enum
import hashlib
import logging
import datetime
import functools
from pathlib import Path
from typing import (
    Dict, List, Any, Optional, Tuple, Set, Union,
    TypeVar, Generic, Callable, Iterator, Sequence
)
from dataclasses import dataclass, field, asdict
from abc import ABC, abstractmethod

# ─── Module Logger ────────────────────────────────────────────────────────────

logger = logging.getLogger("operatorRL.historical_battle.core")
logger.setLevel(logging.DEBUG)

# ─── Constants ────────────────────────────────────────────────────────────────

MAX_MATCH_HISTORY_DEPTH = 100
MAX_PARTICIPANTS_PER_MATCH = 10
TEAMS_PER_MATCH = 2
PLAYERS_PER_TEAM = 5
SUMMONER_NAME_MAX_LENGTH = 16
CHAMPION_ID_RANGE = (1, 999)
QUEUE_TYPE_RANKED_SOLO = 420
QUEUE_TYPE_RANKED_FLEX = 440
QUEUE_TYPE_NORMAL_DRAFT = 400
QUEUE_TYPE_ARAM = 450
MATCH_DURATION_MIN_SECONDS = 180
MATCH_DURATION_MAX_SECONDS = 7200
DEFAULT_REGION = "NA1"
SUPPORTED_REGIONS = [
    "BR1", "EUN1", "EUW1", "JP1", "KR", "LA1", "LA2",
    "NA1", "OC1", "PH2", "RU", "SG2", "TH2", "TR1", "TW2", "VN2"
]
SUPPORTED_PLATFORMS = [
    "americas", "asia", "europe", "sea"
]

TIER_ORDER = {
    "IRON": 0, "BRONZE": 1, "SILVER": 2, "GOLD": 3,
    "PLATINUM": 4, "EMERALD": 5, "DIAMOND": 6,
    "MASTER": 7, "GRANDMASTER": 8, "CHALLENGER": 9
}

DIVISION_ORDER = {"IV": 0, "III": 1, "II": 2, "I": 3}

DATA_VERSION = "1.0.0"
SCHEMA_REVISION = 27  # M806 is task #27


# ─── Enumerations ─────────────────────────────────────────────────────────────

class Region(enum.Enum):
    """Supported game regions."""
    BR1 = "BR1"
    EUN1 = "EUN1"
    EUW1 = "EUW1"
    JP1 = "JP1"
    KR = "KR"
    LA1 = "LA1"
    LA2 = "LA2"
    NA1 = "NA1"
    OC1 = "OC1"
    TR1 = "TR1"


class QueueType(enum.Enum):
    """Game queue types with their IDs."""
    RANKED_SOLO = 420
    RANKED_FLEX = 440
    NORMAL_DRAFT = 400
    NORMAL_BLIND = 430
    ARAM = 450
    CLASH = 700
    URF = 900
    CUSTOM = 0


class GameResult(enum.Enum):
    """Match result enumeration."""
    WIN = "Win"
    LOSS = "Fail"
    REMAKE = "Remake"
    UNKNOWN = "Unknown"


class Role(enum.Enum):
    """In-game role enumeration."""
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MIDDLE"
    ADC = "BOTTOM"
    SUPPORT = "UTILITY"
    UNKNOWN = "UNKNOWN"


class Tier(enum.Enum):
    """Ranked tier enumeration."""
    IRON = "IRON"
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"
    EMERALD = "EMERALD"
    DIAMOND = "DIAMOND"
    MASTER = "MASTER"
    GRANDMASTER = "GRANDMASTER"
    CHALLENGER = "CHALLENGER"
    UNRANKED = "UNRANKED"


class DataSource(enum.Enum):
    """Source of the data."""
    RIOT_API = "riot_api"
    LCU_API = "lcu_api"
    NETWORK_CAPTURE = "network_capture"
    REPLAY_FILE = "replay_file"
    CACHE = "cache"
    MANUAL = "manual"


# ─── Validation Utilities ────────────────────────────────────────────────────

class ValidationError(Exception):
    """Raised when data validation fails."""
    def __init__(self, field: str, value: Any, reason: str):
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"Validation failed for '{field}': {reason} (got: {value})")


class SchemaValidator:
    """
    Centralized validation engine for all battle data schemas.
    Provides composable validation rules with detailed error reporting.
    """

    @staticmethod
    def validate_summoner_name(name: str) -> str:
        """Validate and normalize a summoner name."""
        if not name or not isinstance(name, str):
            raise ValidationError("summoner_name", name, "Must be a non-empty string")
        name = name.strip()
        if len(name) > SUMMONER_NAME_MAX_LENGTH:
            raise ValidationError(
                "summoner_name", name,
                f"Exceeds max length of {SUMMONER_NAME_MAX_LENGTH}"
            )
        return name

    @staticmethod
    def validate_champion_id(champion_id: int) -> int:
        """Validate a champion ID is within valid range."""
        if not isinstance(champion_id, int):
            raise ValidationError("champion_id", champion_id, "Must be an integer")
        if not (CHAMPION_ID_RANGE[0] <= champion_id <= CHAMPION_ID_RANGE[1]):
            raise ValidationError(
                "champion_id", champion_id,
                f"Must be between {CHAMPION_ID_RANGE[0]} and {CHAMPION_ID_RANGE[1]}"
            )
        return champion_id

    @staticmethod
    def validate_match_duration(duration_seconds: int) -> int:
        """Validate match duration is within reasonable bounds."""
        if duration_seconds < MATCH_DURATION_MIN_SECONDS:
            logger.warning(
                f"Match duration {duration_seconds}s below minimum "
                f"({MATCH_DURATION_MIN_SECONDS}s) - possible remake"
            )
        if duration_seconds > MATCH_DURATION_MAX_SECONDS:
            raise ValidationError(
                "match_duration", duration_seconds,
                f"Exceeds maximum of {MATCH_DURATION_MAX_SECONDS}s"
            )
        return duration_seconds

    @staticmethod
    def validate_region(region: str) -> Region:
        """Validate and convert region string to enum."""
        try:
            return Region(region)
        except ValueError:
            raise ValidationError(
                "region", region,
                f"Must be one of: {[r.value for r in Region]}"
            )

    @staticmethod
    def validate_kda(kills: int, deaths: int, assists: int) -> Tuple[int, int, int]:
        """Validate KDA values are non-negative."""
        for name, val in [("kills", kills), ("deaths", deaths), ("assists", assists)]:
            if not isinstance(val, int) or val < 0:
                raise ValidationError(name, val, "Must be a non-negative integer")
        return kills, deaths, assists

    @staticmethod
    def validate_queue_type(queue_id: int) -> QueueType:
        """Validate and convert queue ID to enum."""
        for qt in QueueType:
            if qt.value == queue_id:
                return qt
        logger.warning(f"Unknown queue type ID: {queue_id}, using CUSTOM")
        return QueueType.CUSTOM

    @staticmethod
    def validate_timestamp(ts: Union[int, float, str]) -> datetime.datetime:
        """Convert various timestamp formats to datetime."""
        try:
            if isinstance(ts, (int, float)):
                if ts > 1e12:
                    ts = ts / 1000  # milliseconds to seconds
                return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            elif isinstance(ts, str):
                return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                raise ValidationError("timestamp", ts, "Unsupported type")
        except (ValueError, OSError) as e:
            raise ValidationError("timestamp", ts, str(e))


# ─── Core Data Models ────────────────────────────────────────────────────────

@dataclass
class ChampionInfo:
    """Champion static data reference."""
    champion_id: int
    name: str
    title: str = ""
    tags: List[str] = field(default_factory=list)
    image_url: str = ""

    def __post_init__(self):
        SchemaValidator.validate_champion_id(self.champion_id)


@dataclass
class SummonerIdentity:
    """Unique summoner identification across regions."""
    puuid: str
    summoner_id: str = ""
    account_id: str = ""
    game_name: str = ""
    tag_line: str = ""
    region: Region = Region.NA1
    profile_icon_id: int = 0
    summoner_level: int = 0

    @property
    def riot_id(self) -> str:
        """Full Riot ID in GameName#TagLine format."""
        return f"{self.game_name}#{self.tag_line}"

    @property
    def display_name(self) -> str:
        """Best available display name."""
        if self.game_name:
            return self.riot_id
        return self.summoner_id[:8] + "..."

    def fingerprint(self) -> str:
        """Unique hash fingerprint for deduplication."""
        return hashlib.sha256(self.puuid.encode()).hexdigest()[:16]


@dataclass
class RuneSelection:
    """Selected rune configuration."""
    primary_tree: int = 0
    primary_keystone: int = 0
    primary_runes: List[int] = field(default_factory=list)
    secondary_tree: int = 0
    secondary_runes: List[int] = field(default_factory=list)
    stat_shards: List[int] = field(default_factory=list)


@dataclass
class ItemBuild:
    """Item build path and final inventory."""
    items: List[int] = field(default_factory=list)
    item_purchase_order: List[Tuple[int, int]] = field(default_factory=list)
    trinket: int = 0
    gold_spent: int = 0
    gold_earned: int = 0

    @property
    def completed_items(self) -> List[int]:
        """Filter out components, return only completed items."""
        return [i for i in self.items if i > 3000]  # Simplified heuristic

    @property
    def gold_efficiency(self) -> float:
        """Ratio of gold spent to gold earned."""
        if self.gold_earned == 0:
            return 0.0
        return self.gold_spent / self.gold_earned


@dataclass
class CombatStats:
    """Detailed combat statistics for a participant."""
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    largest_killing_spree: int = 0
    largest_multi_kill: int = 0
    double_kills: int = 0
    triple_kills: int = 0
    quadra_kills: int = 0
    penta_kills: int = 0
    total_damage_dealt: int = 0
    total_damage_to_champions: int = 0
    physical_damage_dealt: int = 0
    magic_damage_dealt: int = 0
    true_damage_dealt: int = 0
    total_damage_taken: int = 0
    damage_self_mitigated: int = 0
    total_heal: int = 0
    total_units_healed: int = 0
    time_ccing_others: int = 0

    @property
    def kda_ratio(self) -> float:
        """Calculate KDA ratio. Perfect KDA if no deaths."""
        if self.deaths == 0:
            return float(self.kills + self.assists)
        return (self.kills + self.assists) / self.deaths

    @property
    def kill_participation_score(self) -> float:
        """Simplified kill participation (needs team kills for full calc)."""
        return float(self.kills + self.assists)

    def damage_composition(self) -> Dict[str, float]:
        """Percentage breakdown of damage types."""
        total = self.physical_damage_dealt + self.magic_damage_dealt + self.true_damage_dealt
        if total == 0:
            return {"physical": 0.0, "magic": 0.0, "true": 0.0}
        return {
            "physical": self.physical_damage_dealt / total,
            "magic": self.magic_damage_dealt / total,
            "true": self.true_damage_dealt / total,
        }


@dataclass
class VisionStats:
    """Vision control statistics."""
    wards_placed: int = 0
    wards_killed: int = 0
    vision_wards_bought: int = 0
    vision_score: int = 0
    detector_wards_placed: int = 0

    @property
    def ward_efficiency(self) -> float:
        """Wards killed per ward placed ratio."""
        if self.wards_placed == 0:
            return 0.0
        return self.wards_killed / self.wards_placed


@dataclass
class FarmingStats:
    """Creep score and objective statistics."""
    total_minions_killed: int = 0
    neutral_minions_killed: int = 0
    cs_per_minute: float = 0.0
    first_blood: bool = False
    first_tower: bool = False
    turret_kills: int = 0
    inhibitor_kills: int = 0
    dragon_kills: int = 0
    baron_kills: int = 0
    rift_herald_kills: int = 0

    @property
    def total_cs(self) -> int:
        return self.total_minions_killed + self.neutral_minions_killed


@dataclass
class ParticipantData:
    """
    Complete data for a single match participant.
    Central data model referenced by all analysis modules.
    """
    # Identity
    summoner: SummonerIdentity = field(default_factory=lambda: SummonerIdentity(puuid=""))
    champion_id: int = 0
    champion_name: str = ""
    team_id: int = 0  # 100 = Blue, 200 = Red
    role: Role = Role.UNKNOWN
    summoner_spell_1: int = 0
    summoner_spell_2: int = 0

    # Stats
    combat: CombatStats = field(default_factory=CombatStats)
    vision: VisionStats = field(default_factory=VisionStats)
    farming: FarmingStats = field(default_factory=FarmingStats)
    items: ItemBuild = field(default_factory=ItemBuild)
    runes: RuneSelection = field(default_factory=RuneSelection)

    # Result
    win: bool = False
    game_result: GameResult = GameResult.UNKNOWN
    champion_level: int = 0
    time_played: int = 0

    @property
    def team_color(self) -> str:
        return "Blue" if self.team_id == 100 else "Red"

    def to_summary_dict(self) -> Dict[str, Any]:
        """Compact summary for quick lookups."""
        return {
            "champion": self.champion_name,
            "role": self.role.value,
            "kda": f"{self.combat.kills}/{self.combat.deaths}/{self.combat.assists}",
            "kda_ratio": round(self.combat.kda_ratio, 2),
            "cs": self.farming.total_cs,
            "vision": self.vision.vision_score,
            "damage": self.combat.total_damage_to_champions,
            "win": self.win,
            "team": self.team_color,
        }


@dataclass
class TeamData:
    """Team-level match data."""
    team_id: int = 0
    win: bool = False
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
    bans: List[int] = field(default_factory=list)
    participants: List[ParticipantData] = field(default_factory=list)

    @property
    def total_kills(self) -> int:
        return sum(p.combat.kills for p in self.participants)

    @property
    def total_deaths(self) -> int:
        return sum(p.combat.deaths for p in self.participants)

    @property
    def total_gold(self) -> int:
        return sum(p.items.gold_earned for p in self.participants)

    @property
    def total_damage(self) -> int:
        return sum(p.combat.total_damage_to_champions for p in self.participants)


@dataclass
class MatchTimeline:
    """Timeline events within a match."""
    frames: List[Dict[str, Any]] = field(default_factory=list)
    frame_interval_ms: int = 60000
    events: List[Dict[str, Any]] = field(default_factory=list)

    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        """Filter timeline events by type."""
        return [e for e in self.events if e.get("type") == event_type]

    def get_champion_kills(self) -> List[Dict[str, Any]]:
        return self.get_events_by_type("CHAMPION_KILL")

    def get_building_kills(self) -> List[Dict[str, Any]]:
        return self.get_events_by_type("BUILDING_KILL")

    def get_elite_monster_kills(self) -> List[Dict[str, Any]]:
        return self.get_events_by_type("ELITE_MONSTER_KILL")

    @property
    def total_duration_frames(self) -> int:
        return len(self.frames)


@dataclass
class MatchData:
    """
    Complete match record - the central aggregate model.
    All analysis modules consume and produce data relative to this schema.
    """
    # Identifiers
    match_id: str = ""
    game_id: int = 0
    platform_id: str = ""
    data_version: str = DATA_VERSION

    # Match metadata
    game_creation: Optional[datetime.datetime] = None
    game_start: Optional[datetime.datetime] = None
    game_end: Optional[datetime.datetime] = None
    game_duration: int = 0  # seconds
    game_mode: str = ""
    game_type: str = ""
    queue_type: QueueType = QueueType.CUSTOM
    map_id: int = 11  # Summoner's Rift
    game_version: str = ""

    # Teams and participants
    teams: List[TeamData] = field(default_factory=list)
    participants: List[ParticipantData] = field(default_factory=list)

    # Timeline (optional, heavy data)
    timeline: Optional[MatchTimeline] = None

    # Source tracking
    data_source: DataSource = DataSource.RIOT_API
    collected_at: Optional[datetime.datetime] = None
    schema_revision: int = SCHEMA_REVISION

    def __post_init__(self):
        if self.collected_at is None:
            self.collected_at = datetime.datetime.now(datetime.timezone.utc)

    @property
    def blue_team(self) -> Optional[TeamData]:
        for t in self.teams:
            if t.team_id == 100:
                return t
        return None

    @property
    def red_team(self) -> Optional[TeamData]:
        for t in self.teams:
            if t.team_id == 200:
                return t
        return None

    @property
    def winning_team_id(self) -> int:
        for t in self.teams:
            if t.win:
                return t.team_id
        return 0

    @property
    def is_remake(self) -> bool:
        return self.game_duration < MATCH_DURATION_MIN_SECONDS

    def get_participant_by_puuid(self, puuid: str) -> Optional[ParticipantData]:
        for p in self.participants:
            if p.summoner.puuid == puuid:
                return p
        return None

    def get_participants_by_team(self, team_id: int) -> List[ParticipantData]:
        return [p for p in self.participants if p.team_id == team_id]

    def get_participant_by_champion(self, champion_name: str) -> Optional[ParticipantData]:
        for p in self.participants:
            if p.champion_name.lower() == champion_name.lower():
                return p
        return None

    def fingerprint(self) -> str:
        """Unique match fingerprint for dedup."""
        return hashlib.sha256(
            f"{self.match_id}:{self.game_id}:{self.platform_id}".encode()
        ).hexdigest()[:24]

    def to_compact_dict(self) -> Dict[str, Any]:
        """Minimal representation for list views."""
        return {
            "match_id": self.match_id,
            "queue": self.queue_type.name,
            "duration": self.game_duration,
            "version": self.game_version,
            "blue_kills": self.blue_team.total_kills if self.blue_team else 0,
            "red_kills": self.red_team.total_kills if self.red_team else 0,
            "winning_team": "Blue" if self.winning_team_id == 100 else "Red",
            "is_remake": self.is_remake,
        }


# ─── Data Transformation Pipeline ────────────────────────────────────────────

T = TypeVar("T")


class DataTransformer(ABC, Generic[T]):
    """Abstract base for data transformations."""

    @abstractmethod
    def transform(self, raw: Dict[str, Any]) -> T:
        """Transform raw data dict into typed model."""
        ...

    @abstractmethod
    def reverse(self, model: T) -> Dict[str, Any]:
        """Reverse transform model back to dict."""
        ...


class RiotAPITransformer(DataTransformer[MatchData]):
    """
    Transform Riot API v5 match data into internal MatchData model.
    Handles all field mapping, normalization, and validation.
    """

    def transform(self, raw: Dict[str, Any]) -> MatchData:
        """Parse Riot API match-v5 response."""
        try:
            metadata = raw.get("metadata", {})
            info = raw.get("info", {})

            match = MatchData(
                match_id=metadata.get("matchId", ""),
                game_id=info.get("gameId", 0),
                platform_id=info.get("platformId", ""),
                game_duration=info.get("gameDuration", 0),
                game_mode=info.get("gameMode", ""),
                game_type=info.get("gameType", ""),
                game_version=info.get("gameVersion", ""),
                map_id=info.get("mapId", 11),
                data_source=DataSource.RIOT_API,
            )

            # Parse queue type
            queue_id = info.get("queueId", 0)
            match.queue_type = SchemaValidator.validate_queue_type(queue_id)

            # Parse timestamps
            game_creation_ts = info.get("gameCreation", 0)
            if game_creation_ts:
                match.game_creation = SchemaValidator.validate_timestamp(game_creation_ts)

            game_start_ts = info.get("gameStartTimestamp", 0)
            if game_start_ts:
                match.game_start = SchemaValidator.validate_timestamp(game_start_ts)

            game_end_ts = info.get("gameEndTimestamp", 0)
            if game_end_ts:
                match.game_end = SchemaValidator.validate_timestamp(game_end_ts)

            # Parse teams
            for team_raw in info.get("teams", []):
                team = self._parse_team(team_raw)
                match.teams.append(team)

            # Parse participants
            for p_raw in info.get("participants", []):
                participant = self._parse_participant(p_raw)
                match.participants.append(participant)

                # Link to team
                for team in match.teams:
                    if team.team_id == participant.team_id:
                        team.participants.append(participant)

            return match

        except Exception as e:
            logger.error(f"Failed to transform Riot API data: {e}")
            raise

    def _parse_team(self, raw: Dict[str, Any]) -> TeamData:
        """Parse team data from Riot API format."""
        objectives = raw.get("objectives", {})
        return TeamData(
            team_id=raw.get("teamId", 0),
            win=raw.get("win", False),
            first_blood=objectives.get("champion", {}).get("first", False),
            first_tower=objectives.get("tower", {}).get("first", False),
            first_dragon=objectives.get("dragon", {}).get("first", False),
            first_baron=objectives.get("baron", {}).get("first", False),
            first_rift_herald=objectives.get("riftHerald", {}).get("first", False),
            tower_kills=objectives.get("tower", {}).get("kills", 0),
            inhibitor_kills=objectives.get("inhibitor", {}).get("kills", 0),
            dragon_kills=objectives.get("dragon", {}).get("kills", 0),
            baron_kills=objectives.get("baron", {}).get("kills", 0),
            rift_herald_kills=objectives.get("riftHerald", {}).get("kills", 0),
            bans=[b.get("championId", 0) for b in raw.get("bans", [])],
        )

    def _parse_participant(self, raw: Dict[str, Any]) -> ParticipantData:
        """Parse participant data from Riot API format."""
        # Role mapping
        role_str = raw.get("teamPosition", raw.get("individualPosition", ""))
        try:
            role = Role(role_str) if role_str else Role.UNKNOWN
        except ValueError:
            role = Role.UNKNOWN

        participant = ParticipantData(
            summoner=SummonerIdentity(
                puuid=raw.get("puuid", ""),
                summoner_id=raw.get("summonerId", ""),
                game_name=raw.get("riotIdGameName", ""),
                tag_line=raw.get("riotIdTagline", ""),
                summoner_level=raw.get("summonerLevel", 0),
                profile_icon_id=raw.get("profileIcon", 0),
            ),
            champion_id=raw.get("championId", 0),
            champion_name=raw.get("championName", ""),
            team_id=raw.get("teamId", 0),
            role=role,
            summoner_spell_1=raw.get("summoner1Id", 0),
            summoner_spell_2=raw.get("summoner2Id", 0),
            win=raw.get("win", False),
            champion_level=raw.get("champLevel", 0),
            time_played=raw.get("timePlayed", 0),
            combat=CombatStats(
                kills=raw.get("kills", 0),
                deaths=raw.get("deaths", 0),
                assists=raw.get("assists", 0),
                largest_killing_spree=raw.get("largestKillingSpree", 0),
                largest_multi_kill=raw.get("largestMultiKill", 0),
                double_kills=raw.get("doubleKills", 0),
                triple_kills=raw.get("tripleKills", 0),
                quadra_kills=raw.get("quadraKills", 0),
                penta_kills=raw.get("pentaKills", 0),
                total_damage_dealt=raw.get("totalDamageDealt", 0),
                total_damage_to_champions=raw.get("totalDamageDealtToChampions", 0),
                physical_damage_dealt=raw.get("physicalDamageDealt", 0),
                magic_damage_dealt=raw.get("magicDamageDealt", 0),
                true_damage_dealt=raw.get("trueDamageDealt", 0),
                total_damage_taken=raw.get("totalDamageTaken", 0),
                damage_self_mitigated=raw.get("damageSelfMitigated", 0),
                total_heal=raw.get("totalHeal", 0),
                total_units_healed=raw.get("totalUnitsHealed", 0),
                time_ccing_others=raw.get("timeCCingOthers", 0),
            ),
            vision=VisionStats(
                wards_placed=raw.get("wardsPlaced", 0),
                wards_killed=raw.get("wardsKilled", 0),
                vision_wards_bought=raw.get("visionWardsBoughtInGame", 0),
                vision_score=raw.get("visionScore", 0),
                detector_wards_placed=raw.get("detectorWardsPlaced", 0),
            ),
            farming=FarmingStats(
                total_minions_killed=raw.get("totalMinionsKilled", 0),
                neutral_minions_killed=raw.get("neutralMinionsKilled", 0),
                first_blood=raw.get("firstBloodKill", False),
                first_tower=raw.get("firstTowerKill", False),
                turret_kills=raw.get("turretKills", 0),
                inhibitor_kills=raw.get("inhibitorKills", 0),
                dragon_kills=raw.get("dragonKills", 0),
                baron_kills=raw.get("baronKills", 0),
            ),
            items=ItemBuild(
                items=[
                    raw.get(f"item{i}", 0) for i in range(7)
                ],
                gold_spent=raw.get("goldSpent", 0),
                gold_earned=raw.get("goldEarned", 0),
            ),
            runes=self._parse_runes(raw.get("perks", {})),
        )

        # Set game result
        if participant.win:
            participant.game_result = GameResult.WIN
        else:
            participant.game_result = GameResult.LOSS

        return participant

    def _parse_runes(self, perks: Dict[str, Any]) -> RuneSelection:
        """Parse rune/perk data."""
        rune = RuneSelection()
        styles = perks.get("styles", [])
        if len(styles) >= 1:
            primary = styles[0]
            rune.primary_tree = primary.get("style", 0)
            selections = primary.get("selections", [])
            if selections:
                rune.primary_keystone = selections[0].get("perk", 0)
                rune.primary_runes = [s.get("perk", 0) for s in selections[1:]]
        if len(styles) >= 2:
            secondary = styles[1]
            rune.secondary_tree = secondary.get("style", 0)
            rune.secondary_runes = [
                s.get("perk", 0) for s in secondary.get("selections", [])
            ]
        stat_perks = perks.get("statPerks", {})
        rune.stat_shards = [
            stat_perks.get("defense", 0),
            stat_perks.get("flex", 0),
            stat_perks.get("offense", 0),
        ]
        return rune

    def reverse(self, model: MatchData) -> Dict[str, Any]:
        """Convert MatchData back to Riot API-like dict."""
        return {
            "metadata": {
                "matchId": model.match_id,
                "dataVersion": model.data_version,
                "participants": [p.summoner.puuid for p in model.participants],
            },
            "info": {
                "gameId": model.game_id,
                "platformId": model.platform_id,
                "gameDuration": model.game_duration,
                "gameMode": model.game_mode,
                "gameType": model.game_type,
                "gameVersion": model.game_version,
                "mapId": model.map_id,
                "queueId": model.queue_type.value,
                "teams": [asdict(t) for t in model.teams],
                "participants": [p.to_summary_dict() for p in model.participants],
            }
        }


class LCUTransformer(DataTransformer[MatchData]):
    """
    Transform LCU (League Client Update) API data into internal MatchData.
    LCU provides a different format than the Riot Web API.
    References Seraphine project patterns for LCU data mapping.
    """

    def transform(self, raw: Dict[str, Any]) -> MatchData:
        """Parse LCU match history endpoint response."""
        match = MatchData(
            match_id=str(raw.get("gameId", "")),
            game_id=raw.get("gameId", 0),
            platform_id=raw.get("platformId", ""),
            game_duration=raw.get("gameDuration", 0),
            game_mode=raw.get("gameMode", ""),
            game_type=raw.get("gameType", ""),
            game_version=raw.get("gameVersion", ""),
            data_source=DataSource.LCU_API,
        )

        game_creation = raw.get("gameCreationDate", "")
        if game_creation:
            try:
                match.game_creation = SchemaValidator.validate_timestamp(game_creation)
            except ValidationError:
                logger.warning(f"Could not parse LCU game creation date: {game_creation}")

        queue_id = raw.get("queueId", 0)
        match.queue_type = SchemaValidator.validate_queue_type(queue_id)

        for p_raw in raw.get("participants", []):
            stats = p_raw.get("stats", {})
            participant = ParticipantData(
                summoner=SummonerIdentity(
                    puuid=p_raw.get("puuid", ""),
                    summoner_id=str(p_raw.get("summonerId", "")),
                ),
                champion_id=p_raw.get("championId", 0),
                team_id=p_raw.get("teamId", 0),
                win=stats.get("win", False),
                combat=CombatStats(
                    kills=stats.get("kills", 0),
                    deaths=stats.get("deaths", 0),
                    assists=stats.get("assists", 0),
                    total_damage_to_champions=stats.get("totalDamageDealtToChampions", 0),
                ),
                farming=FarmingStats(
                    total_minions_killed=stats.get("totalMinionsKilled", 0),
                    neutral_minions_killed=stats.get("neutralMinionsKilled", 0),
                ),
                vision=VisionStats(
                    vision_score=stats.get("visionScore", 0),
                    wards_placed=stats.get("wardsPlaced", 0),
                ),
            )
            match.participants.append(participant)

        return match

    def reverse(self, model: MatchData) -> Dict[str, Any]:
        """Convert back to LCU-like format."""
        return {
            "gameId": model.game_id,
            "platformId": model.platform_id,
            "gameDuration": model.game_duration,
            "gameMode": model.game_mode,
            "participants": [
                {
                    "puuid": p.summoner.puuid,
                    "championId": p.champion_id,
                    "teamId": p.team_id,
                    "stats": {
                        "kills": p.combat.kills,
                        "deaths": p.combat.deaths,
                        "assists": p.combat.assists,
                        "win": p.win,
                    }
                }
                for p in model.participants
            ]
        }


# ─── Match History Collection ────────────────────────────────────────────────

@dataclass
class MatchHistoryQuery:
    """Query parameters for match history retrieval."""
    puuid: str
    region: Region = Region.NA1
    queue_type: Optional[QueueType] = None
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    start_index: int = 0
    count: int = 20
    champion_id: Optional[int] = None

    def to_query_params(self) -> Dict[str, Any]:
        """Convert to API query parameters."""
        params: Dict[str, Any] = {
            "start": self.start_index,
            "count": min(self.count, MAX_MATCH_HISTORY_DEPTH),
        }
        if self.queue_type:
            params["queue"] = self.queue_type.value
        if self.start_time:
            params["startTime"] = int(self.start_time.timestamp())
        if self.end_time:
            params["endTime"] = int(self.end_time.timestamp())
        if self.champion_id:
            params["champion"] = self.champion_id
        return params


@dataclass
class MatchHistoryResult:
    """Result container for match history queries."""
    query: MatchHistoryQuery
    matches: List[MatchData] = field(default_factory=list)
    total_available: int = 0
    has_more: bool = False
    fetched_at: Optional[datetime.datetime] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.fetched_at is None:
            self.fetched_at = datetime.datetime.now(datetime.timezone.utc)

    @property
    def match_count(self) -> int:
        return len(self.matches)

    def filter_by_queue(self, queue: QueueType) -> List[MatchData]:
        return [m for m in self.matches if m.queue_type == queue]

    def filter_by_champion(self, champion_name: str) -> List[MatchData]:
        results = []
        for m in self.matches:
            for p in m.participants:
                if (p.champion_name.lower() == champion_name.lower()
                        and p.summoner.puuid == self.query.puuid):
                    results.append(m)
                    break
        return results

    def win_rate(self) -> float:
        """Calculate win rate for the queried player."""
        if not self.matches:
            return 0.0
        wins = sum(
            1 for m in self.matches
            for p in m.participants
            if p.summoner.puuid == self.query.puuid and p.win
        )
        return wins / len(self.matches)


# ─── Module Interface Contract ────────────────────────────────────────────────

class HistoricalBattleInterface(ABC):
    """
    Interface contract that all historical battle submodules must implement.
    Ensures consistent initialization, health checking, and shutdown.
    """

    @abstractmethod
    async def initialize(self, config: Dict[str, Any]) -> bool:
        """Initialize the module with configuration."""
        ...

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Return module health status."""
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        ...

    @abstractmethod
    def get_module_info(self) -> Dict[str, str]:
        """Return module metadata."""
        ...


# ─── Initialization ──────────────────────────────────────────────────────────

def create_default_config() -> Dict[str, Any]:
    """Create default configuration for the battle data core."""
    return {
        "data_version": DATA_VERSION,
        "schema_revision": SCHEMA_REVISION,
        "default_region": DEFAULT_REGION,
        "max_history_depth": MAX_MATCH_HISTORY_DEPTH,
        "supported_regions": SUPPORTED_REGIONS,
        "cache_ttl_seconds": 300,
        "validation_strict_mode": True,
        "data_sources_priority": [
            DataSource.NETWORK_CAPTURE.value,
            DataSource.LCU_API.value,
            DataSource.RIOT_API.value,
            DataSource.CACHE.value,
        ],
    }


if __name__ == "__main__":
    # Self-test: create and validate sample data
    config = create_default_config()
    print(f"M806 Historical Battle Core v{DATA_VERSION}")
    print(f"Schema revision: {SCHEMA_REVISION}")
    print(f"Supported regions: {len(SUPPORTED_REGIONS)}")
    print(f"Config keys: {list(config.keys())}")

    # Test data model creation
    match = MatchData(
        match_id="NA1_4950000001",
        game_id=4950000001,
        platform_id="NA1",
        game_duration=1800,
        queue_type=QueueType.RANKED_SOLO,
    )
    print(f"Test match fingerprint: {match.fingerprint()}")
    print(f"Compact: {json.dumps(match.to_compact_dict(), indent=2)}")
    print("M806 self-test passed.")

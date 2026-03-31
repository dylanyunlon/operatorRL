#!/usr/bin/env python3
"""
M787: Historical Battle Data Integration
==========================================
查看 Seraphine (Zzaphkiel/Seraphine) 上现有 LCU API 的实现方式,
理解其模式,特别是数据获取和接口是如何分离的。
从 match-history endpoint 这个好例子开始。
然后,遵循该模式实现一个新的 HistoricalBattleDataManager,
让 OperatorRL 可以获取玩家历史战斗数据,并能持久化到本地缓存。
接着引入 DataNormalizer, 使分析模块能够消费标准化数据,
同时优化批量请求以减少API调用次数。
随后整合 MatchTimeline, 令系统支持时间线级别的详细数据,
进而增强 PlayerPerformanceTracker 的统计精度。
最终完善 BattleDataExporter, 确保所有数据兼容下游模块,
全面升级数据管道以达成实时+历史的混合数据架构。

Reference: github.com/ljszx/Seraphine → Zzaphkiel/Seraphine
"""

import os
import sys
import json
import time
import hashlib
import sqlite3
import threading
from enum import Enum
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field, asdict
from collections import defaultdict, OrderedDict
from urllib.parse import urljoin, urlencode
import http.client
import ssl
import base64
import struct
import logging

# Add parent to path for logging integration
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from logging_system.core_logger import get_logger, EventCategory
except ImportError:
    get_logger = None
    EventCategory = None

# ============================================================================
# Constants & Configuration
# ============================================================================

LCU_BASE_ENDPOINTS = {
    "match_history": "/lol-match-history/v1/products/lol/{puuid}/matches",
    "match_detail": "/lol-match-history/v1/games/{game_id}",
    "match_timeline": "/lol-match-history/v1/game-timelines/{game_id}",
    "current_summoner": "/lol-summoner/v1/current-summoner",
    "summoner_by_name": "/lol-summoner/v2/summoners?name={name}",
    "summoner_by_puuid": "/lol-summoner/v2/summoners/puuid/{puuid}",
    "ranked_stats": "/lol-ranked/v1/ranked-stats/{puuid}",
    "champion_mastery": "/lol-collections/v1/inventories/{summoner_id}/champion-mastery",
    "game_flow": "/lol-gameflow/v1/gameflow-phase",
    "champ_select": "/lol-champ-select/v1/session",
    "lobby": "/lol-lobby/v2/lobby",
}

RIOT_API_ENDPOINTS = {
    "match_v5": "/lol/match/v5/matches/{match_id}",
    "match_v5_timeline": "/lol/match/v5/matches/{match_id}/timeline",
    "match_v5_by_puuid": "/lol/match/v5/matches/by-puuid/{puuid}/ids",
    "summoner_v4": "/lol/summoner/v4/summoners/by-name/{name}",
    "league_v4": "/lol/league/v4/entries/by-summoner/{summoner_id}",
}

DEFAULT_MATCH_COUNT = 20
MAX_MATCH_COUNT = 100
CACHE_TTL_SECONDS = 3600  # 1 hour
DB_SCHEMA_VERSION = 3
BATCH_SIZE = 10

GAME_MODES = {
    "CLASSIC": "经典模式",
    "ARAM": "极地大乱斗",
    "URF": "无限火力",
    "NEXUSBLITZ": "极限闪击",
    "ULTBOOK": "终极魔典",
    "CHERRY": "斗魂竞技场",
    "PRACTICETOOL": "训练模式",
}

QUEUE_TYPE_MAP = {
    420: "单双排位",
    440: "灵活排位",
    450: "极地大乱斗",
    400: "匹配模式",
    900: "无限火力",
    1700: "斗魂竞技场",
    1300: "极限闪击",
}


# ============================================================================
# Data Models
# ============================================================================

class DataSource(Enum):
    LCU_LOCAL = "lcu_local"
    RIOT_API = "riot_api"
    CACHE = "cache"
    FIDDLER_CAPTURE = "fiddler_capture"


@dataclass
class SummonerProfile:
    """召唤师档案 - 对应 Seraphine 中的 Summoner 数据结构"""
    puuid: str
    summoner_id: str
    account_id: str
    display_name: str
    profile_icon_id: int
    summoner_level: int
    region: str = "NA1"
    last_updated: str = ""
    xp_since_last_level: int = 0
    xp_until_next_level: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_lcu_response(cls, data: Dict) -> 'SummonerProfile':
        return cls(
            puuid=data.get("puuid", ""),
            summoner_id=str(data.get("summonerId", "")),
            account_id=str(data.get("accountId", "")),
            display_name=data.get("displayName", data.get("gameName", "")),
            profile_icon_id=data.get("profileIconId", 0),
            summoner_level=data.get("summonerLevel", 0),
            last_updated=datetime.now(timezone.utc).isoformat(),
            xp_since_last_level=data.get("xpSinceLastLevel", 0),
            xp_until_next_level=data.get("xpUntilNextLevel", 0),
        )


@dataclass
class ParticipantData:
    """单局对局中的参与者数据"""
    puuid: str
    summoner_name: str
    champion_id: int
    champion_name: str
    team_id: int  # 100=蓝方, 200=红方
    role: str
    lane: str
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    gold_earned: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    vision_score: int = 0
    wards_placed: int = 0
    wards_destroyed: int = 0
    items: List[int] = field(default_factory=list)
    summoner_spells: List[int] = field(default_factory=list)
    runes: Dict[str, Any] = field(default_factory=dict)
    kda: float = 0.0
    kill_participation: float = 0.0
    damage_share: float = 0.0

    def compute_kda(self) -> float:
        self.kda = (self.kills + self.assists) / max(1, self.deaths)
        return self.kda

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_lcu_participant(cls, data: Dict) -> 'ParticipantData':
        stats = data.get("stats", {})
        return cls(
            puuid=data.get("puuid", ""),
            summoner_name=data.get("summonerName", ""),
            champion_id=data.get("championId", 0),
            champion_name=data.get("championName", "Unknown"),
            team_id=data.get("teamId", 0),
            role=data.get("role", "NONE"),
            lane=data.get("lane", "NONE"),
            kills=stats.get("kills", 0),
            deaths=stats.get("deaths", 0),
            assists=stats.get("assists", 0),
            cs=stats.get("totalMinionsKilled", 0) + stats.get("neutralMinionsKilled", 0),
            gold_earned=stats.get("goldEarned", 0),
            damage_dealt=stats.get("totalDamageDealtToChampions", 0),
            damage_taken=stats.get("totalDamageTaken", 0),
            vision_score=stats.get("visionScore", 0),
            wards_placed=stats.get("wardsPlaced", 0),
            wards_destroyed=stats.get("wardsKilled", 0),
            items=[stats.get(f"item{i}", 0) for i in range(7)],
            summoner_spells=[data.get("spell1Id", 0), data.get("spell2Id", 0)],
        )


@dataclass
class MatchRecord:
    """单局对局记录 - 对应 Seraphine 中的 Game/Match 数据结构"""
    game_id: int
    game_creation: int  # timestamp ms
    game_duration: int  # seconds
    game_mode: str
    game_type: str
    queue_id: int
    map_id: int
    platform_id: str
    season_id: int
    game_version: str
    participants: List[ParticipantData] = field(default_factory=list)
    teams: List[Dict] = field(default_factory=list)
    winning_team: int = 0
    data_source: str = DataSource.LCU_LOCAL.value
    fetched_at: str = ""
    cache_key: str = ""

    def __post_init__(self):
        if not self.fetched_at:
            self.fetched_at = datetime.now(timezone.utc).isoformat()
        if not self.cache_key:
            self.cache_key = f"match_{self.game_id}_{self.platform_id}"

    @property
    def game_datetime(self) -> datetime:
        return datetime.fromtimestamp(self.game_creation / 1000, tz=timezone.utc)

    @property
    def duration_str(self) -> str:
        m, s = divmod(self.game_duration, 60)
        return f"{m}:{s:02d}"

    @property
    def queue_name(self) -> str:
        return QUEUE_TYPE_MAP.get(self.queue_id, f"Queue#{self.queue_id}")

    def get_participant(self, puuid: str) -> Optional[ParticipantData]:
        for p in self.participants:
            if p.puuid == puuid:
                return p
        return None

    def get_team(self, team_id: int) -> List[ParticipantData]:
        return [p for p in self.participants if p.team_id == team_id]

    def compute_team_stats(self, team_id: int) -> Dict[str, Any]:
        team = self.get_team(team_id)
        if not team:
            return {}
        total_kills = sum(p.kills for p in team)
        total_damage = sum(p.damage_dealt for p in team)
        for p in team:
            p.compute_kda()
            p.kill_participation = (p.kills + p.assists) / max(1, total_kills)
            p.damage_share = p.damage_dealt / max(1, total_damage)
        return {
            "team_id": team_id,
            "total_kills": total_kills,
            "total_deaths": sum(p.deaths for p in team),
            "total_assists": sum(p.assists for p in team),
            "total_gold": sum(p.gold_earned for p in team),
            "total_damage": total_damage,
            "avg_vision": sum(p.vision_score for p in team) / len(team),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_id": self.game_id,
            "game_creation": self.game_creation,
            "game_duration": self.game_duration,
            "game_mode": self.game_mode,
            "queue_id": self.queue_id,
            "queue_name": self.queue_name,
            "game_version": self.game_version,
            "duration_str": self.duration_str,
            "winning_team": self.winning_team,
            "participants": [p.to_dict() for p in self.participants],
            "teams": self.teams,
            "data_source": self.data_source,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_lcu_response(cls, data: Dict) -> 'MatchRecord':
        participants_data = data.get("participants", [])
        participant_identities = data.get("participantIdentities", [])
        identity_map = {}
        for pi in participant_identities:
            pid = pi.get("participantId")
            player = pi.get("player", {})
            identity_map[pid] = {
                "puuid": player.get("puuid", ""),
                "summonerName": player.get("summonerName",
                                           player.get("gameName", "")),
            }

        participants = []
        for p in participants_data:
            pid = p.get("participantId")
            identity = identity_map.get(pid, {})
            merged = {**p, **identity}
            participants.append(ParticipantData.from_lcu_participant(merged))

        teams = data.get("teams", [])
        winning_team = 0
        for t in teams:
            if t.get("win") == "Win":
                winning_team = t.get("teamId", 0)
                break

        return cls(
            game_id=data.get("gameId", 0),
            game_creation=data.get("gameCreation", 0),
            game_duration=data.get("gameDuration", 0),
            game_mode=data.get("gameMode", ""),
            game_type=data.get("gameType", ""),
            queue_id=data.get("queueId", 0),
            map_id=data.get("mapId", 11),
            platform_id=data.get("platformId", ""),
            season_id=data.get("seasonId", 0),
            game_version=data.get("gameVersion", ""),
            participants=participants,
            teams=teams,
            winning_team=winning_team,
            data_source=DataSource.LCU_LOCAL.value,
        )


# ============================================================================
# Cache Layer
# ============================================================================

class MatchDataCache:
    """
    SQLite-based persistent cache for match data.
    Follows Seraphine's local caching pattern but adds TTL management
    and cross-session persistence.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_dir = Path(__file__).parent.parent / "data"
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "match_cache.db")
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS match_cache (
                    cache_key TEXT PRIMARY KEY,
                    game_id INTEGER,
                    puuid TEXT,
                    data_json TEXT NOT NULL,
                    data_source TEXT DEFAULT 'lcu_local',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS summoner_cache (
                    puuid TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_match_puuid
                ON match_cache(puuid)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_match_game_id
                ON match_cache(game_id)
            """)
            conn.execute(
                "INSERT OR REPLACE INTO cache_metadata VALUES (?, ?, ?)",
                ("schema_version", str(DB_SCHEMA_VERSION),
                 datetime.now(timezone.utc).isoformat())
            )
            conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def get_match(self, cache_key: str) -> Optional[Dict]:
        with self._lock:
            with self._get_conn() as conn:
                row = conn.execute(
                    "SELECT data_json, expires_at FROM match_cache WHERE cache_key = ?",
                    (cache_key,)
                ).fetchone()
                if row is None:
                    return None
                expires_at = datetime.fromisoformat(row[1])
                if datetime.now(timezone.utc) > expires_at:
                    conn.execute(
                        "DELETE FROM match_cache WHERE cache_key = ?",
                        (cache_key,)
                    )
                    conn.commit()
                    return None
                conn.execute(
                    "UPDATE match_cache SET access_count = access_count + 1, "
                    "last_accessed = ? WHERE cache_key = ?",
                    (datetime.now(timezone.utc).isoformat(), cache_key)
                )
                conn.commit()
                return json.loads(row[0])

    def put_match(self, cache_key: str, game_id: int, puuid: str,
                  data: Dict, source: str = "lcu_local",
                  ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl_seconds)
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO match_cache "
                    "(cache_key, game_id, puuid, data_json, data_source, "
                    "created_at, expires_at, access_count, last_accessed) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                    (cache_key, game_id, puuid,
                     json.dumps(data, ensure_ascii=False, default=str),
                     source, now.isoformat(), expires.isoformat(),
                     now.isoformat())
                )
                conn.commit()

    def get_matches_by_puuid(self, puuid: str,
                             limit: int = DEFAULT_MATCH_COUNT) -> List[Dict]:
        with self._lock:
            with self._get_conn() as conn:
                rows = conn.execute(
                    "SELECT data_json FROM match_cache "
                    "WHERE puuid = ? AND expires_at > ? "
                    "ORDER BY game_id DESC LIMIT ?",
                    (puuid, datetime.now(timezone.utc).isoformat(), limit)
                ).fetchall()
                return [json.loads(r[0]) for r in rows]

    def get_cache_stats(self) -> Dict[str, Any]:
        with self._lock:
            with self._get_conn() as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM match_cache"
                ).fetchone()[0]
                expired = conn.execute(
                    "SELECT COUNT(*) FROM match_cache WHERE expires_at < ?",
                    (datetime.now(timezone.utc).isoformat(),)
                ).fetchone()[0]
                return {
                    "total_entries": total,
                    "expired_entries": expired,
                    "active_entries": total - expired,
                    "db_path": self.db_path,
                    "db_size_mb": round(
                        os.path.getsize(self.db_path) / 1024 / 1024, 2
                    ) if os.path.exists(self.db_path) else 0,
                }

    def cleanup_expired(self) -> int:
        with self._lock:
            with self._get_conn() as conn:
                cursor = conn.execute(
                    "DELETE FROM match_cache WHERE expires_at < ?",
                    (datetime.now(timezone.utc).isoformat(),)
                )
                conn.commit()
                return cursor.rowcount


# ============================================================================
# Data Normalizer
# ============================================================================

class DataNormalizer:
    """
    Normalizes match data from different sources (LCU, Riot API, Fiddler capture)
    into a unified format consumable by downstream analysis modules.
    """

    ROLE_MAPPING = {
        "SOLO": "TOP", "NONE": "FILL",
        "DUO_CARRY": "ADC", "DUO_SUPPORT": "SUPPORT",
        "DUO": "BOT",
    }

    @staticmethod
    def normalize_role(role: str, lane: str) -> str:
        if lane == "JUNGLE":
            return "JUNGLE"
        if lane == "MIDDLE":
            return "MID"
        if lane == "TOP":
            return "TOP"
        if lane == "BOTTOM":
            mapped = DataNormalizer.ROLE_MAPPING.get(role, role)
            return mapped
        return DataNormalizer.ROLE_MAPPING.get(role, role)

    @staticmethod
    def normalize_match(match: MatchRecord) -> Dict[str, Any]:
        normalized = {
            "match_id": f"{match.platform_id}_{match.game_id}",
            "game_id": match.game_id,
            "timestamp": match.game_creation,
            "datetime": match.game_datetime.isoformat(),
            "duration_seconds": match.game_duration,
            "duration_display": match.duration_str,
            "mode": match.game_mode,
            "queue": match.queue_name,
            "queue_id": match.queue_id,
            "patch": match.game_version,
            "winning_team": match.winning_team,
            "blue_team": {
                "participants": [],
                "stats": match.compute_team_stats(100),
            },
            "red_team": {
                "participants": [],
                "stats": match.compute_team_stats(200),
            },
        }

        for p in match.participants:
            p.compute_kda()
            p_norm = {
                "puuid": p.puuid,
                "name": p.summoner_name,
                "champion": p.champion_name,
                "champion_id": p.champion_id,
                "role": DataNormalizer.normalize_role(p.role, p.lane),
                "kda": round(p.kda, 2),
                "kills": p.kills,
                "deaths": p.deaths,
                "assists": p.assists,
                "cs": p.cs,
                "cs_per_min": round(
                    p.cs / max(1, match.game_duration / 60), 1
                ),
                "gold": p.gold_earned,
                "gold_per_min": round(
                    p.gold_earned / max(1, match.game_duration / 60), 0
                ),
                "damage": p.damage_dealt,
                "damage_taken": p.damage_taken,
                "vision_score": p.vision_score,
                "items": p.items,
                "kill_participation": round(p.kill_participation, 2),
                "damage_share": round(p.damage_share, 2),
                "won": p.team_id == match.winning_team,
            }
            if p.team_id == 100:
                normalized["blue_team"]["participants"].append(p_norm)
            else:
                normalized["red_team"]["participants"].append(p_norm)

        return normalized


# ============================================================================
# Historical Battle Data Manager
# ============================================================================

class HistoricalBattleDataManager:
    """
    Core manager for historical battle data retrieval and analysis.
    Integrates LCU API patterns from Seraphine with OperatorRL's
    agentic feedback loop.
    
    Key capabilities:
    1. Batch fetch match history via LCU API
    2. Cache management with TTL
    3. Data normalization for downstream modules
    4. Player performance tracking across matches
    5. Export for analysis pipeline
    """

    def __init__(self, cache: Optional[MatchDataCache] = None):
        self.cache = cache or MatchDataCache()
        self.normalizer = DataNormalizer()
        self._matches: Dict[int, MatchRecord] = OrderedDict()
        self._summoners: Dict[str, SummonerProfile] = {}
        self._logger = None
        if get_logger:
            self._logger = get_logger("M787")

    def _log(self, level: str, message: str, **kwargs):
        if self._logger:
            getattr(self._logger, level)(message, **kwargs)

    def fetch_match_history(self, puuid: str,
                            count: int = DEFAULT_MATCH_COUNT,
                            queue_id: Optional[int] = None,
                            begin_index: int = 0) -> List[MatchRecord]:
        """
        Fetch match history for a player.
        In production, this calls the LCU API. Here we define the interface
        and data flow that matches Seraphine's implementation.
        """
        self._log("info", f"Fetching {count} matches for puuid={puuid[:8]}...",
                  category=EventCategory.MATCH_DATA if EventCategory else None,
                  data={"puuid": puuid[:8], "count": count, "queue_id": queue_id})

        # Check cache first
        cached = self.cache.get_matches_by_puuid(puuid, limit=count)
        if cached and len(cached) >= count:
            self._log("info", f"Cache hit: {len(cached)} matches found",
                      category=EventCategory.MATCH_DATA if EventCategory else None)
            return [self._dict_to_match(m) for m in cached[:count]]

        # Build LCU request (interface definition)
        endpoint = LCU_BASE_ENDPOINTS["match_history"].format(puuid=puuid)
        params = {"begIndex": begin_index, "endIndex": begin_index + count}
        if queue_id is not None:
            params["queueId"] = queue_id

        self._log("debug", f"LCU endpoint: {endpoint}",
                  data={"params": params})

        # In production: result = self._lcu_request("GET", endpoint, params)
        # For now, return empty list (actual LCU connection handled by M788)
        return []

    def store_match(self, match: MatchRecord, puuid: str = "") -> None:
        """Store a match record in both memory and persistent cache."""
        self._matches[match.game_id] = match
        self.cache.put_match(
            cache_key=match.cache_key,
            game_id=match.game_id,
            puuid=puuid,
            data=match.to_dict(),
            source=match.data_source
        )

    def get_player_history_analysis(self, puuid: str,
                                     match_count: int = 20) -> Dict[str, Any]:
        """
        Analyze a player's recent match history.
        This is the core feature that Seraphine provides for champion select.
        """
        matches = self.fetch_match_history(puuid, count=match_count)
        if not matches:
            return {"puuid": puuid, "matches_analyzed": 0, "error": "no_data"}

        wins = 0
        total_kills = 0
        total_deaths = 0
        total_assists = 0
        champion_counts: Dict[str, int] = defaultdict(int)
        champion_wins: Dict[str, int] = defaultdict(int)
        role_counts: Dict[str, int] = defaultdict(int)
        recent_streaks: List[bool] = []

        for match in matches:
            participant = match.get_participant(puuid)
            if not participant:
                continue

            won = participant.team_id == match.winning_team
            recent_streaks.append(won)
            if won:
                wins += 1
                champion_wins[participant.champion_name] += 1

            total_kills += participant.kills
            total_deaths += participant.deaths
            total_assists += participant.assists
            champion_counts[participant.champion_name] += 1
            role = self.normalizer.normalize_role(participant.role, participant.lane)
            role_counts[role] += 1

        n = len(matches)
        avg_kda = (total_kills + total_assists) / max(1, total_deaths)
        win_rate = wins / n if n > 0 else 0

        # Compute current streak
        current_streak = 0
        streak_type = None
        for result in reversed(recent_streaks):
            if streak_type is None:
                streak_type = result
                current_streak = 1
            elif result == streak_type:
                current_streak += 1
            else:
                break

        # Top champions
        top_champs = sorted(
            champion_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]

        return {
            "puuid": puuid,
            "matches_analyzed": n,
            "win_rate": round(win_rate * 100, 1),
            "wins": wins,
            "losses": n - wins,
            "avg_kda": round(avg_kda, 2),
            "avg_kills": round(total_kills / max(1, n), 1),
            "avg_deaths": round(total_deaths / max(1, n), 1),
            "avg_assists": round(total_assists / max(1, n), 1),
            "current_streak": {
                "type": "win" if streak_type else "loss",
                "count": current_streak,
            },
            "top_champions": [
                {
                    "name": name,
                    "games": count,
                    "wins": champion_wins.get(name, 0),
                    "win_rate": round(
                        champion_wins.get(name, 0) / count * 100, 1
                    ),
                }
                for name, count in top_champs
            ],
            "role_distribution": dict(role_counts),
        }

    def get_opponent_scouting_report(self, puuid: str,
                                      match_count: int = 10) -> Dict[str, Any]:
        """
        Generate scouting report for an opponent.
        Used during champion select to evaluate enemy players.
        """
        analysis = self.get_player_history_analysis(puuid, match_count)
        if analysis.get("error"):
            return {"status": "unavailable", "reason": analysis["error"]}

        threat_level = "low"
        if analysis["win_rate"] > 60:
            threat_level = "high"
        elif analysis["win_rate"] > 50:
            threat_level = "medium"

        return {
            "puuid": puuid[:8],
            "threat_level": threat_level,
            "win_rate": analysis["win_rate"],
            "avg_kda": analysis["avg_kda"],
            "streak": analysis["current_streak"],
            "main_champions": analysis["top_champions"][:3],
            "preferred_role": max(
                analysis.get("role_distribution", {"FILL": 1}).items(),
                key=lambda x: x[1]
            )[0] if analysis.get("role_distribution") else "UNKNOWN",
            "recommendation": self._generate_recommendation(analysis),
        }

    def _generate_recommendation(self, analysis: Dict) -> str:
        if analysis["win_rate"] > 65:
            return "高威胁玩家,注意其主玩英雄的ban位,避免正面交锋"
        elif analysis["win_rate"] > 55:
            return "中等威胁,需要团队配合应对"
        elif analysis["win_rate"] < 40:
            return "低威胁玩家,可以考虑针对其薄弱位置"
        else:
            return "普通玩家,正常对局即可"

    def _dict_to_match(self, data: Dict) -> MatchRecord:
        """Convert cached dictionary back to MatchRecord."""
        participants = [
            ParticipantData(**p) if isinstance(p, dict) else p
            for p in data.get("participants", [])
        ]
        return MatchRecord(
            game_id=data.get("game_id", 0),
            game_creation=data.get("game_creation", 0),
            game_duration=data.get("game_duration", 0),
            game_mode=data.get("game_mode", ""),
            game_type=data.get("game_type", ""),
            queue_id=data.get("queue_id", 0),
            map_id=data.get("map_id", 11),
            platform_id=data.get("platform_id", ""),
            season_id=data.get("season_id", 0),
            game_version=data.get("game_version", ""),
            participants=participants,
            teams=data.get("teams", []),
            winning_team=data.get("winning_team", 0),
            data_source=data.get("data_source", DataSource.CACHE.value),
            fetched_at=data.get("fetched_at", ""),
        )

    def export_for_pipeline(self, puuid: str,
                            count: int = 50) -> List[Dict[str, Any]]:
        """Export normalized match data for the data pipeline (M794)."""
        matches = self.fetch_match_history(puuid, count=count)
        return [self.normalizer.normalize_match(m) for m in matches]


# ============================================================================
# Battle Data Exporter
# ============================================================================

class BattleDataExporter:
    """Export historical battle data in various formats for downstream modules."""

    @staticmethod
    def to_json(matches: List[MatchRecord], output_path: str) -> str:
        data = [m.to_dict() for m in matches]
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        return output_path

    @staticmethod
    def to_csv_summary(matches: List[MatchRecord], puuid: str,
                       output_path: str) -> str:
        import csv
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "game_id", "datetime", "duration", "mode", "champion",
                "role", "kills", "deaths", "assists", "kda",
                "cs", "gold", "damage", "vision", "result"
            ])
            for match in matches:
                p = match.get_participant(puuid)
                if not p:
                    continue
                p.compute_kda()
                writer.writerow([
                    match.game_id,
                    match.game_datetime.strftime("%Y-%m-%d %H:%M"),
                    match.duration_str,
                    match.queue_name,
                    p.champion_name,
                    DataNormalizer.normalize_role(p.role, p.lane),
                    p.kills, p.deaths, p.assists,
                    round(p.kda, 2),
                    p.cs, p.gold_earned, p.damage_dealt, p.vision_score,
                    "WIN" if p.team_id == match.winning_team else "LOSS",
                ])
        return output_path

    @staticmethod
    def generate_player_card(analysis: Dict) -> Dict[str, Any]:
        """Generate a player card summary for UI display."""
        return {
            "type": "player_card",
            "version": "1.0",
            "puuid": analysis.get("puuid", ""),
            "summary": {
                "win_rate": f"{analysis.get('win_rate', 0)}%",
                "record": f"{analysis.get('wins', 0)}W {analysis.get('losses', 0)}L",
                "kda": f"{analysis.get('avg_kda', 0)}",
                "streak": analysis.get("current_streak", {}),
            },
            "champions": analysis.get("top_champions", []),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


# ============================================================================
# Module Self-Test
# ============================================================================

def self_test() -> Dict[str, Any]:
    """Run self-test to validate module functionality."""
    results = {"module": "M787", "name": "historical_battle_data", "tests": []}

    # Test 1: Cache initialization
    try:
        cache = MatchDataCache()
        stats = cache.get_cache_stats()
        results["tests"].append({
            "name": "cache_init", "status": "pass",
            "detail": f"DB at {stats['db_path']}"
        })
    except Exception as e:
        results["tests"].append({
            "name": "cache_init", "status": "fail", "error": str(e)
        })
        return results

    # Test 2: Data model creation
    try:
        profile = SummonerProfile(
            puuid="test-puuid-123", summoner_id="12345",
            account_id="67890", display_name="TestPlayer",
            profile_icon_id=1, summoner_level=30
        )
        assert profile.display_name == "TestPlayer"
        results["tests"].append({"name": "data_model", "status": "pass"})
    except Exception as e:
        results["tests"].append({
            "name": "data_model", "status": "fail", "error": str(e)
        })

    # Test 3: Match record creation and normalization
    try:
        match = MatchRecord(
            game_id=123456, game_creation=1711900000000,
            game_duration=1800, game_mode="CLASSIC",
            game_type="MATCHED_GAME", queue_id=420,
            map_id=11, platform_id="NA1", season_id=14,
            game_version="26.6.1",
            participants=[
                ParticipantData(
                    puuid="test-puuid-123", summoner_name="TestPlayer",
                    champion_id=1, champion_name="Annie", team_id=100,
                    role="SOLO", lane="MIDDLE", kills=10, deaths=3,
                    assists=8, cs=200, gold_earned=12000,
                    damage_dealt=25000, damage_taken=15000, vision_score=30
                ),
            ],
            winning_team=100
        )
        normalized = DataNormalizer.normalize_match(match)
        assert normalized["match_id"] == "NA1_123456"
        assert normalized["duration_display"] == "30:00"
        results["tests"].append({"name": "normalization", "status": "pass"})
    except Exception as e:
        results["tests"].append({
            "name": "normalization", "status": "fail", "error": str(e)
        })

    # Test 4: Cache put/get
    try:
        cache.put_match("test_key_1", 123456, "test-puuid-123",
                        match.to_dict())
        retrieved = cache.get_match("test_key_1")
        assert retrieved is not None
        assert retrieved["game_id"] == 123456
        results["tests"].append({"name": "cache_put_get", "status": "pass"})
    except Exception as e:
        results["tests"].append({
            "name": "cache_put_get", "status": "fail", "error": str(e)
        })

    # Test 5: Manager initialization
    try:
        manager = HistoricalBattleDataManager(cache=cache)
        manager.store_match(match, puuid="test-puuid-123")
        results["tests"].append({"name": "manager_init", "status": "pass"})
    except Exception as e:
        results["tests"].append({
            "name": "manager_init", "status": "fail", "error": str(e)
        })

    results["overall"] = "pass" if all(
        t["status"] == "pass" for t in results["tests"]
    ) else "fail"
    return results


if __name__ == "__main__":
    test_results = self_test()
    print(json.dumps(test_results, indent=2))

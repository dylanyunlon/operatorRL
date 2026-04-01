#!/usr/bin/env python3
"""
M1048: Riot API Data Models
============================

OperatorRL Agentic System: 自部署 自环境反馈 自演化

Type-safe data models for all Riot Games API responses intercepted
through Fiddler/Proxifier or queried via LCU API.

References:
    - Seraphine: app/lol/tools.py tier/rank data structures
    - LeagueAI (Oleffa/LeagueAI): game object detection models
    - leagueoflegends-optimizer: oracle-devrel match data schema
    - Riot Developer Portal: developer.riotgames.com

Production Critique:
    1. User: All models support partial parsing — missing fields get
       sensible defaults rather than raising errors. Real API responses
       vary by region, patch version, and game mode.
    2. System: Models are pure dataclasses with no I/O. Serialization
       is JSON-native. Memory footprint per match is ~2KB.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple, Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GameMode(Enum):
    CLASSIC = "CLASSIC"
    ARAM = "ARAM"
    URF = "URF"
    RANKED_SOLO = "RANKED_SOLO_5x5"
    RANKED_FLEX = "RANKED_FLEX_SR"
    NORMAL_DRAFT = "NORMAL"
    CUSTOM = "CUSTOM"
    TUTORIAL = "TUTORIAL"
    PRACTICE = "PRACTICETOOL"
    TFT = "TFT"
    UNKNOWN = "UNKNOWN"


class Tier(Enum):
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

    @property
    def numeric_value(self) -> int:
        """Numeric tier value for comparison (higher = better)."""
        order = {
            "IRON": 1, "BRONZE": 2, "SILVER": 3, "GOLD": 4,
            "PLATINUM": 5, "EMERALD": 6, "DIAMOND": 7,
            "MASTER": 8, "GRANDMASTER": 9, "CHALLENGER": 10,
            "UNRANKED": 0,
        }
        return order.get(self.value, 0)


class Division(Enum):
    I = "I"
    II = "II"
    III = "III"
    IV = "IV"
    NONE = ""


class Lane(Enum):
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MID = "MID"
    MIDDLE = "MIDDLE"
    BOT = "BOTTOM"
    BOTTOM = "BOTTOM"
    ADC = "ADC"
    SUPPORT = "SUPPORT"
    UTILITY = "UTILITY"
    FILL = "FILL"
    NONE = "NONE"
    UNKNOWN = ""

    @classmethod
    def normalize(cls, raw: str) -> 'Lane':
        """Normalize lane names across different API versions."""
        mapping = {
            "TOP": cls.TOP, "JUNGLE": cls.JUNGLE,
            "MID": cls.MID, "MIDDLE": cls.MID,
            "BOT": cls.BOT, "BOTTOM": cls.BOT, "ADC": cls.BOT,
            "SUPPORT": cls.SUPPORT, "UTILITY": cls.SUPPORT,
            "FILL": cls.FILL,
        }
        return mapping.get(raw.upper(), cls.UNKNOWN)


class GamePhase(Enum):
    """LoL client gameflow phases."""
    NONE = "None"
    LOBBY = "Lobby"
    MATCHMAKING = "Matchmaking"
    CHECKED_INTO_TOURNAMENT = "CheckedIntoTournament"
    READY_CHECK = "ReadyCheck"
    CHAMP_SELECT = "ChampSelect"
    GAME_START = "GameStart"
    IN_PROGRESS = "InProgress"
    RECONNECT = "Reconnect"
    WAITING_FOR_STATS = "WaitingForStats"
    PRE_END_OF_GAME = "PreEndOfGame"
    END_OF_GAME = "EndOfGame"


# ---------------------------------------------------------------------------
# Core Data Models
# ---------------------------------------------------------------------------

@dataclass
class SummonerInfo:
    """Summoner profile information."""
    puuid: str = ""
    summoner_id: int = 0
    account_id: int = 0
    display_name: str = ""
    game_name: str = ""
    tag_line: str = ""
    summoner_level: int = 0
    profile_icon_id: int = 0
    internal_name: str = ""
    region: str = ""

    @classmethod
    def from_lcu_response(cls, data: Dict) -> 'SummonerInfo':
        """Parse from LCU /lol-summoner/v1/current-summoner response."""
        if not data:
            return cls()
        return cls(
            puuid=data.get('puuid', ''),
            summoner_id=data.get('summonerId', 0),
            account_id=data.get('accountId', 0),
            display_name=data.get('displayName', ''),
            game_name=data.get('gameName', ''),
            tag_line=data.get('tagLine', ''),
            summoner_level=data.get('summonerLevel', 0),
            profile_icon_id=data.get('profileIconId', 0),
            internal_name=data.get('internalName', ''),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v}

    @property
    def full_name(self) -> str:
        if self.game_name and self.tag_line:
            return f"{self.game_name}#{self.tag_line}"
        return self.display_name or self.internal_name


@dataclass
class RankedInfo:
    """Ranked statistics for a summoner."""
    tier: str = "UNRANKED"
    division: str = ""
    league_points: int = 0
    wins: int = 0
    losses: int = 0
    queue_type: str = ""
    is_provisional: bool = False
    mini_series_progress: str = ""

    @classmethod
    def from_lcu_response(
        cls, data: Dict, queue_type: str = "RANKED_SOLO_5x5"
    ) -> 'RankedInfo':
        """Parse from LCU /lol-ranked/v1/ranked-stats response."""
        if not data:
            return cls()
        queues = data.get('queues', [])
        if isinstance(queues, list):
            for q in queues:
                if q.get('queueType') == queue_type:
                    return cls(
                        tier=q.get('tier', 'UNRANKED'),
                        division=q.get('division', ''),
                        league_points=q.get('leaguePoints', 0),
                        wins=q.get('wins', 0),
                        losses=q.get('losses', 0),
                        queue_type=queue_type,
                        is_provisional=q.get('isProvisional', False),
                    )
        # Alternative format
        qmap = data.get('queueMap', {})
        if isinstance(qmap, dict):
            q = qmap.get(queue_type, {})
            if q:
                return cls(
                    tier=q.get('tier', 'UNRANKED'),
                    division=q.get('division', ''),
                    league_points=q.get('leaguePoints', 0),
                    wins=q.get('wins', 0),
                    losses=q.get('losses', 0),
                    queue_type=queue_type,
                )
        return cls()

    @property
    def total_games(self) -> int:
        return self.wins + self.losses

    @property
    def winrate(self) -> float:
        if self.total_games == 0:
            return 0.0
        return round(self.wins / self.total_games * 100, 1)

    @property
    def rank_string(self) -> str:
        if self.tier == "UNRANKED":
            return "Unranked"
        return f"{self.tier} {self.division} {self.league_points}LP"

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d['total_games'] = self.total_games
        d['winrate'] = self.winrate
        d['rank_string'] = self.rank_string
        return d


@dataclass
class ChampionStats:
    """Per-champion statistics for a summoner."""
    champion_id: int = 0
    champion_name: str = ""
    games_played: int = 0
    wins: int = 0
    losses: int = 0
    kills_avg: float = 0.0
    deaths_avg: float = 0.0
    assists_avg: float = 0.0
    cs_avg: float = 0.0
    gold_avg: float = 0.0
    damage_avg: float = 0.0
    vision_score_avg: float = 0.0
    most_played_lane: str = ""
    last_played_timestamp: int = 0

    @property
    def winrate(self) -> float:
        if self.games_played == 0:
            return 0.0
        return round(self.wins / self.games_played * 100, 1)

    @property
    def kda(self) -> float:
        if self.deaths_avg == 0:
            return (self.kills_avg + self.assists_avg) * 1.2
        return round(
            (self.kills_avg + self.assists_avg) / self.deaths_avg, 2)

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d['winrate'] = self.winrate
        d['kda'] = self.kda
        return d


@dataclass
class MatchParticipant:
    """Single participant in a match."""
    summoner_name: str = ""
    puuid: str = ""
    summoner_id: int = 0
    champion_id: int = 0
    champion_name: str = ""
    team_id: int = 0
    lane: str = ""
    role: str = ""
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    total_damage_dealt: int = 0
    total_damage_taken: int = 0
    gold_earned: int = 0
    cs: int = 0
    vision_score: int = 0
    wards_placed: int = 0
    wards_killed: int = 0
    items: List[int] = field(default_factory=list)
    summoner_spells: List[int] = field(default_factory=list)
    rune_primary: int = 0
    rune_secondary: int = 0
    level: int = 0
    won: bool = False
    multi_kills: int = 0
    largest_killing_spree: int = 0
    time_ccing_others: int = 0
    damage_to_objectives: int = 0
    damage_to_turrets: int = 0

    @classmethod
    def from_match_data(cls, p: Dict) -> 'MatchParticipant':
        """Parse from match history participant data."""
        stats = p.get('stats', p)
        return cls(
            summoner_name=p.get('summonerName',
                                p.get('riotIdGameName', '')),
            puuid=p.get('puuid', ''),
            summoner_id=p.get('summonerId', 0),
            champion_id=p.get('championId', 0),
            champion_name=p.get('championName', ''),
            team_id=p.get('teamId', 0),
            lane=p.get('lane', p.get('individualPosition', '')),
            role=p.get('role', ''),
            kills=stats.get('kills', 0),
            deaths=stats.get('deaths', 0),
            assists=stats.get('assists', 0),
            total_damage_dealt=stats.get(
                'totalDamageDealtToChampions', 0),
            total_damage_taken=stats.get('totalDamageTaken', 0),
            gold_earned=stats.get('goldEarned', 0),
            cs=stats.get('totalMinionsKilled', 0)
                + stats.get('neutralMinionsKilled', 0),
            vision_score=stats.get('visionScore', 0),
            wards_placed=stats.get('wardsPlaced', 0),
            wards_killed=stats.get('wardsKilled', 0),
            items=[stats.get(f'item{i}', 0) for i in range(7)],
            summoner_spells=[p.get('spell1Id', 0), p.get('spell2Id', 0)],
            level=stats.get('champLevel', 0),
            won=stats.get('win', False),
            multi_kills=stats.get('largestMultiKill', 0),
            largest_killing_spree=stats.get('largestKillingSpree', 0),
            time_ccing_others=stats.get('timeCCingOthers', 0),
            damage_to_objectives=stats.get('damageDealtToObjectives', 0),
            damage_to_turrets=stats.get('damageDealtToTurrets', 0),
        )

    @property
    def kda(self) -> float:
        if self.deaths == 0:
            return float(self.kills + self.assists)
        return round(
            (self.kills + self.assists) / self.deaths, 2)

    @property
    def cs_per_min(self) -> float:
        """Requires match_duration to be set externally; returns raw cs."""
        return float(self.cs)

    def to_dict(self) -> Dict[str, Any]:
        d = self.__dict__.copy()
        d['kda'] = self.kda
        return d


@dataclass
class MatchData:
    """Complete match data."""
    game_id: int = 0
    game_creation: int = 0
    game_duration: int = 0
    game_mode: str = ""
    game_type: str = ""
    game_version: str = ""
    map_id: int = 0
    queue_id: int = 0
    platform_id: str = ""
    participants: List[MatchParticipant] = field(default_factory=list)
    teams: List[Dict] = field(default_factory=list)

    @classmethod
    def from_lcu_response(cls, data: Dict) -> 'MatchData':
        """Parse from LCU match history response."""
        if not data:
            return cls()
        participants = []
        identity_map = {}
        for pi in data.get('participantIdentities', []):
            pid = pi.get('participantId', 0)
            player = pi.get('player', {})
            identity_map[pid] = player
        for p in data.get('participants', []):
            pid = p.get('participantId', 0)
            identity = identity_map.get(pid, {})
            merged = {**p, **identity}
            participants.append(MatchParticipant.from_match_data(merged))
        return cls(
            game_id=data.get('gameId', 0),
            game_creation=data.get('gameCreation', 0),
            game_duration=data.get('gameDuration', 0),
            game_mode=data.get('gameMode', ''),
            game_type=data.get('gameType', ''),
            game_version=data.get('gameVersion', ''),
            map_id=data.get('mapId', 0),
            queue_id=data.get('queueId', 0),
            platform_id=data.get('platformId', ''),
            participants=participants,
            teams=data.get('teams', []),
        )

    @property
    def duration_minutes(self) -> float:
        return round(self.game_duration / 60.0, 1)

    @property
    def winning_team_id(self) -> int:
        for team in self.teams:
            if isinstance(team, dict) and team.get('win') in (
                'Win', True, 'true'):
                return team.get('teamId', 0)
        for p in self.participants:
            if p.won:
                return p.team_id
        return 0

    def get_team_participants(
        self, team_id: int
    ) -> List[MatchParticipant]:
        return [p for p in self.participants if p.team_id == team_id]

    def get_participant_by_puuid(
        self, puuid: str
    ) -> Optional[MatchParticipant]:
        for p in self.participants:
            if p.puuid == puuid:
                return p
        return None

    def get_participant_by_name(
        self, name: str
    ) -> Optional[MatchParticipant]:
        name_lower = name.lower()
        for p in self.participants:
            if p.summoner_name.lower() == name_lower:
                return p
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'game_id': self.game_id,
            'game_creation': self.game_creation,
            'game_duration': self.game_duration,
            'duration_minutes': self.duration_minutes,
            'game_mode': self.game_mode,
            'game_version': self.game_version,
            'queue_id': self.queue_id,
            'winning_team_id': self.winning_team_id,
            'participants': [p.to_dict() for p in self.participants],
        }


@dataclass
class ChampSelectState:
    """Current champion select state."""
    phase: str = "unknown"
    timer_remaining: float = 0.0
    local_player_cell_id: int = -1
    my_team: List[Dict] = field(default_factory=list)
    their_team: List[Dict] = field(default_factory=list)
    bans: List[int] = field(default_factory=list)
    is_planning_phase: bool = False

    @classmethod
    def from_lcu_response(cls, data: Dict) -> 'ChampSelectState':
        if not data:
            return cls()
        timer = data.get('timer', {})
        bans_list = []
        for action_row in data.get('actions', []):
            if isinstance(action_row, list):
                for action in action_row:
                    if (action.get('type') == 'ban'
                            and action.get('completed')):
                        bans_list.append(action.get('championId', 0))
        return cls(
            phase=timer.get('phase', 'unknown'),
            timer_remaining=timer.get('adjustedTimeLeftInPhase', 0) / 1000.0,
            local_player_cell_id=data.get('localPlayerCellId', -1),
            my_team=data.get('myTeam', []),
            their_team=data.get('theirTeam', []),
            bans=bans_list,
            is_planning_phase=timer.get('phase') == 'PLANNING',
        )

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class OpponentProfile:
    """
    Aggregated profile of an opponent for strategic analysis.

    This is the key data structure that drives the strategy engine.
    Combines summoner info, ranked stats, champion pool, and
    recent performance patterns.
    """
    summoner: SummonerInfo = field(default_factory=SummonerInfo)
    ranked: RankedInfo = field(default_factory=RankedInfo)
    champion_pool: List[ChampionStats] = field(default_factory=list)
    recent_matches: List[MatchData] = field(default_factory=list)
    detected_lane: str = ""
    current_champion_id: int = 0
    current_champion_name: str = ""
    threat_level: float = 0.0  # 0.0-1.0, computed by strategy engine
    playstyle_tags: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    fetched_at: str = ""

    @property
    def main_champions(self) -> List[ChampionStats]:
        """Top 5 most played champions."""
        sorted_pool = sorted(
            self.champion_pool,
            key=lambda c: c.games_played, reverse=True)
        return sorted_pool[:5]

    @property
    def recent_winrate(self) -> float:
        if not self.recent_matches:
            return 0.0
        wins = sum(1 for m in self.recent_matches
                   for p in m.participants
                   if p.puuid == self.summoner.puuid and p.won)
        return round(wins / len(self.recent_matches) * 100, 1)

    @property
    def is_one_trick(self) -> bool:
        """Detect one-trick-pony players."""
        if not self.champion_pool or len(self.champion_pool) < 3:
            return False
        total = sum(c.games_played for c in self.champion_pool)
        if total == 0:
            return False
        top = self.champion_pool[0].games_played if self.champion_pool else 0
        return (top / total) > 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            'summoner': self.summoner.to_dict(),
            'ranked': self.ranked.to_dict(),
            'champion_pool_size': len(self.champion_pool),
            'main_champions': [c.to_dict() for c in self.main_champions],
            'recent_matches_count': len(self.recent_matches),
            'recent_winrate': self.recent_winrate,
            'detected_lane': self.detected_lane,
            'current_champion': self.current_champion_name,
            'threat_level': self.threat_level,
            'playstyle_tags': self.playstyle_tags,
            'strengths': self.strengths,
            'weaknesses': self.weaknesses,
            'is_one_trick': self.is_one_trick,
        }

    def to_strategy_prompt(self) -> str:
        """Format for LLM strategy engine consumption."""
        lines = [
            f"Opponent: {self.summoner.full_name}",
            f"Rank: {self.ranked.rank_string} "
            f"(WR: {self.ranked.winrate}%, "
            f"{self.ranked.total_games} games)",
            f"Lane: {self.detected_lane}",
            f"Champion: {self.current_champion_name}",
        ]
        if self.main_champions:
            mains = ", ".join(
                f"{c.champion_name}({c.winrate}%/{c.games_played}g)"
                for c in self.main_champions[:3])
            lines.append(f"Main champions: {mains}")
        if self.playstyle_tags:
            lines.append(f"Playstyle: {', '.join(self.playstyle_tags)}")
        if self.strengths:
            lines.append(f"Strengths: {', '.join(self.strengths)}")
        if self.weaknesses:
            lines.append(f"Weaknesses: {', '.join(self.weaknesses)}")
        lines.append(f"Threat: {self.threat_level:.1%}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Champion ID → Name mapping (partial, for offline use)
# ---------------------------------------------------------------------------

CHAMPION_NAMES: Dict[int, str] = {
    1: "Annie", 2: "Olaf", 3: "Galio", 4: "TwistedFate", 5: "XinZhao",
    6: "Urgot", 7: "LeBlanc", 8: "Vladimir", 9: "Fiddlesticks",
    10: "Kayle", 11: "MasterYi", 12: "Alistar", 13: "Ryze",
    14: "Sion", 15: "Sivir", 16: "Soraka", 17: "Teemo",
    18: "Tristana", 19: "Warwick", 20: "Nunu", 21: "MissFortune",
    22: "Ashe", 23: "Tryndamere", 24: "Jax", 25: "Morgana",
    26: "Zilean", 27: "Singed", 28: "Evelynn", 29: "Twitch",
    30: "Karthus", 31: "Cho'Gath", 32: "Amumu", 33: "Rammus",
    34: "Anivia", 35: "Shaco", 36: "DrMundo", 37: "Sona",
    38: "Kassadin", 39: "Irelia", 40: "Janna", 41: "Gangplank",
    42: "Corki", 43: "Karma", 44: "Taric", 45: "Veigar",
    48: "Trundle", 50: "Swain", 51: "Caitlyn", 53: "Blitzcrank",
    54: "Malphite", 55: "Katarina", 56: "Nocturne", 57: "Maokai",
    58: "Renekton", 59: "JarvanIV", 60: "Elise", 61: "Orianna",
    62: "Wukong", 63: "Brand", 64: "LeeSin", 67: "Vayne",
    68: "Rumble", 69: "Cassiopeia", 72: "Skarner", 74: "Heimerdinger",
    75: "Nasus", 76: "Nidalee", 77: "Udyr", 78: "Poppy",
    79: "Gragas", 80: "Pantheon", 81: "Ezreal", 82: "Mordekaiser",
    83: "Yorick", 84: "Akali", 85: "Kennen", 86: "Garen",
    89: "Leona", 90: "Malzahar", 91: "Talon", 92: "Riven",
    96: "Kog'Maw", 98: "Shen", 99: "Lux", 101: "Xerath",
    102: "Shyvana", 103: "Ahri", 104: "Graves", 105: "Fizz",
    106: "Volibear", 107: "Rengar", 110: "Varus", 111: "Nautilus",
    112: "Viktor", 113: "Sejuani", 114: "Fiora", 115: "Ziggs",
    117: "Lulu", 119: "Draven", 120: "Hecarim", 121: "Kha'Zix",
    122: "Darius", 126: "Jayce", 127: "Lissandra", 131: "Diana",
    133: "Quinn", 134: "Syndra", 136: "AurelionSol", 141: "Kayn",
    142: "Zoe", 143: "Zyra", 145: "Kai'Sa", 147: "Seraphine",
    150: "Gnar", 154: "Zac", 157: "Yasuo", 161: "Vel'Koz",
    163: "Taliyah", 166: "Akshan", 200: "Bel'Veth", 201: "Braum",
    202: "Jhin", 203: "Kindred", 221: "Zeri", 222: "Jinx",
    223: "Tahm Kench", 233: "Briar", 234: "Viego", 235: "Senna",
    236: "Lucian", 238: "Zed", 240: "Kled", 245: "Ekko",
    246: "Qiyana", 254: "Vi", 266: "Aatrox", 267: "Nami",
    268: "Azir", 350: "Yuumi", 360: "Samira", 412: "Thresh",
    420: "Illaoi", 421: "Rek'Sai", 427: "Ivern", 429: "Kalista",
    432: "Bard", 516: "Ornn", 517: "Sylas", 518: "Neeko",
    523: "Aphelios", 526: "Rell", 555: "Pyke", 711: "Vex",
    777: "Yone", 875: "Sett", 876: "Lillia", 887: "Gwen",
    888: "Renata Glasc", 895: "Nilah", 897: "K'Sante",
    901: "Smolder", 902: "Milio", 910: "Hwei", 950: "Naafiri",
}


def champion_name(champion_id: int) -> str:
    """Get champion name by ID, with fallback."""
    return CHAMPION_NAMES.get(champion_id, f"Champion#{champion_id}")


if __name__ == '__main__':
    # Quick model validation
    s = SummonerInfo.from_lcu_response({
        'puuid': 'test-puuid', 'displayName': 'TestPlayer',
        'summonerLevel': 150})
    assert s.display_name == 'TestPlayer'
    r = RankedInfo()
    assert r.winrate == 0.0
    p = MatchParticipant(kills=10, deaths=2, assists=8)
    assert p.kda == 9.0
    print("[M1048] All model tests PASSED")

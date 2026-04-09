"""
Game State Messages — Shared data structures for inter-module communication.
=============================================================================

These dataclasses serve as the "protobuf" message definitions that flow
through CyberNode channels.  Every module reads and writes these types,
ensuring type safety and consistent schema across the pipeline.

Architecture position:
    modules/common/adapters/game_messages.py   ← YOU ARE HERE
    ├─ Published by: canbus → /lol/raw_data
    ├─ Consumed by: perception → assembles into GameSnapshot
    ├─ Published by: perception → /lol/game_state
    ├─ Consumed by: prediction, planning, control
    └─ Mirrors: Live Client Data API JSON schema

Apollo reference:
    modules/common_msgs/chassis_msgs/chassis.proto
    modules/common_msgs/perception_msgs/perception_obstacle.proto

Design notes:
    - Frozen dataclasses for immutability through the pipeline
    - game_time is the canonical ordering key
    - All coordinates normalized to minimap [0,1] space
    - Champion IDs use Riot's integer ID system
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, FrozenSet, List, Optional, Tuple


# ─── Enums ───────────────────────────────────────────────────────────────────

class TeamSide(Enum):
    """Team identification."""
    BLUE = "ORDER"
    RED = "CHAOS"
    UNKNOWN = "UNKNOWN"

    @staticmethod
    def from_riot(team_str: str) -> "TeamSide":
        mapping = {"ORDER": TeamSide.BLUE, "CHAOS": TeamSide.RED}
        return mapping.get(team_str.upper(), TeamSide.UNKNOWN)


class GamePhase(Enum):
    """High-level game phase classification."""
    LOADING = auto()
    EARLY = auto()       # 0-14 min
    MID = auto()         # 14-25 min
    LATE = auto()        # 25+ min
    ENDING = auto()
    POST_GAME = auto()

    @staticmethod
    def from_game_time(seconds: float) -> "GamePhase":
        if seconds < 0:
            return GamePhase.LOADING
        elif seconds < 840:    # 14 min
            return GamePhase.EARLY
        elif seconds < 1500:   # 25 min
            return GamePhase.MID
        else:
            return GamePhase.LATE


class ObjectiveType(Enum):
    """Map objective types."""
    DRAGON_INFERNAL = "InfernalDrake"
    DRAGON_MOUNTAIN = "MountainDrake"
    DRAGON_OCEAN = "OceanDrake"
    DRAGON_CLOUD = "CloudDrake"
    DRAGON_HEXTECH = "HextechDrake"
    DRAGON_CHEMTECH = "ChemtechDrake"
    DRAGON_ELDER = "ElderDrake"
    BARON = "Baron"
    RIFT_HERALD = "RiftHerald"
    VOID_GRUB = "VoidGrub"
    TOWER = "Tower"
    INHIBITOR = "Inhibitor"
    NEXUS = "Nexus"


class EventType(Enum):
    """In-game event classifications."""
    CHAMPION_KILL = "ChampionKill"
    MULTI_KILL = "Multikill"
    TURRET_KILLED = "TurretKilled"
    INHIBITOR_KILLED = "InhibKilled"
    DRAGON_KILL = "DragonKill"
    BARON_KILL = "BaronKill"
    HERALD_KILL = "HeraldKill"
    ACE = "Ace"
    FIRST_BLOOD = "FirstBlood"
    GAME_START = "GameStart"
    GAME_END = "GameEnd"
    ITEM_PURCHASED = "ItemPurchased"
    ITEM_SOLD = "ItemSold"
    LEVEL_UP = "LevelUp"
    WARD_PLACED = "WardPlaced"
    WARD_KILLED = "WardKilled"
    VOID_GRUB_KILL = "VoidGrubKill"


# ─── Player data ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlayerScore:
    """Kill/death/assist and CS scores for a player."""
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    creep_score: int = 0
    ward_score: float = 0.0

    @property
    def kda(self) -> float:
        """KDA ratio (kills + assists) / max(1, deaths)."""
        return (self.kills + self.assists) / max(1, self.deaths)


@dataclass(frozen=True)
class PlayerItems:
    """Items currently held by a player."""
    item_ids: Tuple[int, ...] = ()
    gold_spent: int = 0

    @property
    def slot_count(self) -> int:
        return len(self.item_ids)

    @property
    def has_completed_mythic(self) -> bool:
        # Mythic items have IDs in 6600-6699 range (approximate)
        return any(6600 <= iid <= 6699 for iid in self.item_ids)


@dataclass(frozen=True)
class PlayerAbilities:
    """Ability levels and cooldown state."""
    q_level: int = 0
    w_level: int = 0
    e_level: int = 0
    r_level: int = 0

    @property
    def total_skill_points(self) -> int:
        return self.q_level + self.w_level + self.e_level + self.r_level

    @property
    def has_ultimate(self) -> bool:
        return self.r_level > 0


@dataclass(frozen=True)
class PlayerState:
    """Complete state of a single player at a point in time.

    Corresponds to one entry in the Live Client Data API's
    ``allPlayers`` array, enriched with active player data when
    applicable.
    """
    summoner_name: str = ""
    champion_name: str = ""
    champion_id: int = 0
    team: TeamSide = TeamSide.UNKNOWN
    level: int = 1
    position: str = ""  # TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY
    is_active_player: bool = False
    is_dead: bool = False
    respawn_timer: float = 0.0

    # Resources
    current_health: float = 0.0
    max_health: float = 0.0
    current_mana: float = 0.0
    max_mana: float = 0.0

    # Combat stats
    attack_damage: float = 0.0
    ability_power: float = 0.0
    armor: float = 0.0
    magic_resist: float = 0.0
    move_speed: float = 0.0

    # Economy
    current_gold: float = 0.0

    # Sub-objects
    scores: PlayerScore = field(default_factory=PlayerScore)
    items: PlayerItems = field(default_factory=PlayerItems)
    abilities: PlayerAbilities = field(default_factory=PlayerAbilities)

    # Summoner spells
    spell_d: str = ""
    spell_f: str = ""

    @property
    def health_pct(self) -> float:
        if self.max_health <= 0:
            return 0.0
        return self.current_health / self.max_health

    @property
    def mana_pct(self) -> float:
        if self.max_mana <= 0:
            return 1.0  # manaless champions
        return self.current_mana / self.max_mana

    @property
    def is_low_health(self) -> bool:
        return self.health_pct < 0.3


# ─── Team data ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TeamState:
    """Aggregated team-level state."""
    side: TeamSide = TeamSide.UNKNOWN
    players: Tuple[PlayerState, ...] = ()
    total_kills: int = 0
    total_deaths: int = 0
    total_gold: float = 0.0
    towers_destroyed: int = 0
    dragons_taken: int = 0
    barons_taken: int = 0
    inhibitors_destroyed: int = 0
    dragon_soul: str = ""  # empty if no soul

    @property
    def alive_count(self) -> int:
        return sum(1 for p in self.players if not p.is_dead)

    @property
    def avg_level(self) -> float:
        if not self.players:
            return 0.0
        return sum(p.level for p in self.players) / len(self.players)


# ─── Game events ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GameEvent:
    """A single in-game event.

    Maps to one entry in Live Client Data API's ``events.Events`` array.
    """
    event_id: int = 0
    event_type: EventType = EventType.GAME_START
    game_time: float = 0.0
    killer: str = ""
    victim: str = ""
    assisters: Tuple[str, ...] = ()
    position_x: float = 0.0
    position_y: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_objective(self) -> bool:
        return self.event_type in (
            EventType.DRAGON_KILL, EventType.BARON_KILL,
            EventType.HERALD_KILL, EventType.VOID_GRUB_KILL,
        )

    @property
    def is_structure(self) -> bool:
        return self.event_type in (
            EventType.TURRET_KILLED, EventType.INHIBITOR_KILLED,
        )


# ─── Top-level game snapshot ────────────────────────────────────────────────

@dataclass(frozen=True)
class GameSnapshot:
    """Complete immutable snapshot of game state at one point in time.

    This is the primary message type on the ``/lol/game_state`` channel.
    Every downstream module (prediction, planning, control) consumes
    this as their input.

    Invariant: ``game_time`` increases monotonically across snapshots.
    """
    # Timing
    game_time: float = 0.0
    real_timestamp: float = field(default_factory=time.time)
    sequence: int = 0

    # Phase
    phase: GamePhase = GamePhase.LOADING
    game_mode: str = "CLASSIC"
    map_number: int = 11  # Summoner's Rift

    # Teams
    blue_team: TeamState = field(default_factory=lambda: TeamState(side=TeamSide.BLUE))
    red_team: TeamState = field(default_factory=lambda: TeamState(side=TeamSide.RED))

    # Active player
    active_player: Optional[PlayerState] = None
    active_team: TeamSide = TeamSide.UNKNOWN

    # All players indexed
    all_players: Tuple[PlayerState, ...] = ()

    # Events since last snapshot
    new_events: Tuple[GameEvent, ...] = ()
    # Cumulative event history (for pattern mining)
    all_events: Tuple[GameEvent, ...] = ()

    # Economy delta
    gold_diff: float = 0.0  # blue_gold - red_gold

    @property
    def total_kills(self) -> int:
        return self.blue_team.total_kills + self.red_team.total_kills

    @property
    def player_count(self) -> int:
        return len(self.all_players)

    @property
    def my_team(self) -> TeamState:
        """The active player's team."""
        if self.active_team == TeamSide.RED:
            return self.red_team
        return self.blue_team

    @property
    def enemy_team(self) -> TeamState:
        """The opponent team."""
        if self.active_team == TeamSide.RED:
            return self.blue_team
        return self.red_team

    def get_player(self, summoner_name: str) -> Optional[PlayerState]:
        """Find a player by summoner name."""
        for p in self.all_players:
            if p.summoner_name == summoner_name:
                return p
        return None

    def to_feature_dict(self) -> Dict[str, Any]:
        """Extract a flat feature dictionary for ML models.

        Used by prediction components for feature engineering.
        """
        blue = self.blue_team
        red = self.red_team
        return {
            "game_time": self.game_time,
            "phase": self.phase.name,
            "gold_diff": self.gold_diff,
            "blue_kills": blue.total_kills,
            "red_kills": red.total_kills,
            "blue_towers": blue.towers_destroyed,
            "red_towers": red.towers_destroyed,
            "blue_dragons": blue.dragons_taken,
            "red_dragons": red.dragons_taken,
            "blue_barons": blue.barons_taken,
            "red_barons": red.barons_taken,
            "blue_avg_level": blue.avg_level,
            "red_avg_level": red.avg_level,
            "blue_alive": blue.alive_count,
            "red_alive": red.alive_count,
            "kill_diff": blue.total_kills - red.total_kills,
            "tower_diff": blue.towers_destroyed - red.towers_destroyed,
            "dragon_diff": blue.dragons_taken - red.dragons_taken,
        }


# ─── Canbus raw data (pre-perception) ───────────────────────────────────────

@dataclass
class RawLCUData:
    """Raw data payload from the LCU Live Client Data API.

    Published on ``/lol/raw_lcu`` by the canbus component.
    Mutable because canbus writes it before publishing.
    """
    allgamedata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    lcu_latency_ms: float = 0.0
    http_status: int = 200
    source: str = "lcu"  # "lcu" or "fiddler"


@dataclass
class RawFiddlerData:
    """Raw network packet data from Fiddler MCP bridge.

    Published on ``/lol/raw_fiddler`` by the canbus component.
    """
    sessions: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    capture_latency_ms: float = 0.0
    packet_count: int = 0


# ─── Prediction outputs ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class WinPrediction:
    """Win probability prediction result.

    Published on ``/lol/win_prediction`` by the prediction component.
    """
    blue_win_prob: float = 0.5
    confidence: float = 0.0
    model_version: str = "v1"
    game_time: float = 0.0
    timestamp: float = field(default_factory=time.time)
    top_features: Tuple[Tuple[str, float], ...] = ()

    @property
    def red_win_prob(self) -> float:
        return 1.0 - self.blue_win_prob

    @property
    def predicted_winner(self) -> TeamSide:
        if self.blue_win_prob > 0.5:
            return TeamSide.BLUE
        elif self.blue_win_prob < 0.5:
            return TeamSide.RED
        return TeamSide.UNKNOWN


@dataclass(frozen=True)
class TeamfightPrediction:
    """Teamfight likelihood and outcome prediction.

    Published on ``/lol/teamfight_prediction``.
    """
    likelihood: float = 0.0  # 0-1 probability teamfight imminent
    blue_win_if_fight: float = 0.5
    recommended_action: str = "hold"  # "engage", "disengage", "hold"
    reasoning: str = ""
    game_time: float = 0.0
    timestamp: float = field(default_factory=time.time)


# ─── Planning outputs ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class StrategyAdvice:
    """Strategic recommendation from the planning module.

    Published on ``/lol/strategy_advice``.
    """
    primary_action: str = ""
    secondary_action: str = ""
    reasoning: str = ""
    confidence: float = 0.0
    urgency: float = 0.0  # 0=low, 1=immediate
    game_time: float = 0.0
    timestamp: float = field(default_factory=time.time)
    item_suggestions: Tuple[str, ...] = ()
    macro_call: str = ""  # "baron", "dragon", "push_mid", "split", "group"


# ─── Voice output ────────────────────────────────────────────────────────────

@dataclass
class VoiceCommand:
    """Command for the voice narration engine.

    Published on ``/lol/voice_command``.
    """
    text: str = ""
    priority: int = 5  # 1=highest, 10=lowest
    max_age_s: float = 5.0  # discard if older than this
    game_time: float = 0.0
    timestamp: float = field(default_factory=time.time)
    source_module: str = ""

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > self.max_age_s


# ─── Apollo FillHeader pattern (Claude23) ────────────────────────────────────
#
# Apollo canbus_component.cc:153:
#   common::util::FillHeader(node_->Name(), &chassis);
#
# Every published message should have a header with:
# - timestamp_sec: wall-clock time
# - module_name: which component published it
# - sequence_num: monotonic counter
#
# This function stamps messages before publishing, enabling downstream
# components to check data freshness (OnControlCommandCheck pattern).


@dataclass
class MessageHeader:
    """Apollo-style message header for all channel messages.

    Every message published on a CyberNode channel should carry this
    header so downstream readers can detect staleness.

    Apollo reference: common/proto/header.proto
    """
    timestamp_sec: float = 0.0
    module_name: str = ""
    sequence_num: int = 0

    def age_s(self) -> float:
        """Seconds since this header was stamped."""
        if self.timestamp_sec <= 0:
            return float("inf")
        return time.time() - self.timestamp_sec


def fill_header(
    module_name: str,
    message: Any,
    sequence: int = 0,
) -> None:
    """Stamp a message with Apollo-style header fields.

    Apollo equivalent: common::util::FillHeader(node_->Name(), &msg)

    If the message is a dataclass or has settable attributes,
    sets header fields directly. Otherwise, if it's a dict, adds
    a "_header" key.

    Args:
        module_name: Name of the publishing component.
        message: The message to stamp.
        sequence: Monotonic sequence number.
    """
    header = MessageHeader(
        timestamp_sec=time.time(),
        module_name=module_name,
        sequence_num=sequence,
    )

    if isinstance(message, dict):
        message["_header"] = {
            "timestamp_sec": header.timestamp_sec,
            "module_name": header.module_name,
            "sequence_num": header.sequence_num,
        }
    elif hasattr(message, "header"):
        try:
            message.header = header
        except (AttributeError, TypeError):
            pass
    elif hasattr(message, "_header"):
        try:
            message._header = header
        except (AttributeError, TypeError):
            pass


def get_header_age(message: Any) -> float:
    """Get the age in seconds of a message's header.

    Returns float('inf') if no header found.
    """
    if isinstance(message, dict):
        h = message.get("_header", {})
        ts = h.get("timestamp_sec", 0.0)
        if ts > 0:
            return time.time() - ts
    elif hasattr(message, "header") and hasattr(message.header, "timestamp_sec"):
        ts = message.header.timestamp_sec
        if ts > 0:
            return time.time() - ts
    return float("inf")

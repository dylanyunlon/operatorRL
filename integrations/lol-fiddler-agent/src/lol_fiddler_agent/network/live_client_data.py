"""
League of Legends Live Client Data API Interface

Provides structured access to LoL's Live Client Data API (localhost:2999).
This API is available when a game is in progress and provides real-time
game state information.

Reference: https://developer.riotgames.com/docs/lol#league-client-api_live-client-data-api

Data flow architecture:
1. Fiddler MCP captures HTTP traffic to localhost:2999
2. This module parses captured data into structured game state
3. Strategy agent uses game state for decision making
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field

logger = logging.getLogger(__name__)


class Team(str, Enum):
    """Game teams."""
    ORDER = "ORDER"   # Blue side
    CHAOS = "CHAOS"   # Red side
    NEUTRAL = "NEUTRAL"


class Position(str, Enum):
    """Player positions/lanes."""
    TOP = "TOP"
    JUNGLE = "JUNGLE"
    MIDDLE = "MIDDLE"
    BOTTOM = "BOTTOM"
    UTILITY = "UTILITY"  # Support
    UNKNOWN = "UNKNOWN"


class DragonType(str, Enum):
    """Dragon types."""
    INFERNAL = "Infernal"
    OCEAN = "Ocean"
    MOUNTAIN = "Mountain"
    CLOUD = "Cloud"
    HEXTECH = "Hextech"
    CHEMTECH = "Chemtech"
    ELDER = "Elder"


class GamePhase(str, Enum):
    """Game phases based on time and objectives."""
    EARLY = "early"       # 0-14 min
    MID = "mid"           # 14-25 min
    LATE = "late"         # 25+ min


# ── Ability Data ───────────────────────────────────────────────────────────

class AbilityLevel(BaseModel):
    """Current ability state."""
    ability_level: int = Field(default=0, alias="abilityLevel")
    display_name: str = Field(default="", alias="displayName")
    ability_id: str = Field(default="", alias="id")
    raw_description: str = Field(default="", alias="rawDescription")
    raw_display_name: str = Field(default="", alias="rawDisplayName")
    
    class Config:
        populate_by_name = True


class Abilities(BaseModel):
    """Player abilities (QWER + Passive)."""
    passive: Optional[AbilityLevel] = Field(default=None, alias="Passive")
    q: Optional[AbilityLevel] = Field(default=None, alias="Q")
    w: Optional[AbilityLevel] = Field(default=None, alias="W")
    e: Optional[AbilityLevel] = Field(default=None, alias="E")
    r: Optional[AbilityLevel] = Field(default=None, alias="R")
    
    class Config:
        populate_by_name = True
    
    def get_total_skill_points(self) -> int:
        """Get total skill points invested."""
        total = 0
        for ability in [self.q, self.w, self.e, self.r]:
            if ability:
                total += ability.ability_level
        return total


# ── Rune Data ──────────────────────────────────────────────────────────────

class RuneTree(BaseModel):
    """Rune tree information."""
    display_name: str = Field(default="", alias="displayName")
    rune_id: int = Field(default=0, alias="id")
    raw_description: str = Field(default="", alias="rawDescription")
    raw_display_name: str = Field(default="", alias="rawDisplayName")
    
    class Config:
        populate_by_name = True


class Runes(BaseModel):
    """Player rune configuration."""
    keystone: Optional[RuneTree] = Field(default=None, alias="keystone")
    primary_rune_tree: Optional[RuneTree] = Field(default=None, alias="primaryRuneTree")
    secondary_rune_tree: Optional[RuneTree] = Field(default=None, alias="secondaryRuneTree")
    
    class Config:
        populate_by_name = True


# ── Item Data ──────────────────────────────────────────────────────────────

class Item(BaseModel):
    """An item in player inventory."""
    can_use: bool = Field(default=False, alias="canUse")
    consumable: bool = Field(default=False)
    count: int = Field(default=1)
    display_name: str = Field(default="", alias="displayName")
    item_id: int = Field(default=0, alias="itemID")
    price: int = Field(default=0)
    raw_description: str = Field(default="", alias="rawDescription")
    raw_display_name: str = Field(default="", alias="rawDisplayName")
    slot: int = Field(default=0)
    
    class Config:
        populate_by_name = True
    
    def is_completed_item(self) -> bool:
        """Check if this is a completed (non-component) item."""
        # Completed items typically cost 2500+ gold
        return self.price >= 2500


# ── Score Data ─────────────────────────────────────────────────────────────

class Scores(BaseModel):
    """Player KDA and CS scores."""
    assists: int = Field(default=0)
    creep_score: int = Field(default=0, alias="creepScore")
    deaths: int = Field(default=0)
    kills: int = Field(default=0)
    ward_score: float = Field(default=0.0, alias="wardScore")
    
    class Config:
        populate_by_name = True
    
    @computed_field
    @property
    def kda(self) -> float:
        """Calculate KDA ratio."""
        if self.deaths == 0:
            return float(self.kills + self.assists)
        return (self.kills + self.assists) / self.deaths
    
    @computed_field
    @property
    def kill_participation_estimate(self) -> float:
        """Estimate kill participation (needs team kills for accuracy)."""
        return float(self.kills + self.assists)


# ── Champion Stats ─────────────────────────────────────────────────────────

class ChampionStats(BaseModel):
    """Current champion statistics."""
    ability_haste: float = Field(default=0.0, alias="abilityHaste")
    ability_power: float = Field(default=0.0, alias="abilityPower")
    armor: float = Field(default=0.0)
    armor_penetration_flat: float = Field(default=0.0, alias="armorPenetrationFlat")
    armor_penetration_percent: float = Field(default=0.0, alias="armorPenetrationPercent")
    attack_damage: float = Field(default=0.0, alias="attackDamage")
    attack_range: float = Field(default=0.0, alias="attackRange")
    attack_speed: float = Field(default=0.0, alias="attackSpeed")
    bonus_armor_penetration_percent: float = Field(default=0.0, alias="bonusArmorPenetrationPercent")
    bonus_magic_penetration_percent: float = Field(default=0.0, alias="bonusMagicPenetrationPercent")
    crit_chance: float = Field(default=0.0, alias="critChance")
    crit_damage: float = Field(default=0.0, alias="critDamage")
    current_health: float = Field(default=0.0, alias="currentHealth")
    heal_shield_power: float = Field(default=0.0, alias="healShieldPower")
    health_regen_rate: float = Field(default=0.0, alias="healthRegenRate")
    life_steal: float = Field(default=0.0, alias="lifeSteal")
    magic_lethality: float = Field(default=0.0, alias="magicLethality")
    magic_penetration_flat: float = Field(default=0.0, alias="magicPenetrationFlat")
    magic_penetration_percent: float = Field(default=0.0, alias="magicPenetrationPercent")
    magic_resist: float = Field(default=0.0, alias="magicResist")
    max_health: float = Field(default=0.0, alias="maxHealth")
    move_speed: float = Field(default=0.0, alias="moveSpeed")
    omnivamp: float = Field(default=0.0)
    physical_lethality: float = Field(default=0.0, alias="physicalLethality")
    physical_vamp: float = Field(default=0.0, alias="physicalVamp")
    resource_max: float = Field(default=0.0, alias="resourceMax")
    resource_regen_rate: float = Field(default=0.0, alias="resourceRegenRate")
    resource_type: str = Field(default="MANA", alias="resourceType")
    resource_value: float = Field(default=0.0, alias="resourceValue")
    spell_vamp: float = Field(default=0.0, alias="spellVamp")
    tenacity: float = Field(default=0.0)
    
    class Config:
        populate_by_name = True
    
    @computed_field
    @property
    def health_percent(self) -> float:
        """Get current health as percentage."""
        if self.max_health == 0:
            return 100.0
        return (self.current_health / self.max_health) * 100
    
    @computed_field
    @property
    def resource_percent(self) -> float:
        """Get current resource (mana/energy) as percentage."""
        if self.resource_max == 0:
            return 100.0
        return (self.resource_value / self.resource_max) * 100
    
    def is_low_health(self, threshold: float = 30.0) -> bool:
        """Check if health is below threshold percentage."""
        return self.health_percent < threshold
    
    def get_effective_hp_physical(self) -> float:
        """Calculate effective HP against physical damage."""
        return self.current_health * (1 + self.armor / 100)
    
    def get_effective_hp_magic(self) -> float:
        """Calculate effective HP against magic damage."""
        return self.current_health * (1 + self.magic_resist / 100)


# ── Summoner Spells ────────────────────────────────────────────────────────

class SummonerSpell(BaseModel):
    """Summoner spell data."""
    display_name: str = Field(default="", alias="displayName")
    raw_description: str = Field(default="", alias="rawDescription")
    raw_display_name: str = Field(default="", alias="rawDisplayName")
    
    class Config:
        populate_by_name = True


class SummonerSpells(BaseModel):
    """Both summoner spells."""
    summoner_spell_one: Optional[SummonerSpell] = Field(default=None, alias="summonerSpellOne")
    summoner_spell_two: Optional[SummonerSpell] = Field(default=None, alias="summonerSpellTwo")
    
    class Config:
        populate_by_name = True
    
    def has_flash(self) -> bool:
        """Check if player has Flash."""
        spells = [self.summoner_spell_one, self.summoner_spell_two]
        for spell in spells:
            if spell and "flash" in spell.display_name.lower():
                return True
        return False
    
    def has_tp(self) -> bool:
        """Check if player has Teleport."""
        spells = [self.summoner_spell_one, self.summoner_spell_two]
        for spell in spells:
            if spell and "teleport" in spell.display_name.lower():
                return True
        return False


# ── Active Player (You) ────────────────────────────────────────────────────

class ActivePlayer(BaseModel):
    """The player running the client (you)."""
    abilities: Optional[Abilities] = None
    champion_stats: Optional[ChampionStats] = Field(default=None, alias="championStats")
    current_gold: float = Field(default=0.0, alias="currentGold")
    full_runes: Optional[Runes] = Field(default=None, alias="fullRunes")
    level: int = Field(default=1)
    summoner_name: str = Field(default="", alias="summonerName")
    riot_id: str = Field(default="", alias="riotId")
    team_relative_colors: bool = Field(default=False, alias="teamRelativeColors")
    
    class Config:
        populate_by_name = True


# ── All Players ────────────────────────────────────────────────────────────

class Player(BaseModel):
    """A player in the game."""
    champion_name: str = Field(default="", alias="championName")
    is_bot: bool = Field(default=False, alias="isBot")
    is_dead: bool = Field(default=False, alias="isDead")
    items: list[Item] = Field(default_factory=list)
    level: int = Field(default=1)
    position: str = Field(default="UNKNOWN")
    raw_champion_name: str = Field(default="", alias="rawChampionName")
    raw_skin_name: Optional[str] = Field(default=None, alias="rawSkinName")
    respawn_timer: float = Field(default=0.0, alias="respawnTimer")
    runes: Optional[Runes] = None
    scores: Scores = Field(default_factory=Scores)
    skin_id: int = Field(default=0, alias="skinID")
    skin_name: Optional[str] = Field(default=None, alias="skinName")
    summoner_name: str = Field(default="", alias="summonerName")
    riot_id: str = Field(default="", alias="riotId")
    summoner_spells: Optional[SummonerSpells] = Field(default=None, alias="summonerSpells")
    team: str = Field(default="UNKNOWN")
    
    class Config:
        populate_by_name = True
    
    @computed_field
    @property
    def team_enum(self) -> Team:
        """Get team as enum."""
        try:
            return Team(self.team)
        except ValueError:
            return Team.NEUTRAL
    
    @computed_field
    @property
    def position_enum(self) -> Position:
        """Get position as enum."""
        try:
            return Position(self.position)
        except ValueError:
            return Position.UNKNOWN
    
    def get_total_gold_estimate(self) -> int:
        """Estimate total gold from items."""
        return sum(item.price * item.count for item in self.items)
    
    def get_completed_items_count(self) -> int:
        """Count completed (non-component) items."""
        return sum(1 for item in self.items if item.is_completed_item())


# ── Events ─────────────────────────────────────────────────────────────────

class GameEvent(BaseModel):
    """A game event from the event log."""
    event_id: int = Field(default=0, alias="EventID")
    event_name: str = Field(default="", alias="EventName")
    event_time: float = Field(default=0.0, alias="EventTime")
    
    # Optional fields depending on event type
    killer_name: Optional[str] = Field(default=None, alias="KillerName")
    victim_name: Optional[str] = Field(default=None, alias="VictimName")
    assisters: list[str] = Field(default_factory=list, alias="Assisters")
    dragon_type: Optional[str] = Field(default=None, alias="DragonType")
    stolen: bool = Field(default=False, alias="Stolen")
    turret_killed: Optional[str] = Field(default=None, alias="TurretKilled")
    inhib_killed: Optional[str] = Field(default=None, alias="InhibKilled")
    recipient: Optional[str] = Field(default=None, alias="Recipient")
    ace_team: Optional[str] = Field(default=None, alias="Acer")
    
    class Config:
        populate_by_name = True
    
    def is_kill_event(self) -> bool:
        return self.event_name == "ChampionKill"
    
    def is_dragon_event(self) -> bool:
        return self.event_name == "DragonKill"
    
    def is_baron_event(self) -> bool:
        return self.event_name == "BaronKill"
    
    def is_turret_event(self) -> bool:
        return self.event_name == "TurretKilled"
    
    def is_inhibitor_event(self) -> bool:
        return self.event_name == "InhibKilled"


# ── Game Data ──────────────────────────────────────────────────────────────

class GameData(BaseModel):
    """Top-level game metadata."""
    game_mode: str = Field(default="CLASSIC", alias="gameMode")
    game_time: float = Field(default=0.0, alias="gameTime")
    map_name: str = Field(default="Map11", alias="mapName")
    map_number: int = Field(default=11, alias="mapNumber")
    map_terrain: str = Field(default="Default", alias="mapTerrain")
    
    class Config:
        populate_by_name = True
    
    @computed_field
    @property
    def game_time_minutes(self) -> float:
        """Get game time in minutes."""
        return self.game_time / 60.0
    
    @computed_field
    @property
    def game_phase(self) -> GamePhase:
        """Determine current game phase."""
        minutes = self.game_time_minutes
        if minutes < 14:
            return GamePhase.EARLY
        elif minutes < 25:
            return GamePhase.MID
        return GamePhase.LATE
    
    def format_time(self) -> str:
        """Format game time as MM:SS."""
        minutes = int(self.game_time // 60)
        seconds = int(self.game_time % 60)
        return f"{minutes:02d}:{seconds:02d}"


# ── Complete Game State ────────────────────────────────────────────────────

class LiveGameState(BaseModel):
    """Complete game state from Live Client Data API.
    
    Combines all data from /liveclientdata/allgamedata endpoint.
    This is the main data structure used by the strategy agent.
    """
    active_player: Optional[ActivePlayer] = Field(default=None, alias="activePlayer")
    all_players: list[Player] = Field(default_factory=list, alias="allPlayers")
    events: list[GameEvent] = Field(default_factory=list)
    game_data: Optional[GameData] = Field(default=None, alias="gameData")
    
    # Timestamp when this state was captured
    captured_at: datetime = Field(default_factory=datetime.now)
    
    class Config:
        populate_by_name = True
    
    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> "LiveGameState":
        """Parse from Live Client API response."""
        return cls.model_validate(data)
    
    @classmethod
    def from_json(cls, json_str: str) -> "LiveGameState":
        """Parse from JSON string."""
        data = json.loads(json_str)
        return cls.from_api_response(data)
    
    # ── Team Helpers ───────────────────────────────────────────────────────
    
    def get_my_team(self) -> Team:
        """Get the active player's team."""
        if not self.active_player:
            return Team.NEUTRAL
        
        my_name = self.active_player.summoner_name or self.active_player.riot_id
        for player in self.all_players:
            if player.summoner_name == my_name or player.riot_id == my_name:
                return player.team_enum
        
        return Team.NEUTRAL
    
    def get_allies(self) -> list[Player]:
        """Get allied players (excluding self)."""
        my_team = self.get_my_team()
        my_name = (self.active_player.summoner_name if self.active_player else "") or ""
        
        return [
            p for p in self.all_players
            if p.team_enum == my_team and p.summoner_name != my_name
        ]
    
    def get_enemies(self) -> list[Player]:
        """Get enemy players."""
        my_team = self.get_my_team()
        return [p for p in self.all_players if p.team_enum != my_team]
    
    def get_player_by_position(self, position: Position, team: Optional[Team] = None) -> Optional[Player]:
        """Find player by position, optionally filtered by team."""
        for player in self.all_players:
            if player.position_enum == position:
                if team is None or player.team_enum == team:
                    return player
        return None
    
    # ── Score Aggregation ──────────────────────────────────────────────────
    
    def get_team_kills(self, team: Team) -> int:
        """Get total kills for a team."""
        return sum(p.scores.kills for p in self.all_players if p.team_enum == team)
    
    def get_team_deaths(self, team: Team) -> int:
        """Get total deaths for a team."""
        return sum(p.scores.deaths for p in self.all_players if p.team_enum == team)
    
    def get_team_gold_estimate(self, team: Team) -> int:
        """Estimate total gold for a team from items."""
        return sum(p.get_total_gold_estimate() for p in self.all_players if p.team_enum == team)
    
    def get_gold_difference(self) -> int:
        """Get gold difference (positive = my team ahead)."""
        my_team = self.get_my_team()
        enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER
        
        my_gold = self.get_team_gold_estimate(my_team)
        enemy_gold = self.get_team_gold_estimate(enemy_team)
        
        return my_gold - enemy_gold
    
    def get_kill_difference(self) -> int:
        """Get kill difference (positive = my team ahead)."""
        my_team = self.get_my_team()
        enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER
        
        my_kills = self.get_team_kills(my_team)
        enemy_kills = self.get_team_kills(enemy_team)
        
        return my_kills - enemy_kills
    
    # ── Event Analysis ─────────────────────────────────────────────────────
    
    def get_recent_events(self, seconds: float = 60.0) -> list[GameEvent]:
        """Get events from the last N seconds."""
        if not self.game_data:
            return []
        
        cutoff_time = self.game_data.game_time - seconds
        return [e for e in self.events if e.event_time >= cutoff_time]
    
    def get_dragon_count(self, team: Team) -> int:
        """Count dragons killed by team."""
        count = 0
        for event in self.events:
            if event.is_dragon_event() and event.killer_name:
                # Check if killer is on the specified team
                for player in self.all_players:
                    if player.summoner_name == event.killer_name:
                        if player.team_enum == team:
                            count += 1
                        break
        return count
    
    def has_baron_buff(self, team: Team) -> bool:
        """Check if team has Baron buff (killed Baron in last 3 minutes)."""
        if not self.game_data:
            return False
        
        baron_duration = 180.0  # 3 minutes
        cutoff_time = self.game_data.game_time - baron_duration
        
        for event in reversed(self.events):
            if event.event_time < cutoff_time:
                break
            if event.is_baron_event() and event.killer_name:
                for player in self.all_players:
                    if player.summoner_name == event.killer_name:
                        return player.team_enum == team
        
        return False
    
    # ── Player State ───────────────────────────────────────────────────────
    
    def get_dead_players(self, team: Optional[Team] = None) -> list[Player]:
        """Get currently dead players."""
        dead = [p for p in self.all_players if p.is_dead]
        if team:
            dead = [p for p in dead if p.team_enum == team]
        return dead
    
    def get_alive_count(self, team: Team) -> int:
        """Get number of alive players on team."""
        return sum(1 for p in self.all_players if p.team_enum == team and not p.is_dead)
    
    def has_numbers_advantage(self) -> bool:
        """Check if my team has more players alive."""
        my_team = self.get_my_team()
        enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER
        
        return self.get_alive_count(my_team) > self.get_alive_count(enemy_team)
    
    # ── Performance Metrics (for ML model) ─────────────────────────────────
    
    def calculate_performance_features(self) -> dict[str, float]:
        """Calculate features for ML prediction model.
        
        Based on leagueoflegends-optimizer feature engineering:
        - f1: deaths per minute
        - f2: kills + assists per minute
        - f3: level per minute
        
        Returns:
            Dictionary of feature values for model input
        """
        if not self.game_data or not self.active_player:
            return {"f1": 0.0, "f2": 0.0, "f3": 0.0, "duration": 0.0}
        
        duration_minutes = max(self.game_data.game_time_minutes, 0.1)  # Avoid division by zero
        
        # Find active player's scores
        my_name = self.active_player.summoner_name or self.active_player.riot_id
        my_scores = Scores()
        for player in self.all_players:
            if player.summoner_name == my_name or player.riot_id == my_name:
                my_scores = player.scores
                break
        
        return {
            "f1": my_scores.deaths / duration_minutes,  # deaths_per_min
            "f2": (my_scores.kills + my_scores.assists) / duration_minutes,  # k_a_per_min
            "f3": self.active_player.level / duration_minutes,  # level_per_min
            "duration": duration_minutes,
            "championName": self.get_my_champion_name(),
        }
    
    def get_my_champion_name(self) -> str:
        """Get the active player's champion name."""
        if not self.active_player:
            return "Unknown"
        
        my_name = self.active_player.summoner_name or self.active_player.riot_id
        for player in self.all_players:
            if player.summoner_name == my_name or player.riot_id == my_name:
                return player.champion_name
        
        return "Unknown"


# ── Parser for Fiddler Captured Data ───────────────────────────────────────

def parse_captured_game_state(response_body: str) -> Optional[LiveGameState]:
    """Parse game state from captured Fiddler HTTP response body.
    
    Args:
        response_body: The raw response body from captured HTTP session
        
    Returns:
        LiveGameState if parsing succeeds, None otherwise
    """
    try:
        data = json.loads(response_body)
        return LiveGameState.from_api_response(data)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON: {e}")
        return None
    except Exception as e:
        logger.warning(f"Failed to parse game state: {e}")
        return None

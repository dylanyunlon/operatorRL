"""
TDD Tests for Live Client Data API Parser

Tests cover:
- Game state parsing from JSON
- Team/player data extraction
- Score calculations
- Event analysis
- Performance feature calculation (for ML model)
"""

import json
import pytest
from datetime import datetime, timezone

import sys
sys.path.insert(0, "/home/claude/lol-fiddler-agent/src")

from lol_fiddler_agent.network.live_client_data import (
    LiveGameState,
    Team,
    Position,
    GamePhase,
    Player,
    ActivePlayer,
    Scores,
    ChampionStats,
    Item,
    GameData,
    GameEvent,
    Abilities,
    AbilityLevel,
    SummonerSpells,
    SummonerSpell,
    parse_captured_game_state,
)


# ═══════════════════════════════════════════════════════════════════════════
# Test Fixtures - Sample Game Data
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_game_data():
    """Sample game data matching Live Client API format."""
    return {
        "activePlayer": {
            "abilities": {
                "Passive": {"abilityLevel": 0, "displayName": "Passive", "id": "Passive"},
                "Q": {"abilityLevel": 3, "displayName": "Q", "id": "Q"},
                "W": {"abilityLevel": 2, "displayName": "W", "id": "W"},
                "E": {"abilityLevel": 1, "displayName": "E", "id": "E"},
                "R": {"abilityLevel": 1, "displayName": "R", "id": "R"},
            },
            "championStats": {
                "abilityHaste": 20.0,
                "abilityPower": 150.0,
                "armor": 80.0,
                "attackDamage": 65.0,
                "currentHealth": 800.0,
                "maxHealth": 1600.0,
                "moveSpeed": 350.0,
                "resourceValue": 400.0,
                "resourceMax": 600.0,
                "resourceType": "MANA",
            },
            "currentGold": 1500.0,
            "level": 9,
            "summonerName": "TestPlayer",
            "riotId": "TestPlayer#NA1",
        },
        "allPlayers": [
            {
                "championName": "Lux",
                "isBot": False,
                "isDead": False,
                "items": [
                    {"displayName": "Luden's Tempest", "itemID": 3100, "price": 3200, "count": 1},
                ],
                "level": 9,
                "position": "MIDDLE",
                "rawChampionName": "game_character_displayname_Lux",
                "respawnTimer": 0.0,
                "scores": {
                    "assists": 5,
                    "creepScore": 120,
                    "deaths": 2,
                    "kills": 4,
                    "wardScore": 15.0,
                },
                "summonerName": "TestPlayer",
                "riotId": "TestPlayer#NA1",
                "team": "ORDER",
            },
            {
                "championName": "Jinx",
                "isBot": False,
                "isDead": False,
                "items": [],
                "level": 8,
                "position": "BOTTOM",
                "rawChampionName": "game_character_displayname_Jinx",
                "respawnTimer": 0.0,
                "scores": {
                    "assists": 3,
                    "creepScore": 100,
                    "deaths": 1,
                    "kills": 5,
                    "wardScore": 10.0,
                },
                "summonerName": "Ally1",
                "team": "ORDER",
            },
            {
                "championName": "Zed",
                "isBot": False,
                "isDead": True,
                "items": [],
                "level": 8,
                "position": "MIDDLE",
                "rawChampionName": "game_character_displayname_Zed",
                "respawnTimer": 15.0,
                "scores": {
                    "assists": 2,
                    "creepScore": 90,
                    "deaths": 3,
                    "kills": 2,
                    "wardScore": 5.0,
                },
                "summonerName": "Enemy1",
                "team": "CHAOS",
            },
        ],
        "events": [
            {
                "EventID": 1,
                "EventName": "GameStart",
                "EventTime": 0.0,
            },
            {
                "EventID": 2,
                "EventName": "ChampionKill",
                "EventTime": 300.0,
                "KillerName": "TestPlayer",
                "VictimName": "Enemy1",
                "Assisters": ["Ally1"],
            },
            {
                "EventID": 3,
                "EventName": "DragonKill",
                "EventTime": 400.0,
                "KillerName": "TestPlayer",
                "DragonType": "Infernal",
                "Stolen": False,
            },
        ],
        "gameData": {
            "gameMode": "CLASSIC",
            "gameTime": 900.0,  # 15 minutes
            "mapName": "Map11",
            "mapNumber": 11,
            "mapTerrain": "Default",
        },
    }


@pytest.fixture
def sample_game_state(sample_game_data):
    """Parsed LiveGameState from sample data."""
    return LiveGameState.from_api_response(sample_game_data)


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: Basic parsing from JSON
# ═══════════════════════════════════════════════════════════════════════════

class TestLiveGameStateParsing:
    """Test LiveGameState parsing."""
    
    def test_parse_from_dict(self, sample_game_data):
        """Should parse from dictionary."""
        state = LiveGameState.from_api_response(sample_game_data)
        
        assert state is not None
        assert state.active_player is not None
        assert len(state.all_players) == 3
        assert len(state.events) == 3
        assert state.game_data is not None
    
    def test_parse_from_json_string(self, sample_game_data):
        """Should parse from JSON string."""
        json_str = json.dumps(sample_game_data)
        state = LiveGameState.from_json(json_str)
        
        assert state is not None
        assert state.active_player.summoner_name == "TestPlayer"
    
    def test_parse_captured_game_state_valid(self, sample_game_data):
        """Helper function should parse valid JSON."""
        json_str = json.dumps(sample_game_data)
        state = parse_captured_game_state(json_str)
        
        assert state is not None
    
    def test_parse_captured_game_state_invalid(self):
        """Helper function should return None for invalid JSON."""
        result = parse_captured_game_state("not valid json")
        assert result is None
    
    def test_parse_captured_game_state_empty(self):
        """Helper function should return None for empty string."""
        result = parse_captured_game_state("")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: ActivePlayer data
# ═══════════════════════════════════════════════════════════════════════════

class TestActivePlayer:
    """Test ActivePlayer parsing."""
    
    def test_active_player_basic_fields(self, sample_game_state):
        """Active player should have correct basic fields."""
        player = sample_game_state.active_player
        
        assert player.summoner_name == "TestPlayer"
        assert player.riot_id == "TestPlayer#NA1"
        assert player.level == 9
        assert player.current_gold == 1500.0
    
    def test_active_player_champion_stats(self, sample_game_state):
        """Active player should have champion stats."""
        stats = sample_game_state.active_player.champion_stats
        
        assert stats is not None
        assert stats.ability_power == 150.0
        assert stats.armor == 80.0
        assert stats.current_health == 800.0
        assert stats.max_health == 1600.0
    
    def test_champion_stats_health_percent(self, sample_game_state):
        """Health percent should be calculated correctly."""
        stats = sample_game_state.active_player.champion_stats
        
        # 800 / 1600 = 50%
        assert stats.health_percent == 50.0
    
    def test_champion_stats_resource_percent(self, sample_game_state):
        """Resource percent should be calculated correctly."""
        stats = sample_game_state.active_player.champion_stats
        
        # 400 / 600 = 66.67%
        assert abs(stats.resource_percent - 66.67) < 0.1
    
    def test_champion_stats_is_low_health(self, sample_game_state):
        """is_low_health should work with thresholds."""
        stats = sample_game_state.active_player.champion_stats
        
        assert not stats.is_low_health(threshold=30)  # 50% > 30%
        assert stats.is_low_health(threshold=60)  # 50% < 60%


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: Player data
# ═══════════════════════════════════════════════════════════════════════════

class TestPlayer:
    """Test Player model."""
    
    def test_player_basic_fields(self, sample_game_state):
        """Players should have correct basic fields."""
        players = sample_game_state.all_players
        lux = players[0]
        
        assert lux.champion_name == "Lux"
        assert lux.summoner_name == "TestPlayer"
        assert lux.level == 9
        assert lux.team == "ORDER"
        assert lux.position == "MIDDLE"
    
    def test_player_team_enum(self, sample_game_state):
        """team_enum should return Team enum."""
        lux = sample_game_state.all_players[0]
        zed = sample_game_state.all_players[2]
        
        assert lux.team_enum == Team.ORDER
        assert zed.team_enum == Team.CHAOS
    
    def test_player_position_enum(self, sample_game_state):
        """position_enum should return Position enum."""
        lux = sample_game_state.all_players[0]
        jinx = sample_game_state.all_players[1]
        
        assert lux.position_enum == Position.MIDDLE
        assert jinx.position_enum == Position.BOTTOM
    
    def test_player_is_dead(self, sample_game_state):
        """isDead should be parsed correctly."""
        lux = sample_game_state.all_players[0]
        zed = sample_game_state.all_players[2]
        
        assert not lux.is_dead
        assert zed.is_dead


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: Scores
# ═══════════════════════════════════════════════════════════════════════════

class TestScores:
    """Test Scores model."""
    
    def test_scores_basic_fields(self, sample_game_state):
        """Scores should have correct fields."""
        lux = sample_game_state.all_players[0]
        scores = lux.scores
        
        assert scores.kills == 4
        assert scores.deaths == 2
        assert scores.assists == 5
        assert scores.creep_score == 120
    
    def test_scores_kda_calculation(self):
        """KDA should be calculated correctly."""
        scores = Scores(kills=4, deaths=2, assists=5)
        
        # (4 + 5) / 2 = 4.5
        assert scores.kda == 4.5
    
    def test_scores_kda_zero_deaths(self):
        """KDA with zero deaths should be kills + assists."""
        scores = Scores(kills=5, deaths=0, assists=3)
        
        assert scores.kda == 8.0  # Perfect KDA


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: Team operations
# ═══════════════════════════════════════════════════════════════════════════

class TestTeamOperations:
    """Test team-related methods."""
    
    def test_get_my_team(self, sample_game_state):
        """get_my_team should return active player's team."""
        team = sample_game_state.get_my_team()
        assert team == Team.ORDER
    
    def test_get_allies(self, sample_game_state):
        """get_allies should return teammates excluding self."""
        allies = sample_game_state.get_allies()
        
        assert len(allies) == 1
        assert allies[0].champion_name == "Jinx"
    
    def test_get_enemies(self, sample_game_state):
        """get_enemies should return enemy players."""
        enemies = sample_game_state.get_enemies()
        
        assert len(enemies) == 1
        assert enemies[0].champion_name == "Zed"
    
    def test_get_team_kills(self, sample_game_state):
        """get_team_kills should sum kills for a team."""
        order_kills = sample_game_state.get_team_kills(Team.ORDER)
        chaos_kills = sample_game_state.get_team_kills(Team.CHAOS)
        
        assert order_kills == 9  # 4 + 5
        assert chaos_kills == 2
    
    def test_get_kill_difference(self, sample_game_state):
        """get_kill_difference should be correct."""
        diff = sample_game_state.get_kill_difference()
        
        # ORDER has 9 kills, CHAOS has 2 = +7
        assert diff == 7


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: Game phase detection
# ═══════════════════════════════════════════════════════════════════════════

class TestGamePhase:
    """Test game phase detection."""
    
    def test_early_game(self):
        """Games under 14 min should be early game."""
        game_data = GameData(gameTime=600.0)  # 10 minutes
        assert game_data.game_phase == GamePhase.EARLY
    
    def test_mid_game(self):
        """Games 14-25 min should be mid game."""
        game_data = GameData(gameTime=1200.0)  # 20 minutes
        assert game_data.game_phase == GamePhase.MID
    
    def test_late_game(self):
        """Games over 25 min should be late game."""
        game_data = GameData(gameTime=2100.0)  # 35 minutes
        assert game_data.game_phase == GamePhase.LATE
    
    def test_game_time_format(self):
        """format_time should produce MM:SS string."""
        game_data = GameData(gameTime=905.0)  # 15:05
        assert game_data.format_time() == "15:05"


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Event analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestEventAnalysis:
    """Test event analysis methods."""
    
    def test_get_recent_events(self, sample_game_state):
        """get_recent_events should filter by time."""
        # Game time is 900s, events at 0, 300, 400
        recent = sample_game_state.get_recent_events(seconds=600)
        
        # Should include events at 300 and 400 (within last 600s = 10min)
        assert len(recent) == 2
    
    def test_event_type_detection(self, sample_game_state):
        """Event type methods should work."""
        kill_event = sample_game_state.events[1]
        dragon_event = sample_game_state.events[2]
        
        assert kill_event.is_kill_event()
        assert not kill_event.is_dragon_event()
        assert dragon_event.is_dragon_event()


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: Player state analysis
# ═══════════════════════════════════════════════════════════════════════════

class TestPlayerStateAnalysis:
    """Test player state analysis."""
    
    def test_get_dead_players(self, sample_game_state):
        """get_dead_players should return dead players."""
        dead = sample_game_state.get_dead_players()
        
        assert len(dead) == 1
        assert dead[0].champion_name == "Zed"
    
    def test_get_dead_players_by_team(self, sample_game_state):
        """get_dead_players should filter by team."""
        dead_order = sample_game_state.get_dead_players(Team.ORDER)
        dead_chaos = sample_game_state.get_dead_players(Team.CHAOS)
        
        assert len(dead_order) == 0
        assert len(dead_chaos) == 1
    
    def test_get_alive_count(self, sample_game_state):
        """get_alive_count should count alive players."""
        order_alive = sample_game_state.get_alive_count(Team.ORDER)
        chaos_alive = sample_game_state.get_alive_count(Team.CHAOS)
        
        assert order_alive == 2
        assert chaos_alive == 0
    
    def test_has_numbers_advantage(self, sample_game_state):
        """has_numbers_advantage should detect advantage."""
        assert sample_game_state.has_numbers_advantage()


# ═══════════════════════════════════════════════════════════════════════════
# Test 9: Performance features (for ML model)
# ═══════════════════════════════════════════════════════════════════════════

class TestPerformanceFeatures:
    """Test performance feature calculation for ML model."""
    
    def test_calculate_performance_features(self, sample_game_state):
        """Should calculate f1, f2, f3 features."""
        features = sample_game_state.calculate_performance_features()
        
        assert "f1" in features
        assert "f2" in features
        assert "f3" in features
        assert "duration" in features
        assert "championName" in features
    
    def test_deaths_per_minute(self, sample_game_state):
        """f1 should be deaths per minute."""
        features = sample_game_state.calculate_performance_features()
        
        # 2 deaths / 15 minutes = 0.133...
        assert abs(features["f1"] - 0.133) < 0.01
    
    def test_ka_per_minute(self, sample_game_state):
        """f2 should be (kills + assists) per minute."""
        features = sample_game_state.calculate_performance_features()
        
        # (4 + 5) / 15 = 0.6
        assert abs(features["f2"] - 0.6) < 0.01
    
    def test_level_per_minute(self, sample_game_state):
        """f3 should be level per minute."""
        features = sample_game_state.calculate_performance_features()
        
        # 9 / 15 = 0.6
        assert abs(features["f3"] - 0.6) < 0.01
    
    def test_get_my_champion_name(self, sample_game_state):
        """get_my_champion_name should return correct champion."""
        name = sample_game_state.get_my_champion_name()
        assert name == "Lux"


# ═══════════════════════════════════════════════════════════════════════════
# Test 10: Edge cases and robustness
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Test edge cases and robustness."""
    
    def test_empty_game_state(self):
        """Should handle minimal game state."""
        state = LiveGameState()
        
        assert state.active_player is None
        assert state.all_players == []
        assert state.events == []
    
    def test_missing_active_player(self):
        """Should handle missing active player."""
        state = LiveGameState.from_api_response({
            "allPlayers": [],
            "events": [],
            "gameData": {"gameTime": 0},
        })
        
        assert state.get_my_team() == Team.NEUTRAL
        features = state.calculate_performance_features()
        assert features["f1"] == 0.0
    
    def test_zero_game_time(self):
        """Should handle zero game time without division error."""
        state = LiveGameState.from_api_response({
            "activePlayer": {"level": 1, "summonerName": "Test"},
            "allPlayers": [],
            "events": [],
            "gameData": {"gameTime": 0},
        })
        
        features = state.calculate_performance_features()
        # Should use 0.1 as minimum to avoid division by zero
        assert features["duration"] > 0
    
    def test_unknown_team(self):
        """Should handle unknown team values."""
        player = Player(
            champion_name="Test",
            summoner_name="Test",
            team="INVALID",
        )
        
        assert player.team_enum == Team.NEUTRAL
    
    def test_unknown_position(self):
        """Should handle unknown position values."""
        player = Player(
            champion_name="Test",
            summoner_name="Test",
            position="INVALID",
        )
        
        assert player.position_enum == Position.UNKNOWN

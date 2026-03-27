"""
TDD Tests for Strategy Agent

Tests cover:
- StrategyAgentConfig validation
- Evaluator behavior
- Advice generation and filtering
- Feedback recording
- AgentOS integration
"""

import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import sys
sys.path.insert(0, "/home/claude/lol-fiddler-agent/src")

from lol_fiddler_agent.agents.strategy_agent import (
    LoLStrategyAgent,
    StrategyAgentConfig,
    StrategicAdvice,
    ActionType,
    Urgency,
    PerformanceFeedback,
    LanePhaseEvaluator,
    ObjectiveEvaluator,
    TeamfightEvaluator,
    WinPredictionEvaluator,
)

from lol_fiddler_agent.network.live_client_data import (
    LiveGameState,
    ActivePlayer,
    Player,
    ChampionStats,
    Scores,
    GameData,
    Team,
    GamePhase,
)


# ═══════════════════════════════════════════════════════════════════════════
# Test Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_config():
    """Sample agent configuration."""
    return StrategyAgentConfig(
        agent_id="test-agent",
        fiddler_api_key="test-key",
        poll_interval_seconds=1.0,
        advice_cooldown_seconds=5.0,
    )


@pytest.fixture
def low_health_state():
    """Game state with low health active player."""
    return LiveGameState(
        active_player=ActivePlayer(
            summoner_name="TestPlayer",
            level=9,
            current_gold=1500.0,
            champion_stats=ChampionStats(
                current_health=250.0,
                max_health=1000.0,
                resource_value=400.0,
                resource_max=600.0,
                resource_type="MANA",
            ),
        ),
        all_players=[
            Player(
                champion_name="Lux",
                summoner_name="TestPlayer",
                team="ORDER",
                position="MIDDLE",
                level=9,
                is_dead=False,
                scores=Scores(kills=2, deaths=1, assists=3, creep_score=100),
            ),
        ],
        game_data=GameData(gameTime=600.0),  # 10 min = early game
    )


@pytest.fixture
def teamfight_advantage_state():
    """Game state with numbers advantage for teamfight."""
    return LiveGameState(
        active_player=ActivePlayer(
            summoner_name="TestPlayer",
            level=13,
            current_gold=3000.0,
            champion_stats=ChampionStats(
                current_health=1500.0,
                max_health=2000.0,
                resource_value=800.0,
                resource_max=1000.0,
            ),
        ),
        all_players=[
            # 4 alive allies
            Player(champion_name="Lux", summoner_name="TestPlayer", team="ORDER", is_dead=False, level=13),
            Player(champion_name="Jinx", summoner_name="Ally1", team="ORDER", is_dead=False, level=12),
            Player(champion_name="Leona", summoner_name="Ally2", team="ORDER", is_dead=False, level=11),
            Player(champion_name="Lee Sin", summoner_name="Ally3", team="ORDER", is_dead=False, level=12),
            # 2 alive enemies, 2 dead
            Player(champion_name="Zed", summoner_name="Enemy1", team="CHAOS", is_dead=True, level=11),
            Player(champion_name="Kaisa", summoner_name="Enemy2", team="CHAOS", is_dead=True, level=10),
            Player(champion_name="Thresh", summoner_name="Enemy3", team="CHAOS", is_dead=False, level=10),
            Player(champion_name="Graves", summoner_name="Enemy4", team="CHAOS", is_dead=False, level=11),
        ],
        game_data=GameData(gameTime=1200.0),  # 20 min = mid game
    )


# ═══════════════════════════════════════════════════════════════════════════
# Test 1: StrategyAgentConfig
# ═══════════════════════════════════════════════════════════════════════════

class TestStrategyAgentConfig:
    """Test StrategyAgentConfig."""
    
    def test_default_values(self):
        """Should have sensible defaults."""
        config = StrategyAgentConfig()
        
        assert config.agent_id == "lol-strategy-agent"
        assert config.fiddler_host == "localhost"
        assert config.fiddler_port == 8868
        assert config.poll_interval_seconds == 2.0
        assert config.enable_win_prediction is True
    
    def test_custom_values(self, sample_config):
        """Should accept custom values."""
        assert sample_config.agent_id == "test-agent"
        assert sample_config.fiddler_api_key == "test-key"
        assert sample_config.poll_interval_seconds == 1.0


# ═══════════════════════════════════════════════════════════════════════════
# Test 2: StrategicAdvice
# ═══════════════════════════════════════════════════════════════════════════

class TestStrategicAdvice:
    """Test StrategicAdvice model."""
    
    def test_basic_advice_creation(self):
        """Should create advice with required fields."""
        advice = StrategicAdvice(
            action=ActionType.FARM,
            urgency=Urgency.MEDIUM,
            reason="Focus on CS",
            confidence=0.75,
        )
        
        assert advice.action == ActionType.FARM
        assert advice.urgency == Urgency.MEDIUM
        assert advice.confidence == 0.75
    
    def test_confidence_bounds(self):
        """Confidence should be between 0 and 1."""
        advice = StrategicAdvice(
            action=ActionType.ALL_IN,
            urgency=Urgency.HIGH,
            reason="Go!",
            confidence=0.0,
        )
        assert advice.confidence == 0.0
        
        advice = StrategicAdvice(
            action=ActionType.ALL_IN,
            urgency=Urgency.HIGH,
            reason="Go!",
            confidence=1.0,
        )
        assert advice.confidence == 1.0
    
    def test_to_display_format(self):
        """to_display should produce readable string."""
        advice = StrategicAdvice(
            action=ActionType.RECALL,
            urgency=Urgency.HIGH,
            reason="Low health",
            confidence=0.85,
        )
        
        display = advice.to_display()
        assert "RECALL" in display
        assert "Low health" in display
        assert "85%" in display
        assert "⚠️" in display  # HIGH urgency emoji


# ═══════════════════════════════════════════════════════════════════════════
# Test 3: LanePhaseEvaluator
# ═══════════════════════════════════════════════════════════════════════════

class TestLanePhaseEvaluator:
    """Test lane phase strategy evaluation."""
    
    def test_low_health_recall_advice(self, low_health_state):
        """Should advise recall when health is low."""
        evaluator = LanePhaseEvaluator()
        advice_list = evaluator.evaluate(low_health_state)
        
        # Should have recall advice
        recall_advice = [a for a in advice_list if a.action == ActionType.RECALL]
        assert len(recall_advice) >= 1
        assert recall_advice[0].urgency == Urgency.HIGH
    
    def test_high_gold_recall_advice(self):
        """Should advise recall when gold is high enough for items."""
        state = LiveGameState(
            active_player=ActivePlayer(
                summoner_name="Test",
                level=6,
                current_gold=1400.0,
                champion_stats=ChampionStats(
                    current_health=900.0,
                    max_health=1000.0,
                ),
            ),
            game_data=GameData(gameTime=480.0),  # 8 min
        )
        
        evaluator = LanePhaseEvaluator()
        advice_list = evaluator.evaluate(state)
        
        # Should have recall advice for item purchase
        recall_advice = [a for a in advice_list if a.action == ActionType.RECALL]
        assert len(recall_advice) >= 1
    
    def test_no_advice_mid_game(self):
        """Should not give lane phase advice in mid game."""
        state = LiveGameState(
            active_player=ActivePlayer(
                summoner_name="Test",
                level=13,
                current_gold=500.0,
                champion_stats=ChampionStats(
                    current_health=200.0,
                    max_health=2000.0,
                ),
            ),
            game_data=GameData(gameTime=1200.0),  # 20 min = mid game
        )
        
        evaluator = LanePhaseEvaluator()
        advice_list = evaluator.evaluate(state)
        
        # Lane phase evaluator should return nothing in mid/late game
        assert len(advice_list) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Test 4: TeamfightEvaluator
# ═══════════════════════════════════════════════════════════════════════════

class TestTeamfightEvaluator:
    """Test teamfight evaluation."""
    
    def test_numbers_advantage_engage(self, teamfight_advantage_state):
        """Should advise engage when numbers advantage."""
        evaluator = TeamfightEvaluator()
        advice_list = evaluator.evaluate(teamfight_advantage_state)
        
        # Should have all_in or trade advice
        engage_advice = [a for a in advice_list if a.action in (ActionType.ALL_IN, ActionType.TRADE)]
        assert len(engage_advice) >= 1
    
    def test_numbers_disadvantage_disengage(self):
        """Should advise disengage when at disadvantage."""
        state = LiveGameState(
            active_player=ActivePlayer(
                summoner_name="Test",
                level=13,
            ),
            all_players=[
                # Only 2 alive allies
                Player(champion_name="Lux", summoner_name="Test", team="ORDER", is_dead=False),
                Player(champion_name="Jinx", summoner_name="Ally1", team="ORDER", is_dead=True),
                # 4 alive enemies
                Player(champion_name="Zed", summoner_name="Enemy1", team="CHAOS", is_dead=False),
                Player(champion_name="Kaisa", summoner_name="Enemy2", team="CHAOS", is_dead=False),
                Player(champion_name="Thresh", summoner_name="Enemy3", team="CHAOS", is_dead=False),
                Player(champion_name="Graves", summoner_name="Enemy4", team="CHAOS", is_dead=False),
            ],
            game_data=GameData(gameTime=1500.0),  # Mid game
        )
        
        evaluator = TeamfightEvaluator()
        advice_list = evaluator.evaluate(state)
        
        # Should have disengage advice
        disengage_advice = [a for a in advice_list if a.action == ActionType.DISENGAGE]
        assert len(disengage_advice) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Test 5: WinPredictionEvaluator
# ═══════════════════════════════════════════════════════════════════════════

class TestWinPredictionEvaluator:
    """Test ML-based win prediction."""
    
    def test_good_performance_aggressive_advice(self):
        """Good performance should get aggressive advice."""
        # High KDA, good level, low deaths
        state = LiveGameState(
            active_player=ActivePlayer(
                summoner_name="Test",
                level=12,
            ),
            all_players=[
                Player(
                    champion_name="Lux",
                    summoner_name="Test",
                    team="ORDER",
                    level=12,
                    scores=Scores(kills=8, deaths=1, assists=10, creep_score=150),
                ),
            ],
            game_data=GameData(gameTime=900.0),  # 15 min
        )
        
        evaluator = WinPredictionEvaluator()
        advice_list = evaluator.evaluate(state)
        
        # Should have advice encouraging aggression
        assert len(advice_list) >= 1
    
    def test_poor_performance_safe_advice(self):
        """Poor performance should get safe advice."""
        # High deaths, low participation
        state = LiveGameState(
            active_player=ActivePlayer(
                summoner_name="Test",
                level=8,
            ),
            all_players=[
                Player(
                    champion_name="Lux",
                    summoner_name="Test",
                    team="ORDER",
                    level=8,
                    scores=Scores(kills=1, deaths=7, assists=2, creep_score=80),
                ),
            ],
            game_data=GameData(gameTime=900.0),  # 15 min
        )
        
        evaluator = WinPredictionEvaluator()
        advice_list = evaluator.evaluate(state)
        
        # Should have safe play advice (farm/defend)
        safe_advice = [a for a in advice_list if a.action in (ActionType.FARM, ActionType.DEFEND)]
        assert len(safe_advice) >= 1


# ═══════════════════════════════════════════════════════════════════════════
# Test 6: LoLStrategyAgent initialization
# ═══════════════════════════════════════════════════════════════════════════

class TestLoLStrategyAgentInit:
    """Test agent initialization."""
    
    def test_agent_creation(self, sample_config):
        """Should create agent with config."""
        agent = LoLStrategyAgent(sample_config)
        
        assert agent.config == sample_config
        assert agent._running is False
        assert len(agent._evaluators) >= 3  # Lane, Objective, Teamfight at minimum
    
    def test_agent_with_win_prediction_disabled(self):
        """Should not include WinPredictionEvaluator when disabled."""
        config = StrategyAgentConfig(enable_win_prediction=False)
        agent = LoLStrategyAgent(config)
        
        # Should not have WinPredictionEvaluator
        evaluator_types = [type(e).__name__ for e in agent._evaluators]
        assert "WinPredictionEvaluator" not in evaluator_types


# ═══════════════════════════════════════════════════════════════════════════
# Test 7: Advice filtering
# ═══════════════════════════════════════════════════════════════════════════

class TestAdviceFiltering:
    """Test advice filtering logic."""
    
    def test_filter_by_cooldown(self, sample_config):
        """Should filter advice on cooldown."""
        agent = LoLStrategyAgent(sample_config)
        
        # Simulate recent advice
        import time
        agent._advice_cooldowns[ActionType.RECALL] = time.time()
        
        advice_list = [
            StrategicAdvice(action=ActionType.RECALL, urgency=Urgency.HIGH, reason="A", confidence=0.9),
            StrategicAdvice(action=ActionType.FARM, urgency=Urgency.MEDIUM, reason="B", confidence=0.8),
        ]
        
        filtered = agent._filter_advice(advice_list)
        
        # RECALL should be filtered out (on cooldown)
        assert ActionType.RECALL not in [a.action for a in filtered]
        assert ActionType.FARM in [a.action for a in filtered]
    
    def test_filter_limits_to_top_3(self, sample_config):
        """Should limit to 3 most important advice."""
        agent = LoLStrategyAgent(sample_config)
        
        advice_list = [
            StrategicAdvice(action=ActionType.RECALL, urgency=Urgency.LOW, reason="1", confidence=0.5),
            StrategicAdvice(action=ActionType.FARM, urgency=Urgency.LOW, reason="2", confidence=0.5),
            StrategicAdvice(action=ActionType.TRADE, urgency=Urgency.LOW, reason="3", confidence=0.5),
            StrategicAdvice(action=ActionType.ALL_IN, urgency=Urgency.LOW, reason="4", confidence=0.5),
            StrategicAdvice(action=ActionType.ROAM, urgency=Urgency.LOW, reason="5", confidence=0.5),
        ]
        
        filtered = agent._filter_advice(advice_list)
        
        assert len(filtered) <= 3
    
    def test_filter_prioritizes_urgency(self, sample_config):
        """Should prioritize higher urgency advice."""
        agent = LoLStrategyAgent(sample_config)
        
        advice_list = [
            StrategicAdvice(action=ActionType.FARM, urgency=Urgency.LOW, reason="Low", confidence=0.9),
            StrategicAdvice(action=ActionType.ALL_IN, urgency=Urgency.CRITICAL, reason="Critical", confidence=0.7),
            StrategicAdvice(action=ActionType.TRADE, urgency=Urgency.MEDIUM, reason="Medium", confidence=0.8),
        ]
        
        filtered = agent._filter_advice(advice_list)
        
        # CRITICAL should be first
        assert filtered[0].action == ActionType.ALL_IN


# ═══════════════════════════════════════════════════════════════════════════
# Test 8: Feedback recording
# ═══════════════════════════════════════════════════════════════════════════

class TestFeedbackRecording:
    """Test feedback recording for RL training."""
    
    def test_record_feedback(self, sample_config, low_health_state):
        """Should record feedback correctly."""
        agent = LoLStrategyAgent(sample_config)
        agent._last_state = low_health_state
        
        agent.record_feedback(
            advice_id="adv_123",
            followed=True,
            outcome="positive",
            details="Recalled and bought items",
        )
        
        assert len(agent._feedback_history) == 1
        feedback = agent._feedback_history[0]
        assert feedback.advice_id == "adv_123"
        assert feedback.followed is True
        assert feedback.outcome == "positive"
    
    def test_export_training_data(self, sample_config, low_health_state):
        """Should export advice/feedback pairs."""
        agent = LoLStrategyAgent(sample_config)
        agent._last_state = low_health_state
        
        # Add some advice
        advice = StrategicAdvice(
            action=ActionType.RECALL,
            urgency=Urgency.HIGH,
            reason="Test",
            confidence=0.9,
            advice_id="adv_456",
        )
        agent._advice_history.append(advice)
        
        # Add feedback
        agent.record_feedback("adv_456", True, "positive")
        
        data = agent.export_training_data()
        
        assert data["total_advice"] == 1
        assert data["total_feedback"] == 1
        assert len(data["pairs"]) == 1
        assert data["pairs"][0]["advice"]["advice_id"] == "adv_456"
        assert data["pairs"][0]["feedback"]["followed"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Test 9: Agent lifecycle
# ═══════════════════════════════════════════════════════════════════════════

class TestAgentLifecycle:
    """Test agent start/stop lifecycle."""
    
    @pytest.mark.asyncio
    async def test_start_sets_running(self, sample_config):
        """Starting should set running flag."""
        agent = LoLStrategyAgent(sample_config)
        
        # Mock Fiddler init
        with patch.object(agent, "_init_fiddler", new_callable=AsyncMock):
            result = await agent._start_monitoring()
            
            assert result.success is True
            assert agent._running is True
            
            # Cleanup
            agent._running = False
    
    @pytest.mark.asyncio
    async def test_stop_clears_state(self, sample_config):
        """Stopping should clear state."""
        agent = LoLStrategyAgent(sample_config)
        agent._running = True
        
        # Mock Fiddler close
        with patch.object(agent, "_close_fiddler", new_callable=AsyncMock):
            result = await agent._stop_monitoring()
            
            assert result.success is True
            assert agent._running is False
    
    @pytest.mark.asyncio
    async def test_cannot_start_twice(self, sample_config):
        """Should not start if already running."""
        agent = LoLStrategyAgent(sample_config)
        agent._running = True
        
        result = await agent._start_monitoring()
        
        assert result.success is False
        assert "Already monitoring" in result.error

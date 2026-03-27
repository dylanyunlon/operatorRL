"""
LoL Strategy Agent - AgentOS Integration

This agent uses Fiddler MCP network capture to monitor League of Legends
game state and provide real-time strategic recommendations.

Architecture:
1. FiddlerMCPClient captures HTTP traffic from LoL client
2. LiveClientData parser extracts structured game state
3. StrategyAgent (AgentOS BaseAgent) analyzes state and generates advice
4. Feedback loop records player actions vs recommendations for RL training

Key Features:
- Real-time win probability prediction
- Lane-specific advice (CS, trading, roaming)
- Objective timing (dragon, baron, tower)
- Team fight engagement recommendations
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

# Import from AgentOS (assumed to be in PYTHONPATH)
try:
    from agent_os.base_agent import AgentConfig, BaseAgent, PolicyDecision
    from agent_os.stateless import ExecutionContext, ExecutionResult
    AGENTOS_AVAILABLE = True
except ImportError:
    # Fallback for standalone testing
    AGENTOS_AVAILABLE = False
    
    @dataclass
    class AgentConfig:
        agent_id: str
        policies: list = field(default_factory=list)
        metadata: dict = field(default_factory=dict)
    
    class ExecutionResult:
        def __init__(self, success: bool = True, data: Any = None, error: str = None):
            self.success = success
            self.data = data
            self.error = error
    
    class BaseAgent(ABC):
        def __init__(self, config: AgentConfig):
            self._config = config
        
        @abstractmethod
        async def run(self, *args, **kwargs) -> ExecutionResult:
            pass

from lol_fiddler_agent.network.fiddler_client import FiddlerMCPClient, FiddlerConfig, HTTPSession
from lol_fiddler_agent.network.live_client_data import (
    LiveGameState,
    Team,
    Position,
    GamePhase,
    Player,
    parse_captured_game_state,
)

logger = logging.getLogger(__name__)


# ── Strategy Types ─────────────────────────────────────────────────────────

class ActionType(str, Enum):
    """Types of strategic actions."""
    FARM = "farm"               # Focus on CS
    TRADE = "trade"             # Look for trades
    ALL_IN = "all_in"           # Commit to kill
    ROAM = "roam"               # Leave lane to help team
    RECALL = "recall"           # Back to base
    OBJECTIVE = "objective"     # Contest dragon/baron/tower
    DEFEND = "defend"           # Defend tower/inhibitor
    GROUP = "group"             # Group with team
    SPLIT = "split"             # Split push
    DISENGAGE = "disengage"     # Avoid fight


class Urgency(str, Enum):
    """Urgency level of advice."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StrategicAdvice(BaseModel):
    """A piece of strategic advice for the player."""
    action: ActionType
    urgency: Urgency
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    
    # Optional details
    target_position: Optional[str] = None  # e.g., "dragon pit", "top lane"
    time_window_seconds: Optional[float] = None  # How long this advice is valid
    
    # For feedback tracking
    advice_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_display(self) -> str:
        """Format for display to player."""
        urgency_emoji = {
            Urgency.LOW: "💭",
            Urgency.MEDIUM: "💡",
            Urgency.HIGH: "⚠️",
            Urgency.CRITICAL: "🚨",
        }
        return f"{urgency_emoji[self.urgency]} [{self.action.value.upper()}] {self.reason} (confidence: {self.confidence:.0%})"


class PerformanceFeedback(BaseModel):
    """Feedback on player's performance relative to advice."""
    advice_id: str
    followed: bool
    outcome: str  # "positive", "negative", "neutral"
    details: str
    game_time: float
    
    # Metrics at time of evaluation
    kda_change: float = 0.0
    gold_change: int = 0
    objective_taken: bool = False


# ── Strategy Evaluators ────────────────────────────────────────────────────

class StrategyEvaluator(ABC):
    """Base class for strategy evaluation components."""
    
    @abstractmethod
    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        """Evaluate game state and return strategic advice."""
        pass


class LanePhaseEvaluator(StrategyEvaluator):
    """Evaluates lane phase strategy (early game)."""
    
    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice = []
        
        if not state.active_player or not state.game_data:
            return advice
        
        # Only relevant in early game
        if state.game_data.game_phase != GamePhase.EARLY:
            return advice
        
        # Get my stats
        my_stats = state.active_player.champion_stats
        if not my_stats:
            return advice
        
        # Low health - consider recall
        if my_stats.is_low_health(threshold=30):
            advice.append(StrategicAdvice(
                action=ActionType.RECALL,
                urgency=Urgency.HIGH,
                reason="Health below 30%, recall to avoid dying",
                confidence=0.85,
            ))
        elif my_stats.is_low_health(threshold=50):
            advice.append(StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.MEDIUM,
                reason="Health at 50%, play safe and farm under tower",
                confidence=0.70,
            ))
        
        # Low mana/resource - play passive
        if my_stats.resource_percent < 20 and my_stats.resource_type == "MANA":
            advice.append(StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.MEDIUM,
                reason="Low mana, conserve spells and farm with autos",
                confidence=0.75,
            ))
        
        # Check gold for item spike
        gold = state.active_player.current_gold
        if gold >= 1300:
            advice.append(StrategicAdvice(
                action=ActionType.RECALL,
                urgency=Urgency.MEDIUM,
                reason=f"Have {int(gold)}g, good time to recall for items",
                confidence=0.65,
            ))
        
        return advice


class ObjectiveEvaluator(StrategyEvaluator):
    """Evaluates objective control strategy."""
    
    # Approximate spawn times (seconds)
    DRAGON_SPAWN = 5 * 60  # 5 minutes
    DRAGON_RESPAWN = 5 * 60  # 5 minutes after kill
    BARON_SPAWN = 20 * 60  # 20 minutes
    
    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice = []
        
        if not state.game_data:
            return advice
        
        game_time = state.game_data.game_time
        my_team = state.get_my_team()
        
        # Dragon advice
        if game_time >= self.DRAGON_SPAWN - 30:  # 30s before spawn
            # Check if we have numbers advantage
            if state.has_numbers_advantage():
                advice.append(StrategicAdvice(
                    action=ActionType.OBJECTIVE,
                    urgency=Urgency.HIGH,
                    reason="Dragon spawning soon and we have numbers advantage",
                    confidence=0.80,
                    target_position="dragon pit",
                    time_window_seconds=60,
                ))
            else:
                # Check bot lane priority
                dead_enemies = state.get_dead_players(
                    Team.CHAOS if my_team == Team.ORDER else Team.ORDER
                )
                if len(dead_enemies) >= 2:
                    advice.append(StrategicAdvice(
                        action=ActionType.OBJECTIVE,
                        urgency=Urgency.CRITICAL,
                        reason=f"{len(dead_enemies)} enemies dead - secure dragon NOW",
                        confidence=0.90,
                        target_position="dragon pit",
                        time_window_seconds=30,
                    ))
        
        # Baron advice
        if game_time >= self.BARON_SPAWN:
            # Baron with advantage
            gold_diff = state.get_gold_difference()
            if gold_diff > 3000 and state.has_numbers_advantage():
                advice.append(StrategicAdvice(
                    action=ActionType.OBJECTIVE,
                    urgency=Urgency.HIGH,
                    reason="Significant gold lead and numbers - baron opportunity",
                    confidence=0.75,
                    target_position="baron pit",
                    time_window_seconds=45,
                ))
        
        return advice


class TeamfightEvaluator(StrategyEvaluator):
    """Evaluates team fight engagement strategy."""
    
    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice = []
        
        if not state.game_data:
            return advice
        
        # Mid to late game team fight evaluation
        if state.game_data.game_phase == GamePhase.EARLY:
            return advice
        
        my_team = state.get_my_team()
        enemy_team = Team.CHAOS if my_team == Team.ORDER else Team.ORDER
        
        # Calculate team strength
        my_alive = state.get_alive_count(my_team)
        enemy_alive = state.get_alive_count(enemy_team)
        
        # Check for fight opportunities
        if my_alive > enemy_alive + 1:  # 5v3 or better
            advice.append(StrategicAdvice(
                action=ActionType.ALL_IN,
                urgency=Urgency.HIGH,
                reason=f"Numbers advantage ({my_alive}v{enemy_alive}) - ENGAGE!",
                confidence=0.85,
            ))
        elif my_alive > enemy_alive:  # 5v4 or 4v3
            advice.append(StrategicAdvice(
                action=ActionType.TRADE,
                urgency=Urgency.MEDIUM,
                reason=f"Slight numbers advantage ({my_alive}v{enemy_alive}) - look for picks",
                confidence=0.70,
            ))
        elif my_alive < enemy_alive:  # Disadvantage
            advice.append(StrategicAdvice(
                action=ActionType.DISENGAGE,
                urgency=Urgency.HIGH,
                reason=f"Numbers disadvantage ({my_alive}v{enemy_alive}) - avoid fights",
                confidence=0.80,
            ))
        
        # Check for Baron buff
        if state.has_baron_buff(enemy_team):
            advice.append(StrategicAdvice(
                action=ActionType.DEFEND,
                urgency=Urgency.CRITICAL,
                reason="Enemy has Baron buff - defend towers and clear waves",
                confidence=0.90,
            ))
        elif state.has_baron_buff(my_team):
            advice.append(StrategicAdvice(
                action=ActionType.GROUP,
                urgency=Urgency.HIGH,
                reason="We have Baron buff - group and siege",
                confidence=0.85,
            ))
        
        return advice


class WinPredictionEvaluator(StrategyEvaluator):
    """Predicts win probability based on game state.
    
    Uses the feature engineering from leagueoflegends-optimizer:
    - f1: deaths per minute
    - f2: kills + assists per minute  
    - f3: level per minute
    
    Thresholds from their dataset analysis:
    - calculated_player_performance mean: 49.33
    - 25th percentile: 33.95 (likely loss)
    - 75th percentile: 65.10 (likely win)
    """
    
    # Thresholds from lol-optimizer dataset statistics
    THRESHOLD_F1 = [0.194691, 0.126134, 0.267318]  # deaths/min: median, q1, q3
    THRESHOLD_F2 = [0.466420, 0.306988, 0.639247]  # (k+a)/min: median, q1, q3
    THRESHOLD_F3 = [0.505454, 0.462111, 0.555198]  # level/min: median, q1, q3
    
    def evaluate(self, state: LiveGameState) -> list[StrategicAdvice]:
        advice = []
        
        features = state.calculate_performance_features()
        duration = features.get("duration", 0)
        
        if duration < 2:  # Not enough game time
            return advice
        
        # Evaluate each feature
        f1_score = self._evaluate_feature(features["f1"], self.THRESHOLD_F1, lower_is_better=True)
        f2_score = self._evaluate_feature(features["f2"], self.THRESHOLD_F2, lower_is_better=False)
        f3_score = self._evaluate_feature(features["f3"], self.THRESHOLD_F3, lower_is_better=False)
        
        # Weighted average for win prediction
        # f1 (deaths) is inverted since lower is better
        win_score = (f1_score * 0.3 + f2_score * 0.4 + f3_score * 0.3)
        
        # Convert to win probability (roughly linear between thresholds)
        win_probability = max(0.0, min(1.0, (win_score + 1) / 2))
        
        # Generate advice based on performance
        if win_probability >= 0.65:
            advice.append(StrategicAdvice(
                action=ActionType.ALL_IN,
                urgency=Urgency.MEDIUM,
                reason=f"Strong performance ({win_probability:.0%} win estimate) - press your advantage",
                confidence=win_probability,
            ))
        elif win_probability <= 0.35:
            advice.append(StrategicAdvice(
                action=ActionType.FARM,
                urgency=Urgency.MEDIUM,
                reason=f"Behind pace ({win_probability:.0%} win estimate) - focus on farming and scaling",
                confidence=1 - win_probability,
            ))
        
        # Specific feedback on metrics
        if features["f1"] > self.THRESHOLD_F1[2]:  # High deaths
            advice.append(StrategicAdvice(
                action=ActionType.DEFEND,
                urgency=Urgency.HIGH,
                reason=f"Death rate too high ({features['f1']:.2f}/min) - play safer",
                confidence=0.80,
            ))
        
        if features["f2"] < self.THRESHOLD_F2[1]:  # Low participation
            advice.append(StrategicAdvice(
                action=ActionType.GROUP,
                urgency=Urgency.MEDIUM,
                reason=f"Low kill participation ({features['f2']:.2f}/min) - join team fights",
                confidence=0.70,
            ))
        
        return advice
    
    def _evaluate_feature(
        self,
        value: float,
        thresholds: list[float],
        lower_is_better: bool = False,
    ) -> float:
        """Evaluate a feature value against thresholds.
        
        Returns a score from -1 (bad) to +1 (good).
        """
        median, q1, q3 = thresholds
        
        if lower_is_better:
            if value < q1:
                return 1.0  # Excellent
            elif value < median:
                return 0.5
            elif value < q3:
                return -0.5
            else:
                return -1.0  # Bad
        else:
            if value > q3:
                return 1.0  # Excellent
            elif value > median:
                return 0.5
            elif value > q1:
                return -0.5
            else:
                return -1.0  # Bad


# ── Main Strategy Agent ────────────────────────────────────────────────────

@dataclass
class StrategyAgentConfig:
    """Configuration for the LoL Strategy Agent."""
    agent_id: str = "lol-strategy-agent"
    fiddler_api_key: str = ""
    fiddler_host: str = "localhost"
    fiddler_port: int = 8868
    
    # Polling settings
    poll_interval_seconds: float = 2.0  # How often to check for game state
    advice_cooldown_seconds: float = 10.0  # Min time between same advice
    
    # Feature flags
    enable_win_prediction: bool = True
    enable_objective_tracking: bool = True
    enable_teamfight_analysis: bool = True
    
    # AgentOS policies
    policies: list[str] = field(default_factory=lambda: ["read_only", "no_pii"])


class LoLStrategyAgent(BaseAgent):
    """League of Legends Strategy Agent.
    
    Monitors game state via Fiddler MCP and provides real-time strategic advice.
    Integrates with AgentOS for policy governance and audit logging.
    
    Example:
        >>> config = StrategyAgentConfig(fiddler_api_key="your-key")
        >>> agent = LoLStrategyAgent(config)
        >>> await agent.start()
        >>> # Agent now monitors game and prints advice
        >>> await agent.stop()
    """
    
    def __init__(self, config: StrategyAgentConfig) -> None:
        agent_config = AgentConfig(
            agent_id=config.agent_id,
            policies=config.policies,
            metadata={"type": "lol_strategy", "version": "0.1.0"},
        )
        super().__init__(agent_config)
        
        self.config = config
        self._fiddler: Optional[FiddlerMCPClient] = None
        self._running = False
        self._last_state: Optional[LiveGameState] = None
        self._advice_history: list[StrategicAdvice] = []
        self._feedback_history: list[PerformanceFeedback] = []
        self._advice_cooldowns: dict[ActionType, float] = {}
        
        # Initialize evaluators
        self._evaluators: list[StrategyEvaluator] = [
            LanePhaseEvaluator(),
            ObjectiveEvaluator(),
            TeamfightEvaluator(),
        ]
        
        if config.enable_win_prediction:
            self._evaluators.append(WinPredictionEvaluator())
    
    async def _init_fiddler(self) -> None:
        """Initialize Fiddler MCP client."""
        fiddler_config = FiddlerConfig(
            host=self.config.fiddler_host,
            port=self.config.fiddler_port,
            api_key=self.config.fiddler_api_key,
        )
        self._fiddler = FiddlerMCPClient(fiddler_config)
        await self._fiddler.connect()
        await self._fiddler.setup_lol_capture()
    
    async def _close_fiddler(self) -> None:
        """Close Fiddler MCP client."""
        if self._fiddler:
            await self._fiddler.disconnect()
            self._fiddler = None
    
    async def run(self, task: str = "monitor") -> ExecutionResult:
        """Run the strategy agent.
        
        Args:
            task: "monitor" to start monitoring, "stop" to stop
            
        Returns:
            ExecutionResult with success status
        """
        if task == "monitor":
            return await self._start_monitoring()
        elif task == "stop":
            return await self._stop_monitoring()
        else:
            return ExecutionResult(
                success=False,
                data=None,
                error=f"Unknown task: {task}",
            )
    
    async def _start_monitoring(self) -> ExecutionResult:
        """Start the game monitoring loop."""
        if self._running:
            return ExecutionResult(
                success=False,
                data=None,
                error="Already monitoring",
            )
        
        try:
            await self._init_fiddler()
            self._running = True
            
            logger.info("LoL Strategy Agent started monitoring")
            
            # Start monitoring loop
            asyncio.create_task(self._monitoring_loop())
            
            return ExecutionResult(
                success=True,
                data={"status": "monitoring_started"},
            )
        except Exception as e:
            logger.error(f"Failed to start monitoring: {e}")
            return ExecutionResult(
                success=False,
                data=None,
                error=str(e),
            )
    
    async def _stop_monitoring(self) -> ExecutionResult:
        """Stop the game monitoring loop."""
        self._running = False
        await self._close_fiddler()
        
        logger.info("LoL Strategy Agent stopped monitoring")
        
        return ExecutionResult(
            success=True,
            data={
                "status": "monitoring_stopped",
                "advice_given": len(self._advice_history),
                "feedback_collected": len(self._feedback_history),
            },
        )
    
    async def _monitoring_loop(self) -> None:
        """Main monitoring loop - polls Fiddler for game state."""
        while self._running:
            try:
                # Get LoL sessions from Fiddler
                await self._process_game_state()
            except Exception as e:
                logger.warning(f"Error in monitoring loop: {e}")
            
            await asyncio.sleep(self.config.poll_interval_seconds)
    
    async def _process_game_state(self) -> None:
        """Process latest game state from captured traffic."""
        if not self._fiddler:
            return
        
        # Get LoL-specific sessions with response bodies
        sessions = await self._fiddler.get_lol_sessions(include_details=True)
        
        # Find Live Client API responses
        for session in sessions:
            if session.is_live_client() and session.response_body:
                state = parse_captured_game_state(session.response_body)
                if state:
                    await self._handle_game_state(state)
                    break
    
    async def _handle_game_state(self, state: LiveGameState) -> None:
        """Handle a new game state update."""
        self._last_state = state
        
        # Generate advice from all evaluators
        all_advice: list[StrategicAdvice] = []
        for evaluator in self._evaluators:
            try:
                advice = evaluator.evaluate(state)
                all_advice.extend(advice)
            except Exception as e:
                logger.warning(f"Evaluator {evaluator.__class__.__name__} error: {e}")
        
        # Filter by cooldown and priority
        filtered_advice = self._filter_advice(all_advice)
        
        # Display and record advice
        for advice in filtered_advice:
            advice.advice_id = f"adv_{int(time.time() * 1000)}"
            self._advice_history.append(advice)
            self._advice_cooldowns[advice.action] = time.time()
            
            # Display to player
            logger.info(advice.to_display())
            print(advice.to_display())
    
    def _filter_advice(self, advice_list: list[StrategicAdvice]) -> list[StrategicAdvice]:
        """Filter advice by cooldown and deduplicate."""
        now = time.time()
        cooldown = self.config.advice_cooldown_seconds
        
        filtered = []
        seen_actions = set()
        
        # Sort by urgency (critical first) then confidence
        sorted_advice = sorted(
            advice_list,
            key=lambda a: (
                -["low", "medium", "high", "critical"].index(a.urgency.value),
                -a.confidence,
            ),
        )
        
        for advice in sorted_advice:
            # Skip if same action recently given
            last_time = self._advice_cooldowns.get(advice.action, 0)
            if now - last_time < cooldown:
                continue
            
            # Skip duplicate actions in same batch
            if advice.action in seen_actions:
                continue
            
            seen_actions.add(advice.action)
            filtered.append(advice)
        
        # Limit to top 3 most important
        return filtered[:3]
    
    def record_feedback(
        self,
        advice_id: str,
        followed: bool,
        outcome: str,
        details: str = "",
    ) -> None:
        """Record feedback on whether player followed advice and outcome.
        
        This data is used for RL training of the strategy model.
        """
        if not self._last_state or not self._last_state.game_data:
            return
        
        feedback = PerformanceFeedback(
            advice_id=advice_id,
            followed=followed,
            outcome=outcome,
            details=details,
            game_time=self._last_state.game_data.game_time,
        )
        self._feedback_history.append(feedback)
        
        logger.info(f"Feedback recorded: {advice_id} followed={followed} outcome={outcome}")
    
    def get_advice_history(self) -> list[StrategicAdvice]:
        """Get all advice given this session."""
        return list(self._advice_history)
    
    def get_feedback_history(self) -> list[PerformanceFeedback]:
        """Get all feedback recorded this session."""
        return list(self._feedback_history)
    
    def get_current_state(self) -> Optional[LiveGameState]:
        """Get the most recent game state."""
        return self._last_state
    
    def export_training_data(self) -> dict[str, Any]:
        """Export advice/feedback pairs for RL training."""
        pairs = []
        feedback_map = {f.advice_id: f for f in self._feedback_history}
        
        for advice in self._advice_history:
            feedback = feedback_map.get(advice.advice_id)
            pairs.append({
                "advice": advice.model_dump(),
                "feedback": feedback.model_dump() if feedback else None,
            })
        
        return {
            "session_id": self._config.agent_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pairs": pairs,
            "total_advice": len(self._advice_history),
            "total_feedback": len(self._feedback_history),
        }


# ── CLI Entry Point ────────────────────────────────────────────────────────

async def main() -> None:
    """CLI entry point for running the agent."""
    import argparse
    
    parser = argparse.ArgumentParser(description="LoL Strategy Agent")
    parser.add_argument("--api-key", required=True, help="Fiddler MCP API key")
    parser.add_argument("--host", default="localhost", help="Fiddler host")
    parser.add_argument("--port", type=int, default=8868, help="Fiddler port")
    parser.add_argument("--poll-interval", type=float, default=2.0, help="Poll interval (seconds)")
    
    args = parser.parse_args()
    
    config = StrategyAgentConfig(
        fiddler_api_key=args.api_key,
        fiddler_host=args.host,
        fiddler_port=args.port,
        poll_interval_seconds=args.poll_interval,
    )
    
    agent = LoLStrategyAgent(config)
    
    try:
        result = await agent.run("monitor")
        if not result.success:
            print(f"Failed to start: {result.error}")
            return
        
        print("LoL Strategy Agent running. Press Ctrl+C to stop.")
        
        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping agent...")
        await agent.run("stop")
        
        # Export training data
        data = agent.export_training_data()
        print(f"\nSession summary: {data['total_advice']} advice given, {data['total_feedback']} feedback recorded")


if __name__ == "__main__":
    asyncio.run(main())

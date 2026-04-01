"""
ABC 实现适配器 — 将 lolbot-HyperAI 组件适配到 operatorRL ABC 接口
==================================================================
查看 modules/game_bridge_abc.py + modules/strategy_advisor_abc.py 上现有
跨游戏抽象接口的实现方式, 理解其模式, 特别是 @abstractmethod
和 game_name property 是如何与游戏特定逻辑分离的。
可以从 integrations/lol/src/lol_agent/lol_strategy_advisor.py 的
LoL 特定策略适配 这个好例子开始。
然后, 遵循该模式实现 LoLGameBridge / LoLStrategyAdvisor / LoLObjectiveTracker,
让 operatorRL 的统一接口 可以 通过 ABC 实现直接调用 lolbot-HyperAI 的组件,
并能 作为 modules/ 下 8 个 ABC 的 LoL 具体实现注册到系统。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

Architecture position:
    modules/common/adapters/abc_impl.py   ← YOU ARE HERE
    ├─ Implements: modules/game_bridge_abc.py
    ├─ Implements: modules/strategy_advisor_abc.py
    ├─ Implements: modules/objective_tracker_abc.py
    ├─ Implements: modules/danger_assessment_abc.py
    ├─ Wraps: CanbusComponent, PlanningComponent, ObjectiveTimer, etc.
    └─ Consumed by: operatorRL unified orchestrator

Apollo reference:
    modules/canbus/vehicle/lincoln/ — vehicle-specific implementation
    modules/canbus/vehicle/abstract_vehicle_factory.h — factory pattern
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from cyber.logger.cyber_logger import get_logger
from cyber.node.node import CyberNode, Reader, Writer
from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    StrategyAdvice,
    TeamSide,
    VoiceCommand,
    WinPrediction,
)

logger = get_logger("adapters.abc")


# ─── LoLGameBridge (implements GameBridgeABC) ────────────────────────────────

class LoLGameBridge:
    """LoL-specific implementation of GameBridgeABC.

    Bridges operatorRL's unified game interface to lolbot-HyperAI's
    CyberNode pub/sub channels.  Reads /lol/game_state for state
    and writes to /lol/voice_command for actions.

    Reference: modules/game_bridge_abc.py
    """

    def __init__(self) -> None:
        self._node: Optional[CyberNode] = None
        self._state_reader: Optional[Reader] = None
        self._voice_writer: Optional[Writer] = None
        self._connected: bool = False
        self._last_state: Optional[GameSnapshot] = None

    @property
    def game_name(self) -> str:
        return "league_of_legends"

    def connect(self) -> None:
        """Establish connection to the lolbot-HyperAI pipeline."""
        self._node = CyberNode("lol_bridge")
        self._state_reader = self._node.CreateReader(
            "/lol/game_state", GameSnapshot, pending_queue_size=4,
        )
        self._voice_writer = self._node.CreateWriter(
            "/lol/voice_command", VoiceCommand,
        )
        self._connected = True
        logger.info("LoLGameBridge connected")

    def disconnect(self) -> None:
        """Close connection."""
        if self._node:
            self._node.shutdown()
        self._connected = False
        logger.info("LoLGameBridge disconnected")

    def get_game_state(self) -> dict[str, Any]:
        """Retrieve current game state as a dict.

        Returns:
            Dict from GameSnapshot.to_feature_dict(), or empty dict.
        """
        if self._state_reader is None:
            return {}

        self._state_reader.Observe()
        snapshot = self._state_reader.GetLatestObserved()
        if snapshot is None:
            return {}

        self._last_state = snapshot
        state = snapshot.to_feature_dict()
        state["game_mode"] = snapshot.game_mode
        state["phase"] = snapshot.phase.name
        state["player_count"] = snapshot.player_count
        state["active_team"] = snapshot.active_team.name
        return state

    def send_action(self, action: Any) -> bool:
        """Dispatch an action (voice command) to the game.

        Args:
            action: Either a string (narrate) or dict with keys
                    "text", "priority", "source".

        Returns:
            True if action was dispatched.
        """
        if self._voice_writer is None:
            return False

        if isinstance(action, str):
            cmd = VoiceCommand(
                text=action, priority=5,
                game_time=self._last_state.game_time if self._last_state else 0,
                source_module="lol_bridge",
            )
        elif isinstance(action, dict):
            cmd = VoiceCommand(
                text=action.get("text", ""),
                priority=action.get("priority", 5),
                game_time=action.get("game_time", 0),
                source_module="lol_bridge",
            )
        else:
            return False

        self._voice_writer.Write(cmd)
        return True

    @property
    def is_connected(self) -> bool:
        return self._connected


# ─── LoLStrategyAdvisor (implements StrategyAdvisorABC) ──────────────────────

class LoLStrategyAdvisor:
    """LoL-specific implementation of StrategyAdvisorABC.

    Reads from lolbot-HyperAI's /lol/strategy_advice and
    /lol/win_prediction channels to provide strategy advice
    through the operatorRL unified interface.

    Reference: modules/strategy_advisor_abc.py
    """

    def __init__(self) -> None:
        self._node: Optional[CyberNode] = None
        self._advice_reader: Optional[Reader] = None
        self._win_reader: Optional[Reader] = None
        self._confidence: float = 0.5
        self._action_scores: List[float] = []
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    @property
    def game_name(self) -> str:
        return "league_of_legends"

    def initialize(self) -> None:
        """Set up channel readers."""
        self._node = CyberNode("lol_advisor")
        self._advice_reader = self._node.CreateReader(
            "/lol/strategy_advice", StrategyAdvice, pending_queue_size=4,
        )
        self._win_reader = self._node.CreateReader(
            "/lol/win_prediction", WinPrediction, pending_queue_size=4,
        )

    def advise(self, game_state: dict[str, Any]) -> dict[str, Any]:
        """Produce a strategy suggestion.

        Reads the latest StrategyAdvice from the planning pipeline
        and packages it for the operatorRL unified interface.
        """
        if self._advice_reader is None:
            return {"action": "no_advice", "reasoning": "not initialized"}

        self._advice_reader.Observe()
        advice = self._advice_reader.GetLatestObserved()

        if advice is None:
            return {"action": "waiting", "reasoning": "no advice yet"}

        # Update confidence from win prediction
        if self._win_reader:
            self._win_reader.Observe()
            win = self._win_reader.GetLatestObserved()
            if win:
                self._confidence = win.confidence

        result = {
            "action": advice.primary_action,
            "secondary": advice.secondary_action,
            "macro": advice.macro_call,
            "reasoning": advice.reasoning,
            "confidence": advice.confidence,
            "urgency": advice.urgency,
            "game_time": advice.game_time,
        }

        if self.evolution_callback:
            self.evolution_callback({"event": "advice_given", "data": result})

        return result

    def evaluate_action(self, action: Any, outcome: Any) -> float:
        """Evaluate an action given its outcome.

        Used for evolution loop feedback.
        """
        score = 0.0
        if isinstance(outcome, dict):
            if outcome.get("success", False):
                score = 1.0
            elif outcome.get("neutral", False):
                score = 0.0
            else:
                score = -0.5

        self._action_scores.append(score)
        return score

    def get_confidence(self) -> float:
        """Get current confidence level."""
        return self._confidence

    def shutdown(self) -> None:
        if self._node:
            self._node.shutdown()


# ─── LoLObjectiveTracker (implements ObjectiveTrackerABC) ────────────────────

class LoLObjectiveTracker:
    """LoL-specific implementation of ObjectiveTrackerABC.

    Wraps the ObjectiveTimer from prediction module to provide
    the operatorRL unified objective tracking interface.

    Reference: modules/objective_tracker_abc.py
    """

    def __init__(self) -> None:
        # Lazy import to avoid circular dependency
        self._timer = None
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def _ensure_timer(self):
        if self._timer is None:
            from modules.prediction.objective.objective_timer import ObjectiveTimer
            self._timer = ObjectiveTimer()

    def start_timer(self, objective: str, game_time: float) -> None:
        self._ensure_timer()
        self._timer.start_timer(objective, game_time)

    def time_remaining(self, objective: str, current_time: float) -> float:
        self._ensure_timer()
        return self._timer.time_remaining(objective, current_time)

    def clear(self, objective: str) -> None:
        self._ensure_timer()
        self._timer.clear(objective)

    def active_timers(self) -> list[str]:
        self._ensure_timer()
        return self._timer.active_timers()


# ─── LoLDangerAssessment (implements DangerAssessmentABC) ────────────────────

class LoLDangerAssessment:
    """LoL-specific implementation of DangerAssessmentABC.

    Assesses danger based on enemy alive count, level disadvantage,
    gold deficit, and recent kill activity from /lol/game_state.

    Reference: modules/danger_assessment_abc.py
    """

    def __init__(self) -> None:
        self._node: Optional[CyberNode] = None
        self._state_reader: Optional[Reader] = None

    def initialize(self) -> None:
        self._node = CyberNode("lol_danger")
        self._state_reader = self._node.CreateReader(
            "/lol/game_state", GameSnapshot, pending_queue_size=4,
        )

    def assess(self, game_state: dict[str, Any] | None = None) -> dict[str, Any]:
        """Assess current danger level.

        Returns:
            Dict with danger_level (0-1), safe_zones, threats.
        """
        if self._state_reader is None:
            return {"danger_level": 0.5, "safe": True}

        self._state_reader.Observe()
        snapshot = self._state_reader.GetLatestObserved()
        if snapshot is None:
            return {"danger_level": 0.0, "safe": True}

        active = snapshot.active_player
        if active is None:
            return {"danger_level": 0.0, "safe": True}

        enemy = snapshot.enemy_team
        my_team = snapshot.my_team

        # Danger factors
        factors = []

        # Health danger
        if active.is_low_health:
            factors.append(("low_health", 0.4))

        # Numerical disadvantage
        alive_diff = my_team.alive_count - enemy.alive_count
        if alive_diff < 0:
            factors.append(("outnumbered", min(0.5, abs(alive_diff) * 0.15)))

        # Level disadvantage
        level_diff = my_team.avg_level - enemy.avg_level
        if level_diff < -1:
            factors.append(("underleveled", min(0.3, abs(level_diff) * 0.1)))

        # Gold deficit
        gold_diff = snapshot.gold_diff
        if snapshot.active_team == TeamSide.RED:
            gold_diff = -gold_diff
        if gold_diff < -3000:
            factors.append(("gold_deficit", min(0.3, abs(gold_diff) / 15000)))

        # Compute total danger
        total_danger = sum(f[1] for f in factors)
        total_danger = min(1.0, total_danger)

        return {
            "danger_level": round(total_danger, 3),
            "safe": total_danger < 0.3,
            "factors": factors,
            "recommendation": (
                "retreat" if total_danger > 0.7
                else "caution" if total_danger > 0.4
                else "normal"
            ),
        }

    def is_safe(self) -> bool:
        result = self.assess()
        return result.get("safe", True)

    def shutdown(self) -> None:
        if self._node:
            self._node.shutdown()


# ─── Registry: all ABC implementations ──────────────────────────────────────

def get_lol_implementations() -> Dict[str, Any]:
    """Return a registry of all LoL ABC implementations.

    Used by operatorRL's unified orchestrator to discover and
    instantiate LoL-specific components.
    """
    return {
        "game_bridge": LoLGameBridge,
        "strategy_advisor": LoLStrategyAdvisor,
        "objective_tracker": LoLObjectiveTracker,
        "danger_assessment": LoLDangerAssessment,
    }

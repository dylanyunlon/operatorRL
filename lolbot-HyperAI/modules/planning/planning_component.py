"""
PlanningComponent — Strategy, macro, and item recommendations (1Hz).
=====================================================================

Reads game state, win predictions, and teamfight predictions to
generate actionable strategy advice: macro calls (baron/dragon/push),
item build suggestions, and engagement recommendations.

Architecture position:
    modules/planning/planning_component.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (GameSnapshot)
    ├─ Reads: /lol/win_prediction (WinPrediction)
    ├─ Reads: /lol/teamfight_prediction (TeamfightPrediction)
    ├─ Publishes: /lol/strategy_advice (StrategyAdvice)
    └─ Publishes: /lol/voice_command (VoiceCommand)

Apollo reference:
    modules/planning/planning_component/planning_component.cc
    modules/planning/tasks/deciders/ — decision trees

Design notes:
    - 1Hz cycle (1000ms) — strategy doesn't change faster than this
    - Decision tree structure: phase → situation → recommendation
    - Urgency-based voice command generation
    - Cooldown on repeated advice to avoid spam
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    StrategyAdvice,
    TeamfightPrediction,
    TeamSide,
    VoiceCommand,
    WinPrediction,
)

logger = get_logger("planning")

# ─── Constants ───────────────────────────────────────────────────────────────

_PLANNING_INTERVAL_MS = 1000.0   # 1Hz
_WARN_THRESHOLD_MS = 800.0
_ADVICE_COOLDOWN_S = 15.0        # don't repeat same advice within 15s
_VOICE_COOLDOWN_S = 8.0          # min gap between voice commands
_OBJECTIVE_WINDOW_URGENCY = 0.8  # urgency when objective window detected


# ─── Macro Decision Engine ──────────────────────────────────────────────────

class MacroDecisionEngine:
    """Generates macro-level strategic decisions.

    Uses a decision tree based on game phase, gold advantage,
    objective availability, and team composition state.
    """

    def __init__(self) -> None:
        self._last_advice_time: Dict[str, float] = {}
        self._advice_count: int = 0

    def decide(
        self,
        snapshot: GameSnapshot,
        win_pred: Optional[WinPrediction],
        tf_pred: Optional[TeamfightPrediction],
    ) -> StrategyAdvice:
        """Generate strategic advice based on current game state.

        Decision hierarchy:
            1. Check for immediate opportunities (ace, baron, etc.)
            2. Phase-specific strategy
            3. Win probability-adjusted aggression level
        """
        game_time = snapshot.game_time
        phase = snapshot.phase
        my_team = snapshot.my_team
        enemy = snapshot.enemy_team
        gold_diff = snapshot.gold_diff
        active_team = snapshot.active_team

        # Adjust gold_diff sign for active player perspective
        effective_gold = gold_diff if active_team == TeamSide.BLUE else -gold_diff

        # Win probability from active player's perspective
        if win_pred is not None:
            my_win_prob = (
                win_pred.blue_win_prob if active_team == TeamSide.BLUE
                else win_pred.red_win_prob
            )
        else:
            my_win_prob = 0.5

        # ── Immediate opportunities ──────────────────────────────────
        # Check for numerical advantage
        alive_diff = my_team.alive_count - enemy.alive_count

        if alive_diff >= 2 and game_time > 1200:
            return self._make_advice(
                primary="Push for Baron/Dragon — numerical advantage!",
                secondary="Group mid if Baron is down",
                macro="baron" if game_time > 1200 else "dragon",
                confidence=0.85,
                urgency=0.9,
                game_time=game_time,
            )

        if enemy.alive_count <= 2 and game_time > 900:
            return self._make_advice(
                primary="Enemy nearly wiped — take objectives NOW",
                secondary="Baron > Inhibitor > Dragon priority",
                macro="baron",
                confidence=0.9,
                urgency=1.0,
                game_time=game_time,
            )

        # ── Phase-specific strategy ──────────────────────────────────
        if phase == GamePhase.EARLY:
            return self._early_game_strategy(
                snapshot, my_win_prob, effective_gold, tf_pred
            )
        elif phase == GamePhase.MID:
            return self._mid_game_strategy(
                snapshot, my_win_prob, effective_gold, tf_pred
            )
        else:
            return self._late_game_strategy(
                snapshot, my_win_prob, effective_gold, tf_pred
            )

    def _early_game_strategy(
        self,
        snapshot: GameSnapshot,
        my_win_prob: float,
        gold_diff: float,
        tf_pred: Optional[TeamfightPrediction],
    ) -> StrategyAdvice:
        """Early game (0-14 min): focus on laning and objectives."""
        if gold_diff > 1500:
            return self._make_advice(
                primary="Strong early lead — freeze lane or roam",
                secondary="Help jungler secure Void Grubs or Dragon",
                macro="dragon",
                confidence=0.7,
                urgency=0.3,
                game_time=snapshot.game_time,
            )
        elif gold_diff < -1500:
            return self._make_advice(
                primary="Behind in gold — play safe, farm under tower",
                secondary="Avoid 2v2s, wait for jungler",
                macro="farm",
                confidence=0.7,
                urgency=0.4,
                game_time=snapshot.game_time,
            )
        else:
            return self._make_advice(
                primary="Even game — focus CS and trading patterns",
                secondary="Look for roam timings after pushing wave",
                macro="farm",
                confidence=0.5,
                urgency=0.2,
                game_time=snapshot.game_time,
            )

    def _mid_game_strategy(
        self,
        snapshot: GameSnapshot,
        my_win_prob: float,
        gold_diff: float,
        tf_pred: Optional[TeamfightPrediction],
    ) -> StrategyAdvice:
        """Mid game (14-25 min): objectives and vision control."""
        should_fight = (
            tf_pred is not None
            and tf_pred.recommended_action == "engage"
        )

        if my_win_prob > 0.6:
            action = "Force objectives — you have the advantage"
            macro = "group"
            if should_fight:
                action = "Look for engage — favorable teamfight"
        elif my_win_prob < 0.4:
            action = "Play for picks, avoid 5v5"
            macro = "split"
        else:
            action = "Contest next dragon/baron, set up vision"
            macro = "dragon"

        return self._make_advice(
            primary=action,
            secondary="Ward baron pit and dragon 1 min before spawn",
            macro=macro,
            confidence=0.65,
            urgency=0.5,
            game_time=snapshot.game_time,
        )

    def _late_game_strategy(
        self,
        snapshot: GameSnapshot,
        my_win_prob: float,
        gold_diff: float,
        tf_pred: Optional[TeamfightPrediction],
    ) -> StrategyAdvice:
        """Late game (25+ min): decisive fights and win conditions."""
        should_fight = (
            tf_pred is not None
            and tf_pred.recommended_action == "engage"
        )

        if my_win_prob > 0.55:
            return self._make_advice(
                primary="Force Baron or Elder Dragon — close the game",
                secondary="One good fight wins it" if should_fight
                          else "Catch enemy rotating",
                macro="baron",
                confidence=0.8,
                urgency=0.8,
                game_time=snapshot.game_time,
            )
        elif my_win_prob < 0.45:
            return self._make_advice(
                primary="Stall and look for a pick — one fight can flip it",
                secondary="Do NOT facecheck — use abilities to check brush",
                macro="defend",
                confidence=0.75,
                urgency=0.7,
                game_time=snapshot.game_time,
            )
        else:
            return self._make_advice(
                primary="Even game — next teamfight is decisive",
                secondary="Group with team, don't get caught alone",
                macro="group",
                confidence=0.6,
                urgency=0.6,
                game_time=snapshot.game_time,
            )

    def _make_advice(
        self,
        primary: str,
        secondary: str,
        macro: str,
        confidence: float,
        urgency: float,
        game_time: float,
    ) -> StrategyAdvice:
        self._advice_count += 1
        return StrategyAdvice(
            primary_action=primary,
            secondary_action=secondary,
            reasoning=f"Phase-aware decision (count={self._advice_count})",
            confidence=confidence,
            urgency=urgency,
            game_time=game_time,
            macro_call=macro,
        )


# ─── PlanningComponent ──────────────────────────────────────────────────────

class PlanningComponent(TimerComponent):
    """Planning component: 1Hz strategy generation.

    Each Proc() cycle:
    1. Read latest game state and predictions
    2. Run macro decision engine
    3. Generate voice commands for significant advice
    4. Publish strategy advice

    Apollo equivalent: ``PlanningComponent::Proc()``
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="planning",
                interval_ms=_PLANNING_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None
        self._game_state_reader: Optional[Reader[GameSnapshot]] = None
        self._win_pred_reader: Optional[Reader[WinPrediction]] = None
        self._tf_pred_reader: Optional[Reader[TeamfightPrediction]] = None
        self._advice_writer: Optional[Writer[StrategyAdvice]] = None
        self._voice_writer: Optional[Writer[VoiceCommand]] = None
        self._status_writer: Optional[Writer[StatusMessage]] = None

        self._macro_engine: Optional[MacroDecisionEngine] = None
        self._last_voice_time: float = 0.0
        self._last_advice_text: str = ""
        self._last_advice_time: float = 0.0
        self._plan_count: int = 0

    def Init(self) -> bool:
        logger.info("Initializing PlanningComponent...")
        self._node = CyberNode("planning")

        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", GameSnapshot, pending_queue_size=4,
        )
        self._win_pred_reader = self._node.CreateReader(
            "/lol/win_prediction", WinPrediction, pending_queue_size=4,
        )
        self._tf_pred_reader = self._node.CreateReader(
            "/lol/teamfight_prediction", TeamfightPrediction,
            pending_queue_size=4,
        )
        self._advice_writer = self._node.CreateWriter(
            "/lol/strategy_advice", StrategyAdvice,
        )
        self._voice_writer = self._node.CreateWriter(
            "/lol/voice_command", VoiceCommand,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/planning_status", StatusMessage,
        )

        self._macro_engine = MacroDecisionEngine()
        logger.info("PlanningComponent initialized")
        return True

    def Proc(self) -> bool:
        """One planning cycle.

        Apollo equivalent: ``PlanningComponent::Proc()``
        """
        # Read inputs
        self._game_state_reader.Observe()
        snapshot = self._game_state_reader.GetLatestObserved()
        if snapshot is None:
            return True

        self._win_pred_reader.Observe()
        win_pred = self._win_pred_reader.GetLatestObserved()

        self._tf_pred_reader.Observe()
        tf_pred = self._tf_pred_reader.GetLatestObserved()

        self._plan_count += 1

        # ── Generate strategy advice ─────────────────────────────────
        advice = self._macro_engine.decide(snapshot, win_pred, tf_pred)

        # Publish advice
        if self._advice_writer:
            self._advice_writer.Write(advice)

        # ── Generate voice command if advice is new and urgent ────────
        now = time.time()
        if (
            advice.primary_action != self._last_advice_text
            and advice.urgency > 0.5
            and now - self._last_voice_time >= _VOICE_COOLDOWN_S
        ):
            voice_cmd = VoiceCommand(
                text=advice.primary_action,
                priority=max(1, int(10 - advice.urgency * 10)),
                max_age_s=_VOICE_COOLDOWN_S,
                game_time=snapshot.game_time,
                source_module="planning",
            )
            if self._voice_writer:
                self._voice_writer.Write(voice_cmd)
            self._last_voice_time = now
            self._last_advice_text = advice.primary_action

        # Cooldown tracking
        self._last_advice_time = now

        if self._plan_count % 30 == 0:
            logger.info(
                "Strategy: %s | Macro: %s | Urgency: %.0f%%",
                advice.primary_action[:60],
                advice.macro_call,
                advice.urgency * 100,
            )

        self._publish_status(Status.ok())
        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    def _publish_status(self, status: Status) -> None:
        if self._status_writer:
            self._status_writer.Write(StatusMessage(
                status=status,
                sequence=self._plan_count,
                source_component="planning",
            ))

    def planning_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "plan_count": self._plan_count,
            "last_advice": self._last_advice_text,
        })
        return base

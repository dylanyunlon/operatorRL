"""
TeamfightCaller — Teamfight engage/disengage decision with voice dispatch.
===========================================================================
lolbot-HyperAI · Planning Layer

Converts TeamfightPrediction into contextual, actionable VoiceCommands
with cooldown management to prevent advice spam.

Architecture position:
    modules/planning/strategy/teamfight_caller.py   ← YOU ARE HERE
    ├─ Reads: /lol/teamfight_prediction (from prediction)
    ├─ Reads: /lol/game_state (GameSnapshot for location context)
    ├─ Reads: /lol/objective_timers (for baron/dragon contest context)
    ├─ Publishes: /lol/voice_command (VoiceCommand)
    └─ Consumed by: ControlComponent (voice dispatch)

Apollo reference:
    modules/planning/planner/lattice_planner.cc — contextual path selection

Design notes:
    - Cooldowns: same recommendation type not repeated within 15s
    - Context enrichment: "engage near baron pit" vs generic "engage"
    - Confidence gate: suppress below 0.4 confidence
    - Urgency mapping: engage=0.9, disengage=1.0, hold=0.3
    - Game phase awareness: early game → suppress teamfight calls
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    TeamfightPrediction,
    TeamSide,
    VoiceCommand,
)

logger = get_logger("planning.teamfight")

_CONFIDENCE_GATE = 0.40
_COOLDOWN_PER_ACTION_S = 15.0
_COOLDOWN_SAME_TEXT_S = 30.0
_EARLY_GAME_SUPPRESS_TIME = 600.0   # suppress before 10 min
_ENGAGE_URGENCY = 0.9
_DISENGAGE_URGENCY = 1.0
_HOLD_URGENCY = 0.3


@dataclass
class _CooldownEntry:
    last_time: float = 0.0
    last_text: str = ""


class TeamfightCaller:
    """Converts predictions into voice-ready teamfight calls.

    Not a TimerComponent itself — called by PlanningComponent.Proc()
    as a sub-module.  Maintains its own cooldown state.

    Usage::
        caller = TeamfightCaller(voice_writer)
        caller.evaluate(prediction, snapshot, objective_timers)
    """

    def __init__(self, voice_writer: Optional[Writer] = None) -> None:
        self._voice_writer = voice_writer
        self._cooldowns: Dict[str, _CooldownEntry] = defaultdict(
            _CooldownEntry
        )
        self._last_dispatched_text: str = ""
        self._last_dispatched_time: float = 0.0
        self._dispatch_count: int = 0
        self._suppressed_count: int = 0

    def set_voice_writer(self, writer: Writer) -> None:
        self._voice_writer = writer

    def evaluate(
        self,
        prediction: TeamfightPrediction,
        snapshot: Optional[GameSnapshot] = None,
        objective_timers: Optional[Any] = None,
    ) -> Optional[VoiceCommand]:
        """Evaluate a teamfight prediction and optionally dispatch voice.

        Returns the VoiceCommand if one was generated, None otherwise.
        """
        now = time.time()
        game_time = prediction.game_time if prediction else 0.0
        action = prediction.recommended_action

        # ── Gate: early game suppression ─────────────────────────────
        if game_time < _EARLY_GAME_SUPPRESS_TIME:
            return None

        # ── Gate: confidence threshold ───────────────────────────────
        likelihood = prediction.likelihood
        if likelihood < _CONFIDENCE_GATE:
            return None

        # ── Gate: action cooldown ────────────────────────────────────
        cd = self._cooldowns[action]
        if now - cd.last_time < _COOLDOWN_PER_ACTION_S:
            self._suppressed_count += 1
            return None

        # ── Build contextual text ────────────────────────────────────
        text = self._build_text(action, prediction, snapshot, objective_timers)

        # ── Gate: same text cooldown ─────────────────────────────────
        if text == self._last_dispatched_text:
            if now - self._last_dispatched_time < _COOLDOWN_SAME_TEXT_S:
                self._suppressed_count += 1
                return None

        # ── Map urgency and priority ─────────────────────────────────
        urgency_map = {
            "engage": _ENGAGE_URGENCY,
            "disengage": _DISENGAGE_URGENCY,
            "hold": _HOLD_URGENCY,
        }
        urgency = urgency_map.get(action, 0.5)
        priority = 2 if urgency >= 0.8 else 4 if urgency >= 0.3 else 6

        # ── Suppress low-urgency hold in quiet moments ───────────────
        if action == "hold" and likelihood < 0.5:
            return None

        # ── Dispatch ─────────────────────────────────────────────────
        cmd = VoiceCommand(
            text=text,
            priority=priority,
            max_age_s=10.0,
            game_time=game_time,
            source_module="teamfight_caller",
        )

        if self._voice_writer:
            self._voice_writer.Write(cmd)

        cd.last_time = now
        cd.last_text = text
        self._last_dispatched_text = text
        self._last_dispatched_time = now
        self._dispatch_count += 1

        logger.info(
            "Teamfight call: %s (likelihood=%.0f%%, p%d) — %s",
            action, likelihood * 100, priority, text[:60],
        )

        return cmd

    def _build_text(
        self,
        action: str,
        prediction: TeamfightPrediction,
        snapshot: Optional[GameSnapshot],
        objective_timers: Optional[Any],
    ) -> str:
        """Build contextual voice text based on action and game state."""
        blue_win = prediction.blue_win_if_fight
        pct = max(blue_win, 1.0 - blue_win) * 100

        # Determine if we're favored
        is_our_team_blue = True
        if snapshot and snapshot.active_team == TeamSide.RED:
            is_our_team_blue = False

        we_favored = (blue_win > 0.5) == is_our_team_blue

        # Location context from objective timers
        location = self._infer_location(snapshot, objective_timers)
        location_str = f" near {location}" if location else ""

        if action == "engage":
            if we_favored:
                return (
                    f"Good time to fight{location_str}. "
                    f"We have {pct:.0f}% advantage."
                )
            else:
                return (
                    f"They want to fight{location_str}, but we're behind. "
                    f"Be careful if engaging."
                )

        elif action == "disengage":
            if not we_favored:
                return (
                    f"Avoid fighting{location_str}. "
                    f"Enemy has {pct:.0f}% fight advantage."
                )
            else:
                return (
                    f"No need to force a fight{location_str}. "
                    f"We're ahead, play safe."
                )

        elif action == "hold":
            return f"Standoff{location_str}. Wait for a pick or objective."

        return f"Teamfight assessment: {action}{location_str}."

    @staticmethod
    def _infer_location(
        snapshot: Optional[GameSnapshot],
        objective_timers: Optional[Any],
    ) -> str:
        """Infer likely fight location from game context."""
        if objective_timers is not None:
            # Check if baron or dragon is spawning soon
            baron = getattr(objective_timers, 'baron', None)
            if isinstance(baron, dict):
                status = baron.get("status", "")
                remaining = baron.get("time_until_respawn", 999)
                if status in ("SPAWNING_SOON", "ALIVE") or remaining < 90:
                    return "Baron pit"

            drake = getattr(objective_timers, 'drake', None)
            if isinstance(drake, dict):
                status = drake.get("status", "")
                remaining = drake.get("time_until_respawn", 999)
                if status in ("SPAWNING_SOON", "ALIVE") or remaining < 90:
                    return "Dragon pit"

        if snapshot:
            if snapshot.game_time > 1200:
                return "Baron side"
            elif snapshot.game_time > 300:
                return "river"

        return ""

    def stats(self) -> Dict[str, Any]:
        return {
            "dispatch_count": self._dispatch_count,
            "suppressed_count": self._suppressed_count,
            "last_action": self._last_dispatched_text[:50] if self._last_dispatched_text else "",
        }

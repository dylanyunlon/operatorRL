"""
modules/prediction/objective/objective_window_advisor.py
=========================================================
Claude18 · Strategic objective window advisor

Wraps the existing ObjectiveTimer (Claude11) to produce planning-ready
spawn window recommendations. ObjectiveTimer tracks state; this module
translates that state into actionable advice for PlanningComponent.

Architecture position:
    modules/prediction/objective/objective_window_advisor.py  ← YOU ARE HERE
    ├─ Reads: ObjectiveTimer state
    ├─ Input: current GameSnapshot
    ├─ Output: List[ObjectiveWindowAdvice] for planning
    └─ Used by: prediction_component.py, planning_component.py

File location: lolbot-HyperAI/modules/prediction/objective/objective_window_advisor.py
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class WindowUrgency(Enum):
    """How urgently the team should respond to an objective window."""
    PLAN_AHEAD = auto()  # >60s: start preparing waves/vision
    PREPARE = auto()     # 30-60s: group, clear vision
    CONTEST = auto()     # <30s: be at the pit
    ACTIVE = auto()      # Objective is alive and contestable


@dataclass
class ObjectiveWindowAdvice:
    """Single piece of objective-aware strategic advice."""
    objective_name: str
    urgency: WindowUrgency
    seconds_until_spawn: float
    game_time_at_spawn: float
    strategic_priority: float  # 0-1
    advice_text: str
    voice_text: str  # Shortened for TTS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective": self.objective_name,
            "urgency": self.urgency.name,
            "seconds_until": round(self.seconds_until_spawn, 1),
            "priority": round(self.strategic_priority, 3),
            "advice": self.advice_text,
        }


# Objective priority weights (higher = more important to contest)
_PRIORITY_WEIGHTS = {
    "baron": 0.95,
    "elder_dragon": 0.92,
    "dragon": 0.75,
    "herald": 0.55,
    "void_grubs": 0.40,
}


class ObjectiveWindowAdvisor:
    """Translates objective timer state into strategic recommendations.

    Designed to be called each planning tick (2Hz). Maintains internal
    cooldowns to avoid spamming the same advice repeatedly.

    Usage::
        advisor = ObjectiveWindowAdvisor()
        # In PlanningComponent.Proc():
        windows = advisor.evaluate(game_time, objective_timer, game_snapshot)
        for w in windows:
            if w.strategic_priority > 0.6:
                publish_to_strategy(w)
    """

    _ADVICE_COOLDOWN_S = 15.0  # Don't repeat same advice for 15s

    def __init__(self) -> None:
        self._last_advice_time: Dict[str, float] = {}
        self._advice_count: int = 0

    def evaluate(
        self,
        game_time: float,
        objective_states: Dict[str, Any],
        team_alive_count: int = 5,
        enemy_alive_count: int = 5,
        win_probability: float = 0.5,
    ) -> List[ObjectiveWindowAdvice]:
        """Evaluate all objectives and produce ordered advice list.

        Args:
            game_time: Current game time in seconds.
            objective_states: Dict from ObjectiveTimer.stats()["objectives"]
            team_alive_count: Our team members alive.
            enemy_alive_count: Enemy team members alive.
            win_probability: Current win probability for our team.

        Returns:
            List of ObjectiveWindowAdvice sorted by priority.
        """
        advices: List[ObjectiveWindowAdvice] = []

        for obj_name, obj_info in objective_states.items():
            if not isinstance(obj_info, dict):
                continue

            state = obj_info.get("state", "NOT_SPAWNED")
            respawn_time = obj_info.get("respawn_game_time", 0.0)

            if state == "DEAD" and respawn_time > game_time:
                # Objective is dead, calculate time until respawn
                seconds_until = respawn_time - game_time
                if seconds_until > 120:
                    continue  # Too far out

                advice = self._make_advice(
                    obj_name, seconds_until, respawn_time, game_time,
                    team_alive_count, enemy_alive_count, win_probability,
                )
                if advice and self._check_cooldown(obj_name, game_time):
                    advices.append(advice)
                    self._advice_count += 1

            elif state == "ALIVE":
                # Objective is on the map — should we contest?
                advice = self._make_contest_advice(
                    obj_name, game_time,
                    team_alive_count, enemy_alive_count, win_probability,
                )
                if advice and self._check_cooldown(
                    f"{obj_name}_contest", game_time
                ):
                    advices.append(advice)
                    self._advice_count += 1

        advices.sort(key=lambda a: a.strategic_priority, reverse=True)
        return advices

    def _make_advice(
        self,
        obj_name: str,
        seconds_until: float,
        respawn_time: float,
        game_time: float,
        team_alive: int,
        enemy_alive: int,
        win_prob: float,
    ) -> Optional[ObjectiveWindowAdvice]:
        """Create spawn-window advice for a dead objective."""
        base_priority = _PRIORITY_WEIGHTS.get(obj_name, 0.5)

        # Adjust priority based on game state
        if team_alive < enemy_alive:
            base_priority *= 0.6  # Hard to contest when outnumbered
        if win_prob < 0.35:
            # When behind, baron/elder become do-or-die plays
            if obj_name in ("baron", "elder_dragon"):
                base_priority *= 1.1
            else:
                base_priority *= 0.7

        base_priority = min(1.0, base_priority)

        # Determine urgency
        if seconds_until <= 15:
            urgency = WindowUrgency.CONTEST
        elif seconds_until <= 45:
            urgency = WindowUrgency.PREPARE
        else:
            urgency = WindowUrgency.PLAN_AHEAD

        # Generate advice text
        pretty_name = obj_name.replace("_", " ").title()
        mins = int(seconds_until // 60)
        secs = int(seconds_until % 60)
        time_str = f"{mins}m{secs:02d}s" if mins > 0 else f"{secs}s"

        if urgency == WindowUrgency.CONTEST:
            advice = (
                f"{pretty_name} spawning in {time_str}! "
                f"Group at objective immediately."
            )
            voice = f"{pretty_name} spawning in {secs} seconds. Group now!"
        elif urgency == WindowUrgency.PREPARE:
            advice = (
                f"{pretty_name} spawns in {time_str}. "
                f"Clear vision and prepare to group."
            )
            voice = f"{pretty_name} in {time_str}. Set up vision."
        else:
            advice = (
                f"{pretty_name} spawns in {time_str}. "
                f"Push waves and establish vision control."
            )
            voice = f"{pretty_name} coming up in {time_str}."

        return ObjectiveWindowAdvice(
            objective_name=obj_name,
            urgency=urgency,
            seconds_until_spawn=seconds_until,
            game_time_at_spawn=respawn_time,
            strategic_priority=base_priority,
            advice_text=advice,
            voice_text=voice,
        )

    def _make_contest_advice(
        self,
        obj_name: str,
        game_time: float,
        team_alive: int,
        enemy_alive: int,
        win_prob: float,
    ) -> Optional[ObjectiveWindowAdvice]:
        """Create advice for a currently alive objective."""
        # Only generate contest advice for high-value objectives
        if obj_name not in ("baron", "elder_dragon", "dragon"):
            return None

        base_priority = _PRIORITY_WEIGHTS.get(obj_name, 0.5)

        # Outnumber advantage → strong contest signal
        alive_diff = team_alive - enemy_alive
        if alive_diff >= 2:
            base_priority = min(1.0, base_priority * 1.2)
            pretty_name = obj_name.replace("_", " ").title()
            advice = (
                f"{pretty_name} is alive and we have a {alive_diff} "
                f"player advantage. Consider starting it."
            )
            voice = (
                f"We have numbers advantage. "
                f"Consider starting {pretty_name}."
            )
            return ObjectiveWindowAdvice(
                objective_name=obj_name,
                urgency=WindowUrgency.ACTIVE,
                seconds_until_spawn=0.0,
                game_time_at_spawn=game_time,
                strategic_priority=base_priority,
                advice_text=advice,
                voice_text=voice,
            )

        return None

    def _check_cooldown(self, key: str, game_time: float) -> bool:
        """Check if enough time has passed since last advice for this key."""
        last = self._last_advice_time.get(key, 0.0)
        if game_time - last < self._ADVICE_COOLDOWN_S:
            return False
        self._last_advice_time[key] = game_time
        return True

    def stats(self) -> Dict[str, Any]:
        return {
            "advice_count": self._advice_count,
            "active_cooldowns": len(self._last_advice_time),
        }

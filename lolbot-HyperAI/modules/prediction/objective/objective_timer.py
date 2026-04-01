"""
ObjectiveTimer — 目标重生计时器 (龙/男爵/峡谷先锋)
====================================================
查看 integrations/lol/src/lol_agent/objective_timer.py 上现有
龙/男爵计时器的实现方式, 理解其模式, 特别是 spawn_time 常量表
和 event-driven 计时启动 是如何与 UI 显示分离的。
可以从 modules/objective_tracker_abc.py 的统一接口定义 这个好例子开始。
然后, 遵循该模式实现一个新的 ObjectiveTimer (实现 ObjectiveTrackerABC),
让 PredictionComponent 可以 监听 /lol/events 频道的目标击杀事件
并自动启动重生倒计时, 并能 在距离重生 60 秒时触发 VoiceCommand 提醒。
从头开始构建, 除了代码库中已有的库之外, 不要使用其他库。

Architecture position:
    modules/prediction/objective/objective_timer.py   ← YOU ARE HERE
    ├─ Implements: modules/objective_tracker_abc.py (ObjectiveTrackerABC)
    ├─ Called by: prediction_component.py
    ├─ Input: GameEvent (DRAGON_KILL, BARON_KILL, HERALD_KILL)
    ├─ Output: Timer state, upcoming alerts
    └─ Publishes: VoiceCommand when objective respawning soon

Apollo reference:
    modules/prediction/container/ — obstacle container with time tracking
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    EventType,
    GameEvent,
    VoiceCommand,
)

logger = get_logger("prediction.objectives")

# ─── Objective Spawn Times (seconds) ────────────────────────────────────────

_RESPAWN_TIMES: Dict[str, float] = {
    "dragon": 300.0,         # 5 minutes
    "elder_dragon": 360.0,   # 6 minutes
    "baron": 360.0,          # 6 minutes
    "rift_herald": 480.0,    # 8 minutes (doesn't respawn after 2nd)
    "void_grub": 240.0,      # 4 minutes
}

# Initial spawn times (from game start)
_INITIAL_SPAWN: Dict[str, float] = {
    "dragon": 300.0,         # 5:00
    "rift_herald": 480.0,    # 8:00 (replaced by baron at 20:00)
    "baron": 1200.0,         # 20:00
    "void_grub": 360.0,      # 6:00
}

# Alert thresholds — announce at these seconds before spawn
_ALERT_THRESHOLDS = [60.0, 30.0, 10.0]

# Map event types to objective keys
_EVENT_TO_OBJECTIVE: Dict[EventType, str] = {
    EventType.DRAGON_KILL: "dragon",
    EventType.BARON_KILL: "baron",
    EventType.HERALD_KILL: "rift_herald",
    EventType.VOID_GRUB_KILL: "void_grub",
}


@dataclass
class ObjectiveTimerEntry:
    """State of a single objective timer."""
    objective: str
    taken_at: float         # game time when killed
    respawn_time: float     # total respawn duration
    taken_by: str = ""      # killer team/player
    alerts_fired: List[float] = field(default_factory=list)

    @property
    def respawn_at(self) -> float:
        """Game time when the objective respawns."""
        return self.taken_at + self.respawn_time

    def time_remaining(self, current_time: float) -> float:
        """Seconds until respawn."""
        return max(0.0, self.respawn_at - current_time)

    def is_expired(self, current_time: float) -> bool:
        """True if the objective has respawned."""
        return current_time >= self.respawn_at

    def should_alert(self, current_time: float) -> Optional[float]:
        """Check if an alert should fire.

        Returns:
            Seconds-before-spawn threshold if alert needed, else None.
        """
        remaining = self.time_remaining(current_time)
        for threshold in _ALERT_THRESHOLDS:
            if (
                remaining <= threshold
                and remaining > 0
                and threshold not in self.alerts_fired
            ):
                self.alerts_fired.append(threshold)
                return threshold
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective": self.objective,
            "taken_at": self.taken_at,
            "respawn_at": self.respawn_at,
            "taken_by": self.taken_by,
            "alerts_fired": list(self.alerts_fired),
        }


class ObjectiveTimer:
    """Objective respawn timer manager.

    Implements the ObjectiveTrackerABC interface pattern.  Listens for
    objective kill events and tracks respawn timers.  Generates voice
    command alerts when objectives are about to respawn.

    Usage::

        timer = ObjectiveTimer()
        # Each perception tick:
        alerts = timer.process_events(new_events, game_time)
        for cmd in alerts:
            voice_writer.Write(cmd)
    """

    def __init__(self) -> None:
        self._timers: Dict[str, ObjectiveTimerEntry] = {}
        self._history: List[ObjectiveTimerEntry] = []
        self._dragon_count: int = 0
        self._herald_count: int = 0

    # ─── ObjectiveTrackerABC interface ───────────────────────────────

    def start_timer(self, objective: str, game_time: float) -> None:
        """Start a respawn timer for an objective.

        Args:
            objective: Objective identifier (e.g., "dragon", "baron").
            game_time: Game time when objective was taken.
        """
        respawn = _RESPAWN_TIMES.get(objective, 300.0)

        # Elder dragon has different respawn
        if objective == "dragon" and self._dragon_count >= 4:
            respawn = _RESPAWN_TIMES["elder_dragon"]

        entry = ObjectiveTimerEntry(
            objective=objective,
            taken_at=game_time,
            respawn_time=respawn,
        )
        self._timers[objective] = entry
        self._history.append(entry)

        logger.info(
            "Timer started: %s respawns at %.0fs (in %.0fs)",
            objective, entry.respawn_at, respawn,
        )

    def time_remaining(self, objective: str, current_time: float) -> float:
        """Get remaining respawn time for an objective."""
        entry = self._timers.get(objective)
        if entry is None:
            return 0.0
        return entry.time_remaining(current_time)

    def clear(self, objective: str) -> None:
        """Clear an objective timer."""
        self._timers.pop(objective, None)

    def active_timers(self) -> list[str]:
        """List currently active objective timers."""
        return list(self._timers.keys())

    # ─── Event processing ────────────────────────────────────────────

    def process_events(
        self,
        events: List[GameEvent],
        current_time: float,
    ) -> List[VoiceCommand]:
        """Process game events and generate alerts.

        Args:
            events: New game events from perception.
            current_time: Current game time.

        Returns:
            List of VoiceCommand alerts to narrate.
        """
        voice_commands: List[VoiceCommand] = []

        # Process new objective kills
        for event in events:
            objective = _EVENT_TO_OBJECTIVE.get(event.event_type)
            if objective is None:
                continue

            # Track dragon count
            if objective == "dragon":
                self._dragon_count += 1

            if objective == "rift_herald":
                self._herald_count += 1
                # Herald doesn't respawn after 2nd or after 20:00
                if self._herald_count >= 2 or current_time >= 1200:
                    continue

            self.start_timer(objective, event.game_time)

        # Check for alerts on existing timers
        expired_keys: List[str] = []
        for key, entry in self._timers.items():
            if entry.is_expired(current_time):
                expired_keys.append(key)
                continue

            threshold = entry.should_alert(current_time)
            if threshold is not None:
                remaining = entry.time_remaining(current_time)
                text = self._format_alert(entry.objective, remaining, threshold)
                voice_commands.append(VoiceCommand(
                    text=text,
                    priority=2 if threshold <= 30 else 4,
                    max_age_s=10.0,
                    game_time=current_time,
                    source_module="objective_timer",
                ))

        # Clean up expired timers
        for key in expired_keys:
            logger.info("Timer expired: %s has respawned", key)
            del self._timers[key]

        return voice_commands

    def _format_alert(
        self, objective: str, remaining: float, threshold: float,
    ) -> str:
        """Format a voice alert message."""
        obj_name = {
            "dragon": "Dragon",
            "baron": "Baron Nashor",
            "rift_herald": "Rift Herald",
            "void_grub": "Void Grubs",
        }.get(objective, objective.title())

        if threshold <= 10:
            return f"{obj_name} spawning in {int(remaining)} seconds!"
        elif threshold <= 30:
            return f"{obj_name} spawning in {int(remaining)} seconds, get ready"
        else:
            return f"{obj_name} respawns in {int(remaining)} seconds, set up vision"

    # ─── Queries ─────────────────────────────────────────────────────

    def next_spawn(self, current_time: float) -> Optional[Tuple[str, float]]:
        """Get the next objective to spawn.

        Returns:
            Tuple of (objective_name, seconds_until_spawn) or None.
        """
        best: Optional[Tuple[str, float]] = None
        for key, entry in self._timers.items():
            remaining = entry.time_remaining(current_time)
            if remaining > 0:
                if best is None or remaining < best[1]:
                    best = (key, remaining)
        return best

    def is_contested(self, current_time: float, window_s: float = 90.0) -> bool:
        """Check if any objective is about to spawn (contest window)."""
        for entry in self._timers.values():
            if 0 < entry.time_remaining(current_time) <= window_s:
                return True
        return False

    # ─── Introspection ───────────────────────────────────────────────

    @property
    def dragon_count(self) -> int:
        return self._dragon_count

    def summary(self) -> Dict[str, Any]:
        return {
            "active_timers": {
                k: v.to_dict() for k, v in self._timers.items()
            },
            "dragon_count": self._dragon_count,
            "herald_count": self._herald_count,
            "history_size": len(self._history),
        }

"""
ObjectiveTracker — Dragon/Baron/Herald respawn countdown and contest alerts.
==============================================================================
lolbot-HyperAI · Prediction Layer

Subscribes to ``/lol/objective_events`` (published by EventStreamProcessor)
and maintains countdowns for objective respawns.  Publishes timed alerts
to ``/lol/objective_timers`` for planning and voice consumption.

Architecture position:
    modules/prediction/objective/objective_tracker.py   ← YOU ARE HERE
    ├─ Reads: /lol/objective_events (ObjectiveEvent from event_stream_processor)
    ├─ Reads: /lol/game_state (GameSnapshot for current game time)
    ├─ Publishes: /lol/objective_timers (ObjectiveTimerState)
    ├─ Publishes: /lol/voice_command (VoiceCommand for 60s/30s alerts)
    └─ Consumed by: PlanningComponent, VoiceNarrator, DreamView dashboard

Apollo reference:
    modules/prediction/evaluator/evaluator_manager.cc
    — time-based event prediction from tracked entity states

Design notes:
    - Respawn times: Drake 5:00, Baron 6:00, Herald 6:00, Elder 6:00
    - Herald despawns at 19:45, Baron spawns at 20:00
    - Elder Drake spawns after soul (4 drakes by one team)
    - Voice alerts at 60s, 30s, and 10s before respawn
    - Thread-safe: all state reads/writes through Proc() only
    - De-duplicate alerts: each countdown fires each alert tier once
    - Publishes consolidated ObjectiveTimerState every Proc() cycle
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    GameSnapshot,
    TeamSide,
    VoiceCommand,
)

logger = get_logger("prediction.objectives")

# ─── Constants ───────────────────────────────────────────────────────────────

_TRACKER_INTERVAL_MS = 1000.0     # 1Hz countdown check
_WARN_THRESHOLD_MS = 500.0

# Respawn timers (seconds)
_DRAKE_RESPAWN_S = 300.0          # 5 minutes
_BARON_RESPAWN_S = 360.0          # 6 minutes
_HERALD_RESPAWN_S = 360.0         # 6 minutes
_ELDER_RESPAWN_S = 360.0          # 6 minutes

# Herald availability window
_HERALD_SPAWN_TIME = 480.0        # 8:00 game time
_HERALD_DESPAWN_TIME = 1185.0     # 19:45 game time
_BARON_SPAWN_TIME = 1200.0        # 20:00 game time

# Drake soul
_SOUL_DRAKE_COUNT = 4             # 4 drakes by one team triggers soul

# Alert thresholds (seconds before respawn)
_ALERT_TIERS = (60.0, 30.0, 10.0)

# First spawn times
_FIRST_DRAKE_SPAWN = 300.0        # 5:00
_FIRST_HERALD_SPAWN = 480.0       # 8:00


# ─── Data types ──────────────────────────────────────────────────────────────

class ObjectiveStatus(Enum):
    """State of a tracked objective."""
    ALIVE = auto()          # Objective is alive on the map
    DEAD = auto()           # Recently killed, respawn countdown active
    SPAWNING_SOON = auto()  # Within 60s of respawn
    NOT_AVAILABLE = auto()  # Not yet spawned or permanently gone


class ObjectiveId(Enum):
    """Tracked objective identifiers."""
    DRAKE = "drake"
    BARON = "baron"
    HERALD = "herald"
    ELDER = "elder"


@dataclass
class TrackedObjective:
    """State of a single tracked objective."""
    obj_id: ObjectiveId
    status: ObjectiveStatus = ObjectiveStatus.NOT_AVAILABLE
    kill_time: float = 0.0          # Game time when killed
    respawn_time: float = 0.0       # Game time when it respawns
    time_until_respawn: float = 0.0 # Seconds until respawn (updated each tick)
    taken_by: TeamSide = TeamSide.UNKNOWN
    kill_count_blue: int = 0        # How many times blue has taken this
    kill_count_red: int = 0         # How many times red has taken this
    alerts_fired: Set[float] = field(default_factory=set)  # Alert tiers fired

    def record_kill(
        self, game_time: float, team: TeamSide, respawn_duration: float
    ) -> None:
        """Record that this objective was killed."""
        self.status = ObjectiveStatus.DEAD
        self.kill_time = game_time
        self.respawn_time = game_time + respawn_duration
        self.taken_by = team
        self.alerts_fired.clear()

        if team == TeamSide.BLUE:
            self.kill_count_blue += 1
        elif team == TeamSide.RED:
            self.kill_count_red += 1

    def tick(self, game_time: float) -> None:
        """Update countdown each tick."""
        if self.status == ObjectiveStatus.DEAD:
            self.time_until_respawn = max(0.0, self.respawn_time - game_time)
            if self.time_until_respawn <= 60.0:
                self.status = ObjectiveStatus.SPAWNING_SOON
            if self.time_until_respawn <= 0.0:
                self.status = ObjectiveStatus.ALIVE
                self.time_until_respawn = 0.0
        elif self.status == ObjectiveStatus.SPAWNING_SOON:
            self.time_until_respawn = max(0.0, self.respawn_time - game_time)
            if self.time_until_respawn <= 0.0:
                self.status = ObjectiveStatus.ALIVE
                self.time_until_respawn = 0.0

    def pending_alerts(self) -> List[float]:
        """Return alert tier thresholds that haven't been fired yet."""
        if self.status not in (ObjectiveStatus.DEAD, ObjectiveStatus.SPAWNING_SOON):
            return []
        pending = []
        for tier in _ALERT_TIERS:
            if tier not in self.alerts_fired and self.time_until_respawn <= tier:
                pending.append(tier)
        return pending

    def fire_alert(self, tier: float) -> None:
        """Mark an alert tier as fired."""
        self.alerts_fired.add(tier)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.obj_id.value,
            "status": self.status.name,
            "time_until_respawn": round(self.time_until_respawn, 1),
            "respawn_time": round(self.respawn_time, 1),
            "taken_by": self.taken_by.name,
            "blue_count": self.kill_count_blue,
            "red_count": self.kill_count_red,
        }


@dataclass(frozen=True)
class ObjectiveTimerState:
    """Consolidated objective timer snapshot.

    Published on ``/lol/objective_timers`` every tick.
    """
    drake: Dict[str, Any] = field(default_factory=dict)
    baron: Dict[str, Any] = field(default_factory=dict)
    herald: Dict[str, Any] = field(default_factory=dict)
    elder: Dict[str, Any] = field(default_factory=dict)
    game_time: float = 0.0
    blue_drake_count: int = 0
    red_drake_count: int = 0
    soul_team: str = "none"     # "blue", "red", or "none"
    timestamp: float = field(default_factory=time.time)


# ─── ObjectiveTracker ────────────────────────────────────────────────────────

class ObjectiveTracker(TimerComponent):
    """Tracks objective respawn countdowns and fires voice alerts.

    Each Proc() cycle:
        1. Read objective events from /lol/objective_events
        2. Update internal timers based on game time
        3. Check for alert thresholds (60s, 30s, 10s)
        4. Publish consolidated timer state
        5. Publish voice alerts for approaching spawns
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="objective_tracker",
                interval_ms=_TRACKER_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._node: Optional[CyberNode] = None

        # Readers
        self._objective_reader: Optional[Reader] = None
        self._game_state_reader: Optional[Reader] = None

        # Writers
        self._timer_writer: Optional[Writer] = None
        self._voice_writer: Optional[Writer] = None
        self._status_writer: Optional[Writer] = None

        # Tracked objectives
        self._drake = TrackedObjective(obj_id=ObjectiveId.DRAKE)
        self._baron = TrackedObjective(obj_id=ObjectiveId.BARON)
        self._herald = TrackedObjective(obj_id=ObjectiveId.HERALD)
        self._elder = TrackedObjective(obj_id=ObjectiveId.ELDER)

        # Soul tracking
        self._soul_team: Optional[TeamSide] = None

        # State
        self._game_time: float = 0.0
        self._proc_count: int = 0
        self._alerts_sent: int = 0
        self._processed_event_ids: Set[int] = set()

    def Init(self) -> bool:
        logger.info("Initializing ObjectiveTracker...")

        self._node = CyberNode("objective_tracker")

        self._objective_reader = self._node.CreateReader(
            "/lol/objective_events", object, pending_queue_size=16,
        )
        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", object, pending_queue_size=4,
        )
        self._timer_writer = self._node.CreateWriter(
            "/lol/objective_timers", ObjectiveTimerState,
        )
        self._voice_writer = self._node.CreateWriter(
            "/lol/voice_command", VoiceCommand,
        )
        self._status_writer = self._node.CreateWriter(
            "/lol/objective_tracker_status", StatusMessage,
        )

        logger.info("ObjectiveTracker initialized")
        return True

    def Proc(self) -> bool:
        """One tracking cycle: update timers, check alerts, publish."""
        self._proc_count += 1

        # ── Get current game time ────────────────────────────────────
        self._game_state_reader.Observe()
        snapshot = self._game_state_reader.GetLatestObserved()
        if snapshot is not None and hasattr(snapshot, 'game_time'):
            self._game_time = snapshot.game_time

        if self._game_time <= 0:
            return True  # Game hasn't started

        # ── Process new objective events ─────────────────────────────
        self._objective_reader.Observe()
        obj_event = self._objective_reader.GetLatestObserved()
        if obj_event is not None:
            self._handle_objective_event(obj_event)

        # ── Update availability based on game time ───────────────────
        self._update_availability()

        # ── Tick all countdowns ──────────────────────────────────────
        self._drake.tick(self._game_time)
        self._baron.tick(self._game_time)
        self._herald.tick(self._game_time)
        self._elder.tick(self._game_time)

        # ── Check and fire alerts ────────────────────────────────────
        self._check_alerts(self._drake, "Drake")
        self._check_alerts(self._baron, "Baron")
        self._check_alerts(self._herald, "Herald")
        self._check_alerts(self._elder, "Elder Drake")

        # ── Publish consolidated state ───────────────────────────────
        self._publish_timer_state()

        # ── Periodic log ─────────────────────────────────────────────
        if self._proc_count % 30 == 0:  # every 30s
            self._log_status()

        return True

    def on_shutdown(self) -> None:
        if self._node:
            self._node.shutdown()

    # ─── Event handling ──────────────────────────────────────────────

    def _handle_objective_event(self, event: Any) -> None:
        """Route an objective event to the appropriate tracker."""
        if not hasattr(event, 'event_id'):
            return
        if event.event_id in self._processed_event_ids:
            return
        self._processed_event_ids.add(event.event_id)

        obj_type = getattr(event, 'objective_type', '')
        taken_by = getattr(event, 'taken_by', TeamSide.UNKNOWN)
        game_time = getattr(event, 'game_time', self._game_time)

        if "Dragon" in obj_type or "Drake" in obj_type:
            if "Elder" in obj_type:
                self._elder.record_kill(game_time, taken_by, _ELDER_RESPAWN_S)
                logger.info(
                    "Elder Drake taken by %s at %.0fs",
                    taken_by.name, game_time,
                )
            else:
                self._drake.record_kill(game_time, taken_by, _DRAKE_RESPAWN_S)
                self._check_soul()
                logger.info(
                    "Drake #%d taken by %s at %.0fs",
                    self._drake.kill_count_blue + self._drake.kill_count_red,
                    taken_by.name, game_time,
                )

        elif "Baron" in obj_type:
            self._baron.record_kill(game_time, taken_by, _BARON_RESPAWN_S)
            logger.info(
                "Baron taken by %s at %.0fs", taken_by.name, game_time,
            )
            # Baron is a high-impact event: immediate voice alert
            self._send_voice_alert(
                f"Baron Nashor secured by {taken_by.name.lower()} team!",
                priority=1,
                game_time=game_time,
            )

        elif "Herald" in obj_type:
            self._herald.record_kill(game_time, taken_by, _HERALD_RESPAWN_S)
            logger.info(
                "Herald taken by %s at %.0fs", taken_by.name, game_time,
            )

        elif "Turret" in obj_type or "Tower" in obj_type:
            # Towers don't respawn, but we track for logging
            logger.info(
                "Tower destroyed by %s at %.0fs", taken_by.name, game_time,
            )

    def _check_soul(self) -> None:
        """Check if a team has achieved dragon soul (4 drakes)."""
        if self._drake.kill_count_blue >= _SOUL_DRAKE_COUNT:
            self._soul_team = TeamSide.BLUE
            logger.info("Dragon Soul achieved by BLUE team!")
            self._send_voice_alert(
                "Dragon Soul achieved by blue team!",
                priority=1,
                game_time=self._game_time,
            )
        elif self._drake.kill_count_red >= _SOUL_DRAKE_COUNT:
            self._soul_team = TeamSide.RED
            logger.info("Dragon Soul achieved by RED team!")
            self._send_voice_alert(
                "Dragon Soul achieved by red team!",
                priority=1,
                game_time=self._game_time,
            )

    # ─── Availability ────────────────────────────────────────────────

    def _update_availability(self) -> None:
        """Update objective availability based on game time rules."""
        # First drake spawn
        if (self._drake.status == ObjectiveStatus.NOT_AVAILABLE
                and self._game_time >= _FIRST_DRAKE_SPAWN):
            self._drake.status = ObjectiveStatus.ALIVE
            logger.info("First Drake now available at %.0fs", self._game_time)

        # Herald availability window
        if (self._herald.status == ObjectiveStatus.NOT_AVAILABLE
                and self._game_time >= _FIRST_HERALD_SPAWN):
            self._herald.status = ObjectiveStatus.ALIVE
            logger.info("Herald now available at %.0fs", self._game_time)

        # Herald despawns at 19:45
        if (self._herald.status in (ObjectiveStatus.ALIVE, ObjectiveStatus.DEAD)
                and self._game_time >= _HERALD_DESPAWN_TIME):
            self._herald.status = ObjectiveStatus.NOT_AVAILABLE
            logger.info("Herald despawned at %.0fs", self._game_time)

        # Baron spawns at 20:00
        if (self._baron.status == ObjectiveStatus.NOT_AVAILABLE
                and self._game_time >= _BARON_SPAWN_TIME):
            self._baron.status = ObjectiveStatus.ALIVE
            logger.info("Baron Nashor now available at %.0fs", self._game_time)
            self._send_voice_alert(
                "Baron Nashor has spawned!",
                priority=2,
                game_time=self._game_time,
            )

        # Elder drake: only after soul
        if self._soul_team is not None:
            if (self._elder.status == ObjectiveStatus.NOT_AVAILABLE
                    and self._drake.status == ObjectiveStatus.ALIVE):
                # Elder replaces regular drake after soul
                self._elder.status = ObjectiveStatus.ALIVE
                self._drake.status = ObjectiveStatus.NOT_AVAILABLE

    # ─── Alert system ────────────────────────────────────────────────

    def _check_alerts(self, obj: TrackedObjective, name: str) -> None:
        """Check and fire voice alerts for approaching respawns."""
        pending = obj.pending_alerts()
        for tier in pending:
            seconds = int(obj.time_until_respawn)
            if tier == 60.0:
                text = f"{name} respawns in about one minute."
                priority = 3
            elif tier == 30.0:
                text = f"{name} respawns in 30 seconds. Get into position."
                priority = 2
            elif tier == 10.0:
                text = f"{name} spawning NOW!"
                priority = 1
            else:
                continue

            self._send_voice_alert(text, priority, self._game_time)
            obj.fire_alert(tier)
            self._alerts_sent += 1
            logger.info("Alert: %s (tier=%.0fs, remaining=%.0fs)",
                        text, tier, obj.time_until_respawn)

    def _send_voice_alert(
        self, text: str, priority: int, game_time: float
    ) -> None:
        """Send a voice command to the narration queue."""
        if self._voice_writer:
            self._voice_writer.Write(VoiceCommand(
                text=text,
                priority=priority,
                max_age_s=15.0,
                game_time=game_time,
                source_module="objective_tracker",
            ))

    # ─── Publishing ──────────────────────────────────────────────────

    def _publish_timer_state(self) -> None:
        """Publish consolidated objective timer snapshot."""
        if not self._timer_writer:
            return

        soul_str = "none"
        if self._soul_team == TeamSide.BLUE:
            soul_str = "blue"
        elif self._soul_team == TeamSide.RED:
            soul_str = "red"

        state = ObjectiveTimerState(
            drake=self._drake.to_dict(),
            baron=self._baron.to_dict(),
            herald=self._herald.to_dict(),
            elder=self._elder.to_dict(),
            game_time=self._game_time,
            blue_drake_count=self._drake.kill_count_blue,
            red_drake_count=self._drake.kill_count_red,
            soul_team=soul_str,
        )
        self._timer_writer.Write(state)

    def _log_status(self) -> None:
        """Periodic status log."""
        objectives = [
            ("Drake", self._drake),
            ("Baron", self._baron),
            ("Herald", self._herald),
            ("Elder", self._elder),
        ]
        active = [
            f"{name}({o.status.name}, {o.time_until_respawn:.0f}s)"
            for name, o in objectives
            if o.status not in (ObjectiveStatus.NOT_AVAILABLE,)
        ]
        logger.info(
            "Objectives at %.0fs: %s | Drakes: B%d R%d | Alerts sent: %d",
            self._game_time,
            ", ".join(active) if active else "none active",
            self._drake.kill_count_blue,
            self._drake.kill_count_red,
            self._alerts_sent,
        )

    # ─── Introspection ───────────────────────────────────────────────

    def tracker_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "game_time": self._game_time,
            "proc_count": self._proc_count,
            "alerts_sent": self._alerts_sent,
            "drake": self._drake.to_dict(),
            "baron": self._baron.to_dict(),
            "herald": self._herald.to_dict(),
            "elder": self._elder.to_dict(),
            "soul_team": self._soul_team.name if self._soul_team else "none",
        })
        return base

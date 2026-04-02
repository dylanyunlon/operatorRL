"""
ControlComponent — Unified control layer orchestrator (1Hz).
==============================================================
lolbot-HyperAI · Control Layer

The single TimerComponent entry point for the control layer.  Aggregates
voice_narrator, action_dispatcher, and overlay_renderer sub-modules into
a coordinated output pipeline.

Architecture position:
    modules/control/dispatch/control_component.py   ← YOU ARE HERE
    ├─ Reads: /lol/strategy_advice (StrategyAdvice from planning)
    ├─ Reads: /lol/voice_command (VoiceCommand from multiple sources)
    ├─ Reads: /lol/win_prediction (WinPrediction for periodic updates)
    ├─ Reads: /lol/objective_timers (ObjectiveTimerState)
    ├─ Reads: /lol/teamfight_active (TeamfightCluster)
    ├─ Reads: /lol/game_state (GameSnapshot for context)
    ├─ Delegates to: VoiceNarratorComponent (TTS output)
    ├─ Delegates to: ActionDispatcher (routing decisions)
    ├─ Delegates to: OverlayRenderer (HUD elements)
    └─ Publishes: /lol/control_status (StatusMessage)

Apollo reference:
    modules/control/control_component.cc
    — reads trajectory from planning, outputs to canbus/chassis

Design notes:
    - Runs at 1Hz (human perception rate; faster would waste TTS queue)
    - Priority merging: voice commands from multiple sources are merged
      into a single priority queue before dispatch
    - Rate limiting: per-source cooldowns prevent any module from
      flooding the voice output
    - Win probability periodic announcement: every 60s if significant
      change, or every 120s unconditionally
    - Overlay state maintained as a set of active elements with TTL
    - All output channels are fire-and-forget; no back-pressure to planning
"""

from __future__ import annotations

import heapq
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger
from modules.common.status.error_code import ErrorCode, Status, StatusMessage
from modules.common.adapters.game_messages import (
    GameSnapshot,
    StrategyAdvice,
    TeamSide,
    VoiceCommand,
    WinPrediction,
)

logger = get_logger("control")

# ─── Constants ───────────────────────────────────────────────────────────────

_CONTROL_INTERVAL_MS = 1000.0       # 1Hz
_WARN_THRESHOLD_MS = 800.0

# Voice output limits
_MIN_NARRATION_GAP_S = 4.0          # Min seconds between any narration
_SOURCE_COOLDOWN_S = 10.0            # Per-source cooldown
_MAX_QUEUE_SIZE = 30                 # Max pending voice commands
_VOICE_COMMAND_MAX_AGE_S = 15.0      # Discard expired commands

# Win probability announcements
_WIN_PROB_ANNOUNCE_INTERVAL_S = 60.0
_WIN_PROB_SIGNIFICANT_CHANGE = 0.08  # 8% change triggers announcement
_WIN_PROB_FORCE_INTERVAL_S = 120.0   # Force announce every 2 min

# Overlay element TTL
_OVERLAY_DEFAULT_TTL_S = 10.0
_OVERLAY_MAX_ELEMENTS = 20


# ─── Overlay element types ──────────────────────────────────────────────────

class OverlayElementType:
    TEXT = "text"
    BAR = "bar"
    TIMER = "timer"
    ICON = "icon"


@dataclass
class OverlayElement:
    """A visual element on the in-game overlay."""
    element_id: str = ""
    element_type: str = OverlayElementType.TEXT
    content: str = ""
    value: float = 0.0             # For bars: 0-1 fill ratio
    position: str = "top_right"    # top_left, top_right, bottom_left, etc.
    priority: int = 5
    ttl_s: float = _OVERLAY_DEFAULT_TTL_S
    created_at: float = field(default_factory=time.time)
    source: str = ""

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_s


@dataclass
class _PrioritizedVoice:
    """Voice command wrapper for priority queue ordering."""
    priority: int
    insert_order: int
    command: VoiceCommand

    def __lt__(self, other: "_PrioritizedVoice") -> bool:
        if self.priority != other.priority:
            return self.priority < other.priority  # lower = higher priority
        return self.insert_order < other.insert_order


# ─── ControlComponent ───────────────────────────────────────────────────────

class ControlComponent(TimerComponent):
    """Unified control layer: merges all advice into voice + overlay output.

    Each Proc() cycle:
        1. Read voice commands from all sources
        2. Read strategy advice from planning
        3. Read win prediction for periodic announcement
        4. Read objective timers for overlay
        5. Merge into priority queue, apply rate limits
        6. Dispatch top command to TTS
        7. Update overlay element set
        8. Log all dispatched actions for replay analysis
    """

    def __init__(self, tts_enabled: bool = True) -> None:
        super().__init__(
            config=ComponentConfig(
                name="control",
                interval_ms=_CONTROL_INTERVAL_MS,
                warn_threshold_ms=_WARN_THRESHOLD_MS,
            ),
        )
        self._tts_enabled = tts_enabled
        self._node: Optional[CyberNode] = None

        # Readers
        self._voice_cmd_reader: Optional[Reader] = None
        self._strategy_reader: Optional[Reader] = None
        self._win_pred_reader: Optional[Reader] = None
        self._objective_reader: Optional[Reader] = None
        self._game_state_reader: Optional[Reader] = None
        self._teamfight_reader: Optional[Reader] = None

        # Writers
        self._status_writer: Optional[Writer] = None

        # Voice priority queue
        self._voice_queue: List[_PrioritizedVoice] = []
        self._voice_insert_counter: int = 0
        self._last_narration_time: float = 0.0
        self._source_last_narration: Dict[str, float] = defaultdict(float)

        # Win probability tracking
        self._last_win_prob: float = 0.5
        self._last_win_announce_time: float = 0.0

        # Overlay state
        self._overlay_elements: Dict[str, OverlayElement] = {}

        # TTS backend (simplified: print to log for now)
        self._tts_queue: List[str] = []

        # Stats
        self._proc_count: int = 0
        self._narrations_dispatched: int = 0
        self._commands_received: int = 0
        self._commands_dropped: int = 0
        self._commands_expired: int = 0
        self._game_time: float = 0.0

    def Init(self) -> bool:
        logger.info("Initializing ControlComponent (tts=%s)...",
                     self._tts_enabled)

        self._node = CyberNode("control")

        # ── Readers ──────────────────────────────────────────────────
        self._voice_cmd_reader = self._node.CreateReader(
            "/lol/voice_command", VoiceCommand, pending_queue_size=32,
        )
        self._strategy_reader = self._node.CreateReader(
            "/lol/strategy_advice", StrategyAdvice, pending_queue_size=8,
        )
        self._win_pred_reader = self._node.CreateReader(
            "/lol/win_prediction", WinPrediction, pending_queue_size=4,
        )
        self._objective_reader = self._node.CreateReader(
            "/lol/objective_timers", object, pending_queue_size=4,
        )
        self._game_state_reader = self._node.CreateReader(
            "/lol/game_state", object, pending_queue_size=4,
        )
        self._teamfight_reader = self._node.CreateReader(
            "/lol/teamfight_active", object, pending_queue_size=4,
        )

        # ── Writers ──────────────────────────────────────────────────
        self._status_writer = self._node.CreateWriter(
            "/lol/control_status", StatusMessage,
        )

        logger.info("ControlComponent initialized (6 input channels)")
        return True

    def Proc(self) -> bool:
        """One control cycle: read inputs → merge → dispatch."""
        self._proc_count += 1
        now = time.time()

        # ── 1. Update game time ──────────────────────────────────────
        self._game_state_reader.Observe()
        snapshot = self._game_state_reader.GetLatestObserved()
        if snapshot and hasattr(snapshot, 'game_time'):
            self._game_time = snapshot.game_time

        # ── 2. Ingest voice commands ─────────────────────────────────
        self._ingest_voice_commands()

        # ── 3. Generate win probability announcement ─────────────────
        self._check_win_prob_announce()

        # ── 4. Generate strategy overlay ─────────────────────────────
        self._update_strategy_overlay()

        # ── 5. Update objective overlay ──────────────────────────────
        self._update_objective_overlay()

        # ── 6. Dispatch top voice command ────────────────────────────
        self._dispatch_voice(now)

        # ── 7. Expire old overlay elements ───────────────────────────
        self._expire_overlay_elements()

        # ── 8. Publish status ────────────────────────────────────────
        self._publish_status(Status.ok())

        return True

    def on_shutdown(self) -> None:
        logger.info(
            "ControlComponent shutdown: %d narrations dispatched, "
            "%d commands received, %d dropped, %d expired",
            self._narrations_dispatched, self._commands_received,
            self._commands_dropped, self._commands_expired,
        )
        if self._node:
            self._node.shutdown()

    # ─── Voice command ingestion ─────────────────────────────────────

    def _ingest_voice_commands(self) -> None:
        """Read all pending voice commands and add to priority queue."""
        self._voice_cmd_reader.Observe()
        cmd = self._voice_cmd_reader.GetLatestObserved()

        if cmd is None:
            return
        if not isinstance(cmd, VoiceCommand):
            return

        self._commands_received += 1

        # Check expiry
        if cmd.is_expired:
            self._commands_expired += 1
            return

        # Check queue capacity
        if len(self._voice_queue) >= _MAX_QUEUE_SIZE:
            self._commands_dropped += 1
            return

        # Add to priority queue
        self._voice_insert_counter += 1
        heapq.heappush(self._voice_queue, _PrioritizedVoice(
            priority=cmd.priority,
            insert_order=self._voice_insert_counter,
            command=cmd,
        ))

    # ─── Voice dispatch ──────────────────────────────────────────────

    def _dispatch_voice(self, now: float) -> None:
        """Dispatch the highest-priority non-expired voice command.

        Respects minimum narration gap and per-source cooldowns.
        """
        if not self._tts_enabled:
            self._voice_queue.clear()
            return

        # Check global cooldown
        if now - self._last_narration_time < _MIN_NARRATION_GAP_S:
            return

        # Find first eligible command
        while self._voice_queue:
            entry = heapq.heappop(self._voice_queue)
            cmd = entry.command

            # Check expiry
            if cmd.is_expired:
                self._commands_expired += 1
                continue

            # Check per-source cooldown
            source = cmd.source_module or "unknown"
            if now - self._source_last_narration[source] < _SOURCE_COOLDOWN_S:
                # Re-insert if not expired (might be dispatched next cycle)
                if not cmd.is_expired:
                    heapq.heappush(self._voice_queue, entry)
                continue

            # Dispatch!
            self._speak(cmd.text)
            self._last_narration_time = now
            self._source_last_narration[source] = now
            self._narrations_dispatched += 1

            logger.info(
                "Voice [p%d] %s: %s",
                cmd.priority, source, cmd.text[:80],
            )
            return

    def _speak(self, text: str) -> None:
        """Send text to TTS backend.

        Currently logs to console. In production, this would invoke
        pyttsx3, edge-tts, or OS-native speech synthesis.
        """
        self._tts_queue.append(text)
        # Production TTS would go here:
        # self._tts_engine.say(text)
        # self._tts_engine.runAndWait()

    # ─── Win probability announcement ────────────────────────────────

    def _check_win_prob_announce(self) -> None:
        """Periodically announce win probability changes."""
        self._win_pred_reader.Observe()
        pred = self._win_pred_reader.GetLatestObserved()

        if pred is None or not isinstance(pred, WinPrediction):
            return

        now = time.time()
        prob = pred.blue_win_prob
        change = abs(prob - self._last_win_prob)
        time_since_last = now - self._last_win_announce_time

        should_announce = False
        if change >= _WIN_PROB_SIGNIFICANT_CHANGE and time_since_last >= _WIN_PROB_ANNOUNCE_INTERVAL_S:
            should_announce = True
        elif time_since_last >= _WIN_PROB_FORCE_INTERVAL_S:
            should_announce = True

        if should_announce and self._game_time > 120:
            # Determine which team is winning from active player perspective
            winner = "our" if prob > 0.5 else "enemy"
            pct = max(prob, 1.0 - prob) * 100

            text = f"Win probability update: {winner} team at {pct:.0f}%."
            if change >= 0.15:
                text += " Significant shift!"

            self._voice_insert_counter += 1
            heapq.heappush(self._voice_queue, _PrioritizedVoice(
                priority=4,
                insert_order=self._voice_insert_counter,
                command=VoiceCommand(
                    text=text,
                    priority=4,
                    max_age_s=20.0,
                    game_time=self._game_time,
                    source_module="control_win_prob",
                ),
            ))

            self._last_win_prob = prob
            self._last_win_announce_time = now

        # Update overlay
        self._set_overlay_element(OverlayElement(
            element_id="win_prob",
            element_type=OverlayElementType.BAR,
            content=f"Win: {prob * 100:.0f}%",
            value=prob,
            position="top_right",
            priority=1,
            ttl_s=5.0,
            source="control",
        ))

    # ─── Strategy overlay ────────────────────────────────────────────

    def _update_strategy_overlay(self) -> None:
        """Read strategy advice and update overlay elements."""
        self._strategy_reader.Observe()
        advice = self._strategy_reader.GetLatestObserved()

        if advice is None or not isinstance(advice, StrategyAdvice):
            return

        # Primary action display
        if advice.primary_action:
            self._set_overlay_element(OverlayElement(
                element_id="strategy_primary",
                element_type=OverlayElementType.TEXT,
                content=advice.primary_action,
                position="top_left",
                priority=2,
                ttl_s=8.0,
                source="planning",
            ))

        # Macro call display
        if advice.macro_call:
            self._set_overlay_element(OverlayElement(
                element_id="macro_call",
                element_type=OverlayElementType.ICON,
                content=advice.macro_call.upper(),
                position="center_top",
                priority=1,
                ttl_s=10.0,
                source="planning",
            ))

    # ─── Objective overlay ───────────────────────────────────────────

    def _update_objective_overlay(self) -> None:
        """Read objective timers and display countdown on overlay."""
        self._objective_reader.Observe()
        timer_state = self._objective_reader.GetLatestObserved()

        if timer_state is None:
            return

        # Display active countdowns
        for obj_name, attr_name in [
            ("Drake", "drake"), ("Baron", "baron"),
            ("Herald", "herald"), ("Elder", "elder"),
        ]:
            obj_data = getattr(timer_state, attr_name, None)
            if not isinstance(obj_data, dict):
                continue

            status = obj_data.get("status", "")
            remaining = obj_data.get("time_until_respawn", 0.0)

            if status in ("DEAD", "SPAWNING_SOON") and remaining > 0:
                minutes = int(remaining) // 60
                seconds = int(remaining) % 60
                self._set_overlay_element(OverlayElement(
                    element_id=f"timer_{attr_name}",
                    element_type=OverlayElementType.TIMER,
                    content=f"{obj_name}: {minutes}:{seconds:02d}",
                    value=remaining,
                    position="bottom_right",
                    priority=2,
                    ttl_s=3.0,
                    source="objective_tracker",
                ))

    # ─── Overlay management ──────────────────────────────────────────

    def _set_overlay_element(self, element: OverlayElement) -> None:
        """Add or update an overlay element."""
        self._overlay_elements[element.element_id] = element

        # Enforce max elements (evict lowest priority)
        if len(self._overlay_elements) > _OVERLAY_MAX_ELEMENTS:
            sorted_elements = sorted(
                self._overlay_elements.items(),
                key=lambda x: x[1].priority,
                reverse=True,
            )
            # Remove the lowest priority (highest number)
            evict_id = sorted_elements[0][0]
            del self._overlay_elements[evict_id]

    def _expire_overlay_elements(self) -> None:
        """Remove expired overlay elements."""
        expired = [
            eid for eid, elem in self._overlay_elements.items()
            if elem.is_expired
        ]
        for eid in expired:
            del self._overlay_elements[eid]

    # ─── Status ──────────────────────────────────────────────────────

    def _publish_status(self, status: Status) -> None:
        if self._status_writer:
            self._status_writer.Write(StatusMessage(
                status=status,
                sequence=self._proc_count,
                source_component="control",
            ))

    def control_status(self) -> Dict[str, Any]:
        base = self.status()
        base.update({
            "proc_count": self._proc_count,
            "narrations_dispatched": self._narrations_dispatched,
            "commands_received": self._commands_received,
            "commands_dropped": self._commands_dropped,
            "commands_expired": self._commands_expired,
            "voice_queue_size": len(self._voice_queue),
            "overlay_elements": len(self._overlay_elements),
            "tts_enabled": self._tts_enabled,
            "last_win_prob": round(self._last_win_prob, 3),
            "game_time": self._game_time,
        })
        return base

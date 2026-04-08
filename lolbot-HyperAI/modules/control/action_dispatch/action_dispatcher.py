"""
ActionDispatcher — Unified action dispatch to voice, overlay, and log channels.
=================================================================================
lolbot-HyperAI · Control Layer

Routes strategy decisions and predictions to appropriate output channels:
    - Voice (TTS): High-urgency calls and periodic updates
    - Overlay (HUD): Visual status, timers, probability displays
    - Log (structured): Every action for replay analysis

This is the "last mile" of the planning pipeline — it decides HOW to
present information, not WHAT information to present (that's planning's job).

Architecture position:
    modules/control/action_dispatch/action_dispatcher.py   ← YOU ARE HERE
    ├─ Reads: /lol/strategy_advice (from planning)
    ├─ Reads: /lol/win_prediction (from prediction)
    ├─ Reads: /lol/macro_decision (from macro planner)
    ├─ Dispatches to: VoiceNarrator, OverlayRenderer, ActionLog
    └─ Publishes: /lol/dispatch_stats (for monitoring)

Apollo reference:
    modules/control/controller_agent.cc — dispatches planned trajectory
    modules/control/submodules/ — brake, steering, throttle subcontrollers

Design notes:
    - Channel selection: urgency → voice; always → overlay + log
    - Rate limiting per output channel
    - Priority queue with deduplication
    - Supports output channel disable/enable at runtime
    - Action batching: groups related actions within a tick window
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("control.dispatch")

# ─── Constants ───────────────────────────────────────────────────────────────

_VOICE_COOLDOWN_S = 5.0         # Min gap between voice outputs
_OVERLAY_COOLDOWN_S = 1.0       # Min gap between overlay updates per category
_LOG_FLUSH_INTERVAL_S = 5.0     # Flush action log periodically
_MAX_QUEUE_SIZE = 64
_MAX_LOG_ENTRIES = 2000
_BATCH_WINDOW_MS = 200          # Group actions within 200ms window


# ─── Data Types ──────────────────────────────────────────────────────────────

class OutputChannel(Enum):
    """Available output channels."""
    VOICE = "voice"
    OVERLAY = "overlay"
    LOG = "log"


class ActionCategory(Enum):
    """Categories of dispatched actions."""
    WIN_UPDATE = "win_update"           # Win probability change
    STRATEGY_ADVICE = "strategy_advice" # Macro/micro recommendation
    OBJECTIVE_ALERT = "objective_alert" # Dragon/Baron timer
    TEAMFIGHT_CALL = "teamfight_call"   # Engage/disengage
    ITEM_SUGGESTION = "item_suggestion" # Build recommendation
    PHASE_CHANGE = "phase_change"       # Game phase transition
    KILL_FEED = "kill_feed"             # Notable kill event
    DANGER_WARNING = "danger_warning"   # Imminent threat
    GENERAL_INFO = "general_info"       # Miscellaneous info


class ActionPriority(Enum):
    """Action priority levels (lower number = higher priority)."""
    CRITICAL = 1    # Must voice immediately
    HIGH = 2        # Voice if cooldown allows
    MEDIUM = 3      # Overlay + log
    LOW = 4         # Log only
    TRACE = 5       # Debug log only

    @property
    def should_voice(self) -> bool:
        return self.value <= 2

    @property
    def should_overlay(self) -> bool:
        return self.value <= 3


@dataclass
class DispatchAction:
    """An action to be dispatched to output channels.

    Created by the planning/prediction layers and routed by the dispatcher.
    """
    category: ActionCategory
    priority: ActionPriority
    text: str                     # Human-readable action text
    voice_text: str = ""          # Optional: alternate text for TTS
    overlay_text: str = ""        # Optional: alternate text for overlay
    source: str = "unknown"       # Which module generated this
    data: Dict[str, Any] = field(default_factory=dict)  # Extra payload
    timestamp: float = field(default_factory=time.monotonic)
    dedup_key: str = ""           # If set, deduplicates on this key

    def __post_init__(self) -> None:
        if not self.voice_text:
            self.voice_text = self.text
        if not self.overlay_text:
            self.overlay_text = self.text
        if not self.dedup_key:
            self.dedup_key = f"{self.source}:{self.category.value}"


@dataclass
class _ActionLogEntry:
    """Structured log entry for an action."""
    timestamp: float
    category: str
    priority: str
    text: str
    source: str
    channels_sent: List[str]
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "category": self.category,
            "priority": self.priority,
            "text": self.text,
            "source": self.source,
            "channels_sent": self.channels_sent,
            "data": self.data,
        }


@dataclass
class DispatchStats:
    """Statistics for the dispatch system."""
    total_dispatched: int = 0
    total_voiced: int = 0
    total_overlayed: int = 0
    total_logged: int = 0
    total_deduplicated: int = 0
    total_rate_limited: int = 0
    by_category: Dict[str, int] = field(default_factory=dict)
    by_priority: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_dispatched": self.total_dispatched,
            "total_voiced": self.total_voiced,
            "total_overlayed": self.total_overlayed,
            "total_logged": self.total_logged,
            "total_deduplicated": self.total_deduplicated,
            "total_rate_limited": self.total_rate_limited,
            "by_category": dict(self.by_category),
            "by_priority": dict(self.by_priority),
        }


# ─── Output Backends (Interfaces) ───────────────────────────────────────────

class VoiceBackend:
    """Interface to the voice narrator.

    In production, this wraps VoiceNarrator. For testing, it can be
    replaced with a no-op or recording backend.
    """

    def __init__(self) -> None:
        self._last_voice_time: float = 0.0
        self._cooldown_s: float = _VOICE_COOLDOWN_S
        self._enabled: bool = True
        self._queue: Deque[str] = deque(maxlen=16)

    def speak(self, text: str, priority: int = 5) -> bool:
        """Queue text for TTS output.

        Returns:
            True if accepted, False if rate-limited.
        """
        if not self._enabled:
            return False

        now = time.monotonic()
        if now - self._last_voice_time < self._cooldown_s:
            return False

        self._queue.append(text)
        self._last_voice_time = now
        logger.info("Voice: %s", text[:80])
        return True

    def set_cooldown(self, seconds: float) -> None:
        self._cooldown_s = max(1.0, seconds)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def drain_queue(self) -> List[str]:
        """Pop all queued voice texts."""
        items = list(self._queue)
        self._queue.clear()
        return items


class OverlayBackend:
    """Interface to the overlay renderer.

    Wraps OverlayRenderer, providing rate limiting per category.
    """

    def __init__(self) -> None:
        self._last_update: Dict[str, float] = {}
        self._cooldown_s: float = _OVERLAY_COOLDOWN_S
        self._enabled: bool = True
        self._pending: List[Dict[str, Any]] = []

    def display(
        self,
        text: str,
        category: str,
        style: str = "info",
        zone: str = "top_center",
        ttl_s: float = 10.0,
    ) -> bool:
        """Queue an overlay element for display.

        Returns:
            True if accepted, False if rate-limited for this category.
        """
        if not self._enabled:
            return False

        now = time.monotonic()
        last = self._last_update.get(category, 0.0)
        if now - last < self._cooldown_s:
            return False

        self._pending.append({
            "text": text,
            "category": category,
            "style": style,
            "zone": zone,
            "ttl_s": ttl_s,
            "timestamp": now,
        })
        self._last_update[category] = now
        return True

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def drain_pending(self) -> List[Dict[str, Any]]:
        """Pop all pending overlay elements."""
        items = self._pending[:]
        self._pending.clear()
        return items


class ActionLog:
    """Structured action log for replay and analysis."""

    def __init__(self, max_entries: int = _MAX_LOG_ENTRIES) -> None:
        self._entries: Deque[_ActionLogEntry] = deque(maxlen=max_entries)
        self._flush_callbacks: List[Callable[[List[Dict]], None]] = []

    def record(self, entry: _ActionLogEntry) -> None:
        """Record an action log entry."""
        self._entries.append(entry)

    def register_flush_callback(
        self, callback: Callable[[List[Dict]], None],
    ) -> None:
        """Register a callback for periodic flush (e.g. write to disk)."""
        self._flush_callbacks.append(callback)

    def flush(self) -> List[Dict[str, Any]]:
        """Export and return all entries as dicts."""
        entries = [e.to_dict() for e in self._entries]
        for cb in self._flush_callbacks:
            try:
                cb(entries)
            except Exception:
                logger.exception("Action log flush callback failed")
        return entries

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


# ─── ActionDispatcher ────────────────────────────────────────────────────────

class ActionDispatcher:
    """Central action dispatch hub.

    Routes actions from planning/prediction to voice/overlay/log based
    on priority, category, and rate limiting rules.

    Usage::

        dispatcher = ActionDispatcher()
        dispatcher.dispatch(DispatchAction(
            category=ActionCategory.STRATEGY_ADVICE,
            priority=ActionPriority.HIGH,
            text="Take Baron now — 3 man advantage",
            source="planning.macro",
        ))
        # Action is routed to voice (if cooldown allows),
        # overlay (always), and log (always).
    """

    def __init__(
        self,
        voice: Optional[VoiceBackend] = None,
        overlay: Optional[OverlayBackend] = None,
        action_log: Optional[ActionLog] = None,
    ) -> None:
        self._voice = voice or VoiceBackend()
        self._overlay = overlay or OverlayBackend()
        self._log = action_log or ActionLog()
        self._stats = DispatchStats()

        # Deduplication tracking
        self._recent_dedup_keys: Dict[str, float] = {}
        self._dedup_window_s: float = 3.0

        # Pending batch
        self._pending: Deque[DispatchAction] = deque(maxlen=_MAX_QUEUE_SIZE)
        self._last_flush: float = 0.0

    def dispatch(self, action: DispatchAction) -> Dict[str, bool]:
        """Dispatch an action to appropriate output channels.

        Args:
            action: The action to dispatch.

        Returns:
            Dict of channel_name → was_sent.
        """
        results: Dict[str, bool] = {
            "voice": False,
            "overlay": False,
            "log": False,
        }

        # Deduplication check
        now = time.monotonic()
        if action.dedup_key in self._recent_dedup_keys:
            last_time = self._recent_dedup_keys[action.dedup_key]
            if now - last_time < self._dedup_window_s:
                self._stats.total_deduplicated += 1
                logger.debug(
                    "Deduplicated: %s (%.1fs ago)",
                    action.dedup_key,
                    now - last_time,
                )
                return results

        self._recent_dedup_keys[action.dedup_key] = now

        # Clean old dedup entries
        cutoff = now - self._dedup_window_s * 2
        self._recent_dedup_keys = {
            k: t for k, t in self._recent_dedup_keys.items()
            if t > cutoff
        }

        # Route to channels
        channels_sent: List[str] = []

        # Voice: high priority and critical only
        if action.priority.should_voice:
            sent = self._voice.speak(
                action.voice_text,
                priority=action.priority.value,
            )
            results["voice"] = sent
            if sent:
                self._stats.total_voiced += 1
                channels_sent.append("voice")
            else:
                self._stats.total_rate_limited += 1

        # Overlay: medium priority and above
        if action.priority.should_overlay:
            style_map = {
                ActionPriority.CRITICAL: "danger",
                ActionPriority.HIGH: "warning",
                ActionPriority.MEDIUM: "info",
            }
            zone_map = {
                ActionCategory.WIN_UPDATE: "top_right",
                ActionCategory.STRATEGY_ADVICE: "top_center",
                ActionCategory.OBJECTIVE_ALERT: "top_left",
                ActionCategory.TEAMFIGHT_CALL: "center",
                ActionCategory.DANGER_WARNING: "center",
            }
            sent = self._overlay.display(
                text=action.overlay_text,
                category=action.category.value,
                style=style_map.get(action.priority, "info"),
                zone=zone_map.get(action.category, "top_center"),
            )
            results["overlay"] = sent
            if sent:
                self._stats.total_overlayed += 1
                channels_sent.append("overlay")

        # Log: always
        log_entry = _ActionLogEntry(
            timestamp=action.timestamp,
            category=action.category.value,
            priority=action.priority.name,
            text=action.text,
            source=action.source,
            channels_sent=channels_sent,
            data=action.data,
        )
        self._log.record(log_entry)
        results["log"] = True
        self._stats.total_logged += 1

        # Update stats
        self._stats.total_dispatched += 1
        cat_key = action.category.value
        self._stats.by_category[cat_key] = (
            self._stats.by_category.get(cat_key, 0) + 1
        )
        pri_key = action.priority.name
        self._stats.by_priority[pri_key] = (
            self._stats.by_priority.get(pri_key, 0) + 1
        )

        logger.debug(
            "Dispatched [%s/%s]: %s → %s",
            action.category.value,
            action.priority.name,
            action.text[:60],
            ",".join(channels_sent) or "log_only",
        )

        return results

    def dispatch_batch(self, actions: List[DispatchAction]) -> int:
        """Dispatch multiple actions, sorted by priority.

        Args:
            actions: List of actions to dispatch.

        Returns:
            Number of actions actually dispatched (after dedup).
        """
        # Sort by priority (most urgent first)
        sorted_actions = sorted(actions, key=lambda a: a.priority.value)
        dispatched = 0
        for action in sorted_actions:
            results = self.dispatch(action)
            if any(results.values()):
                dispatched += 1
        return dispatched

    # ── Configuration ────────────────────────────────────────────────────

    def set_voice_enabled(self, enabled: bool) -> None:
        self._voice.set_enabled(enabled)

    def set_overlay_enabled(self, enabled: bool) -> None:
        self._overlay.set_enabled(enabled)

    def set_voice_cooldown(self, seconds: float) -> None:
        self._voice.set_cooldown(seconds)

    def set_dedup_window(self, seconds: float) -> None:
        self._dedup_window_s = max(0.5, seconds)

    # ── Query ────────────────────────────────────────────────────────────

    def get_voice_queue(self) -> List[str]:
        """Drain pending voice texts (for VoiceNarrator to consume)."""
        return self._voice.drain_queue()

    def get_overlay_pending(self) -> List[Dict[str, Any]]:
        """Drain pending overlay elements (for OverlayRenderer to consume)."""
        return self._overlay.drain_pending()

    def get_action_log(self) -> List[Dict[str, Any]]:
        """Export the full action log."""
        return self._log.flush()

    # ── Stats & Reset ────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            **self._stats.to_dict(),
            "voice_enabled": self._voice.is_enabled,
            "overlay_enabled": self._overlay.is_enabled,
            "log_entries": self._log.entry_count,
            "dedup_window_s": self._dedup_window_s,
        }

    def reset(self) -> None:
        """Reset all state between games."""
        self._stats = DispatchStats()
        self._recent_dedup_keys.clear()
        self._pending.clear()
        self._log.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: Overlay + voice joint scheduling + adaptive rate control
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 ActionDispatcher 的 voice/overlay/log 三路分发 这个好例子开始。
#   然后，遵循该模式实现 JointScheduler，让 voice 和 overlay 可以 协同调度，
#   并能 避免同时发出冲突信息。
#   接着 AdaptiveRateController 引入 自适应速率控制，使 系统 能够 根据
#   游戏节奏自动调整输出频率（团战时更频繁/平淡期更稀疏），
#   同时 ActionHistory 优化 历史追溯以支撑回放分析。

from modules.control.overlay.overlay_protocol import (
    OverlayBatch,
    OverlayCommand,
    OverlayAction,
    OverlayElementDef,
    OverlayWebSocketSender,
    Position,
    ElementType,
)


# ─── Joint scheduling state ─────────────────────────────────────────────────

@dataclass
class ScheduleSlot:
    """A time slot for joint voice + overlay scheduling.

    Prevents voice and overlay from delivering conflicting or
    overlapping messages in the same time window.
    """
    start_time: float = 0.0
    duration_s: float = 3.0
    voice_text: str = ""
    overlay_elements: List[Dict[str, Any]] = field(default_factory=list)
    priority: int = 5
    source: str = ""
    delivered: bool = False

    @property
    def end_time(self) -> float:
        return self.start_time + self.duration_s

    @property
    def is_expired(self) -> bool:
        return time.time() > self.end_time


class JointScheduler:
    """Coordinates voice and overlay output to avoid conflicts.

    Rules:
    1. Voice and overlay should deliver the same information
       (voice says it, overlay shows it)
    2. Don't show contradictory overlay while voice is speaking
    3. High-urgency voice preempts overlay updates
    4. During teamfights, reduce overlay updates (reduce distraction)

    Apollo parallel: control/controller_agent.cc coordinates
    brake/throttle/steering commands.
    """

    def __init__(
        self,
        slot_duration_s: float = 3.0,
        max_slots: int = 8,
    ) -> None:
        self._slot_duration = slot_duration_s
        self._max_slots = max_slots
        self._slots: Deque[ScheduleSlot] = deque(maxlen=max_slots)
        self._voice_busy_until: float = 0.0
        self._last_voice_category: str = ""
        self._scheduled_count: int = 0
        self._conflict_count: int = 0

    def schedule(
        self,
        action: "DispatchAction",
        voice_text: str,
        overlay_elements: Optional[List[Dict[str, Any]]] = None,
    ) -> ScheduleSlot:
        """Schedule a joint voice + overlay action.

        Checks for conflicts with existing slots and adjusts timing.
        """
        now = time.time()

        # Find the earliest available time
        earliest = now
        if self._slots:
            last_slot = self._slots[-1]
            if not last_slot.is_expired and last_slot.voice_text:
                earliest = max(earliest, last_slot.end_time)

        # Check for conflict: same category within slot_duration
        for slot in self._slots:
            if (not slot.is_expired
                    and slot.source == action.source
                    and now - slot.start_time < self._slot_duration):
                self._conflict_count += 1
                # Merge into existing slot (update overlay, keep voice)
                if overlay_elements:
                    slot.overlay_elements.extend(overlay_elements)
                return slot

        slot = ScheduleSlot(
            start_time=earliest,
            duration_s=self._slot_duration,
            voice_text=voice_text,
            overlay_elements=overlay_elements or [],
            priority=action.priority.value if hasattr(action.priority, 'value') else 5,
            source=action.source,
        )
        self._slots.append(slot)
        self._scheduled_count += 1
        return slot

    def get_ready_slots(self) -> List[ScheduleSlot]:
        """Get slots that are ready to be delivered."""
        now = time.time()
        ready = []
        for slot in self._slots:
            if not slot.delivered and slot.start_time <= now:
                slot.delivered = True
                ready.append(slot)
        return ready

    def is_voice_busy(self) -> bool:
        return time.time() < self._voice_busy_until

    def mark_voice_busy(self, duration_s: float) -> None:
        self._voice_busy_until = time.time() + duration_s

    def clear_expired(self) -> int:
        """Remove expired slots."""
        initial = len(self._slots)
        self._slots = deque(
            (s for s in self._slots if not s.is_expired),
            maxlen=self._max_slots,
        )
        return initial - len(self._slots)

    def stats(self) -> Dict[str, Any]:
        return {
            "pending_slots": sum(1 for s in self._slots if not s.delivered),
            "total_scheduled": self._scheduled_count,
            "conflicts_resolved": self._conflict_count,
        }


# ─── Adaptive rate controller ────────────────────────────────────────────────

class AdaptiveRateController:
    """Adjusts output rate based on game tempo.

    During teamfights or rapid objective sequences, increases the
    rate of voice/overlay output. During laning phases, reduces it
    to avoid annoying the player.

    Apollo parallel: control rate adaptation based on driving scenario
    (highway vs city vs parking).
    """

    # Game tempo → rate multiplier
    _TEMPO_MULTIPLIERS = {
        "idle": 0.3,        # barely any output
        "laning": 0.5,      # reduced rate
        "roaming": 0.8,     # moderate
        "skirmish": 1.0,    # normal rate
        "teamfight": 1.5,   # increased rate
        "objective": 1.2,   # slightly increased
        "baron": 2.0,       # maximum rate
    }

    def __init__(
        self,
        base_voice_interval_s: float = _VOICE_COOLDOWN_S,
        base_overlay_interval_s: float = _OVERLAY_COOLDOWN_S,
    ) -> None:
        self._base_voice_interval = base_voice_interval_s
        self._base_overlay_interval = base_overlay_interval_s
        self._current_tempo: str = "laning"
        self._multiplier: float = 0.5
        self._events_per_minute: float = 0.0
        self._last_event_times: Deque[float] = deque(maxlen=30)

    def update_tempo(self, tempo: str) -> None:
        """Update the current game tempo."""
        self._current_tempo = tempo
        self._multiplier = self._TEMPO_MULTIPLIERS.get(tempo, 1.0)

    def record_event(self) -> None:
        """Record that an event occurred (for events-per-minute tracking)."""
        self._last_event_times.append(time.time())
        self._recalc_epm()

    def _recalc_epm(self) -> None:
        now = time.time()
        recent = [t for t in self._last_event_times if now - t < 60.0]
        self._events_per_minute = len(recent)

        # Auto-detect tempo from event rate
        if self._events_per_minute > 15:
            self.update_tempo("teamfight")
        elif self._events_per_minute > 8:
            self.update_tempo("skirmish")
        elif self._events_per_minute > 3:
            self.update_tempo("roaming")
        elif self._events_per_minute > 0:
            self.update_tempo("laning")
        else:
            self.update_tempo("idle")

    @property
    def voice_interval_s(self) -> float:
        """Current voice output interval (adjusted for tempo)."""
        return self._base_voice_interval / max(0.1, self._multiplier)

    @property
    def overlay_interval_s(self) -> float:
        """Current overlay update interval (adjusted for tempo)."""
        return self._base_overlay_interval / max(0.1, self._multiplier)

    def stats(self) -> Dict[str, Any]:
        return {
            "tempo": self._current_tempo,
            "multiplier": self._multiplier,
            "events_per_minute": round(self._events_per_minute, 1),
            "voice_interval_s": round(self.voice_interval_s, 2),
            "overlay_interval_s": round(self.overlay_interval_s, 2),
        }


# ─── Action history for replay analysis ─────────────────────────────────────

@dataclass
class HistoryEntry:
    """Immutable record of a dispatched action for post-game analysis."""
    timestamp: float
    category: str
    priority: str
    text: str
    channels: List[str]
    game_time_s: float = 0.0
    momentum_score: float = 0.0
    win_prob: float = 0.5
    was_spoken: bool = False
    was_displayed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t": round(self.timestamp, 3),
            "cat": self.category,
            "pri": self.priority,
            "text": self.text[:100],
            "ch": self.channels,
            "gt": round(self.game_time_s, 1),
            "mom": round(self.momentum_score, 2),
            "wp": round(self.win_prob, 3),
            "spoken": self.was_spoken,
            "displayed": self.was_displayed,
        }


class ActionHistory:
    """Append-only history of all dispatched actions.

    Used for post-game analysis: which advice was given, when,
    and whether it was actually delivered to the player.

    Supports export for correlation with game outcomes.
    """

    def __init__(self, max_entries: int = 5000) -> None:
        self._entries: List[HistoryEntry] = []
        self._max_entries = max_entries

    def record(self, entry: HistoryEntry) -> None:
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            # Drop oldest 10%
            drop = self._max_entries // 10
            self._entries = self._entries[drop:]

    def query(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        category: Optional[str] = None,
    ) -> List[HistoryEntry]:
        """Query history with optional filters."""
        results = self._entries
        if start_time is not None:
            results = [e for e in results if e.timestamp >= start_time]
        if end_time is not None:
            results = [e for e in results if e.timestamp <= end_time]
        if category is not None:
            results = [e for e in results if e.category == category]
        return results

    def export(self) -> List[Dict[str, Any]]:
        """Export full history for post-game analysis."""
        return [e.to_dict() for e in self._entries]

    @property
    def count(self) -> int:
        return len(self._entries)

    def summary(self) -> Dict[str, Any]:
        """Summary statistics of dispatch history."""
        if not self._entries:
            return {"count": 0}

        spoken = sum(1 for e in self._entries if e.was_spoken)
        displayed = sum(1 for e in self._entries if e.was_displayed)
        cats: Dict[str, int] = {}
        for e in self._entries:
            cats[e.category] = cats.get(e.category, 0) + 1

        return {
            "count": len(self._entries),
            "spoken": spoken,
            "displayed": displayed,
            "delivery_rate": round(
                (spoken + displayed) / max(1, len(self._entries) * 2), 3),
            "by_category": cats,
            "time_range_s": round(
                self._entries[-1].timestamp - self._entries[0].timestamp, 1
            ) if len(self._entries) > 1 else 0,
        }

    def clear(self) -> None:
        self._entries.clear()

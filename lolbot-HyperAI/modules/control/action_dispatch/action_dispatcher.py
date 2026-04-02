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

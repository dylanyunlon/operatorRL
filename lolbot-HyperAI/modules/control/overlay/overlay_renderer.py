"""
OverlayRenderer — In-game HUD overlay element manager.
========================================================
lolbot-HyperAI · Control Layer

Manages a set of overlay elements (text labels, status bars, timers)
that represent the assistant's output to the player.  Each element
has a TTL (time-to-live), priority, and position.  The renderer
collects display requests from all modules via ``/lol/overlay_commands``
and maintains the active element set.

Note: This module manages the logical overlay state.  The actual screen
rendering is handled by a separate display backend (e.g. DirectX hook,
OBS overlay, or web dashboard).  This separation mirrors Apollo's
planning→control split where planning decides what to do and control
executes it.

Architecture position:
    modules/control/overlay/overlay_renderer.py   ← YOU ARE HERE
    ├─ Reads: /lol/overlay_commands (OverlayCommand from planning/prediction)
    ├─ Reads: /lol/game_state (GameSnapshot for context)
    ├─ Publishes: /lol/overlay_state (OverlayState for dashboard)
    └─ Used by: control layer, dreamview dashboard

Apollo reference:
    modules/planning/planning_base/trajectory_stitcher.cc — output pipeline
    modules/control/controller_agent.cc — executes planned actions

Design notes:
    - Max 8 simultaneous overlay elements (avoid screen clutter)
    - Priority-based eviction when at capacity
    - TTL auto-expire: elements disappear after their duration
    - Deduplication: same source+category overwrites previous
    - Position zones: TOP_LEFT, TOP_CENTER, TOP_RIGHT, BOTTOM_CENTER
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("control.overlay")

# ─── Constants ───────────────────────────────────────────────────────────────

_MAX_OVERLAY_ELEMENTS = 8
_DEFAULT_TTL_S = 10.0
_MIN_TTL_S = 1.0
_MAX_TTL_S = 300.0
_CLEANUP_INTERVAL_S = 1.0  # How often to prune expired elements
_ELEMENT_ID_COUNTER_START = 1000


# ─── Data Types ──────────────────────────────────────────────────────────────

class OverlayZone(Enum):
    """Screen position zones for overlay elements."""
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER = "center"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class OverlayStyle(Enum):
    """Visual style presets for overlay elements."""
    INFO = "info"           # Neutral blue
    SUCCESS = "success"     # Green
    WARNING = "warning"     # Yellow/orange
    DANGER = "danger"       # Red
    HIGHLIGHT = "highlight" # Gold/accent


class ElementType(Enum):
    """Types of overlay elements."""
    TEXT = "text"                   # Simple text label
    PROGRESS_BAR = "progress_bar"  # Bar with percentage
    COUNTDOWN = "countdown"        # Timer counting down
    STATUS_ICON = "status_icon"    # Icon with text
    WIN_PROBABILITY = "win_prob"   # Special win% display


@dataclass
class OverlayCommand:
    """Request to display an overlay element.

    Modules publish these on ``/lol/overlay_commands``.  The renderer
    processes them in its Proc() cycle.
    """
    source: str                # Module name (e.g. "prediction", "planning")
    category: str              # Category for dedup (e.g. "win_prob", "macro")
    element_type: ElementType = ElementType.TEXT
    text: str = ""
    value: float = 0.0         # For progress bars (0.0-1.0)
    zone: OverlayZone = OverlayZone.TOP_CENTER
    style: OverlayStyle = OverlayStyle.INFO
    priority: int = 5          # 1=highest, 10=lowest
    ttl_s: float = _DEFAULT_TTL_S
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class _ActiveElement:
    """Internal representation of an active overlay element."""
    element_id: int
    command: OverlayCommand
    created_at: float
    expires_at: float

    @property
    def is_expired(self) -> bool:
        return time.monotonic() >= self.expires_at

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.element_id,
            "source": self.command.source,
            "category": self.command.category,
            "type": self.command.element_type.value,
            "text": self.command.text,
            "value": round(self.command.value, 3),
            "zone": self.command.zone.value,
            "style": self.command.style.value,
            "priority": self.command.priority,
            "remaining_s": round(self.remaining_s, 1),
        }


@dataclass
class OverlayState:
    """Snapshot of all active overlay elements — published for dashboard."""
    elements: List[Dict[str, Any]] = field(default_factory=list)
    total_displayed: int = 0
    total_expired: int = 0
    total_evicted: int = 0
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "elements": self.elements,
            "total_displayed": self.total_displayed,
            "total_expired": self.total_expired,
            "total_evicted": self.total_evicted,
        }


# ─── Overlay Renderer ───────────────────────────────────────────────────────

class OverlayRenderer:
    """Manages the set of active overlay elements.

    Lifecycle of an overlay element:
        1. Module publishes OverlayCommand to ``/lol/overlay_commands``
        2. Renderer picks it up in ``process_commands()``
        3. Deduplication: same (source, category) replaces existing
        4. Capacity check: if full, lowest-priority element is evicted
        5. Element expires after TTL seconds
        6. ``get_state()`` returns current active set for display

    Thread-safety: All mutations happen in the single Proc() thread.
    The ``get_state()`` method returns a snapshot (copy) for consumers.
    """

    def __init__(
        self,
        max_elements: int = _MAX_OVERLAY_ELEMENTS,
    ) -> None:
        self._max_elements = max_elements
        self._elements: OrderedDict[int, _ActiveElement] = OrderedDict()
        self._dedup_index: Dict[str, int] = {}  # "source:category" → element_id
        self._id_counter = _ELEMENT_ID_COUNTER_START
        self._last_cleanup = 0.0
        self._stats_displayed = 0
        self._stats_expired = 0
        self._stats_evicted = 0
        self._pending_commands: List[OverlayCommand] = []

    # ── Command Processing ───────────────────────────────────────────────

    def submit_command(self, cmd: OverlayCommand) -> None:
        """Queue an overlay command for processing.

        Called by modules that want to display something.  Commands
        are processed in the next ``process_commands()`` call.
        """
        self._pending_commands.append(cmd)

    def process_commands(self) -> int:
        """Process all pending overlay commands.

        Returns:
            Number of commands processed.
        """
        if not self._pending_commands:
            self._cleanup_expired()
            return 0

        commands = self._pending_commands[:]
        self._pending_commands.clear()

        processed = 0
        for cmd in commands:
            self._process_one(cmd)
            processed += 1

        self._cleanup_expired()
        return processed

    def _process_one(self, cmd: OverlayCommand) -> None:
        """Process a single overlay command."""
        now = time.monotonic()

        # Validate TTL
        ttl = max(_MIN_TTL_S, min(_MAX_TTL_S, cmd.ttl_s))

        # Deduplication key
        dedup_key = f"{cmd.source}:{cmd.category}"

        # Check if we're replacing an existing element
        if dedup_key in self._dedup_index:
            old_id = self._dedup_index[dedup_key]
            if old_id in self._elements:
                del self._elements[old_id]
                logger.debug(
                    "Overlay: replaced element %d (%s)", old_id, dedup_key,
                )

        # Create new element
        self._id_counter += 1
        element = _ActiveElement(
            element_id=self._id_counter,
            command=cmd,
            created_at=now,
            expires_at=now + ttl,
        )

        # Capacity check
        while len(self._elements) >= self._max_elements:
            self._evict_lowest_priority()

        # Insert
        self._elements[element.element_id] = element
        self._dedup_index[dedup_key] = element.element_id
        self._stats_displayed += 1

        logger.debug(
            "Overlay: added #%d [%s] zone=%s ttl=%.1fs — %s",
            element.element_id,
            dedup_key,
            cmd.zone.value,
            ttl,
            cmd.text[:50],
        )

    def _evict_lowest_priority(self) -> None:
        """Remove the lowest-priority (highest number) element."""
        if not self._elements:
            return

        # Find element with highest priority number (lowest importance)
        worst_id = None
        worst_priority = -1
        for eid, elem in self._elements.items():
            if elem.command.priority > worst_priority:
                worst_priority = elem.command.priority
                worst_id = eid

        if worst_id is not None:
            evicted = self._elements.pop(worst_id)
            # Clean dedup index
            dedup_key = f"{evicted.command.source}:{evicted.command.category}"
            self._dedup_index.pop(dedup_key, None)
            self._stats_evicted += 1
            logger.debug(
                "Overlay: evicted #%d (priority %d) to make room",
                worst_id, worst_priority,
            )

    def _cleanup_expired(self) -> None:
        """Remove expired elements."""
        now = time.monotonic()
        if now - self._last_cleanup < _CLEANUP_INTERVAL_S:
            return
        self._last_cleanup = now

        expired_ids = [
            eid for eid, elem in self._elements.items()
            if elem.is_expired
        ]
        for eid in expired_ids:
            elem = self._elements.pop(eid)
            dedup_key = f"{elem.command.source}:{elem.command.category}"
            self._dedup_index.pop(dedup_key, None)
            self._stats_expired += 1

        if expired_ids:
            logger.debug(
                "Overlay: cleaned up %d expired elements", len(expired_ids),
            )

    # ── State Query ──────────────────────────────────────────────────────

    def get_state(self) -> OverlayState:
        """Return a snapshot of all active overlay elements.

        Returns:
            OverlayState with serialized element data.
        """
        elements = []
        for elem in self._elements.values():
            if not elem.is_expired:
                elements.append(elem.to_dict())

        # Sort by priority (most important first), then by zone
        elements.sort(key=lambda e: (e["priority"], e["zone"]))

        return OverlayState(
            elements=elements,
            total_displayed=self._stats_displayed,
            total_expired=self._stats_expired,
            total_evicted=self._stats_evicted,
        )

    def get_elements_in_zone(self, zone: OverlayZone) -> List[Dict[str, Any]]:
        """Return active elements in a specific screen zone."""
        return [
            elem.to_dict()
            for elem in self._elements.values()
            if elem.command.zone == zone and not elem.is_expired
        ]

    @property
    def active_count(self) -> int:
        """Number of currently active (non-expired) elements."""
        return sum(
            1 for e in self._elements.values() if not e.is_expired
        )

    @property
    def is_at_capacity(self) -> bool:
        return len(self._elements) >= self._max_elements

    # ── Convenience Constructors ─────────────────────────────────────────

    def show_win_probability(
        self,
        probability: float,
        source: str = "prediction",
    ) -> None:
        """Shortcut to display win probability in the overlay."""
        style = OverlayStyle.SUCCESS if probability >= 0.55 else (
            OverlayStyle.DANGER if probability < 0.45 else OverlayStyle.INFO
        )
        self.submit_command(OverlayCommand(
            source=source,
            category="win_probability",
            element_type=ElementType.WIN_PROBABILITY,
            text=f"Win: {probability:.0%}",
            value=probability,
            zone=OverlayZone.TOP_RIGHT,
            style=style,
            priority=2,
            ttl_s=15.0,
        ))

    def show_strategy_advice(
        self,
        text: str,
        urgency: str = "medium",
        source: str = "planning",
    ) -> None:
        """Shortcut to display strategy advice."""
        style_map = {
            "low": OverlayStyle.INFO,
            "medium": OverlayStyle.WARNING,
            "high": OverlayStyle.DANGER,
            "critical": OverlayStyle.DANGER,
        }
        self.submit_command(OverlayCommand(
            source=source,
            category="strategy",
            element_type=ElementType.TEXT,
            text=text,
            zone=OverlayZone.TOP_CENTER,
            style=style_map.get(urgency, OverlayStyle.INFO),
            priority=3,
            ttl_s=12.0,
        ))

    def show_objective_timer(
        self,
        objective: str,
        seconds_remaining: float,
        source: str = "prediction",
    ) -> None:
        """Shortcut to display an objective countdown."""
        mins = int(seconds_remaining) // 60
        secs = int(seconds_remaining) % 60
        self.submit_command(OverlayCommand(
            source=source,
            category=f"timer_{objective}",
            element_type=ElementType.COUNTDOWN,
            text=f"{objective}: {mins}:{secs:02d}",
            value=seconds_remaining,
            zone=OverlayZone.TOP_LEFT,
            style=OverlayStyle.WARNING if seconds_remaining < 60 else OverlayStyle.INFO,
            priority=4,
            ttl_s=max(2.0, seconds_remaining + 5.0),
        ))

    # ── Stats & Reset ────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return overlay renderer statistics."""
        zone_counts: Dict[str, int] = {}
        for elem in self._elements.values():
            z = elem.command.zone.value
            zone_counts[z] = zone_counts.get(z, 0) + 1

        return {
            "active_elements": self.active_count,
            "max_elements": self._max_elements,
            "total_displayed": self._stats_displayed,
            "total_expired": self._stats_expired,
            "total_evicted": self._stats_evicted,
            "zone_distribution": zone_counts,
            "at_capacity": self.is_at_capacity,
        }

    def clear_all(self) -> None:
        """Remove all overlay elements."""
        self._elements.clear()
        self._dedup_index.clear()
        self._pending_commands.clear()

    def reset(self) -> None:
        """Full reset (between games)."""
        self.clear_all()
        self._stats_displayed = 0
        self._stats_expired = 0
        self._stats_evicted = 0
        self._id_counter = _ELEMENT_ID_COUNTER_START

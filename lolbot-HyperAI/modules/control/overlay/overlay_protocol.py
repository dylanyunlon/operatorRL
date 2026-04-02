"""
OverlayProtocol — Type-safe overlay element message definitions.
=================================================================
lolbot-HyperAI · Control Layer

Defines OverlayElement, OverlayCommand, and OverlayLayout as the
typed protocol between planning/prediction and overlay_renderer.

Architecture position:
    modules/control/overlay/overlay_protocol.py   ← YOU ARE HERE
    ├─ Published by: PlanningComponent, ObjectiveTracker, ControlComponent
    ├─ Consumed by: OverlayRenderer
    └─ Transported via: /lol/overlay_commands channel
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class ElementType(Enum):
    TEXT = "text"
    BAR = "bar"
    TIMER = "timer"
    ICON = "icon"
    ALERT = "alert"


class Position(Enum):
    TOP_LEFT = "top_left"
    TOP_CENTER = "top_center"
    TOP_RIGHT = "top_right"
    CENTER_LEFT = "center_left"
    CENTER = "center"
    CENTER_RIGHT = "center_right"
    BOTTOM_LEFT = "bottom_left"
    BOTTOM_CENTER = "bottom_center"
    BOTTOM_RIGHT = "bottom_right"


class OverlayAction(Enum):
    SET = "set"
    UPDATE = "update"
    REMOVE = "remove"
    CLEAR_ALL = "clear_all"


@dataclass(frozen=True)
class OverlayElementDef:
    """Definition of a single overlay element."""
    element_id: str = ""
    element_type: ElementType = ElementType.TEXT
    position: Position = Position.TOP_RIGHT
    content: str = ""
    value: float = 0.0
    max_value: float = 1.0
    color: str = "#FFFFFF"
    bg_color: str = "#00000080"
    font_size: int = 14
    priority: int = 5
    ttl_s: float = 10.0
    source_module: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.timestamp) > self.ttl_s

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.element_id,
            "type": self.element_type.value,
            "pos": self.position.value,
            "content": self.content,
            "value": self.value,
            "max_value": self.max_value,
            "color": self.color,
            "priority": self.priority,
            "ttl_s": self.ttl_s,
            "source": self.source_module,
        }


@dataclass(frozen=True)
class OverlayCommand:
    """A command to the overlay renderer.

    Published on ``/lol/overlay_commands``.
    """
    action: OverlayAction = OverlayAction.SET
    element: Optional[OverlayElementDef] = None
    target_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class OverlayLayout:
    """Current overlay state: set of active elements."""
    elements: Dict[str, OverlayElementDef] = field(default_factory=dict)
    max_elements: int = 20

    def apply_command(self, cmd: OverlayCommand) -> None:
        if cmd.action == OverlayAction.SET and cmd.element:
            self.elements[cmd.element.element_id] = cmd.element
            self._enforce_limit()
        elif cmd.action == OverlayAction.UPDATE and cmd.element:
            self.elements[cmd.element.element_id] = cmd.element
        elif cmd.action == OverlayAction.REMOVE:
            self.elements.pop(cmd.target_id, None)
        elif cmd.action == OverlayAction.CLEAR_ALL:
            self.elements.clear()

    def expire_old(self) -> int:
        expired = [eid for eid, e in self.elements.items() if e.is_expired]
        for eid in expired:
            del self.elements[eid]
        return len(expired)

    def _enforce_limit(self) -> None:
        while len(self.elements) > self.max_elements:
            worst = max(self.elements.values(), key=lambda e: e.priority)
            del self.elements[worst.element_id]

    def active_elements(self) -> List[OverlayElementDef]:
        return sorted(self.elements.values(), key=lambda e: e.priority)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": len(self.elements),
            "elements": [e.to_dict() for e in self.active_elements()],
        }

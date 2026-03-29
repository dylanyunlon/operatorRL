"""
Overlay Renderer — In-game HUD overlay element manager.

Manages text, rectangle, and marker overlay elements with TTL,
position updates, and max-element enforcement.

Location: integrations/lol/src/lol_agent/overlay_renderer.py

Reference (拿来主义):
  - LeagueAI/LeagueAI_helper.py: overlay rendering patterns
  - extensions/vision-bridge/src/vision_bridge/frame_buffer.py: frame management
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.overlay_renderer.v1"


class OverlayRenderer:
    """HUD overlay element manager.

    Attributes:
        width: Overlay canvas width.
        height: Overlay canvas height.
        max_elements: Maximum simultaneous elements.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        max_elements: int = 100,
    ) -> None:
        self.width = width
        self.height = height
        self.max_elements = max_elements
        self._elements: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_text(
        self,
        text: str,
        x: int = 0,
        y: int = 0,
        color: str = "white",
        ttl_ms: Optional[int] = None,
    ) -> str:
        return self._add_element("text", {"text": text, "x": x, "y": y, "color": color}, ttl_ms)

    def add_rect(
        self,
        x: int = 0,
        y: int = 0,
        w: int = 100,
        h: int = 100,
        color: str = "red",
        alpha: float = 1.0,
        ttl_ms: Optional[int] = None,
    ) -> str:
        return self._add_element("rect", {"x": x, "y": y, "w": w, "h": h, "color": color, "alpha": alpha}, ttl_ms)

    def _add_element(self, etype: str, props: dict[str, Any], ttl_ms: Optional[int]) -> str:
        eid = uuid.uuid4().hex[:8]
        elem = {
            "id": eid,
            "type": etype,
            "created_at": time.time(),
            "ttl_ms": ttl_ms,
            "remaining_ms": ttl_ms,
            **props,
        }
        # Enforce max elements — remove oldest
        while len(self._elements) >= self.max_elements:
            oldest_id = min(self._elements, key=lambda k: self._elements[k]["created_at"])
            del self._elements[oldest_id]

        self._elements[eid] = elem
        self._fire_evolution({"event": "element_added", "id": eid, "type": etype})
        return eid

    def remove_element(self, eid: str) -> None:
        self._elements.pop(eid, None)

    def clear(self) -> None:
        self._elements.clear()

    def list_elements(self) -> list[dict[str, Any]]:
        return list(self._elements.values())

    def get_element(self, eid: str) -> Optional[dict[str, Any]]:
        return self._elements.get(eid)

    def update_element(self, eid: str, **kwargs: Any) -> None:
        if eid in self._elements:
            self._elements[eid].update(kwargs)

    def tick(self, elapsed_ms: float) -> None:
        """Advance time, expire TTL elements."""
        expired = []
        for eid, elem in self._elements.items():
            if elem["remaining_ms"] is not None:
                elem["remaining_ms"] -= elapsed_ms
                if elem["remaining_ms"] <= 0:
                    expired.append(eid)
        for eid in expired:
            del self._elements[eid]

    def render(self) -> dict[str, Any]:
        return {
            "elements": self.list_elements(),
            "timestamp": time.time(),
            "canvas": {"width": self.width, "height": self.height},
        }

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

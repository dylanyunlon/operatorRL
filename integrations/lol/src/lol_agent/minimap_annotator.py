"""
Minimap Annotator — Danger zone and objective annotation on minimap.

Manages spatial annotations with TTL, coordinate conversion between
game world and minimap pixel space, and render output.

Location: integrations/lol/src/lol_agent/minimap_annotator.py

Reference (拿来主義):
  - extensions/vision-bridge/src/vision_bridge/minimap_detector.py: minimap processing
  - integrations/lol/src/lol_agent/danger_zone_detector.py: danger zone patterns
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.lol.minimap_annotator.v1"

# LoL Summoner's Rift game world bounds (approximate)
_GAME_MIN_X, _GAME_MAX_X = 0.0, 15000.0
_GAME_MIN_Y, _GAME_MAX_Y = 0.0, 15000.0


class MinimapAnnotator:
    """Minimap annotation manager for danger zones and objectives.

    Attributes:
        map_width: Minimap display width in pixels.
        map_height: Minimap display height in pixels.
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self, map_width: int = 256, map_height: int = 256) -> None:
        self.map_width = map_width
        self.map_height = map_height
        self._annotations: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def add_danger_zone(
        self, cx: int, cy: int, radius: int, level: str = "mid", ttl_ms: Optional[int] = None,
    ) -> str:
        aid = uuid.uuid4().hex[:8]
        self._annotations[aid] = {
            "id": aid, "type": "danger_zone", "cx": cx, "cy": cy,
            "radius": radius, "level": level, "ttl_ms": ttl_ms,
            "remaining_ms": ttl_ms, "created_at": time.time(),
        }
        self._fire_evolution({"event": "danger_zone_added", "id": aid})
        return aid

    def add_objective(
        self, name: str, cx: int, cy: int, timer_sec: float = 0.0, ttl_ms: Optional[int] = None,
    ) -> str:
        aid = uuid.uuid4().hex[:8]
        self._annotations[aid] = {
            "id": aid, "type": "objective", "name": name, "cx": cx, "cy": cy,
            "timer_sec": timer_sec, "ttl_ms": ttl_ms,
            "remaining_ms": ttl_ms, "created_at": time.time(),
        }
        return aid

    def remove_annotation(self, aid: str) -> None:
        self._annotations.pop(aid, None)

    def clear(self) -> None:
        self._annotations.clear()

    def list_annotations(self) -> list[dict[str, Any]]:
        return list(self._annotations.values())

    def render(self) -> dict[str, Any]:
        return {
            "annotations": self.list_annotations(),
            "map_size": {"width": self.map_width, "height": self.map_height},
            "timestamp": time.time(),
        }

    def game_to_minimap(self, game_x: float, game_y: float) -> tuple[int, int]:
        """Convert game world coordinates to minimap pixel coordinates."""
        mx = int((game_x - _GAME_MIN_X) / (_GAME_MAX_X - _GAME_MIN_X) * self.map_width)
        my = int((game_y - _GAME_MIN_Y) / (_GAME_MAX_Y - _GAME_MIN_Y) * self.map_height)
        mx = max(0, min(self.map_width, mx))
        my = max(0, min(self.map_height, my))
        return mx, my

    def tick(self, elapsed_ms: float) -> None:
        expired = []
        for aid, ann in self._annotations.items():
            if ann["remaining_ms"] is not None:
                ann["remaining_ms"] -= elapsed_ms
                if ann["remaining_ms"] <= 0:
                    expired.append(aid)
        for aid in expired:
            del self._annotations[aid]

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

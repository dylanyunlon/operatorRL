"""
LeagueAI Adapter — Adapts LeagueAI's detection model interface for operatorRL.

Mirrors LeagueAI's detection class format (object_class, bbox, center, w, h)
and LeagueAIFramework's configuration (resolution, threshold, nms_confidence).

Location: extensions/vision-bridge/src/vision_bridge/league_ai_adapter.py

Reference (拿来主義):
  - LeagueAI/LeagueAI_helper.py: detection class + LeagueAIFramework.__init__
  - LeagueAI/yolov3_detector.py: Detector class + model loading
  - LeagueAI class names from YOLO training
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.league_ai_adapter.v1"

# Default class names from LeagueAI YOLO training (拿来主義)
_DEFAULT_CLASS_NAMES = [
    "ally_champion",
    "enemy_champion",
    "ally_minion",
    "enemy_minion",
    "ally_turret",
    "enemy_turret",
    "jungle_monster",
    "dragon",
    "baron",
    "ward",
    "inhibitor",
]


class LeagueAIAdapter:
    """Adapts LeagueAI detection model interface for operatorRL.

    Mirrors LeagueAIFramework's configuration and detection format.
    In production, loads YOLO weights; stub mode returns empty detections.

    Attributes:
        resolution: Input resolution for the detection model.
        threshold: Detection confidence threshold.
        nms_confidence: NMS confidence threshold.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        resolution: int = 416,
        threshold: float = 0.5,
        nms_confidence: float = 0.4,
    ) -> None:
        self.resolution = resolution
        self.threshold = threshold
        self.nms_confidence = nms_confidence
        self._class_names = list(_DEFAULT_CLASS_NAMES)
        self.evolution_callback: Optional[Callable] = None

    def get_class_names(self) -> list[str]:
        """Get list of detectable class names.

        Reference: LeagueAI load_classes(names_file).
        """
        return list(self._class_names)

    def create_detection(
        self,
        object_class: str,
        x_min: int,
        y_min: int,
        x_max: int,
        y_max: int,
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        """Create a detection dict mirroring LeagueAI detection class.

        Reference: LeagueAI_helper.py detection.__init__

        The original class computes:
          w = abs(x_max - x_min)
          h = abs(y_max - y_min)
          x = x_min + int(w/2)  # center x
          y = y_min + int(h/2)  # center y

        Returns:
            Detection dict with bbox, dimensions, center.
        """
        w = abs(x_max - x_min)
        h = abs(y_max - y_min)
        return {
            "object_class": object_class,
            "x_min": x_min,
            "y_min": y_min,
            "x_max": x_max,
            "y_max": y_max,
            "w": w,
            "h": h,
            "x": x_min + w // 2,
            "y": y_min + h // 2,
            "confidence": confidence,
        }

    def detect(
        self, frame: list, width: int, height: int
    ) -> list[dict[str, Any]]:
        """Run detection on a frame (stub implementation).

        In production, this would:
        1. Resize frame to self.resolution
        2. Run YOLO forward pass
        3. Apply NMS with self.nms_confidence
        4. Filter by self.threshold

        Args:
            frame: Flat pixel list.
            width: Frame width.
            height: Frame height.

        Returns:
            List of detection dicts (empty in stub mode).
        """
        return []

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

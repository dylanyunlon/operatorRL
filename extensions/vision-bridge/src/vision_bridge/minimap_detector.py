"""
Minimap Detector — Detects champion icons and objectives on the LoL minimap.

Uses template matching / YOLO-style detection with NMS filtering,
adapted from LeagueAI's detection class and yolov3_detector.

Location: extensions/vision-bridge/src/vision_bridge/minimap_detector.py

Reference (拿来主義):
  - LeagueAI/LeagueAI_helper.py: detection class (object_class, bbox, center)
  - LeagueAI/yolov3_detector.py: Detector class + NMS
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.minimap_detector.v1"


class MinimapDetector:
    """Detects game elements on the minimap region.

    Mirrors LeagueAI detection format with bounding boxes and
    class labels, plus NMS for overlapping detections.

    Attributes:
        confidence_threshold: Minimum confidence for detections.
        champion_classes: List of detectable champion class names.
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self, confidence_threshold: float = 0.5) -> None:
        self.confidence_threshold = confidence_threshold
        self.champion_classes: list[str] = [
            "ally_champion", "enemy_champion", "ally_minion",
            "enemy_minion", "turret", "dragon", "baron",
            "ward", "ping",
        ]
        self.evolution_callback: Optional[Callable] = None

    def detect(
        self, frame: list, frame_width: int, frame_height: int
    ) -> list[dict[str, Any]]:
        """Detect elements in a minimap frame (stub implementation).

        In production, this would run a YOLO or template matching model.

        Args:
            frame: Flat pixel list or nested list.
            frame_width: Frame width in pixels.
            frame_height: Frame height in pixels.

        Returns:
            List of detection dicts.
        """
        # Stub: return empty detections (no model loaded)
        return []

    def create_detection(
        self,
        object_class: str,
        x_min: int,
        y_min: int,
        x_max: int,
        y_max: int,
        score: float = 1.0,
    ) -> dict[str, Any]:
        """Create a detection dict mirroring LeagueAI detection class.

        Reference: LeagueAI_helper.py detection.__init__

        Returns:
            Dict with class, bbox, dimensions, center, score.
        """
        w = abs(x_max - x_min)
        h = abs(y_max - y_min)
        return {
            "class": object_class,
            "x_min": x_min,
            "y_min": y_min,
            "x_max": x_max,
            "y_max": y_max,
            "w": w,
            "h": h,
            "x": x_min + w // 2,
            "y": y_min + h // 2,
            "score": score,
        }

    def get_minimap_region(
        self, screen_width: int, screen_height: int
    ) -> dict[str, int]:
        """Get default minimap region coordinates for LoL.

        Minimap is typically in the bottom-right corner.

        Returns:
            Dict with x, y, width, height of the minimap region.
        """
        # LoL minimap: ~210x210 pixels at bottom-right
        minimap_size = min(screen_width, screen_height) // 5
        return {
            "x": screen_width - minimap_size - 10,
            "y": screen_height - minimap_size - 10,
            "width": minimap_size,
            "height": minimap_size,
        }

    def apply_nms(
        self,
        boxes: list[dict[str, Any]],
        iou_threshold: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Apply Non-Maximum Suppression to filter overlapping detections.

        Reference: LeagueAI yolov3_detector NMS logic.

        Args:
            boxes: List of detection dicts with bbox and score.
            iou_threshold: IoU threshold for suppression.

        Returns:
            Filtered list of detections.
        """
        if not boxes:
            return []

        # Sort by score descending
        sorted_boxes = sorted(boxes, key=lambda b: b.get("score", 0), reverse=True)
        keep = []

        while sorted_boxes:
            best = sorted_boxes.pop(0)
            keep.append(best)

            remaining = []
            for box in sorted_boxes:
                iou = self._compute_iou(best, box)
                if iou < iou_threshold:
                    remaining.append(box)
            sorted_boxes = remaining

        return keep

    def _compute_iou(self, a: dict, b: dict) -> float:
        """Compute Intersection over Union between two boxes."""
        x1 = max(a["x_min"], b["x_min"])
        y1 = max(a["y_min"], b["y_min"])
        x2 = min(a["x_max"], b["x_max"])
        y2 = min(a["y_max"], b["y_max"])

        intersection = max(0, x2 - x1) * max(0, y2 - y1)
        area_a = (a["x_max"] - a["x_min"]) * (a["y_max"] - a["y_min"])
        area_b = (b["x_max"] - b["x_min"]) * (b["y_max"] - b["y_min"])
        union = area_a + area_b - intersection

        if union <= 0:
            return 0.0
        return intersection / union

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

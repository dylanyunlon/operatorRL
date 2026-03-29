"""
Screen Capture Engine — 14fps game screen capture with frame buffering.

Enhanced screen capture with configurable FPS, region selection,
frame buffering, and multiple capture modes.

Location: extensions/vision-bridge/src/vision_bridge/screen_capture_engine.py

Reference (拿来主義):
  - extensions/vision-bridge/src/vision_bridge/screen_capture.py: base ScreenCapture
  - LeagueAI/LeagueAI_helper.py: input_output class (desktop/video/webcam)
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import collections
import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.screen_capture_engine.v1"


class ScreenCaptureEngine:
    """Enhanced screen capture engine with frame buffering.

    Attributes:
        target_fps: Target capture frames per second.
        width: Capture width.
        height: Capture height.
        frame_interval: Seconds between frames (1/fps).
        capture_mode: Capture source ('desktop', 'video', 'webcam').
        evolution_callback: Optional evolution event callback.
    """

    def __init__(
        self,
        target_fps: int = 14,
        width: int = 1920,
        height: int = 1080,
        buffer_size: int = 30,
    ) -> None:
        self.target_fps = target_fps
        self.width = width
        self.height = height
        self.frame_interval: float = 1.0 / target_fps
        self.capture_mode: str = "desktop"
        self._buffer_size = buffer_size
        self._buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=buffer_size)
        self._frame_count: int = 0
        self.region: dict[str, int] = {"x": 0, "y": 0, "w": width, "h": height}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def capture(self) -> dict[str, Any]:
        """Capture a single frame.

        Returns:
            Frame dict with width, height, timestamp, frame_number.
        """
        self._frame_count += 1
        frame = {
            "width": self.region["w"],
            "height": self.region["h"],
            "timestamp": time.time(),
            "frame_number": self._frame_count,
            "capture_mode": self.capture_mode,
            "pixels": None,  # In production: actual pixel data
        }
        self._buffer.append(frame)
        self._fire_evolution({"event": "frame_captured", "frame": self._frame_count})
        return frame

    def set_region(self, x: int, y: int, w: int, h: int) -> None:
        self.region = {"x": x, "y": y, "w": w, "h": h}

    def set_fps(self, fps: int) -> None:
        self.target_fps = fps
        self.frame_interval = 1.0 / fps

    def set_capture_mode(self, mode: str) -> None:
        self.capture_mode = mode

    def get_stats(self) -> dict[str, Any]:
        return {
            "frames_captured": self._frame_count,
            "target_fps": self.target_fps,
            "buffer_size": len(self._buffer),
            "capture_mode": self.capture_mode,
        }

    def get_buffer(self) -> list[dict[str, Any]]:
        return list(self._buffer)

    def reset(self) -> None:
        self._frame_count = 0
        self._buffer.clear()

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

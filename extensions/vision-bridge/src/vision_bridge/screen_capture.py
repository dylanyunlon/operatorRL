"""
Screen Capture — Game screen capture at configurable FPS.

Provides desktop/video capture modes mirroring LeagueAI's input_output class,
with region selection and output resizing.

Location: extensions/vision-bridge/src/vision_bridge/screen_capture.py

Reference (拿来主義):
  - LeagueAI/LeagueAI_helper.py: input_output class (desktop/webcam/video modes)
  - LeagueAI: mss screen capture + PIL/cv2 processing
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.screen_capture.v1"


class ScreenCapture:
    """Captures game screen frames at a target FPS.

    Mirrors LeagueAI input_output class with mode selection
    and frame retrieval, adapted for operatorRL evolution framework.

    Attributes:
        width: Screen/capture width in pixels.
        height: Screen/capture height in pixels.
        target_fps: Target frames per second.
        frame_interval: Time between frames (1/fps).
        input_mode: Capture source ('desktop', 'video', 'webcam').
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        fps: int = 14,
        mode: str = "desktop",
    ) -> None:
        self.width = width
        self.height = height
        self.target_fps = fps
        self.frame_interval = 1.0 / fps
        self.input_mode = mode

        # Output size (default = same as capture)
        self.output_width = width
        self.output_height = height

        # Capture region (default = full screen)
        self.region: dict[str, int] = {
            "top": 0, "left": 0, "width": width, "height": height
        }

        self.evolution_callback: Optional[Callable] = None
        self._last_capture_time: float = 0.0

    def set_capture_region(
        self, top: int, left: int, width: int, height: int
    ) -> None:
        """Set the screen region to capture.

        Reference: LeagueAI input_output.mon dict.

        Args:
            top: Top pixel offset.
            left: Left pixel offset.
            width: Region width.
            height: Region height.
        """
        self.region = {
            "top": top, "left": left, "width": width, "height": height
        }

    def set_output_size(self, width: int, height: int) -> None:
        """Set output frame dimensions (for resizing).

        Reference: LeagueAI get_pixels output_size parameter.
        """
        self.output_width = width
        self.output_height = height

    def capture_frame_stub(self) -> list[int]:
        """Stub frame capture for testing (no actual screen capture).

        Returns a flat list representing an RGB frame.
        In production, this would use mss or cv2.

        Returns:
            Flat list of pixel values (H * W * 3).
        """
        self._last_capture_time = time.time()
        # Return zeros simulating a black frame
        return [0] * (self.output_height * self.output_width * 3)

    def should_capture(self) -> bool:
        """Check if enough time has elapsed for next frame."""
        return (time.time() - self._last_capture_time) >= self.frame_interval

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

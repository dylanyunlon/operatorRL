"""
Visual State Encoder — Encodes video frames into fixed-length feature vectors.

Provides frame → feature vector encoding with configurable dimensionality,
pooling method, and optional L2 normalization. Stub implementation uses
simple average pooling; production would use CNN feature extraction.

Location: extensions/vision-bridge/src/vision_bridge/visual_state_encoder.py

Reference (拿来主義):
  - LeagueAI yolov3_detector.py: image tensor processing
  - ml-agents visual_encoder: CNN-based visual encoding
  - Mortal engine.py: observation tensor format
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.visual_state_encoder.v1"


class VisualStateEncoder:
    """Encodes raw frame data into fixed-length feature vectors.

    Stub implementation uses spatial average pooling + optional normalization.
    Production version would load a pretrained CNN backbone.

    Attributes:
        feature_dim: Output feature vector dimension.
        pooling: Pooling method ('avg' or 'max').
        normalize: Whether to L2-normalize output vectors.
        channels: Number of input color channels (3=RGB, 1=grayscale).
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(
        self,
        feature_dim: int = 128,
        pooling: str = "avg",
        normalize: bool = False,
        channels: int = 3,
    ) -> None:
        self.feature_dim = feature_dim
        self.pooling = pooling
        self.normalize = normalize
        self.channels = channels
        self.evolution_callback: Optional[Callable] = None

    def encode(
        self, frame: list[float], height: int, width: int
    ) -> list[float]:
        """Encode a single frame into a feature vector.

        Args:
            frame: Flat list of pixel values (H * W * C).
            height: Frame height.
            width: Frame width.

        Returns:
            Feature vector of length feature_dim.
        """
        total_pixels = height * width * self.channels

        if not frame or total_pixels == 0:
            vec = [0.0] * self.feature_dim
            if self.normalize and self.feature_dim > 0:
                # Uniform for zero input
                val = 1.0 / math.sqrt(self.feature_dim)
                vec = [val] * self.feature_dim
            return vec

        # Simple spatial pooling: divide frame into feature_dim bins
        frame_vals = frame[:total_pixels] if len(frame) >= total_pixels else frame + [0.0] * (total_pixels - len(frame))

        bin_size = max(1, len(frame_vals) // self.feature_dim)
        vec = []

        for i in range(self.feature_dim):
            start = i * bin_size
            end = min(start + bin_size, len(frame_vals))
            chunk = frame_vals[start:end]

            if not chunk:
                vec.append(0.0)
            elif self.pooling == "max":
                vec.append(max(chunk))
            else:  # avg
                vec.append(sum(chunk) / len(chunk))

        # Pad or truncate to feature_dim
        while len(vec) < self.feature_dim:
            vec.append(0.0)
        vec = vec[:self.feature_dim]

        # Optional L2 normalization
        if self.normalize:
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 1e-8:
                vec = [v / norm for v in vec]

        return vec

    def encode_batch(
        self, frames: list[list[float]], height: int, width: int
    ) -> list[list[float]]:
        """Encode a batch of frames.

        Args:
            frames: List of flat pixel lists.
            height: Frame height.
            width: Frame width.

        Returns:
            List of feature vectors.
        """
        return [self.encode(f, height, width) for f in frames]

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

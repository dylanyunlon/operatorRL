"""
Dota2 Minimap Reader — Read minimap state via pixel analysis.

Detects hero positions, tower status, and creep waves from minimap
frames. Provides coordinate mapping between minimap and game world.

Location: integrations/dota2/src/dota2_agent/dota2_minimap_reader.py

Reference (拿来主義):
  - extensions/vision-bridge/src/vision_bridge/minimap_detector.py: detection patterns
  - extensions/vision-bridge/src/vision_bridge/ocr_extractor.py: OCR patterns
  - dota2bot-OpenHyperAI: Dota2 game state patterns
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "integrations.dota2.dota2_minimap_reader.v1"

# Dota2 world coordinate bounds (approximate)
_WORLD_MIN = -8288.0
_WORLD_MAX = 8288.0


class Dota2MinimapReader:
    """Reads Dota2 minimap frames and extracts structured game state.

    Attributes:
        minimap_size: Expected minimap frame size (square).
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self, minimap_size: int = 256) -> None:
        self.minimap_size = minimap_size
        self._frame_count: int = 0
        self._prev_frame: Optional[list] = None
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def detect_heroes(self, frame: list) -> list[dict[str, Any]]:
        """Detect hero icons on minimap.

        Args:
            frame: 2D pixel grid (list of lists).

        Returns:
            List of hero position dicts.
        """
        # Stub: in production, use color matching / template matching
        heroes: list[dict[str, Any]] = []
        # Placeholder detection logic
        return heroes

    def detect_towers(self, frame: list) -> dict[str, list[dict[str, Any]]]:
        """Detect tower positions and status.

        Returns:
            Dict with 'radiant' and 'dire' tower lists.
        """
        return {"radiant": [], "dire": []}

    def detect_creep_waves(self, frame: list) -> list[dict[str, Any]]:
        """Detect creep wave positions."""
        return []

    def process_frame(self, frame: list) -> dict[str, Any]:
        """Process a minimap frame into structured state.

        Args:
            frame: 2D pixel grid.

        Returns:
            Dict with heroes, towers, creep_waves.
        """
        if not frame:
            return {"heroes": [], "towers": {"radiant": [], "dire": []}, "creep_waves": []}

        self._frame_count += 1
        self._prev_frame = frame

        result = {
            "heroes": self.detect_heroes(frame),
            "towers": self.detect_towers(frame),
            "creep_waves": self.detect_creep_waves(frame),
            "frame_number": self._frame_count,
        }
        self._fire_evolution({"event": "frame_processed", "frame": self._frame_count})
        return result

    def minimap_to_world(self, mx: int, my: int) -> tuple[float, float]:
        """Convert minimap pixel coordinates to Dota2 world coordinates."""
        wx = _WORLD_MIN + (mx / max(self.minimap_size, 1)) * (_WORLD_MAX - _WORLD_MIN)
        wy = _WORLD_MIN + (my / max(self.minimap_size, 1)) * (_WORLD_MAX - _WORLD_MIN)
        return wx, wy

    def world_to_minimap(self, wx: float, wy: float) -> tuple[int, int]:
        """Convert world coordinates to minimap pixel coordinates."""
        mx = int((wx - _WORLD_MIN) / (_WORLD_MAX - _WORLD_MIN) * self.minimap_size)
        my = int((wy - _WORLD_MIN) / (_WORLD_MAX - _WORLD_MIN) * self.minimap_size)
        mx = max(0, min(self.minimap_size, mx))
        my = max(0, min(self.minimap_size, my))
        return mx, my

    def compute_diff(self, frame_a: list, frame_b: list) -> dict[str, Any]:
        """Compute pixel diff between two frames."""
        changed = 0
        total = 0
        rows_a = len(frame_a)
        rows_b = len(frame_b)
        max_rows = max(rows_a, rows_b)
        for r in range(max_rows):
            row_a = frame_a[r] if r < rows_a else []
            row_b = frame_b[r] if r < rows_b else []
            max_cols = max(len(row_a), len(row_b))
            for c in range(max_cols):
                total += 1
                va = row_a[c] if c < len(row_a) else 0
                vb = row_b[c] if c < len(row_b) else 0
                if va != vb:
                    changed += 1
        return {"changed_pixels": changed, "total_pixels": total}

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

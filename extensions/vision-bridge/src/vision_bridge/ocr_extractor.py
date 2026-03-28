"""
OCR Extractor — Extracts game text/numbers from screen regions.

Parses health bars, gold counts, cooldown timers, and game clock
from captured frames using pattern-based text extraction.

Location: extensions/vision-bridge/src/vision_bridge/ocr_extractor.py

Reference (拿来主義):
  - LeagueAI/LeagueAI_helper.py: frame region extraction
  - operatorRL fiddler-bridge combat_calculator: health/gold parsing
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.ocr_extractor.v1"


class OcrExtractor:
    """Extracts numeric and textual game data from screen regions.

    In production, delegates to Tesseract/EasyOCR. Stub mode provides
    regex-based parsing of pre-extracted text strings.

    Attributes:
        evolution_callback: Optional callback for self-evolution events.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable] = None

    def extract_number(self, text: str) -> Optional[int]:
        """Extract the first integer from a text string.

        Args:
            text: Raw text possibly containing numbers.

        Returns:
            First integer found, or None if no number present.
        """
        if not text:
            return None
        match = re.search(r'\d+', text)
        return int(match.group()) if match else None

    def parse_health_text(self, text: str) -> dict[str, int]:
        """Parse health bar text like '500/1000'.

        Args:
            text: Health text in 'current/max' format.

        Returns:
            Dict with 'current' and 'max' keys.
        """
        match = re.match(r'(\d+)\s*/\s*(\d+)', text.strip())
        if match:
            return {"current": int(match.group(1)), "max": int(match.group(2))}
        # Try single number
        num = self.extract_number(text)
        if num is not None:
            return {"current": num, "max": num}
        return {"current": 0, "max": 0}

    def parse_cooldown_text(self, text: str) -> float:
        """Parse cooldown timer text like '12.5s' or '3.0'.

        Args:
            text: Cooldown text.

        Returns:
            Cooldown in seconds as float.
        """
        match = re.search(r'(\d+\.?\d*)', text.strip())
        if match:
            return float(match.group(1))
        return 0.0

    def parse_game_time(self, text: str) -> int:
        """Parse game clock text like '25:30' to total seconds.

        Args:
            text: Game time in 'MM:SS' format.

        Returns:
            Total seconds as integer.
        """
        match = re.match(r'(\d+):(\d+)', text.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            return minutes * 60 + seconds
        return 0

    def get_default_roi_regions(
        self, screen_width: int, screen_height: int
    ) -> dict[str, dict[str, int]]:
        """Get default ROI (Region of Interest) coordinates for LoL HUD.

        Args:
            screen_width: Screen width in pixels.
            screen_height: Screen height in pixels.

        Returns:
            Dict mapping data type to region coordinates.
        """
        return {
            "gold": {
                "x": screen_width // 2 - 50,
                "y": screen_height - 40,
                "width": 100,
                "height": 30,
            },
            "health": {
                "x": screen_width // 2 - 100,
                "y": screen_height - 100,
                "width": 200,
                "height": 20,
            },
            "game_time": {
                "x": screen_width // 2 - 30,
                "y": 5,
                "width": 60,
                "height": 25,
            },
            "level": {
                "x": screen_width // 2 - 130,
                "y": screen_height - 75,
                "width": 25,
                "height": 25,
            },
        }

    # ---- Evolution pattern ----

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            self.evolution_callback(_EVOLUTION_KEY, data)

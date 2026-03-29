"""
Game State OCR — Extract structured game data from screen text.

Parses gold, health, game clock, level, KDA, and CS from
pre-extracted text regions using regex patterns.

Location: extensions/vision-bridge/src/vision_bridge/game_state_ocr.py

Reference (拿来主義):
  - extensions/vision-bridge/src/vision_bridge/ocr_extractor.py: OcrExtractor patterns
  - LeagueAI/LeagueAI_helper.py: frame region extraction
  - operatorRL: evolution callback pattern
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.vision_bridge.game_state_ocr.v1"


class GameStateOCR:
    """Extracts structured game state from OCR text regions.

    Attributes:
        evolution_callback: Optional evolution event callback.
    """

    def __init__(self) -> None:
        self.evolution_callback: Optional[Callable[[dict], None]] = None
        self._process_count: int = 0

    def extract_gold(self, text: str) -> Optional[int]:
        """Extract gold value from text."""
        if not text:
            return None
        cleaned = text.replace(",", "").replace(" ", "").strip()
        match = re.search(r"(\d+)", cleaned)
        if match:
            return int(match.group(1))
        return None

    def extract_health(self, text: str) -> tuple[Optional[int], Optional[int]]:
        """Extract current/max health from 'HP / MaxHP' text."""
        if not text:
            return None, None
        match = re.search(r"(\d+)\s*/\s*(\d+)", text.replace(",", ""))
        if match:
            return int(match.group(1)), int(match.group(2))
        return None, None

    def extract_game_time(self, text: str) -> Optional[int]:
        """Extract game time in seconds from 'MM:SS' text."""
        if not text:
            return None
        match = re.search(r"(\d+):(\d+)", text)
        if match:
            return int(match.group(1)) * 60 + int(match.group(2))
        return None

    def extract_level(self, text: str) -> Optional[int]:
        """Extract level from 'Lv N' or 'Level N' text."""
        if not text:
            return None
        match = re.search(r"(?:Lv|Level|LV)\s*(\d+)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def extract_kda(self, text: str) -> Optional[dict[str, int]]:
        """Extract KDA from 'K/D/A' text."""
        if not text:
            return None
        match = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", text)
        if match:
            return {
                "kills": int(match.group(1)),
                "deaths": int(match.group(2)),
                "assists": int(match.group(3)),
            }
        return None

    def extract_cs(self, text: str) -> Optional[int]:
        """Extract CS count from text."""
        if not text:
            return None
        match = re.search(r"(?:CS|cs)[:\s]*(\d+)", text)
        if match:
            return int(match.group(1))
        # Fallback: just a number
        match = re.search(r"(\d+)", text)
        if match:
            return int(match.group(1))
        return None

    def process_regions(self, regions: dict[str, str]) -> dict[str, Any]:
        """Process all text regions into structured game state.

        Args:
            regions: Dict with keys 'gold', 'health', 'clock', 'level', 'kda'.

        Returns:
            Structured game state dict.
        """
        self._process_count += 1

        hp, max_hp = self.extract_health(regions.get("health", ""))
        kda = self.extract_kda(regions.get("kda", ""))

        state = {
            "gold": self.extract_gold(regions.get("gold", "")),
            "health": hp,
            "max_health": max_hp,
            "game_time": self.extract_game_time(regions.get("clock", "")),
            "level": self.extract_level(regions.get("level", "")),
            "kills": kda["kills"] if kda else None,
            "deaths": kda["deaths"] if kda else None,
            "assists": kda["assists"] if kda else None,
        }

        self._fire_evolution({"event": "regions_processed", "count": self._process_count})
        return state

    def _fire_evolution(self, payload: dict[str, Any]) -> None:
        if self.evolution_callback is not None:
            try:
                self.evolution_callback(payload)
            except Exception as exc:
                logger.warning("Evolution callback error: %s", exc)

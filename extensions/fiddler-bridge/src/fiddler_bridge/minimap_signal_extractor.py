"""
Minimap Signal Extractor — Realtime minimap signal decoding.

Extracts ping events, champion positions, fog-of-war detection,
zone identification, and movement vector computation from minimap data.

Location: extensions/fiddler-bridge/src/fiddler_bridge/minimap_signal_extractor.py
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "fiddler_bridge.minimap_signal_extractor.v1"

# Summoner's Rift map dimensions (units)
_DEFAULT_MAP_WIDTH: int = 14820
_DEFAULT_MAP_HEIGHT: int = 14881


class MinimapSignalExtractor:
    """Minimap signal decoder and position tracker.

    Parses ping events, tracks champion positions, detects fog-of-war
    status, identifies map zones, and computes movement vectors.
    """

    def __init__(
        self,
        map_width: int = _DEFAULT_MAP_WIDTH,
        map_height: int = _DEFAULT_MAP_HEIGHT,
    ) -> None:
        self.map_width = map_width
        self.map_height = map_height
        # champion_name -> {"x": int, "y": int, "timestamp": float}
        self._positions: dict[str, dict[str, Any]] = {}
        # champion_name -> previous position for movement vector
        self._prev_positions: dict[str, dict[str, Any]] = {}
        self.evolution_callback: Optional[Callable[[dict], None]] = None

    def parse_ping(self, event: dict[str, Any]) -> dict[str, Any]:
        """Parse a minimap ping event.

        Args:
            event: Ping event dict with ping_type, x, y, source_player.

        Returns:
            Normalized ping dict.
        """
        return {
            "ping_type": event.get("ping_type", ""),
            "x": event.get("x", 0),
            "y": event.get("y", 0),
            "source_player": event.get("source_player", ""),
        }

    def normalize_coordinates(
        self, x: int, y: int
    ) -> dict[str, float]:
        """Normalize map coordinates to [0, 1] range.

        Args:
            x: Raw x coordinate.
            y: Raw y coordinate.

        Returns:
            Dict with normalized x and y.
        """
        return {
            "x": x / self.map_width if self.map_width > 0 else 0.0,
            "y": y / self.map_height if self.map_height > 0 else 0.0,
        }

    def identify_zone(
        self, x_norm: float, y_norm: float
    ) -> str:
        """Identify map zone from normalized coordinates.

        Summoner's Rift zones (approximate):
        - top: upper-left corridor
        - mid: diagonal center
        - bot: lower-right corridor
        - jungle_top/jungle_bot: jungle quadrants
        - river_top/river_bot: river segments
        - base_blue/base_red: spawn areas

        Args:
            x_norm: Normalized x [0, 1].
            y_norm: Normalized y [0, 1].

        Returns:
            Zone identifier string.
        """
        if x_norm < 0.15 and y_norm < 0.15:
            return "base_blue"
        if x_norm > 0.85 and y_norm > 0.85:
            return "base_red"
        if x_norm < 0.25 and y_norm > 0.6:
            return "top"
        if x_norm > 0.6 and y_norm < 0.25:
            return "bot"
        if abs(x_norm - y_norm) < 0.15 and 0.3 < x_norm < 0.7:
            return "mid"
        if 0.4 < x_norm < 0.6 and y_norm > 0.5:
            return "river_top"
        if 0.4 < x_norm < 0.6 and y_norm < 0.5:
            return "river_bot"
        if x_norm < 0.5 and y_norm > 0.3:
            return "jungle_top"
        return "jungle_bot"

    def update_champion_position(
        self, champion: str, x: int, y: int, timestamp: float
    ) -> None:
        """Update a champion's tracked position.

        Args:
            champion: Champion name.
            x: X coordinate.
            y: Y coordinate.
            timestamp: Game time when position was observed.
        """
        if champion in self._positions:
            self._prev_positions[champion] = dict(self._positions[champion])
        self._positions[champion] = {
            "x": x, "y": y, "timestamp": timestamp,
        }

    def get_champion_position(
        self, champion: str
    ) -> Optional[dict[str, Any]]:
        """Get last known position for a champion.

        Args:
            champion: Champion name.

        Returns:
            Position dict or None if unknown.
        """
        return self._positions.get(champion)

    def is_in_fog(
        self,
        champion: str,
        current_time: float,
        fog_timeout: float = 10.0,
    ) -> bool:
        """Check if a champion is in fog of war.

        A champion is considered fogged if their position hasn't been
        updated within fog_timeout seconds.

        Args:
            champion: Champion name.
            current_time: Current game time.
            fog_timeout: Seconds before considering champion fogged.

        Returns:
            True if fogged.
        """
        pos = self._positions.get(champion)
        if pos is None:
            return True
        return (current_time - pos["timestamp"]) > fog_timeout

    def compute_movement_vector(
        self, champion: str
    ) -> dict[str, float]:
        """Compute movement vector from previous to current position.

        Args:
            champion: Champion name.

        Returns:
            Dict with dx, dy (zero if insufficient data).
        """
        curr = self._positions.get(champion)
        prev = self._prev_positions.get(champion)
        if curr is None or prev is None:
            return {"dx": 0.0, "dy": 0.0}
        return {
            "dx": float(curr["x"] - prev["x"]),
            "dy": float(curr["y"] - prev["y"]),
        }

    def _fire_evolution(self, data: dict[str, Any]) -> None:
        """Fire evolution callback."""
        if self.evolution_callback is None:
            return
        enriched = {
            "module": "minimap_signal_extractor",
            "timestamp": time.time(),
            **data,
        }
        try:
            self.evolution_callback(enriched)
        except Exception as exc:
            logger.warning("Evolution callback error: %s", exc)

"""
FogEstimator — Fog of war estimation from known ward positions.
================================================================

Estimates enemy team visibility and fog-of-war state based on known
ward placements, champion positions, and tower vision ranges.
Provides a probabilistic estimate of which map areas are visible
to the enemy team.

Architecture position:
    modules/localization/fog_estimator.py   ← YOU ARE HERE
    ├─ Reads: /lol/ward_state (from ward_tracker)
    ├─ Reads: /lol/map_awareness (player positions)
    ├─ Publishes: /lol/fog_estimate (FogMap)
    └─ Used by: modules/planning/ (safe pathing decisions)

Apollo reference:
    modules/perception/ — obstacle grid mapping
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_GRID_RESOLUTION: int = 32  # 32x32 grid over Summoner's Rift
_MAP_SIZE: float = 15000.0
_CELL_SIZE: float = _MAP_SIZE / _GRID_RESOLUTION

# Vision ranges (game units)
_CHAMPION_VISION_RANGE: float = 1200.0
_WARD_VISION_RANGE: float = 1100.0
_CONTROL_WARD_RANGE: float = 900.0
_TOWER_VISION_RANGE: float = 1095.0
_MINION_VISION_RANGE: float = 800.0

# Fog decay rate (seconds for uncertainty to reach max)
_FOG_DECAY_RATE_S: float = 30.0


class VisionSource(Enum):
    CHAMPION = auto()
    WARD = auto()
    CONTROL_WARD = auto()
    TOWER = auto()
    MINION = auto()


@dataclass
class VisionPoint:
    """A single vision-providing entity."""
    x: float
    y: float
    source: VisionSource
    range: float
    team: str
    expires_at: float = 0.0  # 0 = permanent
    is_active: bool = True

    @property
    def is_expired(self) -> bool:
        if self.expires_at <= 0:
            return False
        return time.time() > self.expires_at


@dataclass
class FogCell:
    """Single cell in the fog grid."""
    row: int
    col: int
    friendly_visible: bool = False
    enemy_visible: bool = False
    enemy_last_seen: float = 0.0
    fog_confidence: float = 1.0  # 0=clear, 1=fully fogged

    def to_dict(self) -> Dict[str, Any]:
        return {
            "r": self.row, "c": self.col,
            "fv": self.friendly_visible,
            "ev": self.enemy_visible,
            "fc": round(self.fog_confidence, 2),
        }


class FogMap:
    """2D grid representing fog of war estimate.

    Each cell stores whether it's visible to friendly/enemy teams
    and a confidence value for the fog estimate.
    """

    def __init__(self, resolution: int = _GRID_RESOLUTION) -> None:
        self._resolution = resolution
        self._grid: List[List[FogCell]] = [
            [FogCell(row=r, col=c) for c in range(resolution)]
            for r in range(resolution)
        ]

    def cell_at(self, row: int, col: int) -> Optional[FogCell]:
        if 0 <= row < self._resolution and 0 <= col < self._resolution:
            return self._grid[row][col]
        return None

    def world_to_grid(self, x: float, y: float) -> Tuple[int, int]:
        col = int(max(0, min(self._resolution - 1, x / _CELL_SIZE)))
        row = int(max(0, min(self._resolution - 1, y / _CELL_SIZE)))
        return (row, col)

    def grid_to_world_center(self, row: int, col: int) -> Tuple[float, float]:
        x = (col + 0.5) * _CELL_SIZE
        y = (row + 0.5) * _CELL_SIZE
        return (x, y)

    def clear(self) -> None:
        for row in self._grid:
            for cell in row:
                cell.friendly_visible = False
                cell.enemy_visible = False

    def apply_vision(
        self, x: float, y: float, vision_range: float, is_friendly: bool
    ) -> int:
        """Apply circular vision from a point, returning cells illuminated."""
        center_row, center_col = self.world_to_grid(x, y)
        radius_cells = int(math.ceil(vision_range / _CELL_SIZE))
        illuminated = 0

        for dr in range(-radius_cells, radius_cells + 1):
            for dc in range(-radius_cells, radius_cells + 1):
                r = center_row + dr
                c = center_col + dc
                if r < 0 or r >= self._resolution:
                    continue
                if c < 0 or c >= self._resolution:
                    continue

                cx, cy = self.grid_to_world_center(r, c)
                dist = math.sqrt((cx - x) ** 2 + (cy - y) ** 2)
                if dist <= vision_range:
                    cell = self._grid[r][c]
                    if is_friendly:
                        cell.friendly_visible = True
                    else:
                        cell.enemy_visible = True
                        cell.enemy_last_seen = time.time()
                        cell.fog_confidence = 0.0
                    illuminated += 1

        return illuminated

    def decay_fog(self) -> None:
        """Apply time-based fog decay to all cells."""
        now = time.time()
        for row in self._grid:
            for cell in row:
                if not cell.enemy_visible and cell.enemy_last_seen > 0:
                    elapsed = now - cell.enemy_last_seen
                    cell.fog_confidence = min(
                        1.0, elapsed / _FOG_DECAY_RATE_S
                    )

    def get_danger_zones(self, threshold: float = 0.3) -> List[Tuple[int, int]]:
        """Get cells where enemy might be (low fog confidence)."""
        result = []
        for row in self._grid:
            for cell in row:
                if cell.fog_confidence < threshold and not cell.friendly_visible:
                    result.append((cell.row, cell.col))
        return result

    def friendly_coverage_ratio(self) -> float:
        total = self._resolution * self._resolution
        visible = sum(
            1 for row in self._grid for cell in row
            if cell.friendly_visible
        )
        return visible / total if total > 0 else 0.0

    def enemy_estimated_coverage(self) -> float:
        total = self._resolution * self._resolution
        visible = sum(
            1 for row in self._grid for cell in row
            if cell.enemy_visible
        )
        return visible / total if total > 0 else 0.0

    def to_summary(self) -> Dict[str, Any]:
        return {
            "resolution": self._resolution,
            "friendly_coverage": round(self.friendly_coverage_ratio(), 3),
            "enemy_coverage": round(self.enemy_estimated_coverage(), 3),
            "danger_zones": len(self.get_danger_zones()),
        }


class FogEstimator:
    """Stateful fog of war estimator.

    Maintains a FogMap and updates it based on vision sources
    (wards, champions, towers). Provides safety assessments for
    path planning.

    Usage::

        estimator = FogEstimator(my_team="ORDER")
        estimator.add_vision_source(VisionPoint(...))
        estimator.update()
        safe = estimator.is_safe_zone(x, y)
    """

    def __init__(self, my_team: str = "ORDER") -> None:
        self._my_team = my_team
        self._fog_map = FogMap()
        self._vision_sources: List[VisionPoint] = []
        self._update_count: int = 0

    def add_vision_source(self, source: VisionPoint) -> None:
        self._vision_sources.append(source)

    def clear_sources(self) -> None:
        self._vision_sources.clear()

    def update(self) -> None:
        """Recompute fog map from all active vision sources."""
        self._fog_map.clear()

        # Remove expired sources
        self._vision_sources = [
            s for s in self._vision_sources
            if not s.is_expired and s.is_active
        ]

        for source in self._vision_sources:
            is_friendly = source.team == self._my_team
            self._fog_map.apply_vision(
                source.x, source.y, source.range, is_friendly
            )

        self._fog_map.decay_fog()
        self._update_count += 1

    def is_safe_zone(self, x: float, y: float) -> bool:
        """Check if a position is in a zone with good friendly vision."""
        row, col = self._fog_map.world_to_grid(x, y)
        cell = self._fog_map.cell_at(row, col)
        if cell is None:
            return False
        return cell.friendly_visible and not cell.enemy_visible

    def danger_level(self, x: float, y: float) -> float:
        """0.0 = safe, 1.0 = maximum danger."""
        row, col = self._fog_map.world_to_grid(x, y)
        cell = self._fog_map.cell_at(row, col)
        if cell is None:
            return 1.0

        if cell.friendly_visible:
            return 0.1 if not cell.enemy_visible else 0.5
        return cell.fog_confidence

    @property
    def fog_map(self) -> FogMap:
        return self._fog_map

    def stats(self) -> Dict[str, Any]:
        return {
            "my_team": self._my_team,
            "vision_sources": len(self._vision_sources),
            "update_count": self._update_count,
            **self._fog_map.to_summary(),
        }

"""
CoordinateTransform — Minimap/game/screen coordinate transforms.
=================================================================

Converts between game-world coordinates (Summoner's Rift 0-15000),
normalized [0,1] coordinates, minimap pixel coordinates, and screen
overlay pixel coordinates. Essential for any spatial reasoning.

Architecture position:
    modules/transform/coordinate_transform.py   ← YOU ARE HERE
    ├─ Used by: modules/localization/ (zone classification)
    ├─ Used by: modules/control/overlay/ (screen position rendering)
    └─ Used by: modules/perception/minimap/ (minimap pixel → game coords)

Apollo reference:
    modules/transform/transform_broadcaster.cc
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

# Summoner's Rift game-coordinate bounds
_GAME_MIN_X: float = -120.0
_GAME_MAX_X: float = 14870.0
_GAME_MIN_Y: float = -120.0
_GAME_MAX_Y: float = 14980.0
_GAME_WIDTH: float = _GAME_MAX_X - _GAME_MIN_X
_GAME_HEIGHT: float = _GAME_MAX_Y - _GAME_MIN_Y

# Standard minimap pixel sizes
_MINIMAP_DEFAULT_PX: int = 280


@dataclass
class Point2D:
    """Immutable 2D point with arithmetic support."""
    x: float = 0.0
    y: float = 0.0

    def distance_to(self, other: "Point2D") -> float:
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def midpoint(self, other: "Point2D") -> "Point2D":
        return Point2D((self.x + other.x) / 2, (self.y + other.y) / 2)

    def offset(self, dx: float, dy: float) -> "Point2D":
        return Point2D(self.x + dx, self.y + dy)

    def scale(self, factor: float) -> "Point2D":
        return Point2D(self.x * factor, self.y * factor)

    def clamp(self, min_x: float, min_y: float,
              max_x: float, max_y: float) -> "Point2D":
        return Point2D(
            max(min_x, min(max_x, self.x)),
            max(min_y, min(max_y, self.y)),
        )

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)

    def __repr__(self) -> str:
        return f"Point2D({self.x:.1f}, {self.y:.1f})"


@dataclass
class BoundingBox:
    """Axis-aligned bounding box."""
    min_x: float = 0.0
    min_y: float = 0.0
    max_x: float = 0.0
    max_y: float = 0.0

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center(self) -> Point2D:
        return Point2D(
            (self.min_x + self.max_x) / 2,
            (self.min_y + self.max_y) / 2,
        )

    def contains(self, point: Point2D) -> bool:
        return (self.min_x <= point.x <= self.max_x
                and self.min_y <= point.y <= self.max_y)

    def expanded(self, margin: float) -> "BoundingBox":
        return BoundingBox(
            self.min_x - margin, self.min_y - margin,
            self.max_x + margin, self.max_y + margin,
        )


class CoordinateTransform:
    """Bidirectional coordinate transformer for Summoner's Rift.

    Supports four coordinate frames:
    1. **Game** — raw game units (0–15000)
    2. **Normalized** — [0, 1] range
    3. **Minimap** — minimap pixel coordinates
    4. **Screen** — full-screen overlay pixel coordinates

    Usage::

        tf = CoordinateTransform(
            minimap_size=280,
            screen_width=1920,
            screen_height=1080,
            minimap_origin_screen=(1640, 800),
        )
        game_pt = Point2D(7500, 7500)  # center of map
        mini_pt = tf.game_to_minimap(game_pt)
        screen_pt = tf.game_to_screen(game_pt)
        back = tf.minimap_to_game(mini_pt)
    """

    def __init__(
        self,
        minimap_size: int = _MINIMAP_DEFAULT_PX,
        screen_width: int = 1920,
        screen_height: int = 1080,
        minimap_origin_screen: Tuple[int, int] = (1640, 800),
        game_bounds: Optional[BoundingBox] = None,
    ) -> None:
        self._minimap_size = minimap_size
        self._screen_w = screen_width
        self._screen_h = screen_height
        self._minimap_origin = minimap_origin_screen

        self._game_bounds = game_bounds or BoundingBox(
            min_x=_GAME_MIN_X, min_y=_GAME_MIN_Y,
            max_x=_GAME_MAX_X, max_y=_GAME_MAX_Y,
        )

    # ─── Game ↔ Normalized ───────────────────────────────────────────────

    def game_to_normalized(self, pt: Point2D) -> Point2D:
        nx = (pt.x - self._game_bounds.min_x) / self._game_bounds.width
        ny = (pt.y - self._game_bounds.min_y) / self._game_bounds.height
        return Point2D(
            max(0.0, min(1.0, nx)),
            max(0.0, min(1.0, ny)),
        )

    def normalized_to_game(self, pt: Point2D) -> Point2D:
        gx = pt.x * self._game_bounds.width + self._game_bounds.min_x
        gy = pt.y * self._game_bounds.height + self._game_bounds.min_y
        return Point2D(gx, gy)

    # ─── Game ↔ Minimap ──────────────────────────────────────────────────

    def game_to_minimap(self, pt: Point2D) -> Point2D:
        norm = self.game_to_normalized(pt)
        # Minimap Y is inverted (top of minimap = high Y in game)
        px = norm.x * self._minimap_size
        py = (1.0 - norm.y) * self._minimap_size
        return Point2D(px, py)

    def minimap_to_game(self, pt: Point2D) -> Point2D:
        nx = pt.x / self._minimap_size
        ny = 1.0 - (pt.y / self._minimap_size)
        return self.normalized_to_game(Point2D(nx, ny))

    # ─── Game ↔ Screen ───────────────────────────────────────────────────

    def game_to_screen(self, pt: Point2D) -> Point2D:
        mini = self.game_to_minimap(pt)
        sx = self._minimap_origin[0] + mini.x
        sy = self._minimap_origin[1] + mini.y
        return Point2D(sx, sy)

    def screen_to_game(self, pt: Point2D) -> Optional[Point2D]:
        # Check if point is within minimap region
        mx = pt.x - self._minimap_origin[0]
        my = pt.y - self._minimap_origin[1]
        if mx < 0 or mx > self._minimap_size:
            return None
        if my < 0 or my > self._minimap_size:
            return None
        return self.minimap_to_game(Point2D(mx, my))

    # ─── Minimap ↔ Screen ────────────────────────────────────────────────

    def minimap_to_screen(self, pt: Point2D) -> Point2D:
        return Point2D(
            self._minimap_origin[0] + pt.x,
            self._minimap_origin[1] + pt.y,
        )

    def screen_to_minimap(self, pt: Point2D) -> Optional[Point2D]:
        mx = pt.x - self._minimap_origin[0]
        my = pt.y - self._minimap_origin[1]
        if 0 <= mx <= self._minimap_size and 0 <= my <= self._minimap_size:
            return Point2D(mx, my)
        return None

    # ─── Distance helpers ────────────────────────────────────────────────

    def game_distance(self, a: Point2D, b: Point2D) -> float:
        return a.distance_to(b)

    def game_distance_to_units(self, game_dist: float) -> float:
        """Convert game-unit distance to normalized [0,1] fraction of map."""
        diag = math.sqrt(
            self._game_bounds.width ** 2 + self._game_bounds.height ** 2
        )
        return game_dist / diag if diag > 0 else 0.0

    def is_in_game_bounds(self, pt: Point2D) -> bool:
        return self._game_bounds.contains(pt)

    # ─── Batch transforms ────────────────────────────────────────────────

    def game_to_minimap_batch(
        self, points: list
    ) -> list:
        return [self.game_to_minimap(p) for p in points]

    # ─── Config update ───────────────────────────────────────────────────

    def update_screen_config(
        self,
        screen_width: int,
        screen_height: int,
        minimap_origin: Tuple[int, int],
        minimap_size: int,
    ) -> None:
        self._screen_w = screen_width
        self._screen_h = screen_height
        self._minimap_origin = minimap_origin
        self._minimap_size = minimap_size

    def config_dict(self) -> dict:
        return {
            "minimap_size": self._minimap_size,
            "screen": (self._screen_w, self._screen_h),
            "minimap_origin": self._minimap_origin,
            "game_bounds": {
                "min": (self._game_bounds.min_x, self._game_bounds.min_y),
                "max": (self._game_bounds.max_x, self._game_bounds.max_y),
            },
        }

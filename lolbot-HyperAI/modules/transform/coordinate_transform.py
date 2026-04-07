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


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: CoordinateTransformV2 — minimap projection, champion-relative
# coordinates, zone classification, and distance metrics for game analysis
# ═══════════════════════════════════════════════════════════════════════════

# Summoner's Rift coordinate bounds (from League client)
_SR_MIN_X = -120.0
_SR_MAX_X = 14870.0
_SR_MIN_Y = -120.0
_SR_MAX_Y = 14980.0
_SR_WIDTH = _SR_MAX_X - _SR_MIN_X
_SR_HEIGHT = _SR_MAX_Y - _SR_MIN_Y

# Minimap pixel dimensions (standard)
_MINIMAP_PX = 512


@dataclass
class MinimapPoint:
    """A point on the minimap in pixel coordinates.

    Claude21: Used for overlay rendering and minimap analysis.
    """
    px: int
    py: int

    def to_dict(self) -> Dict[str, Any]:
        return {"px": self.px, "py": self.py}


@dataclass
class GamePoint:
    """A point in game world coordinates."""
    x: float
    y: float

    def distance_to(self, other: "GamePoint") -> float:
        return math.sqrt((self.x - other.x) ** 2 + (self.y - other.y) ** 2)

    def midpoint(self, other: "GamePoint") -> "GamePoint":
        return GamePoint(x=(self.x + other.x) / 2, y=(self.y + other.y) / 2)

    def to_dict(self) -> Dict[str, Any]:
        return {"x": round(self.x, 1), "y": round(self.y, 1)}


# Key landmark positions on Summoner's Rift
_LANDMARKS: Dict[str, GamePoint] = {
    "blue_nexus": GamePoint(394.0, 461.0),
    "red_nexus": GamePoint(14340.0, 14391.0),
    "dragon_pit": GamePoint(9866.0, 4414.0),
    "baron_pit": GamePoint(4966.0, 10542.0),
    "blue_red_buff": GamePoint(7862.0, 4112.0),
    "blue_blue_buff": GamePoint(3832.0, 7908.0),
    "red_red_buff": GamePoint(7082.0, 10838.0),
    "red_blue_buff": GamePoint(10952.0, 6990.0),
    "mid_center": GamePoint(7400.0, 7600.0),
    "top_river_bush": GamePoint(4562.0, 11752.0),
    "bot_river_bush": GamePoint(10342.0, 3292.0),
}


class CoordinateTransformV2(CoordinateTransform):
    """Production-grade coordinate transform with minimap projection,
    champion-relative coordinates, and spatial analysis utilities.

    Claude21: Extends CoordinateTransform with:
    - World→minimap pixel projection and reverse
    - Champion-relative coordinate system (for normalized features)
    - Distance to landmarks (dragon, baron, nexus, etc.)
    - Zone classification from coordinates
    - Geometric utilities (midpoint, clustering, area)

    Apollo reference: modules/transform/transform_component.cc handles
    coordinate transforms between vehicle frame, world frame, and HD map.

    Usage::
        transform = CoordinateTransformV2()
        mp = transform.world_to_minimap(x=9866, y=4414)
        dist = transform.distance_to_landmark(x, y, "dragon_pit")
    """

    def world_to_minimap(self, x: float, y: float) -> MinimapPoint:
        """Project game world coordinates to minimap pixel coordinates.

        Claude21: The minimap is a top-down view with Y-axis inverted
        (origin top-left). The game world has origin at bottom-left.
        """
        # Normalize to [0, 1]
        nx = (x - _SR_MIN_X) / _SR_WIDTH
        ny = (y - _SR_MIN_Y) / _SR_HEIGHT

        # Clamp
        nx = max(0.0, min(1.0, nx))
        ny = max(0.0, min(1.0, ny))

        # Map to pixel (Y inverted for minimap)
        px = int(nx * _MINIMAP_PX)
        py = int((1.0 - ny) * _MINIMAP_PX)

        return MinimapPoint(px=px, py=py)

    def minimap_to_world(self, px: int, py: int) -> GamePoint:
        """Reverse projection: minimap pixel to game world coordinates."""
        nx = px / _MINIMAP_PX
        ny = 1.0 - (py / _MINIMAP_PX)

        x = _SR_MIN_X + nx * _SR_WIDTH
        y = _SR_MIN_Y + ny * _SR_HEIGHT

        return GamePoint(x=x, y=y)

    def to_champion_relative(
        self, target_x: float, target_y: float,
        champion_x: float, champion_y: float,
    ) -> GamePoint:
        """Transform to champion-relative coordinates.

        Claude21: Useful for ML features — all positions expressed
        relative to the active player, making features translation-invariant.
        """
        return GamePoint(
            x=target_x - champion_x,
            y=target_y - champion_y,
        )

    def distance_to_landmark(
        self, x: float, y: float, landmark: str,
    ) -> float:
        """Compute distance from a position to a named landmark."""
        lm = _LANDMARKS.get(landmark)
        if not lm:
            return float("inf")
        return math.sqrt((x - lm.x) ** 2 + (y - lm.y) ** 2)

    def nearest_landmark(
        self, x: float, y: float,
    ) -> Tuple[str, float]:
        """Find the nearest landmark to a position.

        Returns (landmark_name, distance).
        """
        best_name = ""
        best_dist = float("inf")
        for name, lm in _LANDMARKS.items():
            d = math.sqrt((x - lm.x) ** 2 + (y - lm.y) ** 2)
            if d < best_dist:
                best_dist = d
                best_name = name
        return best_name, best_dist

    def lane_proximity(self, x: float, y: float) -> Dict[str, float]:
        """Compute proximity score to each lane.

        Claude21: Returns dict of lane→proximity where 1.0 = in lane,
        0.0 = far away. Used for classifying champion positions.
        """
        # Lane center lines (approximate)
        lanes = {
            "top": [(800, 14000), (800, 800), (800, 800)],
            "mid": [(800, 800), (7400, 7600), (14200, 14200)],
            "bot": [(14200, 800), (14200, 14200), (14200, 14200)],
        }
        # Simple: distance to lane midpoint
        proximity = {}
        for lane, points in lanes.items():
            mid = points[len(points) // 2]
            d = math.sqrt((x - mid[0]) ** 2 + (y - mid[1]) ** 2)
            proximity[lane] = max(0.0, 1.0 - d / 10000.0)
        return proximity

    def group_champions(
        self, positions: List[Tuple[str, float, float]],
        cluster_radius: float = 2000.0,
    ) -> List[List[str]]:
        """Group champions into clusters by proximity.

        Claude21: Simple single-linkage clustering. Champions within
        cluster_radius of any cluster member are grouped together.
        Useful for detecting teamfight formations.

        Args:
            positions: List of (name, x, y) tuples.
            cluster_radius: Max distance to join a cluster.

        Returns:
            List of name-lists, one per cluster.
        """
        if not positions:
            return []

        assigned = [False] * len(positions)
        clusters: List[List[str]] = []

        for i, (name_i, xi, yi) in enumerate(positions):
            if assigned[i]:
                continue
            cluster = [name_i]
            assigned[i] = True

            # Expand cluster
            queue = [(xi, yi)]
            while queue:
                cx, cy = queue.pop(0)
                for j, (name_j, xj, yj) in enumerate(positions):
                    if assigned[j]:
                        continue
                    d = math.sqrt((cx - xj) ** 2 + (cy - yj) ** 2)
                    if d <= cluster_radius:
                        cluster.append(name_j)
                        assigned[j] = True
                        queue.append((xj, yj))

            clusters.append(cluster)

        return clusters

    @staticmethod
    def get_landmark(name: str) -> Optional[GamePoint]:
        """Get a landmark position by name."""
        return _LANDMARKS.get(name)

    @staticmethod
    def all_landmarks() -> Dict[str, Dict[str, float]]:
        return {name: pt.to_dict() for name, pt in _LANDMARKS.items()}

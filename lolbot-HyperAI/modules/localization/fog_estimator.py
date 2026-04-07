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


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: FogEstimatorV2 — ward-aware fog decay, vision score tracking,
# threat assessment in fog zones, and fog-based gank prediction
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class FogZone:
    """A zone of the map with fog-of-war state.

    Claude21: Tracks how long each zone has been in fog and what
    threats might be lurking there based on last-known positions.
    """
    zone_name: str
    in_fog: bool = True
    fog_duration_s: float = 0.0
    last_seen_time: float = 0.0
    last_known_enemies: List[str] = field(default_factory=list)
    threat_level: float = 0.0   # 0-1, higher = more dangerous

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone": self.zone_name,
            "in_fog": self.in_fog,
            "fog_s": round(self.fog_duration_s, 1),
            "threats": self.last_known_enemies,
            "threat_level": round(self.threat_level, 3),
        }


@dataclass
class VisionScore:
    """Team vision scoring.

    Claude21: Quantifies how well each team maintains vision control.
    """
    blue_vision: float = 0.0    # 0-1
    red_vision: float = 0.0     # 0-1
    blue_wards_active: int = 0
    red_wards_active: int = 0
    blue_fog_zones: int = 0     # zones in fog for blue team
    red_fog_zones: int = 0
    vision_advantage: str = "EVEN"  # BLUE, RED, EVEN

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blue_vision": round(self.blue_vision, 3),
            "red_vision": round(self.red_vision, 3),
            "blue_wards": self.blue_wards_active,
            "red_wards": self.red_wards_active,
            "advantage": self.vision_advantage,
        }


@dataclass
class GankPrediction:
    """Predicted gank from fog analysis.

    Claude21: When an enemy champion disappears into fog near a lane,
    predict potential gank with probability based on fog duration,
    champion role, and game state.
    """
    target_lane: str
    predicted_ganker: str
    probability: float
    fog_duration_s: float
    last_seen_zone: str
    game_time: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lane": self.target_lane,
            "ganker": self.predicted_ganker,
            "prob": round(self.probability, 3),
            "fog_s": round(self.fog_duration_s, 1),
            "from_zone": self.last_seen_zone,
        }


# Role-based gank threat multipliers
_GANK_THREAT_BY_ROLE: Dict[str, float] = {
    "jungle": 1.0,    # Junglers gank most
    "mid": 0.6,       # Mid roams frequently
    "support": 0.5,   # Support roams
    "top": 0.2,       # Top rarely roams
    "adc": 0.1,       # ADC almost never roams
}

# Fog duration thresholds for threat escalation
_FOG_THREAT_CURVE = [
    (5.0, 0.1),    # 5s in fog = low threat
    (15.0, 0.3),   # 15s = moderate
    (30.0, 0.6),   # 30s = high
    (60.0, 0.9),   # 60s = very high
]


class FogEstimatorV2(FogEstimator):
    """Production-grade fog estimator with ward awareness, threat scoring,
    vision metrics, and gank prediction from fog analysis.

    Claude21: Extends FogEstimator with:
    - Per-zone fog duration tracking with threat escalation
    - Ward-aware vision scoring per team
    - Gank prediction from fog patterns (enemy disappears near lane)
    - Vision advantage computation for planning
    - Integration with MapAwarenessV2 zone definitions

    Apollo reference: modules/perception/camera_detection_multi_stage/
    handles visibility estimation from sensor coverage maps.

    Usage::
        fog = FogEstimatorV2()
        fog.update_visibility(visible_zones, game_time)
        fog.update_enemy_positions(enemies, game_time)
        ganks = fog.predict_ganks(game_time)
        vision = fog.compute_vision_score()
    """

    _ZONE_NAMES = [
        "top_lane", "mid_lane", "bot_lane",
        "blue_jungle_top", "blue_jungle_bot",
        "red_jungle_top", "red_jungle_bot",
        "dragon_pit", "baron_pit",
        "river_top", "river_bot",
        "blue_base", "red_base",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._fog_zones: Dict[str, FogZone] = {
            name: FogZone(zone_name=name) for name in self._ZONE_NAMES
        }
        self._enemy_last_zone: Dict[str, str] = {}
        self._enemy_last_seen: Dict[str, float] = {}
        self._enemy_roles: Dict[str, str] = {}
        self._blue_wards: List[Tuple[str, float]] = []  # (zone, expire_time)
        self._red_wards: List[Tuple[str, float]] = []

    def update_visibility(
        self, visible_zones: List[str], game_time: float,
    ) -> None:
        """Update which zones are currently visible.

        Claude21: Called each tick with the set of zones that have
        allied champion or ward vision.
        """
        for name, fz in self._fog_zones.items():
            if name in visible_zones:
                fz.in_fog = False
                fz.last_seen_time = game_time
                fz.fog_duration_s = 0.0
            else:
                fz.in_fog = True
                if fz.last_seen_time > 0:
                    fz.fog_duration_s = game_time - fz.last_seen_time
                else:
                    fz.fog_duration_s = game_time

    def update_enemy_positions(
        self, enemies: List[Dict[str, Any]], game_time: float,
    ) -> None:
        """Update last-known enemy positions.

        Args:
            enemies: List of dicts with 'name', 'zone', 'role', 'visible'.
        """
        for enemy in enemies:
            name = enemy.get("name", "")
            zone = enemy.get("zone", "")
            role = enemy.get("role", "")
            visible = enemy.get("visible", False)

            if role:
                self._enemy_roles[name] = role

            if visible and zone:
                self._enemy_last_zone[name] = zone
                self._enemy_last_seen[name] = game_time

                # Update fog zone enemy tracking
                for fz in self._fog_zones.values():
                    if name in fz.last_known_enemies:
                        fz.last_known_enemies.remove(name)
                if zone in self._fog_zones:
                    fz = self._fog_zones[zone]
                    if name not in fz.last_known_enemies:
                        fz.last_known_enemies.append(name)

    def _compute_threat(self, fz: FogZone, game_time: float) -> float:
        """Compute threat level for a fog zone.

        Claude21: Threat increases with fog duration and the roles of
        enemies last seen near this zone.
        """
        if not fz.in_fog:
            return 0.0

        # Duration-based threat
        duration_threat = 0.0
        for threshold, threat in _FOG_THREAT_CURVE:
            if fz.fog_duration_s >= threshold:
                duration_threat = threat

        # Enemy-based threat
        enemy_threat = 0.0
        for enemy_name in fz.last_known_enemies:
            role = self._enemy_roles.get(enemy_name, "")
            role_mult = _GANK_THREAT_BY_ROLE.get(role, 0.3)
            time_since = game_time - self._enemy_last_seen.get(enemy_name, 0)
            recency = max(0.0, 1.0 - time_since / 120.0)
            enemy_threat += role_mult * recency

        return min(1.0, max(duration_threat, enemy_threat))

    def update_threats(self, game_time: float) -> None:
        """Recompute all zone threat levels."""
        for fz in self._fog_zones.values():
            fz.threat_level = self._compute_threat(fz, game_time)

    def predict_ganks(self, game_time: float) -> List[GankPrediction]:
        """Predict potential ganks from fog analysis.

        Claude21: For each enemy in fog with high threat, predict
        which lane they might gank based on their last known zone
        and role.
        """
        predictions: List[GankPrediction] = []

        for enemy_name, last_zone in self._enemy_last_zone.items():
            last_seen = self._enemy_last_seen.get(enemy_name, 0)
            fog_duration = game_time - last_seen
            role = self._enemy_roles.get(enemy_name, "")

            if fog_duration < 5.0:
                continue

            role_threat = _GANK_THREAT_BY_ROLE.get(role, 0.3)
            if role_threat < 0.3:
                continue

            # Predict target lane based on last zone
            target_lane = "mid"
            if "top" in last_zone or "river_top" in last_zone:
                target_lane = "top"
            elif "bot" in last_zone or "river_bot" in last_zone or "dragon" in last_zone:
                target_lane = "bot"

            # Probability from fog duration + role
            duration_threat = 0.0
            for threshold, threat in _FOG_THREAT_CURVE:
                if fog_duration >= threshold:
                    duration_threat = threat

            probability = min(0.95, role_threat * duration_threat)

            if probability > 0.15:
                predictions.append(GankPrediction(
                    target_lane=target_lane,
                    predicted_ganker=enemy_name,
                    probability=probability,
                    fog_duration_s=fog_duration,
                    last_seen_zone=last_zone,
                    game_time=game_time,
                ))

        return sorted(predictions, key=lambda p: -p.probability)

    def compute_vision_score(self) -> VisionScore:
        """Compute vision metrics for both teams."""
        blue_visible = sum(
            1 for fz in self._fog_zones.values() if not fz.in_fog
        )
        total = len(self._fog_zones)

        blue_wards_active = len([
            w for w in self._blue_wards if w[1] > time.time()
        ])
        red_wards_active = len([
            w for w in self._red_wards if w[1] > time.time()
        ])

        blue_vision = blue_visible / max(total, 1)
        red_vision = 1.0 - blue_vision  # simplified

        if blue_vision > red_vision + 0.15:
            advantage = "BLUE"
        elif red_vision > blue_vision + 0.15:
            advantage = "RED"
        else:
            advantage = "EVEN"

        return VisionScore(
            blue_vision=blue_vision,
            red_vision=red_vision,
            blue_wards_active=blue_wards_active,
            red_wards_active=red_wards_active,
            blue_fog_zones=total - blue_visible,
            red_fog_zones=blue_visible,
            vision_advantage=advantage,
        )

    def get_dangerous_zones(self, threshold: float = 0.5) -> List[FogZone]:
        """Get zones with threat level above threshold."""
        return [
            fz for fz in self._fog_zones.values()
            if fz.threat_level >= threshold
        ]

    def extended_stats(self) -> Dict[str, Any]:
        base = self.fog_stats() if hasattr(self, "fog_stats") else {}
        dangerous = self.get_dangerous_zones(0.3)
        base.update({
            "zones_in_fog": sum(1 for fz in self._fog_zones.values() if fz.in_fog),
            "total_zones": len(self._fog_zones),
            "enemies_tracked": len(self._enemy_last_zone),
            "dangerous_zones": [fz.to_dict() for fz in dangerous[:5]],
            "vision": self.compute_vision_score().to_dict(),
        })
        return base

    def reset(self) -> None:
        super().reset()
        for fz in self._fog_zones.values():
            fz.in_fog = True
            fz.fog_duration_s = 0.0
            fz.last_seen_time = 0.0
            fz.last_known_enemies.clear()
            fz.threat_level = 0.0
        self._enemy_last_zone.clear()
        self._enemy_last_seen.clear()
        self._enemy_roles.clear()

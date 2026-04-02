"""
WardTracker — Ward placement, expiry, and vision coverage tracking.
====================================================================

Tracks allied and known enemy ward positions, types, and expiry
times. Computes vision coverage score and identifies vision gaps
for strategic ward placement recommendations.

Architecture position:
    modules/perception/ward_tracker/ward_tracker.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (GameSnapshot from perception)
    ├─ Reads: /lol/events (ward placement/destruction events)
    ├─ Publishes: /lol/vision_state (VisionState)
    └─ Consumed by: modules/planning/strategy/lane_advisor.py
                    modules/localization/fog_estimator.py

Apollo reference:
    modules/perception/lidar/ — sensor coverage tracking
    modules/map/relative_map/ — local area mapping

Design notes:
    - Ward types: CONTROL (permanent until destroyed), STEALTH (90-120s),
      ZOMBIE (Zombie Ward rune), FARSIGHT (4000 range, 2 min), GHOST_PORO
    - Vision radius: 900 units for most wards, 500 for Farsight
    - Brush wards only reveal within the brush bounds
    - Tracks both team's wards separately
    - Expiry prediction based on game time and ward type
    - Coverage heatmap for strategic analysis
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

from cyber.component.timer_component import ComponentConfig, TimerComponent
from cyber.node.node import CyberNode, Reader, Writer
from cyber.logger.cyber_logger import get_logger

logger = get_logger("ward_tracker")

# ─── Constants ───────────────────────────────────────────────────────────────

_WARD_TRACKER_INTERVAL_MS = 500.0  # 2Hz — wards don't change fast
_STEALTH_WARD_DURATION_S = 90.0
_STEALTH_WARD_LATE_DURATION_S = 120.0  # after level 13
_CONTROL_WARD_DURATION_S = float("inf")
_FARSIGHT_DURATION_S = 120.0
_ZOMBIE_WARD_DURATION_S = 120.0
_GHOST_PORO_DURATION_S = 60.0

_STANDARD_VISION_RADIUS = 900.0
_FARSIGHT_VISION_RADIUS = 500.0
_CONTROL_VISION_RADIUS = 900.0

_MAP_WIDTH = 14870.0  # Summoner's Rift dimensions
_MAP_HEIGHT = 14980.0
_GRID_CELL_SIZE = 500.0  # Vision heatmap resolution

_WARD_EXPIRY_WARNING_S = 15.0
_MAX_TRACKED_WARDS = 200
_WARD_HISTORY_SIZE = 500


class WardType(Enum):
    """Types of wards in League of Legends."""
    STEALTH = auto()       # Trinket or support item ward
    CONTROL = auto()       # Control Ward (visible, permanent)
    FARSIGHT = auto()      # Farsight Alteration
    ZOMBIE = auto()        # Zombie Ward rune
    GHOST_PORO = auto()    # Ghost Poro rune
    UNKNOWN = auto()


class WardTeam(Enum):
    """Which team placed the ward."""
    ALLY = auto()
    ENEMY = auto()
    UNKNOWN = auto()


@dataclass
class WardPosition:
    """2D map coordinates for a ward."""
    x: float
    y: float

    def distance_to(self, other: "WardPosition") -> float:
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)


@dataclass
class Ward:
    """A tracked ward instance on the map."""
    ward_id: str
    ward_type: WardType
    team: WardTeam
    position: WardPosition
    placed_game_time_s: float
    duration_s: float
    vision_radius: float
    placer_name: str = ""
    is_in_brush: bool = False
    is_visible: bool = True
    destroyed: bool = False
    destroyed_game_time_s: float = 0.0

    @property
    def expiry_game_time_s(self) -> float:
        if self.duration_s == float("inf"):
            return float("inf")
        return self.placed_game_time_s + self.duration_s

    def is_expired(self, current_game_time_s: float) -> bool:
        if self.destroyed:
            return True
        if self.duration_s == float("inf"):
            return False
        return current_game_time_s >= self.expiry_game_time_s

    def remaining_s(self, current_game_time_s: float) -> float:
        if self.duration_s == float("inf"):
            return float("inf")
        return max(0.0, self.expiry_game_time_s - current_game_time_s)

    def covers_point(self, point: WardPosition) -> bool:
        if self.destroyed:
            return False
        return self.position.distance_to(point) <= self.vision_radius


@dataclass
class VisionZone:
    """A named strategic zone on the map for vision evaluation."""
    name: str
    center: WardPosition
    radius: float
    priority: float = 1.0  # importance weight

    def contains(self, pos: WardPosition) -> bool:
        return self.center.distance_to(pos) <= self.radius


@dataclass
class VisionState:
    """Published vision state for downstream consumption."""
    timestamp_ns: int
    game_time_s: float
    ally_wards: List[Ward]
    known_enemy_wards: List[Ward]
    ally_ward_count: int = 0
    enemy_ward_count: int = 0
    ally_control_wards: int = 0
    vision_score_ally: float = 0.0
    coverage_pct: float = 0.0
    expiring_soon: List[Ward] = field(default_factory=list)
    blind_zones: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time_s": self.game_time_s,
            "ally_ward_count": self.ally_ward_count,
            "enemy_ward_count": self.enemy_ward_count,
            "ally_control_wards": self.ally_control_wards,
            "vision_score_ally": round(self.vision_score_ally, 1),
            "coverage_pct": round(self.coverage_pct, 1),
            "expiring_soon": [
                {"id": w.ward_id, "remaining": w.remaining_s(self.game_time_s)}
                for w in self.expiring_soon
            ],
            "blind_zones": self.blind_zones,
        }


# ─── Strategic zones (Summoner's Rift) ──────────────────────────────────────

_VISION_ZONES: List[VisionZone] = [
    VisionZone("baron_pit", WardPosition(4950, 10400), 1200, 2.0),
    VisionZone("dragon_pit", WardPosition(9850, 4400), 1200, 2.0),
    VisionZone("blue_top_jungle", WardPosition(3800, 8200), 1500, 1.2),
    VisionZone("blue_bot_jungle", WardPosition(6500, 5800), 1500, 1.2),
    VisionZone("red_top_jungle", WardPosition(8300, 9100), 1500, 1.2),
    VisionZone("red_bot_jungle", WardPosition(10900, 6600), 1500, 1.2),
    VisionZone("mid_river_top", WardPosition(6400, 8600), 800, 1.5),
    VisionZone("mid_river_bot", WardPosition(8400, 6200), 800, 1.5),
    VisionZone("blue_tri_bush", WardPosition(5000, 4000), 600, 1.0),
    VisionZone("red_tri_bush", WardPosition(9800, 10800), 600, 1.0),
    VisionZone("blue_base_entry", WardPosition(3000, 3000), 1000, 0.8),
    VisionZone("red_base_entry", WardPosition(11800, 11800), 1000, 0.8),
]


def _ward_duration(ward_type: WardType, game_time_s: float) -> float:
    """Get ward duration based on type and game time."""
    if ward_type == WardType.STEALTH:
        return (_STEALTH_WARD_LATE_DURATION_S
                if game_time_s > 780.0  # ~13 min average level 13
                else _STEALTH_WARD_DURATION_S)
    if ward_type == WardType.CONTROL:
        return _CONTROL_WARD_DURATION_S
    if ward_type == WardType.FARSIGHT:
        return _FARSIGHT_DURATION_S
    if ward_type == WardType.ZOMBIE:
        return _ZOMBIE_WARD_DURATION_S
    if ward_type == WardType.GHOST_PORO:
        return _GHOST_PORO_DURATION_S
    return _STEALTH_WARD_DURATION_S


def _ward_vision_radius(ward_type: WardType) -> float:
    """Get vision radius for a ward type."""
    if ward_type == WardType.FARSIGHT:
        return _FARSIGHT_VISION_RADIUS
    return _STANDARD_VISION_RADIUS


class WardTracker(TimerComponent):
    """Tracks ward placements, expiry, and vision coverage.

    Each ``Proc()`` cycle:
    1. Reads latest GameSnapshot from ``/lol/game_state``
    2. Reads ward-related events from ``/lol/events``
    3. Updates ward expiry states
    4. Computes vision coverage score
    5. Identifies expiring wards and blind zones
    6. Publishes VisionState on ``/lol/vision_state``
    """

    def __init__(self) -> None:
        super().__init__(
            config=ComponentConfig(
                name="ward_tracker",
                interval_ms=_WARD_TRACKER_INTERVAL_MS,
                warn_threshold_ms=400.0,
            ),
        )
        self.node = CyberNode("ward_tracker")

        # Readers
        self._game_state_reader: Optional[Reader] = None
        self._events_reader: Optional[Reader] = None

        # Writers
        self._vision_writer: Optional[Writer] = None

        # Ward tracking
        self._active_wards: Dict[str, Ward] = {}
        self._ward_history: List[Ward] = []
        self._ward_counter: int = 0
        self._processed_event_ids: Set[str] = set()

        # State
        self._current_game_time: float = 0.0
        self._last_vision_state: Optional[VisionState] = None

    def Init(self) -> bool:
        """Initialize readers/writers and reset tracking state."""
        try:
            self._game_state_reader = self.node.create_reader(
                "/lol/game_state", queue_size=4
            )
            self._events_reader = self.node.create_reader(
                "/lol/events", queue_size=32
            )
            self._vision_writer = self.node.create_writer(
                "/lol/vision_state"
            )
            self._active_wards.clear()
            self._ward_history.clear()
            self._ward_counter = 0
            self._processed_event_ids.clear()
            logger.info("WardTracker initialized")
            return True
        except Exception as exc:
            logger.error("WardTracker Init failed: %s", exc)
            return False

    def Proc(self) -> bool:
        """Process one ward tracking cycle."""
        try:
            # Read game state for current time
            game_state = (
                self._game_state_reader.get_latest()
                if self._game_state_reader else None
            )
            if game_state and hasattr(game_state, "game_time"):
                self._current_game_time = game_state.game_time

            # Process ward events
            self._process_events()

            # Expire old wards
            self._expire_wards()

            # Compute vision state
            vision_state = self._compute_vision_state()
            self._last_vision_state = vision_state

            # Publish
            if self._vision_writer:
                self._vision_writer.write(vision_state)

            return True
        except Exception as exc:
            logger.error("WardTracker Proc error: %s", exc)
            return False

    def _process_events(self) -> None:
        """Process ward placement and destruction events."""
        if not self._events_reader:
            return

        events = self._events_reader.get_all_pending()
        if not events:
            return

        for event_list in events:
            if not isinstance(event_list, list):
                event_list = [event_list]

            for event in event_list:
                event_id = getattr(event, "event_id", None) or id(event)
                if event_id in self._processed_event_ids:
                    continue
                self._processed_event_ids.add(event_id)

                event_type = getattr(event, "event_type", None)
                if event_type is None:
                    event_type = getattr(event, "type", "")

                event_type_str = (
                    event_type.name if hasattr(event_type, "name")
                    else str(event_type)
                ).upper()

                if "WARD_PLACED" in event_type_str:
                    self._on_ward_placed(event)
                elif "WARD_KILLED" in event_type_str or "WARD_DESTROYED" in event_type_str:
                    self._on_ward_destroyed(event)

        # Trim processed set
        if len(self._processed_event_ids) > 2000:
            trimmed = sorted(self._processed_event_ids)[-1000:]
            self._processed_event_ids = set(trimmed)

    def _on_ward_placed(self, event: Any) -> None:
        """Handle a ward placement event."""
        self._ward_counter += 1
        ward_id = f"ward_{self._ward_counter:06d}"

        # Extract position
        x = getattr(event, "x", 0.0) or getattr(event, "pos_x", 0.0)
        y = getattr(event, "y", 0.0) or getattr(event, "pos_y", 0.0)

        # Determine ward type
        ward_type_str = str(getattr(event, "ward_type", "stealth")).upper()
        if "CONTROL" in ward_type_str or "PINK" in ward_type_str:
            wtype = WardType.CONTROL
        elif "FARSIGHT" in ward_type_str or "BLUE" in ward_type_str:
            wtype = WardType.FARSIGHT
        elif "ZOMBIE" in ward_type_str:
            wtype = WardType.ZOMBIE
        elif "GHOST" in ward_type_str or "PORO" in ward_type_str:
            wtype = WardType.GHOST_PORO
        else:
            wtype = WardType.STEALTH

        # Determine team
        placer = getattr(event, "placer", "") or getattr(event, "killer_name", "")
        team_str = str(getattr(event, "team", "unknown")).upper()
        if "ALLY" in team_str or "ORDER" in team_str:
            team = WardTeam.ALLY
        elif "ENEMY" in team_str or "CHAOS" in team_str:
            team = WardTeam.ENEMY
        else:
            team = WardTeam.UNKNOWN

        ward = Ward(
            ward_id=ward_id,
            ward_type=wtype,
            team=team,
            position=WardPosition(x, y),
            placed_game_time_s=self._current_game_time,
            duration_s=_ward_duration(wtype, self._current_game_time),
            vision_radius=_ward_vision_radius(wtype),
            placer_name=str(placer),
            is_in_brush=False,
        )

        self._active_wards[ward_id] = ward
        logger.debug(
            "Ward placed: %s type=%s team=%s pos=(%.0f, %.0f)",
            ward_id, wtype.name, team.name, x, y,
        )

        # Enforce max tracked wards
        if len(self._active_wards) > _MAX_TRACKED_WARDS:
            oldest = min(
                self._active_wards.values(),
                key=lambda w: w.placed_game_time_s,
            )
            del self._active_wards[oldest.ward_id]

    def _on_ward_destroyed(self, event: Any) -> None:
        """Handle a ward destruction event."""
        x = getattr(event, "x", 0.0) or getattr(event, "pos_x", 0.0)
        y = getattr(event, "y", 0.0) or getattr(event, "pos_y", 0.0)
        pos = WardPosition(x, y)

        # Find closest active ward to destruction position
        best_id = None
        best_dist = float("inf")
        for wid, ward in self._active_wards.items():
            if not ward.destroyed:
                dist = ward.position.distance_to(pos)
                if dist < best_dist:
                    best_dist = dist
                    best_id = wid

        if best_id and best_dist < 200.0:
            ward = self._active_wards[best_id]
            ward.destroyed = True
            ward.destroyed_game_time_s = self._current_game_time
            self._ward_history.append(ward)
            del self._active_wards[best_id]

            # Trim history
            if len(self._ward_history) > _WARD_HISTORY_SIZE:
                self._ward_history = self._ward_history[-_WARD_HISTORY_SIZE:]

    def _expire_wards(self) -> None:
        """Remove expired wards from active tracking."""
        expired_ids = []
        for wid, ward in self._active_wards.items():
            if ward.is_expired(self._current_game_time):
                ward.destroyed = True
                ward.destroyed_game_time_s = self._current_game_time
                self._ward_history.append(ward)
                expired_ids.append(wid)

        for wid in expired_ids:
            del self._active_wards[wid]

    def _compute_vision_state(self) -> VisionState:
        """Compute current vision coverage and state."""
        ally_wards = [
            w for w in self._active_wards.values()
            if w.team == WardTeam.ALLY and not w.destroyed
        ]
        enemy_wards = [
            w for w in self._active_wards.values()
            if w.team == WardTeam.ENEMY and not w.destroyed
        ]

        # Count control wards
        ally_controls = sum(
            1 for w in ally_wards if w.ward_type == WardType.CONTROL
        )

        # Compute coverage percentage over strategic zones
        covered_zones = 0
        total_weight = 0.0
        blind_zones = []

        for zone in _VISION_ZONES:
            total_weight += zone.priority
            zone_covered = any(
                w.covers_point(zone.center) for w in ally_wards
            )
            if zone_covered:
                covered_zones += zone.priority
            else:
                blind_zones.append(zone.name)

        coverage_pct = (
            (covered_zones / total_weight * 100.0)
            if total_weight > 0 else 0.0
        )

        # Vision score: weighted sum of ward coverage
        vision_score = 0.0
        for w in ally_wards:
            base_score = 1.0
            if w.ward_type == WardType.CONTROL:
                base_score = 1.5
            elif w.ward_type == WardType.FARSIGHT:
                base_score = 0.6
            # Bonus for covering strategic zones
            for zone in _VISION_ZONES:
                if w.covers_point(zone.center):
                    base_score += 0.3 * zone.priority
                    break
            vision_score += base_score

        # Expiring soon
        expiring = [
            w for w in ally_wards
            if (w.remaining_s(self._current_game_time) < _WARD_EXPIRY_WARNING_S
                and w.remaining_s(self._current_game_time) > 0)
        ]
        expiring.sort(key=lambda w: w.remaining_s(self._current_game_time))

        return VisionState(
            timestamp_ns=time.time_ns(),
            game_time_s=self._current_game_time,
            ally_wards=ally_wards,
            known_enemy_wards=enemy_wards,
            ally_ward_count=len(ally_wards),
            enemy_ward_count=len(enemy_wards),
            ally_control_wards=ally_controls,
            vision_score_ally=vision_score,
            coverage_pct=coverage_pct,
            expiring_soon=expiring,
            blind_zones=blind_zones[:5],
        )

    # ─── Query API ───────────────────────────────────────────────────────

    def get_wards_near(
        self, x: float, y: float, radius: float = 1500.0,
        team: Optional[WardTeam] = None,
    ) -> List[Ward]:
        """Find active wards near a position."""
        pos = WardPosition(x, y)
        results = []
        for ward in self._active_wards.values():
            if ward.destroyed:
                continue
            if team and ward.team != team:
                continue
            if ward.position.distance_to(pos) <= radius:
                results.append(ward)
        return results

    def get_vision_state(self) -> Optional[VisionState]:
        """Get the last computed vision state."""
        return self._last_vision_state

    def status(self) -> Dict[str, Any]:
        """Introspection for monitoring."""
        base = super().status()
        base.update({
            "active_wards": len(self._active_wards),
            "history_size": len(self._ward_history),
            "game_time": self._current_game_time,
            "coverage_pct": (
                self._last_vision_state.coverage_pct
                if self._last_vision_state else 0.0
            ),
        })
        return base

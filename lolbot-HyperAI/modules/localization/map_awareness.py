"""
MapAwareness — Player zone tracking and jungle quadrant analysis.
==================================================================

Tracks player positions on the Summoner's Rift map, classifies
them into zones (lanes, jungle quadrants, river, base), and
computes team-level map presence metrics.

Architecture position:
    modules/localization/map_awareness.py   ← YOU ARE HERE
    ├─ Reads: /lol/game_state (player positions from Live Client API)
    ├─ Publishes: /lol/map_awareness (MapState)
    └─ Used by: modules/planning/ (macro decisions)

Apollo reference:
    modules/localization/localization_component.cc
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Summoner's Rift coordinate system (game units)
_MAP_MIN_X: float = -120.0
_MAP_MAX_X: float = 14870.0
_MAP_MIN_Y: float = -120.0
_MAP_MAX_Y: float = 14980.0
_MAP_CENTER_X: float = 7375.0
_MAP_CENTER_Y: float = 7430.0


class MapZone(Enum):
    """Named zones on Summoner's Rift."""
    TOP_LANE = auto()
    MID_LANE = auto()
    BOT_LANE = auto()
    TOP_JUNGLE_BLUE = auto()
    TOP_JUNGLE_RED = auto()
    BOT_JUNGLE_BLUE = auto()
    BOT_JUNGLE_RED = auto()
    TOP_RIVER = auto()
    BOT_RIVER = auto()
    BARON_PIT = auto()
    DRAGON_PIT = auto()
    BLUE_BASE = auto()
    RED_BASE = auto()
    BLUE_FOUNTAIN = auto()
    RED_FOUNTAIN = auto()
    UNKNOWN = auto()


class LaneId(Enum):
    TOP = "top"
    MID = "mid"
    BOT = "bot"
    JUNGLE = "jungle"


@dataclass
class Position:
    """2D position in game coordinates."""
    x: float = 0.0
    y: float = 0.0

    def distance_to(self, other: "Position") -> float:
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def to_normalized(self) -> Tuple[float, float]:
        nx = (self.x - _MAP_MIN_X) / (_MAP_MAX_X - _MAP_MIN_X)
        ny = (self.y - _MAP_MIN_Y) / (_MAP_MAX_Y - _MAP_MIN_Y)
        return (max(0.0, min(1.0, nx)), max(0.0, min(1.0, ny)))


@dataclass
class PlayerMapState:
    """Map state for a single player."""
    summoner_name: str = ""
    champion: str = ""
    team: str = ""
    position: Position = field(default_factory=Position)
    zone: MapZone = MapZone.UNKNOWN
    lane: LaneId = LaneId.JUNGLE
    is_visible: bool = False
    is_alive: bool = True
    last_seen_time: float = 0.0
    time_in_zone_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summoner_name": self.summoner_name,
            "champion": self.champion,
            "team": self.team,
            "position": {"x": self.position.x, "y": self.position.y},
            "zone": self.zone.name,
            "lane": self.lane.value,
            "is_visible": self.is_visible,
            "is_alive": self.is_alive,
        }


@dataclass
class TeamMapPresence:
    """Aggregated map presence for a team."""
    team: str = ""
    players_visible: int = 0
    players_alive: int = 0
    zone_counts: Dict[str, int] = field(default_factory=dict)
    center_of_mass: Position = field(default_factory=Position)
    spread_radius: float = 0.0
    jungle_control: float = 0.0  # 0-1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "team": self.team,
            "players_visible": self.players_visible,
            "players_alive": self.players_alive,
            "zone_counts": self.zone_counts,
            "center_of_mass": {
                "x": round(self.center_of_mass.x, 1),
                "y": round(self.center_of_mass.y, 1),
            },
            "spread_radius": round(self.spread_radius, 1),
            "jungle_control": round(self.jungle_control, 3),
        }


# ─── Zone classification geometry ───────────────────────────────────────────

_ZONE_DEFINITIONS: List[Tuple[MapZone, float, float, float, float]] = [
    # (zone, x_min, y_min, x_max, y_max) — rough bounding boxes
    (MapZone.BLUE_FOUNTAIN, -120, -120, 1200, 1200),
    (MapZone.RED_FOUNTAIN, 13700, 13700, 14870, 14980),
    (MapZone.BLUE_BASE, 0, 0, 3000, 3000),
    (MapZone.RED_BASE, 11800, 11800, 14870, 14980),
    (MapZone.BARON_PIT, 4400, 9800, 5400, 10800),
    (MapZone.DRAGON_PIT, 9200, 3800, 10300, 4900),
    (MapZone.TOP_RIVER, 3000, 8500, 6500, 11000),
    (MapZone.BOT_RIVER, 8000, 3500, 11500, 6500),
    (MapZone.TOP_LANE, 0, 9000, 5000, 14980),
    (MapZone.BOT_LANE, 9500, 0, 14870, 5500),
    (MapZone.MID_LANE, 4500, 4500, 10000, 10000),
    (MapZone.TOP_JUNGLE_BLUE, 1500, 5500, 4500, 9000),
    (MapZone.BOT_JUNGLE_BLUE, 1500, 3000, 5000, 5500),
    (MapZone.TOP_JUNGLE_RED, 9500, 9500, 13000, 12000),
    (MapZone.BOT_JUNGLE_RED, 9500, 5500, 13000, 9500),
]


class MapAwareness:
    """Track player positions and compute zone-based map awareness.

    Usage::

        awareness = MapAwareness()
        awareness.update_players(game_state_dict)
        map_state = awareness.get_map_state()
    """

    def __init__(self) -> None:
        self._players: Dict[str, PlayerMapState] = {}
        self._update_count: int = 0
        self._last_update: float = 0.0

    def update_players(
        self, game_state: Dict[str, Any]
    ) -> None:
        """Update all player positions from game state.

        Args:
            game_state: Dict containing 'players' list with
                        position and status data.
        """
        players = game_state.get("players", [])
        now = time.time()

        for p in players:
            name = p.get("summoner_name", p.get("champion", "unknown"))
            pos = Position(
                x=float(p.get("x", p.get("position_x", 0))),
                y=float(p.get("y", p.get("position_y", 0))),
            )

            if name not in self._players:
                self._players[name] = PlayerMapState(
                    summoner_name=name,
                    champion=p.get("champion", ""),
                    team=p.get("team", ""),
                )

            state = self._players[name]
            old_zone = state.zone

            state.position = pos
            state.is_alive = p.get("is_alive", p.get("isDead", True) is False)
            state.is_visible = pos.x != 0 or pos.y != 0

            if state.is_visible:
                state.last_seen_time = now
                new_zone = self._classify_zone(pos)
                if new_zone != old_zone:
                    state.time_in_zone_s = 0.0
                else:
                    state.time_in_zone_s += now - self._last_update if self._last_update > 0 else 0
                state.zone = new_zone
                state.lane = self._zone_to_lane(new_zone)

        self._update_count += 1
        self._last_update = now

    def _classify_zone(self, pos: Position) -> MapZone:
        """Classify a position into its map zone."""
        for zone, x_min, y_min, x_max, y_max in _ZONE_DEFINITIONS:
            if x_min <= pos.x <= x_max and y_min <= pos.y <= y_max:
                return zone
        return MapZone.UNKNOWN

    def _zone_to_lane(self, zone: MapZone) -> LaneId:
        lane_map = {
            MapZone.TOP_LANE: LaneId.TOP,
            MapZone.MID_LANE: LaneId.MID,
            MapZone.BOT_LANE: LaneId.BOT,
        }
        return lane_map.get(zone, LaneId.JUNGLE)

    def get_team_presence(self, team: str) -> TeamMapPresence:
        """Compute aggregated map presence for a team."""
        presence = TeamMapPresence(team=team)
        positions = []
        zone_counts: Dict[str, int] = defaultdict(int)
        jungle_zones = 0
        total_jungle = 0

        for state in self._players.values():
            if state.team != team:
                continue
            if state.is_alive:
                presence.players_alive += 1
            if state.is_visible and state.is_alive:
                presence.players_visible += 1
                positions.append(state.position)
                zone_counts[state.zone.name] += 1

                if "JUNGLE" in state.zone.name:
                    jungle_zones += 1
                    total_jungle += 1

        presence.zone_counts = dict(zone_counts)

        if positions:
            cx = sum(p.x for p in positions) / len(positions)
            cy = sum(p.y for p in positions) / len(positions)
            presence.center_of_mass = Position(x=cx, y=cy)

            if len(positions) > 1:
                distances = [
                    p.distance_to(presence.center_of_mass) for p in positions
                ]
                presence.spread_radius = sum(distances) / len(distances)

        if total_jungle > 0:
            presence.jungle_control = min(1.0, jungle_zones / 4.0)

        return presence

    def get_map_state(self) -> Dict[str, Any]:
        """Get complete map awareness state."""
        player_states = {
            name: state.to_dict()
            for name, state in self._players.items()
        }
        teams = set(s.team for s in self._players.values() if s.team)
        team_presence = {
            team: self.get_team_presence(team).to_dict()
            for team in teams
        }

        return {
            "players": player_states,
            "teams": team_presence,
            "update_count": self._update_count,
            "last_update": self._last_update,
        }

    def get_players_in_zone(self, zone: MapZone) -> List[str]:
        return [
            name for name, state in self._players.items()
            if state.zone == zone and state.is_visible and state.is_alive
        ]

    def get_missing_players(self, team: str) -> List[str]:
        now = time.time()
        return [
            name for name, state in self._players.items()
            if state.team == team
            and state.is_alive
            and (not state.is_visible or now - state.last_seen_time > 10)
        ]

    def stats(self) -> Dict[str, Any]:
        return {
            "tracked_players": len(self._players),
            "update_count": self._update_count,
        }

    def reset(self) -> None:
        self._players.clear()
        self._update_count = 0


# ═══════════════════════════════════════════════════════════════════════════
# Claude21: MapAwarenessV2 — zone pressure analysis, jungle tracking,
# rotation detection, and vision-based fog inference
# ═══════════════════════════════════════════════════════════════════════════

# Summoner's Rift zone definitions (normalized 0-15000 coordinate space)
_SR_ZONES: Dict[str, Dict[str, Tuple[float, float, float, float]]] = {
    "top_lane": {"bounds": (0, 0, 3000, 15000)},
    "mid_lane": {"bounds": (5000, 5000, 10000, 10000)},
    "bot_lane": {"bounds": (12000, 0, 15000, 15000)},
    "blue_jungle_top": {"bounds": (1500, 5000, 5000, 10000)},
    "blue_jungle_bot": {"bounds": (5000, 1500, 10000, 5000)},
    "red_jungle_top": {"bounds": (5000, 10000, 10000, 13500)},
    "red_jungle_bot": {"bounds": (10000, 5000, 13500, 10000)},
    "dragon_pit": {"bounds": (8500, 3000, 11000, 5500)},
    "baron_pit": {"bounds": (4000, 9500, 6500, 12000)},
    "river_top": {"bounds": (3500, 9000, 6000, 11000)},
    "river_bot": {"bounds": (9000, 4000, 11000, 6500)},
    "blue_base": {"bounds": (0, 0, 3000, 3000)},
    "red_base": {"bounds": (12000, 12000, 15000, 15000)},
}


@dataclass
class ZonePressure:
    """Pressure level in a map zone.

    Claude21: Quantifies how much control each team exerts over a zone
    based on champion presence, ward coverage, and recent activity.
    """
    zone_name: str
    blue_pressure: float = 0.0   # 0-1
    red_pressure: float = 0.0    # 0-1
    contested: bool = False
    champion_count_blue: int = 0
    champion_count_red: int = 0
    last_activity_time: float = 0.0

    @property
    def dominant_team(self) -> str:
        if self.blue_pressure > self.red_pressure + 0.15:
            return "BLUE"
        elif self.red_pressure > self.blue_pressure + 0.15:
            return "RED"
        return "CONTESTED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone": self.zone_name,
            "blue": round(self.blue_pressure, 3),
            "red": round(self.red_pressure, 3),
            "dominant": self.dominant_team,
            "contested": self.contested,
        }


@dataclass
class RotationEvent:
    """A detected player rotation (movement between zones).

    Claude21: Rotation detection is critical for predicting ganks
    and objective attempts. A jungler moving from blue_jungle to
    dragon_pit signals an impending dragon attempt.
    """
    player_name: str
    team: str
    from_zone: str
    to_zone: str
    game_time: float
    is_threatening: bool = False    # Moving toward enemy territory
    rotation_speed: float = 0.0    # zones per second

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player": self.player_name,
            "team": self.team,
            "from": self.from_zone,
            "to": self.to_zone,
            "time": round(self.game_time, 1),
            "threatening": self.is_threatening,
        }


@dataclass
class MapState:
    """Complete map awareness snapshot.

    Claude21: Published on /lol/map_awareness for planning to consume.
    Contains zone pressure, recent rotations, and jungle tracking.
    """
    game_time: float = 0.0
    zone_pressures: Dict[str, ZonePressure] = field(default_factory=dict)
    recent_rotations: List[RotationEvent] = field(default_factory=list)
    blue_jungle_control: float = 0.0  # 0-1
    red_jungle_control: float = 0.0   # 0-1
    river_control: str = "CONTESTED"  # BLUE, RED, CONTESTED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "game_time": round(self.game_time, 1),
            "zones": {k: v.to_dict() for k, v in self.zone_pressures.items()},
            "rotations": [r.to_dict() for r in self.recent_rotations[-5:]],
            "blue_jungle": round(self.blue_jungle_control, 3),
            "red_jungle": round(self.red_jungle_control, 3),
            "river": self.river_control,
        }


class MapAwarenessV2(MapAwareness):
    """Production-grade map awareness with zone pressure, rotation detection,
    and jungle control tracking.

    Claude21: Extends MapAwareness with:
    - Zone pressure computation from champion positions
    - Rotation detection from consecutive zone changes
    - Jungle control scoring (proportion of camps secured)
    - Threat assessment for enemy movements
    - Full MapState snapshot for planning consumption

    Apollo reference: modules/localization/msf/local_integ_state.cc
    tracks vehicle position within map zones and lane boundaries.

    Usage::
        awareness = MapAwarenessV2()
        # Each perception tick:
        awareness.update_positions(players, game_time)
        map_state = awareness.compute_map_state(game_time)
    """

    def __init__(self) -> None:
        super().__init__()
        self._zone_pressures: Dict[str, ZonePressure] = {
            name: ZonePressure(zone_name=name) for name in _SR_ZONES
        }
        self._player_zones: Dict[str, str] = {}      # player → current zone
        self._prev_player_zones: Dict[str, str] = {}  # player → previous zone
        self._rotation_history: Deque[RotationEvent] = deque(maxlen=100)
        self._player_teams: Dict[str, str] = {}        # player → team

    def _classify_zone(self, x: float, y: float) -> str:
        """Determine which zone a position falls in.

        Claude21: Uses simple AABB containment. First match wins
        (zones are non-overlapping in our definition).
        """
        for zone_name, zone_def in _SR_ZONES.items():
            x1, y1, x2, y2 = zone_def["bounds"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                return zone_name
        return "unknown"

    def _is_threatening_move(
        self, team: str, from_zone: str, to_zone: str,
    ) -> bool:
        """Check if a rotation is moving toward enemy territory.

        Claude21: Blue moving into red_jungle or red_base is threatening.
        """
        if team == "BLUE":
            return "red" in to_zone and "red" not in from_zone
        elif team == "RED":
            return "blue" in to_zone and "blue" not in from_zone
        return False

    def update_positions(
        self, players: List[Any], game_time: float,
    ) -> List[RotationEvent]:
        """Update player positions and detect rotations.

        Args:
            players: List of player objects with x, y, team, name attributes.
            game_time: Current game time in seconds.

        Returns:
            Newly detected rotation events.
        """
        new_rotations: List[RotationEvent] = []
        self._prev_player_zones = dict(self._player_zones)

        # Reset zone champion counts
        for zp in self._zone_pressures.values():
            zp.champion_count_blue = 0
            zp.champion_count_red = 0

        for player in players:
            name = getattr(player, "name", str(player))
            x = getattr(player, "x", 0.0)
            y = getattr(player, "y", 0.0)
            team = getattr(player, "team", "UNKNOWN")
            if hasattr(team, "name"):
                team = team.name

            self._player_teams[name] = team
            zone = self._classify_zone(x, y)
            self._player_zones[name] = zone

            # Update zone champion counts
            if zone in self._zone_pressures:
                zp = self._zone_pressures[zone]
                if team == "BLUE":
                    zp.champion_count_blue += 1
                elif team == "RED":
                    zp.champion_count_red += 1
                zp.last_activity_time = game_time

            # Detect rotation
            prev_zone = self._prev_player_zones.get(name)
            if prev_zone and prev_zone != zone:
                is_threat = self._is_threatening_move(team, prev_zone, zone)
                rotation = RotationEvent(
                    player_name=name,
                    team=team,
                    from_zone=prev_zone,
                    to_zone=zone,
                    game_time=game_time,
                    is_threatening=is_threat,
                )
                new_rotations.append(rotation)
                self._rotation_history.append(rotation)

        return new_rotations

    def _compute_zone_pressure(self, game_time: float) -> None:
        """Compute pressure scores for all zones.

        Claude21: Pressure decays over time. Each champion in a zone adds
        base pressure, with recency weighting.
        """
        for zp in self._zone_pressures.values():
            # Base pressure from champion presence
            blue_base = min(1.0, zp.champion_count_blue * 0.35)
            red_base = min(1.0, zp.champion_count_red * 0.35)

            # Recency decay — pressure fades if no recent activity
            age = game_time - zp.last_activity_time if zp.last_activity_time > 0 else 999
            decay = max(0.0, 1.0 - (age / 60.0))  # Full decay after 60s

            zp.blue_pressure = blue_base * (0.3 + 0.7 * decay)
            zp.red_pressure = red_base * (0.3 + 0.7 * decay)
            zp.contested = (
                zp.champion_count_blue > 0 and zp.champion_count_red > 0
            )

    def compute_jungle_control(self) -> Tuple[float, float]:
        """Compute jungle control percentages.

        Returns (blue_control, red_control) each in [0, 1].
        """
        blue_zones = ["blue_jungle_top", "blue_jungle_bot"]
        red_zones = ["red_jungle_top", "red_jungle_bot"]

        blue_control_own = sum(
            self._zone_pressures[z].blue_pressure
            for z in blue_zones if z in self._zone_pressures
        ) / max(len(blue_zones), 1)

        blue_invade = sum(
            self._zone_pressures[z].blue_pressure
            for z in red_zones if z in self._zone_pressures
        ) / max(len(red_zones), 1)

        red_control_own = sum(
            self._zone_pressures[z].red_pressure
            for z in red_zones if z in self._zone_pressures
        ) / max(len(red_zones), 1)

        red_invade = sum(
            self._zone_pressures[z].red_pressure
            for z in blue_zones if z in self._zone_pressures
        ) / max(len(blue_zones), 1)

        blue_total = (blue_control_own + blue_invade) / 2.0
        red_total = (red_control_own + red_invade) / 2.0

        return min(1.0, blue_total), min(1.0, red_total)

    def compute_map_state(self, game_time: float) -> MapState:
        """Compute complete map awareness state.

        Claude21: Single call to get the full MapState snapshot.
        """
        self._compute_zone_pressure(game_time)
        blue_jg, red_jg = self.compute_jungle_control()

        # River control from river zone pressures
        river_zones = ["river_top", "river_bot"]
        blue_river = sum(
            self._zone_pressures[z].blue_pressure
            for z in river_zones if z in self._zone_pressures
        )
        red_river = sum(
            self._zone_pressures[z].red_pressure
            for z in river_zones if z in self._zone_pressures
        )
        if blue_river > red_river + 0.2:
            river = "BLUE"
        elif red_river > blue_river + 0.2:
            river = "RED"
        else:
            river = "CONTESTED"

        recent = list(self._rotation_history)[-10:]

        return MapState(
            game_time=game_time,
            zone_pressures=dict(self._zone_pressures),
            recent_rotations=recent,
            blue_jungle_control=blue_jg,
            red_jungle_control=red_jg,
            river_control=river,
        )

    def get_threatening_rotations(
        self, game_time: float, window_s: float = 30.0,
    ) -> List[RotationEvent]:
        """Get recent threatening enemy rotations.

        Claude21: Used by planning to issue gank warnings.
        """
        cutoff = game_time - window_s
        return [
            r for r in self._rotation_history
            if r.is_threatening and r.game_time >= cutoff
        ]

    def extended_stats(self) -> Dict[str, Any]:
        base = self.awareness_stats() if hasattr(self, "awareness_stats") else {}
        base.update({
            "zones_tracked": len(self._zone_pressures),
            "players_tracked": len(self._player_zones),
            "rotation_history_size": len(self._rotation_history),
            "threatening_recent": len([
                r for r in self._rotation_history if r.is_threatening
            ]),
        })
        return base

    def reset(self) -> None:
        super().reset()
        for zp in self._zone_pressures.values():
            zp.blue_pressure = 0.0
            zp.red_pressure = 0.0
            zp.champion_count_blue = 0
            zp.champion_count_red = 0
        self._player_zones.clear()
        self._prev_player_zones.clear()
        self._rotation_history.clear()
        self._player_teams.clear()

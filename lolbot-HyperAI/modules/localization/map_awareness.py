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

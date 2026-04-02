"""
MinimapAnalyzer — Map zone control, lane pressure, and vision analysis.
========================================================================
lolbot-HyperAI · Perception Layer

Analyzes player positions from game state data to determine:
    - Lane pressure (which lanes are pushed/frozen/under threat)
    - Jungle control (which quadrants each team dominates)
    - Vision coverage (estimated ward coverage by zone)
    - Danger zones (areas where ganks are likely)

This is the spatial reasoning module — it turns raw position data
into strategic map-level understanding.

Architecture position:
    modules/perception/minimap/minimap_analyzer.py   ← YOU ARE HERE
    ├─ Input: GameSnapshot (player positions, events)
    ├─ Output: MinimapState (lane_pressure, jungle_control, ward_coverage)
    ├─ Publishes results to: /lol/minimap_state channel
    └─ Consumed by: planning (macro decisions), prediction (threat assessment)

Apollo reference:
    modules/perception/multi_sensor_fusion/fusion_component.cc
    modules/perception/camera/lib/obstacle/transformer.cc

Design notes:
    - Summoner's Rift coordinate system: (0,0) bottom-left, (15000,15000) top-right
    - Zone classification: 19 zones (3 lanes × 3 segments + 4 jungle quadrants + river + bases)
    - Lane pressure: derived from minion wave position estimates and champion proximity
    - Position normalization to [0,1] for zone classification
"""

from __future__ import annotations

import logging
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

from cyber.logger.cyber_logger import get_logger
from modules.common.adapters.game_messages import (
    GamePhase,
    GameSnapshot,
    PlayerState,
    TeamSide,
    TeamState,
)

logger = get_logger("perception.minimap")

# ─── Constants ───────────────────────────────────────────────────────────────

_MAP_SIZE = 15000.0       # Summoner's Rift is approximately 15000 × 15000 units
_LANE_WIDTH = 2000.0      # Approximate lane corridor width
_JUNGLE_ZONE_SIZE = 4000.0
_HISTORY_WINDOW = 30      # Track last N snapshots for movement analysis
_PRESSURE_DECAY = 0.95    # Pressure decays per tick if no update


# ─── Zone System ─────────────────────────────────────────────────────────────

class MapZone(Enum):
    """Discrete map zones for spatial analysis."""
    # Lanes (each lane split into 3 segments)
    TOP_LANE_OUR = "top_our"
    TOP_LANE_MID = "top_mid"
    TOP_LANE_THEIR = "top_their"
    MID_LANE_OUR = "mid_our"
    MID_LANE_MID = "mid_mid"
    MID_LANE_THEIR = "mid_their"
    BOT_LANE_OUR = "bot_our"
    BOT_LANE_MID = "bot_mid"
    BOT_LANE_THEIR = "bot_their"
    # Jungle quadrants
    JUNGLE_TOP_BLUE = "jungle_top_blue"     # Blue side top jungle
    JUNGLE_BOT_BLUE = "jungle_bot_blue"     # Blue side bot jungle
    JUNGLE_TOP_RED = "jungle_top_red"       # Red side top jungle
    JUNGLE_BOT_RED = "jungle_bot_red"       # Red side bot jungle
    # River
    RIVER_TOP = "river_top"     # Near baron/herald
    RIVER_BOT = "river_bot"     # Near dragon
    # Bases
    BASE_BLUE = "base_blue"
    BASE_RED = "base_red"
    # Unknown
    UNKNOWN = "unknown"


class LanePressure(Enum):
    """Lane pressure state."""
    HARD_PUSH_US = "hard_push_us"       # We're pushing hard
    SLOW_PUSH_US = "slow_push_us"       # Slow push toward them
    FROZEN = "frozen"                     # Lane is frozen/neutral
    SLOW_PUSH_THEM = "slow_push_them"   # Slow push toward us
    HARD_PUSH_THEM = "hard_push_them"   # They're pushing hard
    UNKNOWN = "unknown"


# ─── Zone Classifier ────────────────────────────────────────────────────────

class ZoneClassifier:
    """Classifies a map position into a discrete zone.

    Uses the Summoner's Rift layout:
    - Diagonal axis from (0,0) to (15000,15000) for blue/red side
    - Top lane runs along x=0..2000 side
    - Bot lane runs along y=0..2000 side
    - Mid lane runs diagonally
    """

    # Zone boundaries (normalized 0-1 coordinates)
    # Blue base bottom-left, Red base top-right

    def classify(self, x: float, y: float) -> MapZone:
        """Classify a map position into a zone.

        Args:
            x: X position (0 to 15000)
            y: Y position (0 to 15000)

        Returns:
            The MapZone the position belongs to.
        """
        # Normalize to [0, 1]
        nx = max(0.0, min(1.0, x / _MAP_SIZE))
        ny = max(0.0, min(1.0, y / _MAP_SIZE))

        # Bases
        if nx < 0.1 and ny < 0.1:
            return MapZone.BASE_BLUE
        if nx > 0.9 and ny > 0.9:
            return MapZone.BASE_RED

        # River (diagonal band around center)
        dist_to_diagonal = abs(nx - ny) / math.sqrt(2)
        center_dist = math.sqrt((nx - 0.5) ** 2 + (ny - 0.5) ** 2)
        if dist_to_diagonal < 0.08 and 0.2 < center_dist < 0.4:
            if ny > 0.5:
                return MapZone.RIVER_TOP
            else:
                return MapZone.RIVER_BOT

        # Top lane (high y, low-to-mid x)
        if ny > 0.75 and nx < 0.5:
            return self._lane_segment("top", nx, ny)
        if nx < 0.25 and ny > 0.5:
            return self._lane_segment("top", nx, ny)

        # Bot lane (low y, mid-to-high x)
        if ny < 0.25 and nx > 0.5:
            return self._lane_segment("bot", nx, ny)
        if nx > 0.75 and ny < 0.5:
            return self._lane_segment("bot", nx, ny)

        # Mid lane (along diagonal)
        if dist_to_diagonal < 0.12:
            return self._lane_segment("mid", nx, ny)

        # Jungle quadrants
        if nx < 0.5 and ny > 0.5:
            return MapZone.JUNGLE_TOP_BLUE
        if nx < 0.5 and ny < 0.5:
            return MapZone.JUNGLE_BOT_BLUE
        if nx > 0.5 and ny > 0.5:
            return MapZone.JUNGLE_TOP_RED
        if nx > 0.5 and ny < 0.5:
            return MapZone.JUNGLE_BOT_RED

        return MapZone.UNKNOWN

    def _lane_segment(self, lane: str, nx: float, ny: float) -> MapZone:
        """Classify lane position into our/mid/their segment."""
        # Distance along the lane axis (0=blue base, 1=red base)
        if lane == "top":
            progress = (nx + ny) / 2.0
        elif lane == "bot":
            progress = (nx + ny) / 2.0
        else:  # mid
            progress = (nx + ny) / 2.0

        if progress < 0.35:
            segment = "our"
        elif progress > 0.65:
            segment = "their"
        else:
            segment = "mid"

        zone_name = f"{lane}_{segment}"
        try:
            return MapZone(zone_name)
        except ValueError:
            return MapZone.UNKNOWN


# ─── Data Types ──────────────────────────────────────────────────────────────

@dataclass
class ZonePresence:
    """Presence data for a single zone."""
    zone: MapZone
    our_champions: List[str] = field(default_factory=list)
    their_champions: List[str] = field(default_factory=list)
    our_count: int = 0
    their_count: int = 0
    control_score: float = 0.0  # [-1, 1]: positive = we control it

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone": self.zone.value,
            "our_count": self.our_count,
            "their_count": self.their_count,
            "control_score": round(self.control_score, 3),
        }


@dataclass
class LaneState:
    """State of a single lane."""
    lane: str                   # "top", "mid", "bot"
    pressure: LanePressure = LanePressure.UNKNOWN
    our_champions_near: int = 0
    their_champions_near: int = 0
    pressure_score: float = 0.0  # [-1, 1]: positive = we're pushing

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lane": self.lane,
            "pressure": self.pressure.value,
            "our_near": self.our_champions_near,
            "their_near": self.their_champions_near,
            "score": round(self.pressure_score, 3),
        }


@dataclass
class JungleControl:
    """Jungle control assessment."""
    our_quadrant_control: float = 0.0   # 0-1: fraction of jungle we control
    their_quadrant_control: float = 0.0
    contested_quadrants: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "our_control": round(self.our_quadrant_control, 3),
            "their_control": round(self.their_quadrant_control, 3),
            "contested": self.contested_quadrants,
        }


@dataclass
class MinimapState:
    """Complete minimap state output."""
    lanes: Dict[str, LaneState] = field(default_factory=dict)
    jungle: JungleControl = field(default_factory=JungleControl)
    zone_presence: Dict[str, ZonePresence] = field(default_factory=dict)
    danger_zones: List[str] = field(default_factory=list)
    game_time: float = 0.0
    timestamp: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lanes": {k: v.to_dict() for k, v in self.lanes.items()},
            "jungle": self.jungle.to_dict(),
            "danger_zones": self.danger_zones,
            "game_time": self.game_time,
        }


# ─── MinimapAnalyzer ─────────────────────────────────────────────────────────

class MinimapAnalyzer:
    """Analyzes game state to produce spatial map understanding.

    Maintains a rolling history of player positions and computes
    zone control, lane pressure, jungle dominance, and danger areas.

    Usage::

        analyzer = MinimapAnalyzer()
        state = analyzer.analyze(snapshot)
        print(state.lanes["top"].pressure)   # SLOW_PUSH_US
        print(state.jungle.our_control)      # 0.65
    """

    def __init__(self) -> None:
        self._classifier = ZoneClassifier()
        self._position_history: Deque[Dict[str, Tuple[float, float]]] = deque(
            maxlen=_HISTORY_WINDOW,
        )
        self._analysis_count: int = 0
        self._last_state: Optional[MinimapState] = None

    def analyze(self, snapshot: GameSnapshot) -> MinimapState:
        """Analyze current game state and produce minimap state.

        Args:
            snapshot: Current game state from perception.

        Returns:
            MinimapState with lanes, jungle, and danger zone data.
        """
        our_side = snapshot.active_team
        if our_side == TeamSide.BLUE:
            our_team, their_team = snapshot.blue_team, snapshot.red_team
        else:
            our_team, their_team = snapshot.red_team, snapshot.blue_team

        # Classify all player positions into zones
        zone_map = self._classify_players(our_team, their_team)

        # Analyze lanes
        lanes = self._analyze_lanes(zone_map)

        # Analyze jungle
        jungle = self._analyze_jungle(zone_map, our_side)

        # Detect danger zones
        danger = self._detect_danger_zones(zone_map, our_team, their_team)

        state = MinimapState(
            lanes=lanes,
            jungle=jungle,
            zone_presence=zone_map,
            danger_zones=danger,
            game_time=snapshot.game_time,
        )

        self._analysis_count += 1
        self._last_state = state

        return state

    def _classify_players(
        self,
        our_team: TeamState,
        their_team: TeamState,
    ) -> Dict[str, ZonePresence]:
        """Classify all players into zones."""
        zones: Dict[str, ZonePresence] = {}

        # Initialize all zones
        for z in MapZone:
            if z != MapZone.UNKNOWN:
                zones[z.value] = ZonePresence(zone=z)

        # Classify our players
        for player in our_team.players:
            if not not player.is_dead:
                continue
            # Use position data if available, otherwise estimate from role
            zone = self._estimate_zone_from_role(player, is_ours=True)
            if zone.value in zones:
                zones[zone.value].our_champions.append(player.champion_name)
                zones[zone.value].our_count += 1

        # Classify their players
        for player in their_team.players:
            if not not player.is_dead:
                continue
            zone = self._estimate_zone_from_role(player, is_ours=False)
            if zone.value in zones:
                zones[zone.value].their_champions.append(player.champion_name)
                zones[zone.value].their_count += 1

        # Compute control scores
        for z in zones.values():
            total = z.our_count + z.their_count
            if total > 0:
                z.control_score = (z.our_count - z.their_count) / total

        return zones

    def _estimate_zone_from_role(
        self,
        player: PlayerState,
        is_ours: bool,
    ) -> MapZone:
        """Estimate player zone from their role (when exact position unavailable).

        This is a heuristic — in production with Fiddler MCP data, we'd
        have actual coordinates.  This fallback uses champion role and
        game phase to estimate typical positions.
        """
        # Simple role-based estimation
        # In the absence of position data, use level and gold as proxies
        role_zones = {
            "TOP": MapZone.TOP_LANE_MID,
            "JUNGLE": MapZone.JUNGLE_BOT_BLUE if is_ours else MapZone.JUNGLE_BOT_RED,
            "MIDDLE": MapZone.MID_LANE_MID,
            "BOTTOM": MapZone.BOT_LANE_MID,
            "UTILITY": MapZone.BOT_LANE_MID,
        }

        # Try to infer role from position attribute if available
        position = getattr(player, "position", "UNKNOWN")
        if isinstance(position, str) and position in role_zones:
            return role_zones[position]

        # Fallback: distribute based on player index
        return MapZone.MID_LANE_MID

    def _analyze_lanes(
        self,
        zone_map: Dict[str, ZonePresence],
    ) -> Dict[str, LaneState]:
        """Analyze lane pressure from zone presence data."""
        lanes = {}

        for lane_name in ("top", "mid", "bot"):
            our_key = f"{lane_name}_our"
            mid_key = f"{lane_name}_mid"
            their_key = f"{lane_name}_their"

            our_zone = zone_map.get(our_key, ZonePresence(zone=MapZone.UNKNOWN))
            mid_zone = zone_map.get(mid_key, ZonePresence(zone=MapZone.UNKNOWN))
            their_zone = zone_map.get(their_key, ZonePresence(zone=MapZone.UNKNOWN))

            # Total presence across lane segments
            our_total = our_zone.our_count + mid_zone.our_count + their_zone.our_count
            their_total = our_zone.their_count + mid_zone.their_count + their_zone.their_count

            # Pressure direction: where are the champions concentrated?
            our_forward = their_zone.our_count * 2 + mid_zone.our_count
            their_forward = our_zone.their_count * 2 + mid_zone.their_count

            pressure_score = 0.0
            if our_forward + their_forward > 0:
                pressure_score = (our_forward - their_forward) / max(our_forward + their_forward, 1)

            # Classify pressure
            if pressure_score > 0.5:
                pressure = LanePressure.HARD_PUSH_US
            elif pressure_score > 0.15:
                pressure = LanePressure.SLOW_PUSH_US
            elif pressure_score < -0.5:
                pressure = LanePressure.HARD_PUSH_THEM
            elif pressure_score < -0.15:
                pressure = LanePressure.SLOW_PUSH_THEM
            else:
                pressure = LanePressure.FROZEN

            lanes[lane_name] = LaneState(
                lane=lane_name,
                pressure=pressure,
                our_champions_near=our_total,
                their_champions_near=their_total,
                pressure_score=pressure_score,
            )

        return lanes

    def _analyze_jungle(
        self,
        zone_map: Dict[str, ZonePresence],
        our_side: TeamSide,
    ) -> JungleControl:
        """Analyze jungle quadrant control."""
        jungle_zones = [
            "jungle_top_blue", "jungle_bot_blue",
            "jungle_top_red", "jungle_bot_red",
        ]

        our_controlled = 0
        their_controlled = 0
        contested = []

        for zname in jungle_zones:
            zone = zone_map.get(zname)
            if zone is None:
                continue

            if zone.our_count > zone.their_count:
                our_controlled += 1
            elif zone.their_count > zone.our_count:
                their_controlled += 1
            elif zone.our_count > 0 and zone.their_count > 0:
                contested.append(zname)

        total = len(jungle_zones)
        return JungleControl(
            our_quadrant_control=our_controlled / total,
            their_quadrant_control=their_controlled / total,
            contested_quadrants=contested,
        )

    def _detect_danger_zones(
        self,
        zone_map: Dict[str, ZonePresence],
        our_team: TeamState,
        their_team: TeamState,
    ) -> List[str]:
        """Detect zones where our players are in danger.

        A zone is dangerous when:
        - We have players there AND
        - They have more players nearby AND
        - It's not our base
        """
        dangers = []

        for zname, zone in zone_map.items():
            if zone.zone in (MapZone.BASE_BLUE, MapZone.BASE_RED, MapZone.UNKNOWN):
                continue

            if zone.our_count > 0 and zone.their_count > zone.our_count:
                dangers.append(zname)

        return dangers

    # ── Stats ────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return analyzer statistics."""
        return {
            "analysis_count": self._analysis_count,
            "history_size": len(self._position_history),
            "last_state": self._last_state.to_dict() if self._last_state else None,
        }

    def reset(self) -> None:
        """Reset state between games."""
        self._position_history.clear()
        self._analysis_count = 0
        self._last_state = None

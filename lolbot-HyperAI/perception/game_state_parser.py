#!/usr/bin/env python3
"""
perception/game_state_parser.py — Game State Normalization & Fusion
=====================================================================
lolbot-HyperAI · Perception Layer

In Apollo, the perception fusion module takes raw sensor data (LiDAR
point clouds, camera images, radar returns) and produces a unified
world model. Our fusion takes raw LoL network data and produces a
normalized GameState that downstream modules can rely on.

Key responsibility: **temporal alignment**. Data arrives at different
rates from different sources:
    - Gameflow phase: ~2 Hz
    - Champ select: ~1 Hz
    - Live client all_game_data: ~2 Hz
    - Live client events: ~5 Hz

The parser fuses these into a single coherent GameState snapshot,
published at a steady 2 Hz rate to CH_LIVE_GAME_STATE.

Design principle: The parser is the ONLY module that understands the
raw Riot API schema. Everything downstream works with our normalized
schema. If Riot changes their API, only this module needs updating.

Subscribes to:
    - CH_GAME_FLOW_PHASE
    - CH_CHAMP_SELECT_STATE
    - CH_SCOREBOARD_SNAPSHOT
    - CH_KILL_EVENT
    - CH_OBJECTIVE_EVENT

Publishes to:
    - CH_LIVE_GAME_STATE (fused, normalized)
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_CHAMP_SELECT_STATE,
    CH_GAME_FLOW_PHASE,
    CH_KILL_EVENT,
    CH_LIVE_GAME_STATE,
    CH_OBJECTIVE_EVENT,
    CH_SCOREBOARD_SNAPSHOT,
    CH_SYSTEM_HEARTBEAT,
    ChannelMessage,
    MessageFactory,
)
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# Normalized data models
# ---------------------------------------------------------------------------
class GamePhase(Enum):
    """Unified game phase (combines gameflow + in-game time)."""
    NONE = "none"
    LOBBY = "lobby"
    QUEUE = "queue"
    CHAMP_SELECT = "champ_select"
    LOADING = "loading"
    EARLY_LANING = "early_laning"      # 0-3 min (first blood territory)
    LANING = "laning"                  # 3-14 min
    MID_GAME = "mid_game"             # 14-25 min (objectives + rotations)
    LATE_GAME = "late_game"           # 25+ min (baron dances, teamfights)
    POST_GAME = "post_game"

    @classmethod
    def from_gameflow_and_time(
        cls,
        gameflow_phase: str,
        game_time_sec: float,
    ) -> "GamePhase":
        """Determine phase from both gameflow string and game time."""
        gf = gameflow_phase.lower()

        if gf in ("none", ""):
            return cls.NONE
        if gf in ("lobby", "matchmaking", "readycheck"):
            return cls.LOBBY
        if gf == "champselect":
            return cls.CHAMP_SELECT
        if gf in ("gamestart",):
            return cls.LOADING

        # In-game phases based on time
        if gf in ("inprogress", "reconnect"):
            if game_time_sec < 180:
                return cls.EARLY_LANING
            elif game_time_sec < 840:
                return cls.LANING
            elif game_time_sec < 1500:
                return cls.MID_GAME
            else:
                return cls.LATE_GAME

        if gf in ("waitingforstats", "preendofgame", "endofgame"):
            return cls.POST_GAME

        return cls.NONE


@dataclass
class PlayerState:
    """Normalized state for a single player."""
    name: str = ""
    champion: str = ""
    champion_id: int = 0
    level: int = 1
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    gold: int = 0
    items: List[str] = field(default_factory=list)
    position: str = ""                # top, jungle, mid, bot, support
    is_dead: bool = False
    respawn_timer_sec: float = 0.0
    summoner_spells: Tuple[str, str] = ("", "")
    kda_ratio: float = 0.0
    cs_per_min: float = 0.0
    gold_per_min: float = 0.0

    def compute_derived(self, game_time_sec: float) -> None:
        """Compute derived stats from raw values."""
        denom = max(self.deaths, 1)
        self.kda_ratio = round(
            (self.kills + self.assists) / denom, 2
        )
        if game_time_sec > 0:
            minutes = game_time_sec / 60.0
            self.cs_per_min = round(self.cs / minutes, 1)
            self.gold_per_min = round(self.gold / minutes, 1)


@dataclass
class TeamState:
    """Aggregated state for one team."""
    players: List[PlayerState] = field(default_factory=list)
    total_kills: int = 0
    total_deaths: int = 0
    total_assists: int = 0
    total_cs: int = 0
    total_gold: int = 0
    turrets_destroyed: int = 0
    dragons_taken: int = 0
    barons_taken: int = 0
    heralds_taken: int = 0
    inhibs_destroyed: int = 0
    dragon_types: List[str] = field(default_factory=list)

    def update_from_players(self) -> None:
        """Recompute totals from individual player states."""
        self.total_kills = sum(p.kills for p in self.players)
        self.total_deaths = sum(p.deaths for p in self.players)
        self.total_assists = sum(p.assists for p in self.players)
        self.total_cs = sum(p.cs for p in self.players)
        self.total_gold = sum(p.gold for p in self.players)


@dataclass
class ObjectiveState:
    """Tracks the state of map objectives."""
    dragon_count_ally: int = 0
    dragon_count_enemy: int = 0
    dragon_types_ally: List[str] = field(default_factory=list)
    dragon_types_enemy: List[str] = field(default_factory=list)
    dragon_soul_ally: Optional[str] = None
    dragon_soul_enemy: Optional[str] = None
    baron_alive: bool = True
    baron_timer_sec: float = 0.0
    elder_alive: bool = False
    elder_timer_sec: float = 0.0
    herald_alive: bool = True
    herald_count_ally: int = 0
    herald_count_enemy: int = 0
    turrets_ally: int = 11          # Start with all turrets
    turrets_enemy: int = 11


@dataclass
class GameState:
    """
    Complete normalized game state.

    This is the central data structure that flows through the CAN bus.
    Every module downstream (prediction, planning, output) works with
    this schema, never with raw Riot API responses.
    """
    # Identity
    game_id: Optional[str] = None
    summoner_name: str = ""

    # Phase
    phase: GamePhase = GamePhase.NONE
    gameflow_raw: str = ""
    game_time_sec: float = 0.0
    game_time_str: str = "0:00"

    # Teams
    our_team: TeamState = field(default_factory=TeamState)
    enemy_team: TeamState = field(default_factory=TeamState)

    # Objectives
    objectives: ObjectiveState = field(default_factory=ObjectiveState)

    # Differentials (our - enemy)
    kill_diff: int = 0
    gold_diff: int = 0
    cs_diff: int = 0
    tower_diff: int = 0
    dragon_diff: int = 0

    # Recent events (last 60 seconds)
    recent_kills: List[Dict[str, Any]] = field(default_factory=list)
    recent_objectives: List[Dict[str, Any]] = field(default_factory=list)

    # Champ select data (if in that phase)
    champ_select: Optional[Dict[str, Any]] = None

    # Meta
    last_update_ms: int = 0
    update_count: int = 0

    def compute_differentials(self) -> None:
        """Compute all team differentials."""
        self.kill_diff = (
            self.our_team.total_kills - self.enemy_team.total_kills
        )
        self.gold_diff = (
            self.our_team.total_gold - self.enemy_team.total_gold
        )
        self.cs_diff = (
            self.our_team.total_cs - self.enemy_team.total_cs
        )
        self.tower_diff = (
            self.enemy_team.turrets_destroyed
            - self.our_team.turrets_destroyed
        )
        self.dragon_diff = (
            self.objectives.dragon_count_ally
            - self.objectives.dragon_count_enemy
        )

    def format_game_time(self) -> str:
        """Format game time as M:SS."""
        minutes = int(self.game_time_sec // 60)
        seconds = int(self.game_time_sec % 60)
        return f"{minutes}:{seconds:02d}"

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for CAN bus / logging."""
        d = asdict(self)
        # Convert enums to strings
        d["phase"] = self.phase.value
        return d

    def momentum_score(self) -> float:
        """
        Compute a -1 to +1 momentum score.

        Positive = we have momentum (getting kills, objectives).
        Negative = enemy has momentum.

        Based on recent events weighted by recency.
        """
        if not self.recent_kills and not self.recent_objectives:
            return 0.0

        now = self.game_time_sec
        score = 0.0

        for kill in self.recent_kills:
            age = now - kill.get("game_time_sec", now)
            recency_weight = max(0, 1.0 - age / 60.0)
            if kill.get("is_ally_kill", False):
                score += 0.15 * recency_weight
            else:
                score -= 0.15 * recency_weight

        for obj in self.recent_objectives:
            age = now - obj.get("game_time_sec", now)
            recency_weight = max(0, 1.0 - age / 120.0)
            if obj.get("is_ally_objective", False):
                score += 0.3 * recency_weight
            else:
                score -= 0.3 * recency_weight

        return max(-1.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Game State Parser Component
# ---------------------------------------------------------------------------
class GameStateParser:
    """
    Fuses raw perception data into normalized GameState.

    Subscribes to raw channels, maintains internal state, and publishes
    the fused GameState at a steady rate.

    Apollo equivalent: modules/perception/fusion/
    """

    PUBLISH_INTERVAL_MS = 500  # 2 Hz fused state output

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._factory = MessageFactory("perception.game_state_parser")
        self._state = GameState()
        self._last_publish_ms = 0
        self._init_time_ms = int(time.monotonic() * 1000)

        # Recent event buffers (60-second sliding window)
        self._recent_kills: Deque[Dict] = deque(maxlen=50)
        self._recent_objectives: Deque[Dict] = deque(maxlen=20)

        # Subscribe to raw perception channels
        self._unsubs: List[Callable] = []

    def init(self) -> None:
        """Wire up subscriptions to raw perception channels."""
        self._unsubs.append(
            self._transport.subscribe(
                CH_GAME_FLOW_PHASE, self._on_gameflow,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_CHAMP_SELECT_STATE, self._on_champ_select,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_SCOREBOARD_SNAPSHOT, self._on_scoreboard,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_KILL_EVENT, self._on_kill,
            )
        )
        self._unsubs.append(
            self._transport.subscribe(
                CH_OBJECTIVE_EVENT, self._on_objective,
            )
        )

    async def proc(self) -> None:
        """
        Periodic fusion tick: recompute derived fields and publish.

        Called by the scheduler at PUBLISH_INTERVAL_MS.
        """
        now_ms = int(time.monotonic() * 1000)
        if now_ms - self._last_publish_ms < self.PUBLISH_INTERVAL_MS:
            return
        self._last_publish_ms = now_ms

        # Recompute derived fields
        self._state.our_team.update_from_players()
        self._state.enemy_team.update_from_players()
        self._state.compute_differentials()
        self._state.game_time_str = self._state.format_game_time()
        self._state.last_update_ms = now_ms
        self._state.update_count += 1

        # Prune old events (keep last 60 seconds)
        cutoff = self._state.game_time_sec - 60.0
        self._state.recent_kills = [
            k for k in self._recent_kills
            if k.get("game_time_sec", 0) > cutoff
        ]
        self._state.recent_objectives = [
            o for o in self._recent_objectives
            if o.get("game_time_sec", 0) > cutoff
        ]

        # Publish fused state
        payload = self._state.to_dict()
        msg = self._factory.create(
            CH_LIVE_GAME_STATE, payload, priority=1, ttl_ms=2000,
        )
        self._transport.publish(msg)

    def shutdown(self) -> Dict[str, Any]:
        """Unsubscribe and return stats."""
        for unsub in self._unsubs:
            unsub()
        return {
            "update_count": self._state.update_count,
            "total_kills_tracked": len(self._recent_kills),
            "total_objectives_tracked": len(self._recent_objectives),
            "final_phase": self._state.phase.value,
        }

    # -- Subscription handlers ------------------------------------------

    def _on_gameflow(self, msg: ChannelMessage) -> None:
        """Handle gameflow phase change."""
        phase_str = msg.payload.get("phase", "None")
        self._state.gameflow_raw = phase_str
        self._state.phase = GamePhase.from_gameflow_and_time(
            phase_str, self._state.game_time_sec,
        )

    def _on_champ_select(self, msg: ChannelMessage) -> None:
        """Handle champ select updates."""
        self._state.champ_select = msg.payload
        self._state.phase = GamePhase.CHAMP_SELECT

    def _on_scoreboard(self, msg: ChannelMessage) -> None:
        """Handle scoreboard snapshot updates."""
        p = msg.payload
        self._state.game_time_sec = p.get("game_time_sec", 0)

        # Update team totals from scoreboard
        self._state.our_team.total_kills = p.get("our_kills", 0)
        self._state.our_team.total_deaths = p.get("our_deaths", 0)
        self._state.enemy_team.total_kills = p.get("enemy_kills", 0)
        self._state.enemy_team.total_deaths = p.get("enemy_deaths", 0)

        # Re-derive phase from time
        self._state.phase = GamePhase.from_gameflow_and_time(
            self._state.gameflow_raw, self._state.game_time_sec,
        )

    def _on_kill(self, msg: ChannelMessage) -> None:
        """Handle a champion kill event."""
        p = msg.payload
        kill_record = {
            "game_time_sec": p.get("game_time_sec", 0),
            "killer": p.get("killer", ""),
            "victim": p.get("victim", ""),
            "assisters": p.get("assisters", []),
            "is_ally_kill": self._is_ally_player(p.get("killer", "")),
        }
        self._recent_kills.append(kill_record)

    def _on_objective(self, msg: ChannelMessage) -> None:
        """Handle an objective event (dragon, baron, turret, etc.)."""
        p = msg.payload
        event_name = p.get("event_name", "")
        killer = p.get("killer", "")
        is_ally = self._is_ally_player(killer)

        obj_record = {
            "game_time_sec": p.get("game_time_sec", 0),
            "event_name": event_name,
            "killer": killer,
            "is_ally_objective": is_ally,
        }
        self._recent_objectives.append(obj_record)

        # Update objective state
        obj = self._state.objectives
        if "Dragon" in event_name:
            if is_ally:
                obj.dragon_count_ally += 1
            else:
                obj.dragon_count_enemy += 1
        elif "Baron" in event_name:
            obj.baron_alive = False
            obj.baron_timer_sec = p.get("game_time_sec", 0) + 360
        elif "Herald" in event_name:
            obj.herald_alive = False
            if is_ally:
                obj.herald_count_ally += 1
            else:
                obj.herald_count_enemy += 1
        elif "Turret" in event_name:
            if is_ally:
                obj.turrets_enemy -= 1
            else:
                obj.turrets_ally -= 1
        elif "Inhib" in event_name:
            if is_ally:
                self._state.enemy_team.inhibs_destroyed += 1
            else:
                self._state.our_team.inhibs_destroyed += 1

    def _is_ally_player(self, name: str) -> bool:
        """Check if a player name belongs to our team."""
        for p in self._state.our_team.players:
            if p.name == name or p.champion == name:
                return True
        return False

    # -- Direct state access (for testing) ------------------------------

    @property
    def current_state(self) -> GameState:
        """Read the current fused state (for testing / debug)."""
        return self._state

    def stats(self) -> Dict[str, Any]:
        """Component stats for evolution logger."""
        return {
            "phase": self._state.phase.value,
            "game_time": self._state.game_time_str,
            "kill_diff": self._state.kill_diff,
            "gold_diff": self._state.gold_diff,
            "momentum": round(self._state.momentum_score(), 3),
            "update_count": self._state.update_count,
        }

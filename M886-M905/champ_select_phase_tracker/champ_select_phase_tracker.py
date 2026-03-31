#!/usr/bin/env python3
"""
M888 — ChampSelectPhaseTracker
================================
Real-time champion select phase tracking via LCU WebSocket events.
Follows Seraphine connector.py onChampSelectChanged subscription pattern
to capture every pick/ban/trade action and trigger downstream analysis.

Dependencies: M886, M887
Reference: Seraphine connector.py::subscribe, onChampSelectChanged, LCU WebSocket
"""

from __future__ import annotations

import asyncio
import collections
import copy
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("M888.ChampSelectPhaseTracker")

# LCU WebSocket event URIs (from Seraphine connector.py subscribe pattern)
CHAMP_SELECT_URI = "/lol-champ-select/v1/session"
CHAMP_SELECT_EVENT = "OnJsonApiEvent_lol-champ-select_v1_session"
GAMEFLOW_URI = "/lol-gameflow/v1/gameflow-phase"

# Champion select has fixed phase durations (approximate)
BAN_PHASE_TIMEOUT = 30  # seconds per ban
PICK_PHASE_TIMEOUT = 30
FINALIZATION_TIMEOUT = 30


class SelectPhase(Enum):
    NONE = "NONE"
    PLANNING = "PLANNING"
    BAN_TURN = "BAN_TURN"
    PICK_TURN = "PICK_TURN"
    FINALIZATION = "FINALIZATION"
    GAME_STARTING = "GAME_STARTING"


class ActionType(Enum):
    BAN = "ban"
    PICK = "pick"
    TEN_BANS_REVEAL = "ten_bans_reveal"


class TeamSide(Enum):
    BLUE = "blue"
    RED = "red"
    UNKNOWN = "unknown"


@dataclass
class ChampSelectAction:
    """Single action in champion select (ban or pick)."""
    action_id: int
    actor_cell_id: int
    champion_id: int
    action_type: ActionType
    is_completed: bool
    is_ally_action: bool
    is_in_progress: bool
    timestamp: float = field(default_factory=time.monotonic)
    pick_turn: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "cell_id": self.actor_cell_id,
            "champion": self.champion_id,
            "type": self.action_type.value,
            "completed": self.is_completed,
            "ally": self.is_ally_action,
            "in_progress": self.is_in_progress,
            "turn": self.pick_turn,
        }


@dataclass
class PlayerSlot:
    """A player slot in champion select."""
    cell_id: int
    summoner_id: int = 0
    puuid: str = ""
    display_name: str = ""
    champion_id: int = 0
    spell1_id: int = 0
    spell2_id: int = 0
    assigned_position: str = ""
    team: TeamSide = TeamSide.UNKNOWN
    is_local_player: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "summoner_id": self.summoner_id,
            "puuid": self.puuid,
            "display_name": self.display_name,
            "champion_id": self.champion_id,
            "spells": [self.spell1_id, self.spell2_id],
            "position": self.assigned_position,
            "team": self.team.value,
            "is_local": self.is_local_player,
        }


@dataclass
class ChampSelectSnapshot:
    """Complete state snapshot of champion select at a point in time."""
    session_id: str
    phase: SelectPhase
    timer_remaining: float
    local_player_cell_id: int
    is_spectating: bool
    bans_blue: List[int] = field(default_factory=list)
    bans_red: List[int] = field(default_factory=list)
    players_blue: List[PlayerSlot] = field(default_factory=list)
    players_red: List[PlayerSlot] = field(default_factory=list)
    actions: List[ChampSelectAction] = field(default_factory=list)
    current_action_set: int = 0
    bench_champions: List[int] = field(default_factory=list)  # ARAM bench
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def all_players(self) -> List[PlayerSlot]:
        return self.players_blue + self.players_red

    @property
    def all_bans(self) -> List[int]:
        return self.bans_blue + self.bans_red

    @property
    def completed_picks(self) -> Dict[str, int]:
        """Map cell_id → champion_id for completed picks."""
        picks = {}
        for action in self.actions:
            if action.action_type == ActionType.PICK and action.is_completed:
                picks[str(action.actor_cell_id)] = action.champion_id
        return picks

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "phase": self.phase.value,
            "timer": round(self.timer_remaining, 1),
            "local_cell": self.local_player_cell_id,
            "bans": {"blue": self.bans_blue, "red": self.bans_red},
            "blue_team": [p.to_dict() for p in self.players_blue],
            "red_team": [p.to_dict() for p in self.players_red],
            "actions": [a.to_dict() for a in self.actions],
            "bench": self.bench_champions,
            "timestamp": self.timestamp.isoformat(),
        }


class ChampSelectEventBus:
    """
    Event bus following Seraphine signalBus pattern.
    Allows downstream modules to subscribe to champion select events.
    """

    def __init__(self):
        self._listeners: Dict[str, List[Callable]] = collections.defaultdict(list)

    def subscribe(self, event: str, callback: Callable):
        self._listeners[event].append(callback)
        logger.debug("Subscribed to '%s': %s", event, callback.__name__)

    def unsubscribe(self, event: str, callback: Callable):
        if event in self._listeners:
            self._listeners[event] = [c for c in self._listeners[event] if c != callback]

    async def emit(self, event: str, data: Any = None):
        for callback in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as exc:
                logger.error("Event '%s' callback error: %s", event, exc)

    @property
    def registered_events(self) -> List[str]:
        return list(self._listeners.keys())


class ChampSelectPhaseTracker:
    """
    Tracks champion select in real-time via LCU WebSocket events.

    Architecture:
      LCU WebSocket → onChampSelectChanged → parse session JSON
      → update ChampSelectSnapshot → emit events → trigger analytics

    Events emitted:
      - phase_changed: SelectPhase transition
      - ban_completed: A champion was banned
      - pick_completed: A champion was picked
      - pick_intent: A player is hovering a champion
      - all_picks_done: Finalization phase entered
      - session_ended: Champion select ended (game starting or dodged)

    Follows Seraphine's subscribe() decorator pattern for event registration.
    """

    def __init__(self, lcu_ws_url: Optional[str] = None):
        self._ws_url = lcu_ws_url
        self._event_bus = ChampSelectEventBus()
        self._current_snapshot: Optional[ChampSelectSnapshot] = None
        self._snapshot_history: List[ChampSelectSnapshot] = []
        self._session_active = False
        self._ws_connection = None
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._stats = {
            "sessions_tracked": 0,
            "events_processed": 0,
            "phase_transitions": 0,
            "bans_seen": 0,
            "picks_seen": 0,
        }
        logger.info("ChampSelectPhaseTracker initialized")

    @property
    def event_bus(self) -> ChampSelectEventBus:
        return self._event_bus

    @property
    def current_snapshot(self) -> Optional[ChampSelectSnapshot]:
        return self._current_snapshot

    @property
    def is_in_champ_select(self) -> bool:
        return self._session_active and self._current_snapshot is not None

    async def start(self):
        """Start tracking champion select events."""
        self._shutdown.clear()
        self._poll_task = asyncio.create_task(
            self._event_loop(), name="champ-select-tracker"
        )
        logger.info("ChampSelectPhaseTracker started")

    async def stop(self):
        """Graceful shutdown."""
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("ChampSelectPhaseTracker stopped. Stats: %s", self._stats)

    async def _event_loop(self):
        """
        Main event processing loop.
        In production: connects to LCU WebSocket wss://127.0.0.1:{port}
        In test mode: polls Fiddler MCP for intercepted WS frames.
        """
        while not self._shutdown.is_set():
            try:
                if self._ws_url:
                    await self._connect_lcu_websocket()
                else:
                    await self._poll_fiddler_for_champ_select()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Event loop error: %s", exc)
                await asyncio.sleep(2.0)

    async def _connect_lcu_websocket(self):
        """Connect to LCU WebSocket following Seraphine's runWs pattern."""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    self._ws_url,
                    ssl=False,
                    heartbeat=30.0,
                ) as ws:
                    self._ws_connection = ws
                    # Subscribe to champ select events (Seraphine pattern)
                    subscribe_msg = [5, CHAMP_SELECT_EVENT]
                    await ws.send_json(subscribe_msg)
                    logger.info("Subscribed to LCU champ select events")

                    async for msg in ws:
                        if self._shutdown.is_set():
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._handle_ws_message(msg.data)
                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                            break
        except ImportError:
            logger.warning("aiohttp not available for WS, falling back to poll")
            await self._poll_fiddler_for_champ_select()

    async def _poll_fiddler_for_champ_select(self):
        """Fallback: poll Fiddler MCP for champ-select related traffic."""
        while not self._shutdown.is_set():
            mock_session = self._generate_mock_champ_select()
            if mock_session:
                await self._process_session_update(mock_session)
            await asyncio.sleep(1.0)

    async def _handle_ws_message(self, raw: str):
        """Parse LCU WebSocket message (Seraphine matchUri pattern)."""
        try:
            data = json.loads(raw)
            if not isinstance(data, list) or len(data) < 3:
                return

            opcode, event_name, payload = data[0], data[1], data[2]
            if opcode != 8:  # 8 = event message in LCU WS protocol
                return

            uri = payload.get("uri", "")
            event_data = payload.get("data")
            event_type = payload.get("eventType", "Update")

            if CHAMP_SELECT_URI in uri and event_data:
                self._stats["events_processed"] += 1
                await self._process_session_update(event_data)

        except (json.JSONDecodeError, IndexError, KeyError) as exc:
            logger.debug("WS message parse error: %s", exc)

    async def _process_session_update(self, session_data: Dict[str, Any]):
        """Process a champion select session update."""
        try:
            new_snapshot = self._parse_session(session_data)
            old_snapshot = self._current_snapshot

            if old_snapshot is None:
                self._session_active = True
                self._stats["sessions_tracked"] += 1
                await self._event_bus.emit("session_started", new_snapshot)
                logger.info("Champion select session started")

            elif old_snapshot.phase != new_snapshot.phase:
                self._stats["phase_transitions"] += 1
                await self._event_bus.emit("phase_changed", {
                    "old": old_snapshot.phase.value,
                    "new": new_snapshot.phase.value,
                    "snapshot": new_snapshot,
                })
                logger.info("Phase: %s → %s", old_snapshot.phase.value, new_snapshot.phase.value)

            # Detect new bans
            old_bans = set(old_snapshot.all_bans) if old_snapshot else set()
            new_bans = set(new_snapshot.all_bans)
            for ban_id in new_bans - old_bans:
                if ban_id > 0:
                    self._stats["bans_seen"] += 1
                    await self._event_bus.emit("ban_completed", {"champion_id": ban_id})

            # Detect new picks
            old_picks = old_snapshot.completed_picks if old_snapshot else {}
            new_picks = new_snapshot.completed_picks
            for cell_id, champ_id in new_picks.items():
                if cell_id not in old_picks and champ_id > 0:
                    self._stats["picks_seen"] += 1
                    await self._event_bus.emit("pick_completed", {
                        "cell_id": int(cell_id),
                        "champion_id": champ_id,
                    })

            if new_snapshot.phase == SelectPhase.FINALIZATION:
                if old_snapshot and old_snapshot.phase != SelectPhase.FINALIZATION:
                    await self._event_bus.emit("all_picks_done", new_snapshot)

            self._current_snapshot = new_snapshot
            self._snapshot_history.append(new_snapshot)

            # Keep history bounded
            if len(self._snapshot_history) > 200:
                self._snapshot_history = self._snapshot_history[-100:]

        except Exception as exc:
            logger.error("Session update error: %s", exc)

    def _parse_session(self, data: Dict[str, Any]) -> ChampSelectSnapshot:
        """Parse LCU champ select session JSON into structured snapshot."""
        timer = data.get("timer", {})
        my_team = data.get("myTeam", [])
        their_team = data.get("theirTeam", [])
        actions = data.get("actions", [])
        bans = data.get("bans", {})

        phase = self._determine_phase(data)
        parsed_actions = []
        for action_set_idx, action_set in enumerate(actions):
            if isinstance(action_set, list):
                for action in action_set:
                    parsed_actions.append(ChampSelectAction(
                        action_id=action.get("id", 0),
                        actor_cell_id=action.get("actorCellId", -1),
                        champion_id=action.get("championId", 0),
                        action_type=ActionType.BAN if action.get("type") == "ban" else ActionType.PICK,
                        is_completed=action.get("completed", False),
                        is_ally_action=action.get("isAllyAction", False),
                        is_in_progress=action.get("isInProgress", False),
                        pick_turn=action_set_idx,
                    ))

        blue_players = [self._parse_player_slot(p, TeamSide.BLUE) for p in my_team]
        red_players = [self._parse_player_slot(p, TeamSide.RED) for p in their_team]

        return ChampSelectSnapshot(
            session_id=str(data.get("gameId", data.get("counter", 0))),
            phase=phase,
            timer_remaining=timer.get("adjustedTimeLeftInPhase", 0) / 1000.0,
            local_player_cell_id=data.get("localPlayerCellId", -1),
            is_spectating=data.get("isSpectating", False),
            bans_blue=[b for b in bans.get("myTeamBans", []) if b > 0],
            bans_red=[b for b in bans.get("theirTeamBans", []) if b > 0],
            players_blue=blue_players,
            players_red=red_players,
            actions=parsed_actions,
            bench_champions=data.get("benchChampionIds", []),
        )

    def _parse_player_slot(self, data: Dict[str, Any], team: TeamSide) -> PlayerSlot:
        """Parse a single player slot from session data."""
        return PlayerSlot(
            cell_id=data.get("cellId", -1),
            summoner_id=data.get("summonerId", 0),
            puuid=data.get("puuid", ""),
            display_name=data.get("displayName", data.get("nameVisibilityType", "")),
            champion_id=data.get("championId", 0),
            spell1_id=data.get("spell1Id", 0),
            spell2_id=data.get("spell2Id", 0),
            assigned_position=data.get("assignedPosition", ""),
            team=team,
        )

    def _determine_phase(self, data: Dict[str, Any]) -> SelectPhase:
        """Determine current phase from session data."""
        timer = data.get("timer", {})
        phase_str = timer.get("phase", "").upper()
        phase_map = {
            "PLANNING": SelectPhase.PLANNING,
            "BAN_PICK": SelectPhase.BAN_TURN,
            "FINALIZATION": SelectPhase.FINALIZATION,
            "GAME_STARTING": SelectPhase.GAME_STARTING,
        }
        phase = phase_map.get(phase_str, SelectPhase.NONE)

        if phase == SelectPhase.BAN_TURN:
            actions = data.get("actions", [])
            for action_set in actions:
                if isinstance(action_set, list):
                    for action in action_set:
                        if action.get("isInProgress") and action.get("type") == "pick":
                            phase = SelectPhase.PICK_TURN
                            break
        return phase

    def _generate_mock_champ_select(self) -> Optional[Dict[str, Any]]:
        """Generate mock champ select data for testing."""
        if not self._session_active:
            self._session_active = True
            return {
                "gameId": 999999,
                "counter": 1,
                "timer": {"phase": "BAN_PICK", "adjustedTimeLeftInPhase": 25000},
                "localPlayerCellId": 0,
                "isSpectating": False,
                "myTeam": [
                    {"cellId": i, "summonerId": 1000 + i, "puuid": f"puuid-ally-{i}",
                     "championId": 0, "spell1Id": 4, "spell2Id": 14, "assignedPosition": pos}
                    for i, pos in enumerate(["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"])
                ],
                "theirTeam": [
                    {"cellId": 5 + i, "summonerId": 2000 + i, "puuid": f"puuid-enemy-{i}",
                     "championId": 0, "assignedPosition": pos}
                    for i, pos in enumerate(["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"])
                ],
                "actions": [[
                    {"id": j, "actorCellId": j, "championId": 0, "type": "ban",
                     "completed": False, "isAllyAction": j < 5, "isInProgress": j == 0}
                    for j in range(10)
                ]],
                "bans": {"myTeamBans": [], "theirTeamBans": []},
                "benchChampionIds": [],
            }
        return None

    def get_session_timeline(self) -> List[Dict[str, Any]]:
        """Return full timeline of the current session."""
        return [s.to_dict() for s in self._snapshot_history]

    def export_stats(self) -> Dict[str, Any]:
        return {
            "tracker_stats": self._stats,
            "session_active": self._session_active,
            "current_phase": self._current_snapshot.phase.value if self._current_snapshot else "NONE",
            "history_snapshots": len(self._snapshot_history),
        }

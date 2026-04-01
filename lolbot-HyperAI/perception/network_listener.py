#!/usr/bin/env python3
"""
perception/network_listener.py — Network Packet Capture & Parse
=================================================================
lolbot-HyperAI · Perception Layer

In Apollo, the sensor drivers (LiDAR, camera, radar) sit at the bottom
of the perception stack. They capture raw data from hardware and publish
typed messages to the cyber bus. Our "sensor" is the LoL client's network
traffic — captured via Fiddler proxy, Proxifier tunneling, or direct
LCU (League Client Update) WebSocket.

This module:
    1. Connects to the data source (Fiddler/LCU/replay file)
    2. Classifies each packet by endpoint category
    3. Parses the JSON payload
    4. Publishes typed ChannelMessages to the CAN bus

Data sources (auto-detected, priority order):
    1. Fiddler proxy export (richest data — sees all HTTPS)
    2. LCU WebSocket (official, but limited to client-side events)
    3. Replay file (offline, for evolution evaluation)

Endpoint categories (from M1046-M1065 capture/network_capture_engine.py):
    - GAMEFLOW: /lol-gameflow/v1/gameflow-phase
    - CHAMP_SELECT: /lol-champ-select/v1/session
    - LIVE_GAME: /liveclientdata/allgamedata
    - MATCH_HISTORY: /lol-match-history/v1/products/lol
    - SUMMONER: /lol-summoner/v1/current-summoner

The listener is a TimerComponent with a 50ms Proc() cycle.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.error import URLError

# Relative imports from sibling packages
sys.path.insert(0, str(Path(__file__).parent.parent))
from canbus.channel_message import (
    CH_CHAMP_SELECT_STATE,
    CH_GAME_FLOW_PHASE,
    CH_ITEM_PURCHASE,
    CH_KILL_EVENT,
    CH_LIVE_GAME_STATE,
    CH_MINIMAP_EVENTS,
    CH_OBJECTIVE_EVENT,
    CH_RAW_NETWORK_PACKET,
    CH_SCOREBOARD_SNAPSHOT,
    CH_SYSTEM_ERROR,
    CH_SYSTEM_HEARTBEAT,
    CH_WARD_EVENT,
    ChannelMessage,
    MessageFactory,
)
from canbus.transport import Transport


# ---------------------------------------------------------------------------
# Endpoint classification
# ---------------------------------------------------------------------------
class EndpointCategory(Enum):
    """Categories of LoL client API endpoints."""
    GAMEFLOW = auto()
    CHAMP_SELECT = auto()
    LIVE_CLIENT_DATA = auto()
    MATCH_HISTORY = auto()
    SUMMONER = auto()
    RANKED_STATS = auto()
    LOBBY = auto()
    UNKNOWN = auto()


# Regex patterns for URL → category mapping
_ENDPOINT_PATTERNS: List[Tuple[re.Pattern, EndpointCategory]] = [
    (re.compile(r"/lol-gameflow/v1/gameflow-phase"), EndpointCategory.GAMEFLOW),
    (re.compile(r"/lol-gameflow/v1/session"), EndpointCategory.GAMEFLOW),
    (re.compile(r"/lol-champ-select/v1/session"), EndpointCategory.CHAMP_SELECT),
    (re.compile(r"/lol-champ-select/v1/"), EndpointCategory.CHAMP_SELECT),
    (re.compile(r"/liveclientdata/"), EndpointCategory.LIVE_CLIENT_DATA),
    (re.compile(r"/lol-match-history/"), EndpointCategory.MATCH_HISTORY),
    (re.compile(r"/lol-summoner/"), EndpointCategory.SUMMONER),
    (re.compile(r"/lol-ranked/"), EndpointCategory.RANKED_STATS),
    (re.compile(r"/lol-lobby/"), EndpointCategory.LOBBY),
]


def classify_endpoint(url: str) -> EndpointCategory:
    """Classify a URL path into an endpoint category."""
    for pattern, category in _ENDPOINT_PATTERNS:
        if pattern.search(url):
            return category
    return EndpointCategory.UNKNOWN


# ---------------------------------------------------------------------------
# Data source abstraction
# ---------------------------------------------------------------------------
class CaptureMode(Enum):
    """How we're capturing network data."""
    LCU_POLLING = "lcu_polling"
    LCU_WEBSOCKET = "lcu_websocket"
    FIDDLER_PROXY = "fiddler_proxy"
    REPLAY_FILE = "replay_file"
    MOCK = "mock"


@dataclass
class CapturedPacket:
    """A single captured network packet/response."""
    url: str
    method: str
    status_code: int
    body: Any
    headers: Dict[str, str]
    timestamp_ms: int
    category: EndpointCategory = EndpointCategory.UNKNOWN
    capture_mode: CaptureMode = CaptureMode.LCU_POLLING
    raw_size_bytes: int = 0


# ---------------------------------------------------------------------------
# LCU Connection Manager
# ---------------------------------------------------------------------------
class LCUConnection:
    """
    Manages connection to the League Client Update (LCU) API.

    The LCU runs a local HTTPS server with a self-signed cert.
    Auth is via Basic auth with username "riot" and a password
    found in the lockfile at:
        <LoL install>/lockfile

    Format: <process_name>:<pid>:<port>:<password>:<protocol>
    Example: LeagueClient:12345:52846:abc123def:https
    """

    # Common lockfile locations
    _LOCKFILE_PATHS = [
        Path("C:/Riot Games/League of Legends/lockfile"),
        Path("D:/Riot Games/League of Legends/lockfile"),
        Path(os.path.expanduser("~/Riot Games/League of Legends/lockfile")),
        # macOS
        Path("/Applications/League of Legends.app/Contents/LoL/lockfile"),
    ]

    def __init__(self) -> None:
        self.port: Optional[int] = None
        self.password: Optional[str] = None
        self.protocol: str = "https"
        self.pid: Optional[int] = None
        self._connected = False
        self._ssl_ctx: Optional[ssl.SSLContext] = None

    def detect(self) -> bool:
        """
        Try to find and parse the LCU lockfile.

        Returns True if the client is running and we found credentials.
        """
        for lockfile_path in self._LOCKFILE_PATHS:
            if lockfile_path.exists():
                return self._parse_lockfile(lockfile_path)

        # Try to find via process list (Windows)
        return self._detect_from_process()

    def _parse_lockfile(self, path: Path) -> bool:
        """Parse the lockfile and extract connection info."""
        try:
            content = path.read_text().strip()
            parts = content.split(":")
            if len(parts) >= 5:
                self.pid = int(parts[1])
                self.port = int(parts[2])
                self.password = parts[3]
                self.protocol = parts[4]
                self._setup_ssl()
                self._connected = True
                return True
        except (ValueError, PermissionError, OSError):
            pass
        return False

    def _detect_from_process(self) -> bool:
        """
        Fallback: scan running processes for LeagueClientUx.

        On Windows, the command line contains --remoting-auth-token
        and --app-port which give us the credentials directly.
        """
        try:
            import subprocess
            result = subprocess.run(
                ["wmic", "PROCESS", "WHERE",
                 "name='LeagueClientUx.exe'", "GET", "CommandLine"],
                capture_output=True, text=True, timeout=5,
            )
            output = result.stdout
            port_match = re.search(r"--app-port=(\d+)", output)
            token_match = re.search(r"--remoting-auth-token=([^\s\"]+)", output)
            if port_match and token_match:
                self.port = int(port_match.group(1))
                self.password = token_match.group(1)
                self._setup_ssl()
                self._connected = True
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return False

    def _setup_ssl(self) -> None:
        """Create an SSL context that accepts the LCU self-signed cert."""
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE

    @property
    def base_url(self) -> str:
        return f"{self.protocol}://127.0.0.1:{self.port}"

    @property
    def auth_header(self) -> str:
        """Basic auth header value."""
        token = base64.b64encode(
            f"riot:{self.password}".encode()
        ).decode()
        return f"Basic {token}"

    @property
    def is_connected(self) -> bool:
        return self._connected

    def request(
        self,
        path: str,
        method: str = "GET",
        timeout: float = 2.0,
    ) -> Optional[CapturedPacket]:
        """
        Make a request to the LCU API.

        Returns a CapturedPacket or None on error.
        """
        if not self._connected:
            return None

        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("Authorization", self.auth_header)
        req.add_header("Accept", "application/json")

        try:
            resp = urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=timeout,
            )
            raw = resp.read()
            body = json.loads(raw) if raw else None
            return CapturedPacket(
                url=path,
                method=method,
                status_code=resp.status,
                body=body,
                headers=dict(resp.headers),
                timestamp_ms=int(time.monotonic() * 1000),
                category=classify_endpoint(path),
                capture_mode=CaptureMode.LCU_POLLING,
                raw_size_bytes=len(raw),
            )
        except (URLError, json.JSONDecodeError, OSError, TimeoutError):
            return None


# ---------------------------------------------------------------------------
# Live Client Data API (in-game only)
# ---------------------------------------------------------------------------
class LiveClientAPI:
    """
    Connects to the Riot Live Client Data API.

    Available only during an active game at https://127.0.0.1:2999.
    Provides real-time game data: all players, events, game time.

    This is separate from LCU — it's a different process (the game itself).
    """

    BASE_URL = "https://127.0.0.1:2999"

    _ENDPOINTS = {
        "all_game_data": "/liveclientdata/allgamedata",
        "active_player": "/liveclientdata/activeplayer",
        "all_players": "/liveclientdata/playerlist",
        "events": "/liveclientdata/eventdata",
        "game_stats": "/liveclientdata/gamestats",
    }

    def __init__(self) -> None:
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE
        self._available = False
        self._last_check_ms = 0
        self._check_interval_ms = 2000

    def is_available(self) -> bool:
        """Check if the live client API is reachable (game is running)."""
        now = int(time.monotonic() * 1000)
        if now - self._last_check_ms < self._check_interval_ms:
            return self._available
        self._last_check_ms = now

        try:
            req = urllib.request.Request(
                f"{self.BASE_URL}/liveclientdata/gamestats",
            )
            resp = urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=1.0,
            )
            self._available = resp.status == 200
        except (URLError, OSError, TimeoutError):
            self._available = False
        return self._available

    def fetch(self, endpoint_key: str) -> Optional[CapturedPacket]:
        """
        Fetch data from a live client endpoint.

        Args:
            endpoint_key: One of: all_game_data, active_player,
                          all_players, events, game_stats.
        """
        path = self._ENDPOINTS.get(endpoint_key)
        if path is None:
            return None

        url = f"{self.BASE_URL}{path}"
        req = urllib.request.Request(url)

        try:
            resp = urllib.request.urlopen(
                req, context=self._ssl_ctx, timeout=2.0,
            )
            raw = resp.read()
            body = json.loads(raw) if raw else None
            return CapturedPacket(
                url=path,
                method="GET",
                status_code=resp.status,
                body=body,
                headers=dict(resp.headers),
                timestamp_ms=int(time.monotonic() * 1000),
                category=EndpointCategory.LIVE_CLIENT_DATA,
                capture_mode=CaptureMode.LCU_POLLING,
                raw_size_bytes=len(raw),
            )
        except (URLError, json.JSONDecodeError, OSError, TimeoutError):
            return None


# ---------------------------------------------------------------------------
# Network Listener Component
# ---------------------------------------------------------------------------
class NetworkListener:
    """
    Main perception component: captures network data and publishes
    parsed events to the CAN bus.

    Lifecycle (Apollo-style):
        Init() → detect data source
        Proc() → poll/read → parse → publish (called every 50-100ms)
        Shutdown() → close connections

    The listener auto-detects the best available data source:
        1. If Fiddler proxy is running → use it (richest data)
        2. If LCU is available → poll it
        3. If replay file specified → use it
        4. Else → mock mode for testing

    In game (InProgress phase), we additionally poll the Live Client
    Data API for real-time scoreboard/events.
    """

    PROC_INTERVAL_MS = 50  # 20 Hz polling rate

    # LCU endpoints to poll and their intervals
    _LCU_POLL_CONFIG: List[Tuple[str, int]] = [
        # (endpoint_path, poll_interval_ms)
        ("/lol-gameflow/v1/gameflow-phase", 500),
        ("/lol-champ-select/v1/session", 1000),
        ("/lol-lobby/v2/lobby", 2000),
        ("/lol-summoner/v1/current-summoner", 10000),
    ]

    # Live Client endpoints and intervals (during game)
    _LIVE_POLL_CONFIG: List[Tuple[str, int]] = [
        ("all_game_data", 500),
        ("events", 200),
    ]

    def __init__(
        self,
        transport: Transport,
        *,
        replay_path: Optional[Path] = None,
    ) -> None:
        self._transport = transport
        self._factory = MessageFactory("perception.network_listener")
        self._replay_path = replay_path

        # Data sources
        self._lcu = LCUConnection()
        self._live_api = LiveClientAPI()
        self._capture_mode = CaptureMode.MOCK
        self._connected = False

        # Polling state
        self._last_poll: Dict[str, int] = {}
        self._poll_errors: Dict[str, int] = defaultdict(int)

        # State tracking (to detect changes and avoid redundant publishes)
        self._last_gameflow_phase: Optional[str] = None
        self._last_champ_select_hash: Optional[str] = None
        self._last_game_data_hash: Optional[str] = None
        self._processed_event_ids: set = set()

        # Stats
        self._total_captures = 0
        self._total_publishes = 0
        self._init_time_ms = int(time.monotonic() * 1000)

    # -- Lifecycle (Apollo pattern) -------------------------------------

    def init(self) -> bool:
        """
        Initialize: detect and connect to the best data source.

        Returns True if any source is available.
        """
        if self._replay_path and self._replay_path.exists():
            self._capture_mode = CaptureMode.REPLAY_FILE
            self._connected = True
            return True

        if self._lcu.detect():
            self._capture_mode = CaptureMode.LCU_POLLING
            self._connected = True
            return True

        # Fallback to mock
        self._capture_mode = CaptureMode.MOCK
        self._connected = True
        return True

    async def proc(self) -> None:
        """
        Single tick of the perception loop.

        Called every PROC_INTERVAL_MS by the scheduler.
        Reads from data source, parses, publishes to bus.
        """
        now_ms = int(time.monotonic() * 1000)

        if self._capture_mode == CaptureMode.LCU_POLLING:
            await self._proc_lcu(now_ms)
        elif self._capture_mode == CaptureMode.REPLAY_FILE:
            pass  # Replay is handled externally by MessageReplayer
        elif self._capture_mode == CaptureMode.MOCK:
            await self._proc_mock(now_ms)

        # Always check live client API if game might be running
        if self._live_api.is_available():
            await self._proc_live_client(now_ms)

        # Heartbeat every 5 seconds
        if now_ms - self._last_poll.get("__heartbeat", 0) > 5000:
            self._publish_heartbeat(now_ms)
            self._last_poll["__heartbeat"] = now_ms

    def shutdown(self) -> Dict[str, Any]:
        """Cleanup and return final stats."""
        self._connected = False
        return {
            "total_captures": self._total_captures,
            "total_publishes": self._total_publishes,
            "capture_mode": self._capture_mode.value,
            "uptime_ms": int(time.monotonic() * 1000) - self._init_time_ms,
            "poll_errors": dict(self._poll_errors),
        }

    # -- LCU polling implementation -------------------------------------

    async def _proc_lcu(self, now_ms: int) -> None:
        """Poll LCU endpoints at their configured intervals."""
        for path, interval_ms in self._LCU_POLL_CONFIG:
            last = self._last_poll.get(path, 0)
            if now_ms - last < interval_ms:
                continue
            self._last_poll[path] = now_ms

            packet = self._lcu.request(path)
            if packet is None:
                self._poll_errors[path] += 1
                continue

            self._total_captures += 1
            self._dispatch_lcu_packet(packet)

    def _dispatch_lcu_packet(self, packet: CapturedPacket) -> None:
        """Parse an LCU packet and publish to the appropriate channel."""
        cat = packet.category

        if cat == EndpointCategory.GAMEFLOW:
            self._handle_gameflow(packet)
        elif cat == EndpointCategory.CHAMP_SELECT:
            self._handle_champ_select(packet)
        elif cat == EndpointCategory.LIVE_CLIENT_DATA:
            self._handle_live_game_data(packet)

        # Always publish raw packet for modules that want unfiltered data
        raw_msg = self._factory.create(
            CH_RAW_NETWORK_PACKET,
            {
                "url": packet.url,
                "method": packet.method,
                "status_code": packet.status_code,
                "category": cat.name,
                "body_size": packet.raw_size_bytes,
                "capture_mode": packet.capture_mode.value,
            },
            ttl_ms=2000,
        )
        self._transport.publish(raw_msg)
        self._total_publishes += 1

    def _handle_gameflow(self, packet: CapturedPacket) -> None:
        """Handle gameflow phase changes."""
        phase = packet.body
        if isinstance(phase, str) and phase != self._last_gameflow_phase:
            self._last_gameflow_phase = phase
            msg = self._factory.create(
                CH_GAME_FLOW_PHASE,
                {
                    "phase": phase,
                    "previous_phase": self._last_gameflow_phase,
                    "transition_time_ms": packet.timestamp_ms,
                },
                priority=2,  # Phase changes are high priority
                ttl_ms=30000,
            )
            self._transport.publish(msg)
            self._total_publishes += 1

    def _handle_champ_select(self, packet: CapturedPacket) -> None:
        """Handle champion select session updates."""
        if not isinstance(packet.body, dict):
            return

        # Hash to detect actual changes (champ select polls frequently)
        body_str = json.dumps(packet.body, sort_keys=True)
        body_hash = str(hash(body_str))
        if body_hash == self._last_champ_select_hash:
            return
        self._last_champ_select_hash = body_hash

        # Extract structured data
        actions = packet.body.get("actions", [])
        my_team = packet.body.get("myTeam", [])
        their_team = packet.body.get("theirTeam", [])
        timer = packet.body.get("timer", {})
        local_player_cell_id = packet.body.get("localPlayerCellId", -1)

        payload = {
            "my_team": [
                {
                    "cell_id": p.get("cellId"),
                    "champion_id": p.get("championId", 0),
                    "champion_pick_intent": p.get("championPickIntent", 0),
                    "summoner_id": p.get("summonerId"),
                    "assigned_position": p.get("assignedPosition", ""),
                    "spell1_id": p.get("spell1Id", 0),
                    "spell2_id": p.get("spell2Id", 0),
                }
                for p in my_team
            ],
            "their_team": [
                {
                    "cell_id": p.get("cellId"),
                    "champion_id": p.get("championId", 0),
                }
                for p in their_team
            ],
            "bans": self._extract_bans(actions),
            "timer_phase": timer.get("phase", ""),
            "timer_remaining_ms": int(
                timer.get("adjustedTimeLeftInPhase", 0) * 1000
            ),
            "local_player_cell_id": local_player_cell_id,
            "is_spectating": packet.body.get("isSpectating", False),
        }

        msg = self._factory.create(
            CH_CHAMP_SELECT_STATE, payload, priority=1, ttl_ms=10000,
        )
        self._transport.publish(msg)
        self._total_publishes += 1

    def _extract_bans(self, actions: List) -> Dict[str, List[int]]:
        """Extract ban champion IDs from champ select actions."""
        bans: Dict[str, List[int]] = {"ally": [], "enemy": []}
        for action_group in actions:
            if not isinstance(action_group, list):
                continue
            for action in action_group:
                if action.get("type") == "ban" and action.get("completed"):
                    champ_id = action.get("championId", 0)
                    if champ_id > 0:
                        if action.get("isAllyAction"):
                            bans["ally"].append(champ_id)
                        else:
                            bans["enemy"].append(champ_id)
        return bans

    # -- Live Client Data (in-game) ------------------------------------

    async def _proc_live_client(self, now_ms: int) -> None:
        """Poll the Live Client Data API during active games."""
        for endpoint_key, interval_ms in self._LIVE_POLL_CONFIG:
            cache_key = f"live_{endpoint_key}"
            last = self._last_poll.get(cache_key, 0)
            if now_ms - last < interval_ms:
                continue
            self._last_poll[cache_key] = now_ms

            packet = self._live_api.fetch(endpoint_key)
            if packet is None:
                continue

            self._total_captures += 1

            if endpoint_key == "all_game_data":
                self._handle_all_game_data(packet)
            elif endpoint_key == "events":
                self._handle_game_events(packet)

    def _handle_all_game_data(self, packet: CapturedPacket) -> None:
        """Parse the full game state from liveclientdata/allgamedata."""
        data = packet.body
        if not isinstance(data, dict):
            return

        game_stats = data.get("gameData", {})
        all_players = data.get("allPlayers", [])
        active_player = data.get("activePlayer", {})
        events = data.get("events", {}).get("Events", [])

        game_time = game_stats.get("gameTime", 0.0)

        # Determine phase from game time
        if game_time < 90:
            phase = "early_laning"
        elif game_time < 840:    # 14 min
            phase = "laning"
        elif game_time < 1500:   # 25 min
            phase = "mid_game"
        else:
            phase = "late_game"

        # Build team rosters
        our_team = []
        enemy_team = []
        active_name = active_player.get("riotIdGameName", "")

        for player in all_players:
            p_info = {
                "name": player.get("riotIdGameName", ""),
                "champion": player.get("championName", ""),
                "level": player.get("level", 1),
                "kills": player.get("scores", {}).get("kills", 0),
                "deaths": player.get("scores", {}).get("deaths", 0),
                "assists": player.get("scores", {}).get("assists", 0),
                "cs": player.get("scores", {}).get("creepScore", 0),
                "gold": player.get("currentGold", 0),
                "items": [
                    item.get("displayName", "")
                    for item in player.get("items", [])
                ],
                "position": player.get("position", ""),
                "is_dead": player.get("isDead", False),
                "respawn_timer": player.get("respawnTimer", 0.0),
                "summoner_spells": {
                    "spell1": player.get("summonerSpells", {}).get(
                        "summonerSpellOne", {}
                    ).get("displayName", ""),
                    "spell2": player.get("summonerSpells", {}).get(
                        "summonerSpellTwo", {}
                    ).get("displayName", ""),
                },
            }
            if player.get("team", "") == "ORDER":
                our_team.append(p_info)
            else:
                enemy_team.append(p_info)

        payload = {
            "game_time_sec": round(game_time, 1),
            "phase": phase,
            "our_team": our_team,
            "enemy_team": enemy_team,
            "active_player": {
                "name": active_name,
                "level": active_player.get("level", 1),
                "gold": active_player.get("currentGold", 0),
                "abilities": {
                    k: {"level": v.get("abilityLevel", 0)}
                    for k, v in active_player.get("abilities", {}).items()
                },
            },
            "map_name": game_stats.get("mapName", ""),
            "game_mode": game_stats.get("gameMode", ""),
        }

        msg = self._factory.create(
            CH_LIVE_GAME_STATE, payload, priority=1, ttl_ms=2000,
        )
        self._transport.publish(msg)
        self._total_publishes += 1

        # Also publish scoreboard snapshot
        self._publish_scoreboard(our_team, enemy_team, game_time)

    def _publish_scoreboard(
        self,
        our_team: List[Dict],
        enemy_team: List[Dict],
        game_time: float,
    ) -> None:
        """Publish a structured scoreboard for prediction/planning."""
        our_kills = sum(p.get("kills", 0) for p in our_team)
        our_deaths = sum(p.get("deaths", 0) for p in our_team)
        enemy_kills = sum(p.get("kills", 0) for p in enemy_team)
        enemy_deaths = sum(p.get("deaths", 0) for p in enemy_team)

        payload = {
            "game_time_sec": round(game_time, 1),
            "our_kills": our_kills,
            "our_deaths": our_deaths,
            "enemy_kills": enemy_kills,
            "enemy_deaths": enemy_deaths,
            "kill_diff": our_kills - enemy_kills,
            "our_total_cs": sum(p.get("cs", 0) for p in our_team),
            "enemy_total_cs": sum(p.get("cs", 0) for p in enemy_team),
        }
        msg = self._factory.create(
            CH_SCOREBOARD_SNAPSHOT, payload, ttl_ms=3000,
        )
        self._transport.publish(msg)
        self._total_publishes += 1

    def _handle_game_events(self, packet: CapturedPacket) -> None:
        """Parse and dispatch individual game events."""
        data = packet.body
        if not isinstance(data, dict):
            return
        events = data.get("Events", [])

        for event in events:
            event_id = event.get("EventID", 0)
            if event_id in self._processed_event_ids:
                continue
            self._processed_event_ids.add(event_id)

            event_name = event.get("EventName", "")
            event_time = event.get("EventTime", 0.0)

            if event_name == "ChampionKill":
                self._publish_kill_event(event, event_time)
            elif event_name in (
                "DragonKill", "BaronKill", "HeraldKill",
                "TurretKilled", "InhibKilled",
            ):
                self._publish_objective_event(event, event_time)

    def _publish_kill_event(
        self, event: Dict, game_time: float,
    ) -> None:
        """Publish a champion kill event."""
        payload = {
            "game_time_sec": round(game_time, 1),
            "killer": event.get("KillerName", ""),
            "victim": event.get("VictimName", ""),
            "assisters": event.get("Assisters", []),
        }
        msg = self._factory.create(
            CH_KILL_EVENT, payload, priority=1, ttl_ms=10000,
        )
        self._transport.publish(msg)
        self._total_publishes += 1

    def _publish_objective_event(
        self, event: Dict, game_time: float,
    ) -> None:
        """Publish an objective (dragon, baron, tower) event."""
        payload = {
            "game_time_sec": round(game_time, 1),
            "event_name": event.get("EventName", ""),
            "killer": event.get("KillerName", ""),
            "assisters": event.get("Assisters", []),
            "stolen": event.get("Stolen", False),
        }
        msg = self._factory.create(
            CH_OBJECTIVE_EVENT, payload, priority=2, ttl_ms=30000,
        )
        self._transport.publish(msg)
        self._total_publishes += 1

    # -- Mock mode (for testing without LoL client) ---------------------

    async def _proc_mock(self, now_ms: int) -> None:
        """Generate synthetic data for testing."""
        if now_ms - self._last_poll.get("mock", 0) < 1000:
            return
        self._last_poll["mock"] = now_ms

        msg = self._factory.create(
            CH_GAME_FLOW_PHASE,
            {
                "phase": "None",
                "previous_phase": "None",
                "transition_time_ms": now_ms,
            },
        )
        self._transport.publish(msg)
        self._total_publishes += 1

    # -- Heartbeat ------------------------------------------------------

    def _publish_heartbeat(self, now_ms: int) -> None:
        """Publish a system heartbeat for health monitoring."""
        msg = self._factory.create(
            CH_SYSTEM_HEARTBEAT,
            {
                "component": "perception.network_listener",
                "uptime_ms": now_ms - self._init_time_ms,
                "status": "ok" if self._connected else "disconnected",
                "capture_mode": self._capture_mode.value,
                "total_captures": self._total_captures,
                "total_publishes": self._total_publishes,
            },
        )
        self._transport.publish(msg)

    # -- Diagnostics ----------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Component statistics for the evolution logger."""
        return {
            "capture_mode": self._capture_mode.value,
            "connected": self._connected,
            "total_captures": self._total_captures,
            "total_publishes": self._total_publishes,
            "poll_errors": dict(self._poll_errors),
            "lcu_available": self._lcu.is_connected,
            "live_api_available": self._live_api._available,
            "processed_events": len(self._processed_event_ids),
        }

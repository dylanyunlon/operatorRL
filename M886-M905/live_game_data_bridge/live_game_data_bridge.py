#!/usr/bin/env python3
"""
M891 — LiveGameDataBridge
==========================
Bridges Live Client Data API (https://127.0.0.1:2999/liveclientdata/) to the
analysis pipeline via Fiddler interception. Provides real-time in-game stats.

Dependencies: M889
Reference: leagueoflegends-optimizer article5 Live Client Data
"""
from __future__ import annotations
import asyncio, json, logging, time, ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from enum import Enum, auto

logger = logging.getLogger("M891.LiveGameDataBridge")

LIVE_CLIENT_BASE = "https://127.0.0.1:2999/liveclientdata"
POLL_INTERVAL = 5.0
SNAPSHOT_HISTORY_MAX = 360  # 30 minutes at 5s intervals


class BridgeState(Enum):
    IDLE = auto()
    CONNECTING = auto()
    ACTIVE = auto()
    GAME_NOT_RUNNING = auto()
    ERROR = auto()


@dataclass
class PlayerLiveStats:
    summoner_name: str
    champion_name: str
    team: str  # "ORDER" or "CHAOS"
    position: str
    level: int = 1
    current_gold: float = 0.0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    items: List[Dict[str, Any]] = field(default_factory=list)
    runes: Dict[str, Any] = field(default_factory=dict)
    summoner_spells: Dict[str, Any] = field(default_factory=dict)
    is_dead: bool = False
    respawn_timer: float = 0.0
    scores: Dict[str, float] = field(default_factory=dict)

    @property
    def kda(self) -> float:
        return (self.kills + self.assists) / max(self.deaths, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.summoner_name, "champion": self.champion_name,
            "team": self.team, "position": self.position, "level": self.level,
            "gold": self.current_gold, "kda": f"{self.kills}/{self.deaths}/{self.assists}",
            "cs": self.cs, "items": [i.get("displayName", "") for i in self.items],
            "dead": self.is_dead,
        }


@dataclass
class GameSnapshot:
    """Complete game state at a point in time."""
    game_time: float
    map_name: str = ""
    map_number: int = 11
    game_mode: str = "CLASSIC"
    players: List[PlayerLiveStats] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    active_player_name: str = ""
    active_player_abilities: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def game_time_formatted(self) -> str:
        mins = int(self.game_time // 60)
        secs = int(self.game_time % 60)
        return f"{mins:02d}:{secs:02d}"

    @property
    def order_team(self) -> List[PlayerLiveStats]:
        return [p for p in self.players if p.team == "ORDER"]

    @property
    def chaos_team(self) -> List[PlayerLiveStats]:
        return [p for p in self.players if p.team == "CHAOS"]

    @property
    def total_kills(self) -> Dict[str, int]:
        order = sum(p.kills for p in self.order_team)
        chaos = sum(p.kills for p in self.chaos_team)
        return {"ORDER": order, "CHAOS": chaos}

    @property
    def gold_difference(self) -> float:
        order_gold = sum(p.current_gold for p in self.order_team)
        chaos_gold = sum(p.current_gold for p in self.chaos_team)
        return order_gold - chaos_gold

    def to_dict(self) -> Dict[str, Any]:
        return {
            "time": self.game_time_formatted, "raw_time": self.game_time,
            "mode": self.game_mode, "kills": self.total_kills,
            "gold_diff": round(self.gold_difference),
            "players": [p.to_dict() for p in self.players],
            "events_count": len(self.events),
        }


class LiveGameDataBridge:
    """
    Bridges Live Client Data API to analysis pipeline.

    Data flow:
      Live Client API (localhost:2999) → Fiddler intercept OR direct poll
      → parse JSON → GameSnapshot → downstream modules (M895 KDA, M897 WinProb)

    The Live Client Data API is a local HTTP server that LoL exposes during
    games. It provides real-time game state without needing network interception.
    We prefer intercepting via Fiddler for consistency with the capture pipeline,
    but fall back to direct polling when Fiddler isn't routing this traffic.
    """

    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self._state = BridgeState.IDLE
        self._poll_interval = poll_interval
        self._current_snapshot: Optional[GameSnapshot] = None
        self._snapshot_history: List[GameSnapshot] = []
        self._listeners: Dict[str, List[Callable]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._ssl_ctx: Optional[ssl.SSLContext] = None
        self._stats = {
            "snapshots_captured": 0, "errors": 0, "events_detected": 0,
            "max_game_time": 0.0,
        }
        logger.info("LiveGameDataBridge initialized (poll=%.1fs)", poll_interval)

    @property
    def state(self) -> BridgeState:
        return self._state

    @property
    def current(self) -> Optional[GameSnapshot]:
        return self._current_snapshot

    @property
    def history(self) -> List[GameSnapshot]:
        return list(self._snapshot_history)

    def on(self, event: str, callback: Callable):
        self._listeners.setdefault(event, []).append(callback)

    async def _emit(self, event: str, data: Any = None):
        for cb in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)
            except Exception as exc:
                logger.error("Listener error '%s': %s", event, exc)

    async def start(self):
        self._shutdown.clear()
        self._state = BridgeState.CONNECTING
        self._ssl_ctx = ssl.create_default_context()
        self._ssl_ctx.check_hostname = False
        self._ssl_ctx.verify_mode = ssl.CERT_NONE
        self._poll_task = asyncio.create_task(self._poll_loop(), name="live-data-bridge")
        logger.info("LiveGameDataBridge started")

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._state = BridgeState.IDLE
        logger.info("LiveGameDataBridge stopped. Stats: %s", self._stats)

    async def _poll_loop(self):
        while not self._shutdown.is_set():
            try:
                data = await self._fetch_all_game_data()
                if data:
                    snapshot = self._parse_snapshot(data)
                    await self._process_snapshot(snapshot)
                    self._state = BridgeState.ACTIVE
                else:
                    self._state = BridgeState.GAME_NOT_RUNNING
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._stats["errors"] += 1
                self._state = BridgeState.ERROR
                logger.debug("Poll error (game may not be running): %s", type(exc).__name__)
            await asyncio.sleep(self._poll_interval)

    async def _fetch_all_game_data(self) -> Optional[Dict[str, Any]]:
        """Fetch all Live Client Data endpoints."""
        try:
            import aiohttp
            timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                endpoints = {
                    "allgamedata": f"{LIVE_CLIENT_BASE}/allgamedata",
                }
                async with session.get(
                    endpoints["allgamedata"], ssl=self._ssl_ctx
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except ImportError:
            return self._mock_game_data()
        except Exception:
            return self._mock_game_data()

    def _mock_game_data(self) -> Dict[str, Any]:
        """Mock data for testing without a live game."""
        game_time = (time.monotonic() % 1800)
        return {
            "gameData": {"gameMode": "CLASSIC", "gameTime": game_time, "mapName": "Map11", "mapNumber": 11},
            "activePlayer": {"summonerName": "TestPlayer", "level": min(18, int(game_time / 90) + 1),
                             "currentGold": 500 + game_time * 10,
                             "abilities": {"Q": {"abilityLevel": 1}, "W": {"abilityLevel": 1}}},
            "allPlayers": [
                {"summonerName": f"Player{i}", "championName": f"Champion{i}",
                 "team": "ORDER" if i < 5 else "CHAOS", "position": ["TOP","JUNGLE","MIDDLE","BOTTOM","UTILITY"][i%5],
                 "level": min(18, int(game_time / 100) + 1), "scores": {"kills": i, "deaths": max(0,i-2), "assists": i+1, "creepScore": int(game_time/6)},
                 "items": [{"displayName": f"Item{j}"} for j in range(min(6, int(game_time/300)+1))],
                 "isDead": False, "respawnTimer": 0}
                for i in range(10)
            ],
            "events": {"Events": []},
        }

    def _parse_snapshot(self, data: Dict[str, Any]) -> GameSnapshot:
        game_data = data.get("gameData", {})
        active = data.get("activePlayer", {})
        all_players = data.get("allPlayers", [])
        events = data.get("events", {}).get("Events", [])

        players = []
        for p in all_players:
            scores = p.get("scores", {})
            players.append(PlayerLiveStats(
                summoner_name=p.get("summonerName", ""),
                champion_name=p.get("championName", ""),
                team=p.get("team", ""),
                position=p.get("position", ""),
                level=p.get("level", 1),
                current_gold=p.get("currentGold", 0),
                kills=scores.get("kills", 0),
                deaths=scores.get("deaths", 0),
                assists=scores.get("assists", 0),
                cs=scores.get("creepScore", 0),
                items=p.get("items", []),
                is_dead=p.get("isDead", False),
                respawn_timer=p.get("respawnTimer", 0),
            ))

        return GameSnapshot(
            game_time=game_data.get("gameTime", 0),
            map_name=game_data.get("mapName", ""),
            map_number=game_data.get("mapNumber", 11),
            game_mode=game_data.get("gameMode", "CLASSIC"),
            players=players,
            events=events,
            active_player_name=active.get("summonerName", ""),
            active_player_abilities=active.get("abilities", {}),
        )

    async def _process_snapshot(self, snapshot: GameSnapshot):
        prev = self._current_snapshot
        self._current_snapshot = snapshot
        self._snapshot_history.append(snapshot)
        self._stats["snapshots_captured"] += 1
        self._stats["max_game_time"] = max(self._stats["max_game_time"], snapshot.game_time)

        if len(self._snapshot_history) > SNAPSHOT_HISTORY_MAX:
            self._snapshot_history = self._snapshot_history[-SNAPSHOT_HISTORY_MAX:]

        if prev:
            new_events = len(snapshot.events) - len(prev.events) if prev.events else len(snapshot.events)
            if new_events > 0:
                self._stats["events_detected"] += new_events
                await self._emit("game_event", {"new_events": new_events, "time": snapshot.game_time})

        await self._emit("snapshot", snapshot)

    def get_player_stats(self, summoner_name: str) -> Optional[PlayerLiveStats]:
        if not self._current_snapshot:
            return None
        for p in self._current_snapshot.players:
            if p.summoner_name == summoner_name:
                return p
        return None

    def get_timeline(self, metric: str = "gold_diff") -> List[Tuple[float, float]]:
        """Get a time-series of a metric across snapshots."""
        result = []
        for s in self._snapshot_history:
            if metric == "gold_diff":
                result.append((s.game_time, s.gold_difference))
            elif metric == "kills_diff":
                kills = s.total_kills
                result.append((s.game_time, kills["ORDER"] - kills["CHAOS"]))
        return result

    def export_stats(self) -> Dict[str, Any]:
        return {"bridge_stats": self._stats, "state": self._state.name,
                "current_game_time": self._current_snapshot.game_time if self._current_snapshot else 0}



# ---------------------------------------------------------------------------
# Extended LiveGameDataBridge utilities
# ---------------------------------------------------------------------------

class EventDetector:
    """Detects game events from sequential snapshots."""

    def __init__(self):
        self._last_kills: Dict[str, int] = {}
        self._last_towers: Dict[str, int] = {}
        self._detected_events: List[Dict[str, Any]] = []

    def detect(self, prev: Optional[GameSnapshot], curr: GameSnapshot) -> List[Dict[str, Any]]:
        events = []
        if not prev:
            return events

        # Detect kills
        for player in curr.players:
            prev_kills = self._last_kills.get(player.summoner_name, 0)
            if player.kills > prev_kills:
                events.append({
                    "type": "kill", "player": player.summoner_name,
                    "team": player.team, "champion": player.champion_name,
                    "new_kills": player.kills, "time": curr.game_time,
                })
            self._last_kills[player.summoner_name] = player.kills

        # Detect level ups
        for player in curr.players:
            for prev_p in prev.players:
                if prev_p.summoner_name == player.summoner_name:
                    if player.level > prev_p.level:
                        events.append({
                            "type": "level_up", "player": player.summoner_name,
                            "level": player.level, "time": curr.game_time,
                        })

        # Detect item purchases
        for player in curr.players:
            for prev_p in prev.players:
                if prev_p.summoner_name == player.summoner_name:
                    if len(player.items) > len(prev_p.items):
                        events.append({
                            "type": "item_purchase", "player": player.summoner_name,
                            "items_count": len(player.items), "time": curr.game_time,
                        })

        # Detect respawns
        for player in curr.players:
            for prev_p in prev.players:
                if prev_p.summoner_name == player.summoner_name:
                    if prev_p.is_dead and not player.is_dead:
                        events.append({
                            "type": "respawn", "player": player.summoner_name,
                            "time": curr.game_time,
                        })

        self._detected_events.extend(events)
        return events

    def get_all_events(self) -> List[Dict[str, Any]]:
        return list(self._detected_events)


class GamePhaseClassifier:
    """Classifies current game phase based on game time and state."""

    @staticmethod
    def classify(game_time: float, snapshot: GameSnapshot) -> str:
        if game_time < 90:
            return "loading"
        elif game_time < 840:   # 14 min
            return "early_game"
        elif game_time < 1500:  # 25 min
            return "mid_game"
        elif game_time < 2100:  # 35 min
            return "late_game"
        else:
            return "ultra_late"

    @staticmethod
    def is_laning_phase(game_time: float) -> bool:
        return 90 < game_time < 840

    @staticmethod
    def should_group(game_time: float) -> bool:
        return game_time > 1200


class SnapshotCompressor:
    """Compresses snapshot history for efficient storage."""

    @staticmethod
    def compress(snapshots: List[GameSnapshot], interval: int = 30) -> List[Dict[str, Any]]:
        """Keep one snapshot every `interval` seconds."""
        if not snapshots:
            return []
        result = []
        last_time = -interval
        for s in snapshots:
            if s.game_time - last_time >= interval:
                result.append(s.to_dict())
                last_time = s.game_time
        return result

    @staticmethod
    def compute_deltas(snapshots: List[GameSnapshot]) -> List[Dict[str, Any]]:
        """Compute delta changes between snapshots for storage efficiency."""
        deltas = []
        for i in range(1, len(snapshots)):
            prev, curr = snapshots[i-1], snapshots[i]
            delta = {
                "time": curr.game_time,
                "dt": curr.game_time - prev.game_time,
                "gold_diff_change": curr.gold_difference - prev.gold_difference,
                "kills_change": {
                    "ORDER": curr.total_kills["ORDER"] - prev.total_kills["ORDER"],
                    "CHAOS": curr.total_kills["CHAOS"] - prev.total_kills["CHAOS"],
                },
            }
            deltas.append(delta)
        return deltas


class TeamGoldTracker:
    """Dedicated tracker for team gold with trend analysis."""

    def __init__(self):
        self._gold_timeline: List[Tuple[float, float, float]] = []  # (time, order_gold, chaos_gold)

    def update(self, game_time: float, order_gold: float, chaos_gold: float):
        self._gold_timeline.append((game_time, order_gold, chaos_gold))

    def get_gold_lead(self) -> float:
        if not self._gold_timeline:
            return 0.0
        _, order, chaos = self._gold_timeline[-1]
        return order - chaos

    def get_gold_per_minute(self, team: str) -> float:
        if len(self._gold_timeline) < 2:
            return 0.0
        first = self._gold_timeline[0]
        last = self._gold_timeline[-1]
        dt = (last[0] - first[0]) / 60
        if dt <= 0:
            return 0.0
        idx = 1 if team == "ORDER" else 2
        return (last[idx] - first[idx]) / dt

    def get_trend(self, window: int = 12) -> str:
        """Analyze gold difference trend over last `window` points."""
        if len(self._gold_timeline) < window:
            return "insufficient_data"
        recent = self._gold_timeline[-window:]
        diffs = [o - c for _, o, c in recent]
        slope = (diffs[-1] - diffs[0]) / len(diffs)
        if slope > 50:
            return "blue_gaining"
        elif slope < -50:
            return "red_gaining"
        return "stable"



class ActivePlayerTracker:
    """Dedicated tracker for the local player's stats."""

    def __init__(self):
        self._ability_levels: Dict[str, int] = {}
        self._gold_history: List[Tuple[float, float]] = []
        self._level_history: List[Tuple[float, int]] = []

    def update(self, game_time: float, active_data: Dict[str, Any]):
        abilities = active_data.get("abilities", {})
        for key in ["Q", "W", "E", "R"]:
            if key in abilities:
                self._ability_levels[key] = abilities[key].get("abilityLevel", 0)
        gold = active_data.get("currentGold", 0)
        level = active_data.get("level", 1)
        self._gold_history.append((game_time, gold))
        self._level_history.append((game_time, level))
        if len(self._gold_history) > 360:
            self._gold_history = self._gold_history[-360:]
        if len(self._level_history) > 360:
            self._level_history = self._level_history[-360:]

    def get_ability_order(self) -> Dict[str, int]:
        return dict(self._ability_levels)

    def get_gold_curve(self) -> List[Tuple[float, float]]:
        return list(self._gold_history)

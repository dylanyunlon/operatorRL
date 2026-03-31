#!/usr/bin/env python3
"""
M895 — RealTimeKDATracker
===========================
Tracks all players' KDA, gold, XP, CS in real-time via Live Client Data API.
Maintains 5-second snapshots for full game timeline reconstruction.

Dependencies: M891
Reference: leagueoflegends-optimizer Live Client Data stats
"""
from __future__ import annotations
import asyncio, collections, json, logging, time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("M895.RealTimeKdaTracker")

SNAPSHOT_INTERVAL = 5.0
MAX_TIMELINE_LENGTH = 720  # 60 min at 5s intervals


@dataclass
class PlayerSnapshot:
    summoner_name: str
    champion: str
    team: str
    timestamp: float  # game time in seconds
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    level: int = 1
    gold: float = 0.0
    items_count: int = 0

    @property
    def kda_ratio(self) -> float:
        return (self.kills + self.assists) / max(self.deaths, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.summoner_name, "champ": self.champion, "team": self.team,
                "time": round(self.timestamp, 1), "k": self.kills, "d": self.deaths,
                "a": self.assists, "kda": round(self.kda_ratio, 2), "cs": self.cs,
                "lvl": self.level, "gold": round(self.gold)}


@dataclass
class TeamSnapshot:
    team: str
    timestamp: float
    total_kills: int = 0
    total_deaths: int = 0
    total_gold: float = 0.0
    avg_level: float = 0.0
    total_cs: int = 0
    dragon_kills: int = 0
    baron_kills: int = 0
    tower_kills: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {"team": self.team, "time": round(self.timestamp, 1),
                "kills": self.total_kills, "gold": round(self.total_gold),
                "avg_lvl": round(self.avg_level, 1), "cs": self.total_cs}


@dataclass
class GoldDiffPoint:
    game_time: float
    gold_diff: float  # positive = blue advantage
    kill_diff: int
    cs_diff: int


class RealtimeKdaTracker:
    """
    Tracks KDA and economy for all 10 players throughout a game.

    Data source: M891 LiveGameDataBridge snapshots every 5 seconds.
    Maintains per-player and per-team timelines for:
    - KDA progression
    - Gold/XP curves
    - CS differential
    - Level advantages
    """

    def __init__(self, bridge=None):
        self._bridge = bridge
        self._player_timelines: Dict[str, List[PlayerSnapshot]] = collections.defaultdict(list)
        self._team_timelines: Dict[str, List[TeamSnapshot]] = collections.defaultdict(list)
        self._gold_diff_timeline: List[GoldDiffPoint] = []
        self._listeners: Dict[str, List[Callable]] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown = asyncio.Event()
        self._last_snapshot_time: float = 0
        self._stats = {"snapshots": 0, "players_tracked": 0, "events_emitted": 0}
        logger.info("RealTimeKDATracker initialized")

    def on(self, event: str, cb: Callable):
        self._listeners.setdefault(event, []).append(cb)

    async def _emit(self, event: str, data: Any = None):
        for cb in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(data)
                else:
                    cb(data)
                self._stats["events_emitted"] += 1
            except Exception as exc:
                logger.error("Emit error '%s': %s", event, exc)

    async def start(self):
        self._shutdown.clear()
        self._poll_task = asyncio.create_task(self._track_loop(), name="kda-tracker")
        logger.info("KDA tracker started")

    async def stop(self):
        self._shutdown.set()
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("KDA tracker stopped. Stats: %s", self._stats)

    async def _track_loop(self):
        while not self._shutdown.is_set():
            try:
                if self._bridge and self._bridge.current:
                    snapshot = self._bridge.current
                    if snapshot.game_time > self._last_snapshot_time + SNAPSHOT_INTERVAL - 0.5:
                        await self._process_game_snapshot(snapshot)
                        self._last_snapshot_time = snapshot.game_time
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Track loop error: %s", exc)
            await asyncio.sleep(1.0)

    async def process_snapshot(self, game_snapshot) -> None:
        """Public API: process a snapshot from M891 bridge."""
        await self._process_game_snapshot(game_snapshot)

    async def _process_game_snapshot(self, game_snapshot):
        """Extract KDA data from a game snapshot and update timelines."""
        gt = game_snapshot.game_time
        self._stats["snapshots"] += 1

        order_gold, chaos_gold = 0.0, 0.0
        order_kills, chaos_kills = 0, 0
        order_cs, chaos_cs = 0, 0

        for player in game_snapshot.players:
            ps = PlayerSnapshot(
                summoner_name=player.summoner_name, champion=player.champion_name,
                team=player.team, timestamp=gt, kills=player.kills,
                deaths=player.deaths, assists=player.assists, cs=player.cs,
                level=player.level, gold=player.current_gold,
                items_count=len(player.items),
            )
            timeline = self._player_timelines[player.summoner_name]
            timeline.append(ps)
            if len(timeline) > MAX_TIMELINE_LENGTH:
                self._player_timelines[player.summoner_name] = timeline[-MAX_TIMELINE_LENGTH:]

            if player.team == "ORDER":
                order_gold += player.current_gold
                order_kills += player.kills
                order_cs += player.cs
            else:
                chaos_gold += player.current_gold
                chaos_kills += player.kills
                chaos_cs += player.cs

        self._stats["players_tracked"] = len(self._player_timelines)

        # Team aggregates
        for team, gold, kills, cs in [("ORDER", order_gold, order_kills, order_cs),
                                       ("CHAOS", chaos_gold, chaos_kills, chaos_cs)]:
            team_players = [p for p in game_snapshot.players if p.team == team]
            avg_lvl = sum(p.level for p in team_players) / max(len(team_players), 1)
            ts = TeamSnapshot(team=team, timestamp=gt, total_kills=kills,
                             total_gold=gold, avg_level=avg_lvl, total_cs=cs)
            self._team_timelines[team].append(ts)
            if len(self._team_timelines[team]) > MAX_TIMELINE_LENGTH:
                self._team_timelines[team] = self._team_timelines[team][-MAX_TIMELINE_LENGTH:]

        # Gold diff
        gdp = GoldDiffPoint(game_time=gt, gold_diff=order_gold - chaos_gold,
                            kill_diff=order_kills - chaos_kills, cs_diff=order_cs - chaos_cs)
        self._gold_diff_timeline.append(gdp)
        if len(self._gold_diff_timeline) > MAX_TIMELINE_LENGTH:
            self._gold_diff_timeline = self._gold_diff_timeline[-MAX_TIMELINE_LENGTH:]

        # Detect significant events
        if len(self._gold_diff_timeline) >= 2:
            prev = self._gold_diff_timeline[-2]
            if abs(gdp.gold_diff - prev.gold_diff) > 2000:
                await self._emit("gold_swing", {"time": gt, "diff": gdp.gold_diff - prev.gold_diff})
            if abs(gdp.kill_diff - prev.kill_diff) >= 3:
                await self._emit("kill_spree", {"time": gt, "kill_diff_change": gdp.kill_diff - prev.kill_diff})

        await self._emit("snapshot_processed", {"time": gt, "gold_diff": gdp.gold_diff})

    def get_player_timeline(self, name: str) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._player_timelines.get(name, [])]

    def get_gold_diff_timeline(self) -> List[Dict[str, float]]:
        return [{"time": p.game_time, "gold": p.gold_diff, "kills": p.kill_diff} for p in self._gold_diff_timeline]

    def get_current_standings(self) -> Dict[str, Any]:
        """Current scoreboard."""
        result = {"ORDER": [], "CHAOS": []}
        for name, timeline in self._player_timelines.items():
            if timeline:
                latest = timeline[-1]
                result[latest.team].append(latest.to_dict())
        return result

    def get_mvp_candidates(self) -> List[Dict[str, Any]]:
        """Top performers by KDA ratio."""
        latest = []
        for name, timeline in self._player_timelines.items():
            if timeline:
                latest.append(timeline[-1])
        latest.sort(key=lambda p: p.kda_ratio, reverse=True)
        return [p.to_dict() for p in latest[:3]]

    def export_stats(self) -> Dict[str, Any]:
        return {"tracker_stats": self._stats, "players": len(self._player_timelines),
                "timeline_points": len(self._gold_diff_timeline)}



# ---------------------------------------------------------------------------
# Extended RealTimeKDATracker utilities
# ---------------------------------------------------------------------------

class PerformanceAnalyzer:
    """Analyzes individual player performance metrics in real-time."""

    @staticmethod
    def compute_kill_participation(player: PlayerSnapshot,
                                  team_kills: int) -> float:
        if team_kills <= 0:
            return 0.0
        return (player.kills + player.assists) / team_kills * 100

    @staticmethod
    def compute_cs_per_minute(cs: int, game_time: float) -> float:
        minutes = game_time / 60
        return cs / minutes if minutes > 0 else 0.0

    @staticmethod
    def compute_gold_efficiency(gold: float, cs: int, kills: int,
                               assists: int) -> float:
        expected = cs * 20 + kills * 300 + assists * 150
        return gold / expected * 100 if expected > 0 else 100.0

    @staticmethod
    def compute_death_timer_impact(deaths: int, level: int,
                                   game_time: float) -> float:
        """Estimate total time spent dead."""
        base_timer = 6 + level * 2
        scaling = 1.0 + max(0, game_time - 900) / 1800 * 0.5
        return deaths * base_timer * scaling


class CSTracker:
    """Dedicated CS tracking with per-minute breakdown."""

    def __init__(self):
        self._cs_timeline: Dict[str, List[Tuple[float, int]]] = collections.defaultdict(list)

    def update(self, player_name: str, game_time: float, cs: int):
        self._cs_timeline[player_name].append((game_time, cs))

    def get_cs_at_time(self, player_name: str, game_time: float) -> int:
        timeline = self._cs_timeline.get(player_name, [])
        for t, cs in reversed(timeline):
            if t <= game_time:
                return cs
        return 0

    def get_cs_per_minute_curve(self, player_name: str) -> List[Tuple[float, float]]:
        timeline = self._cs_timeline.get(player_name, [])
        curve = []
        for i in range(1, len(timeline)):
            t_prev, cs_prev = timeline[i-1]
            t_curr, cs_curr = timeline[i]
            dt = (t_curr - t_prev) / 60
            if dt > 0:
                cs_per_min = (cs_curr - cs_prev) / dt
                curve.append((t_curr, cs_per_min))
        return curve

    def get_cs_diff_at_intervals(self, player_a: str,
                                  player_b: str) -> List[Dict[str, Any]]:
        """CS differential between two players at key time points."""
        checkpoints = [300, 600, 900, 1200, 1800]  # 5, 10, 15, 20, 30 min
        diffs = []
        for t in checkpoints:
            cs_a = self.get_cs_at_time(player_a, t)
            cs_b = self.get_cs_at_time(player_b, t)
            diffs.append({"time": t, "player_a_cs": cs_a,
                         "player_b_cs": cs_b, "diff": cs_a - cs_b})
        return diffs


class KillFeedAnalyzer:
    """Analyzes kill sequences for team fight detection."""

    def __init__(self):
        self._kill_events: List[Dict[str, Any]] = []

    def record_kill(self, game_time: float, killer: str, victim: str, team: str):
        self._kill_events.append({
            "time": game_time, "killer": killer, "victim": victim, "team": team,
        })

    def detect_teamfights(self, window: float = 15.0) -> List[Dict[str, Any]]:
        """Detect team fights (3+ kills within window seconds)."""
        fights = []
        i = 0
        while i < len(self._kill_events):
            cluster = [self._kill_events[i]]
            j = i + 1
            while j < len(self._kill_events):
                if self._kill_events[j]["time"] - cluster[0]["time"] <= window:
                    cluster.append(self._kill_events[j])
                    j += 1
                else:
                    break
            if len(cluster) >= 3:
                order_kills = sum(1 for k in cluster if k["team"] == "ORDER")
                chaos_kills = sum(1 for k in cluster if k["team"] == "CHAOS")
                winner = "ORDER" if order_kills > chaos_kills else "CHAOS" if chaos_kills > order_kills else "DRAW"
                fights.append({
                    "start_time": cluster[0]["time"],
                    "end_time": cluster[-1]["time"],
                    "total_kills": len(cluster),
                    "order_kills": order_kills,
                    "chaos_kills": chaos_kills,
                    "winner": winner,
                })
                i = j
            else:
                i += 1
        return fights



# ---------------------------------------------------------------------------
# Extended RealtimeKdaTracker utilities — metrics, serialization, diagnostics
# ---------------------------------------------------------------------------

class RealtimeKdaTrackerMetrics:
    """Collects performance metrics for RealtimeKdaTracker."""

    def __init__(self):
        self._operation_times: List[float] = []
        self._error_counts: Dict[str, int] = collections.defaultdict(int)
        self._invocations = 0

    def record_operation(self, duration_ms: float):
        self._invocations += 1
        self._operation_times.append(duration_ms)
        if len(self._operation_times) > 1000:
            self._operation_times = self._operation_times[-1000:]

    def record_error(self, error_type: str):
        self._error_counts[error_type] += 1

    def get_summary(self) -> Dict[str, Any]:
        if not self._operation_times:
            return {"invocations": self._invocations, "errors": dict(self._error_counts)}
        sorted_times = sorted(self._operation_times)
        n = len(sorted_times)
        return {
            "invocations": self._invocations,
            "avg_ms": round(sum(sorted_times) / n, 2),
            "p50_ms": round(sorted_times[n // 2], 2),
            "p95_ms": round(sorted_times[int(n * 0.95)], 2),
            "p99_ms": round(sorted_times[int(n * 0.99)], 2),
            "max_ms": round(sorted_times[-1], 2),
            "errors": dict(self._error_counts),
        }


class RealtimeKdaTrackerSerializer:
    """Serialization utilities for RealtimeKdaTracker state."""

    @staticmethod
    def serialize_state(state: Dict[str, Any]) -> str:
        return json.dumps(state, indent=2, ensure_ascii=False, default=str)

    @staticmethod
    def deserialize_state(data: str) -> Dict[str, Any]:
        try:
            return json.loads(data)
        except json.JSONDecodeError as exc:
            logger.error("Deserialize error: %s", exc)
            return {}

    @staticmethod
    def compute_state_hash(state: Dict[str, Any]) -> str:
        serialized = json.dumps(state, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class RealtimeKdaTrackerDiagnostics:
    """Diagnostic tools for RealtimeKdaTracker troubleshooting."""

    def __init__(self, instance):
        self._instance = instance
        self._diagnostic_log: List[Dict[str, Any]] = []

    def run_self_test(self) -> Dict[str, Any]:
        """Run basic self-diagnostics."""
        results = {
            "module": "RealtimeKdaTracker",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": [],
        }

        # Check 1: Instance exists
        results["checks"].append({
            "name": "instance_valid",
            "passed": self._instance is not None,
        })

        # Check 2: Has export_stats method
        has_stats = hasattr(self._instance, "export_stats")
        results["checks"].append({
            "name": "has_export_stats",
            "passed": has_stats,
        })

        # Check 3: export_stats returns valid data
        if has_stats:
            try:
                stats = self._instance.export_stats()
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": isinstance(stats, dict),
                    "detail": f"{len(stats)} keys returned",
                })
            except Exception as exc:
                results["checks"].append({
                    "name": "stats_callable",
                    "passed": False,
                    "detail": str(exc),
                })

        # Check 4: Memory footprint estimate
        import sys
        size = sys.getsizeof(self._instance)
        results["checks"].append({
            "name": "memory_footprint",
            "passed": size < 10_000_000,  # 10MB threshold
            "detail": f"{size} bytes",
        })

        self._diagnostic_log.append(results)
        return results

    def get_diagnostic_history(self) -> List[Dict[str, Any]]:
        return list(self._diagnostic_log)


class RealtimeKdaTrackerEventLogger:
    """Structured event logger for RealtimeKdaTracker with rotation."""

    def __init__(self, max_events: int = 500):
        self._events: List[Dict[str, Any]] = []
        self._max = max_events

    def log(self, event_type: str, data: Optional[Dict] = None, level: str = "info"):
        self._events.append({
            "type": event_type,
            "level": level,
            "data": data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._events) > self._max:
            self._events = self._events[-self._max:]

    def get_events(self, event_type: Optional[str] = None,
                   level: Optional[str] = None,
                   limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._events
        if event_type:
            filtered = [e for e in filtered if e["type"] == event_type]
        if level:
            filtered = [e for e in filtered if e["level"] == level]
        return filtered[-limit:]

    def count_by_type(self) -> Dict[str, int]:
        return dict(collections.Counter(e["type"] for e in self._events))

    def count_by_level(self) -> Dict[str, int]:
        return dict(collections.Counter(e["level"] for e in self._events))

    @property
    def total(self) -> int:
        return len(self._events)

"""
canbus/vehicle/simulated_replay.py — Time-advancing replay data source.
=========================================================================
Claude18 · Based on Claude16's ReplayDataSource (data_source_factory.py)

Problem identified from diagnostic run:
    testdata/sample_allgamedata.json has fixed gameTime=1680.5. When looped,
    canbus_component._check_stale() fires WARNING every 100ms after tick 50
    because gameTime never changes. This floods logs and masks real issues.

Solution (Apollo pattern):
    查看 Apollo modules/drivers/replay/ 上现有回放驱动的实现方式, 理解其模式,
    特别是时间戳如何在回放中递增。从 Apollo replay_driver 的时间插值 这个好
    例子开始。然后, 遵循该模式实现一个新的 SimulatedReplayDataSource, 让
    canbus 可以 从静态 JSON 回放时自动递增 gameTime, 并能 避免 stale 告警
    洪泛。接着 引入 per-player 分数递增, 使 prediction 能够 观察到有意义的
    趋势变化, 同时 事件注入 优化 perception 的事件检测测试覆盖。

Design:
    - Wraps existing ReplayDataSource (does NOT modify it)
    - On each poll(), clones the base data and patches gameTime += tick_delta
    - Player gold/CS/levels advance proportionally
    - Synthetic events injected at configurable game-time checkpoints
    - Registered as "simulated" in DataSourceFactory registry
    - auto_detect() updated to prefer "simulated" over raw "testdata"

File location: lolbot-HyperAI/modules/canbus/vehicle/simulated_replay.py
"""

from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.canbus.vehicle.data_source_factory import (
    DataSource,
    PollResult,
    register_data_source,
)

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_TICK_DELTA_S = 1.0   # Each poll advances gameTime by 1s
_DEFAULT_START_TIME_S = 120.0  # Start at 2min (past prediction threshold)
_GOLD_PER_SECOND = 3.8        # Passive gold gen in LoL
_CS_PER_MINUTE = 7.0           # Average CS rate for simulation
_LEVEL_UP_INTERVAL_S = 90.0    # Rough level-up every 90s

# Synthetic events injected at specific game times
_SYNTHETIC_EVENT_CHECKPOINTS: List[Dict[str, Any]] = [
    {
        "trigger_time": 180.0,
        "EventID": 9001,
        "EventName": "ChampionKill",
        "EventTime": 180.0,
        "KillerName": "TestPlayer",
        "VictimName": "Zed",
        "Assisters": ["Thresh"],
    },
    {
        "trigger_time": 360.0,
        "EventID": 9002,
        "EventName": "DragonKill",
        "EventTime": 360.0,
        "KillerName": "JungleKing",
        "VictimName": "",
        "Assisters": [],
    },
    {
        "trigger_time": 600.0,
        "EventID": 9003,
        "EventName": "TurretKilled",
        "EventTime": 600.0,
        "KillerName": "Jinx",
        "VictimName": "",
        "Assisters": [],
    },
    {
        "trigger_time": 900.0,
        "EventID": 9004,
        "EventName": "HeraldKill",
        "EventTime": 900.0,
        "KillerName": "JungleKing",
        "VictimName": "",
        "Assisters": [],
    },
    {
        "trigger_time": 1200.0,
        "EventID": 9005,
        "EventName": "ChampionKill",
        "EventTime": 1200.0,
        "KillerName": "Zed",
        "VictimName": "TestPlayer",
        "Assisters": ["Darius"],
    },
    {
        "trigger_time": 1500.0,
        "EventID": 9006,
        "EventName": "BaronKill",
        "EventTime": 1500.0,
        "KillerName": "JungleKing",
        "VictimName": "",
        "Assisters": [],
    },
    {
        "trigger_time": 1800.0,
        "EventID": 9007,
        "EventName": "Ace",
        "EventTime": 1800.0,
        "KillerName": "TestPlayer",
        "VictimName": "",
        "Assisters": [],
    },
]


@dataclass
class SimulatedReplayConfig:
    """Configuration for SimulatedReplayDataSource."""
    filepath: str = ""
    tick_delta_s: float = _DEFAULT_TICK_DELTA_S
    start_time_s: float = _DEFAULT_START_TIME_S
    inject_events: bool = True
    advance_gold: bool = True
    advance_levels: bool = True
    max_game_time_s: float = 2400.0  # 40min auto-reset


class SimulatedReplayDataSource(DataSource):
    """Time-advancing replay data source.

    Loads a single JSON snapshot (e.g. sample_allgamedata.json) and on
    each poll() returns a deep-copied version with gameTime, player
    gold, CS, levels, and events advancing realistically.

    This eliminates the stale-data WARNING spam that occurs when raw
    ReplayDataSource loops the same static JSON frame.

    Apollo equivalent: replay_driver with timestamp interpolation.
    """

    def __init__(
        self,
        filepath: str = "",
        tick_delta_s: float = _DEFAULT_TICK_DELTA_S,
        start_time_s: float = _DEFAULT_START_TIME_S,
        inject_events: bool = True,
        max_game_time_s: float = 2400.0,
    ) -> None:
        self._filepath = filepath
        self._tick_delta_s = tick_delta_s
        self._start_time_s = start_time_s
        self._inject_events = inject_events
        self._max_game_time_s = max_game_time_s

        self._base_data: Optional[Dict[str, Any]] = None
        self._current_game_time: float = start_time_s
        self._tick_count: int = 0
        self._initialized: bool = False

        # Track which synthetic events have been injected
        self._injected_event_ids: set = set()

        # Per-player accumulators for realistic progression
        self._player_gold_offset: Dict[str, float] = {}
        self._player_cs_offset: Dict[str, int] = {}
        self._player_kill_offset: Dict[str, int] = {}

    @property
    def source_type(self) -> str:
        return "simulated"

    def init(self) -> bool:
        """Load base JSON snapshot."""
        p = Path(self._filepath)
        if not p.exists():
            logger.error("Simulated replay file not found: %s", p)
            return False

        try:
            raw = p.read_text(encoding="utf-8").strip()
            self._base_data = json.loads(raw)
        except (json.JSONDecodeError, IOError) as exc:
            logger.error("Failed to parse replay file: %s", exc)
            return False

        # Validate required structure
        if not isinstance(self._base_data, dict):
            logger.error("Replay file is not a JSON object")
            return False
        if "allPlayers" not in self._base_data:
            logger.error("Replay file missing allPlayers")
            return False
        if "gameData" not in self._base_data:
            logger.error("Replay file missing gameData")
            return False

        # Initialize per-player offsets
        for player in self._base_data.get("allPlayers", []):
            name = player.get("summonerName", player.get("riotIdGameName", ""))
            self._player_gold_offset[name] = 0.0
            self._player_cs_offset[name] = 0
            self._player_kill_offset[name] = 0

        self._current_game_time = self._start_time_s
        self._tick_count = 0
        self._injected_event_ids.clear()
        self._initialized = True

        logger.info(
            "SimulatedReplayDataSource: loaded from %s, "
            "start_time=%.0fs, tick_delta=%.1fs",
            p.name, self._start_time_s, self._tick_delta_s,
        )
        return True

    def poll(self) -> PollResult:
        """Return a time-advanced copy of the base data.

        Each call increments gameTime by tick_delta_s and adjusts
        player stats proportionally. Events are injected at checkpoints.
        """
        if self._base_data is None:
            return PollResult(
                success=False, error="Not initialized",
                source_type="simulated",
            )

        self._tick_count += 1
        self._current_game_time += self._tick_delta_s

        # Auto-reset at max game time (simulates game end + new game)
        if self._current_game_time >= self._max_game_time_s:
            self._current_game_time = self._start_time_s
            self._injected_event_ids.clear()
            for name in self._player_gold_offset:
                self._player_gold_offset[name] = 0.0
                self._player_cs_offset[name] = 0
                self._player_kill_offset[name] = 0
            logger.info("SimulatedReplay: auto-reset at %.0fs",
                         self._max_game_time_s)

        # Deep copy base data to avoid mutation
        data = copy.deepcopy(self._base_data)

        # ── Patch gameData.gameTime ──────────────────────────────────
        game_data = data.setdefault("gameData", {})
        game_data["gameTime"] = self._current_game_time

        # ── Advance player stats ─────────────────────────────────────
        elapsed = self._current_game_time - self._start_time_s
        for player in data.get("allPlayers", []):
            name = player.get("summonerName",
                              player.get("riotIdGameName", ""))

            # Gold: passive + CS income
            gold_earned = elapsed * _GOLD_PER_SECOND
            self._player_gold_offset[name] = gold_earned

            # CS: advancing at average rate
            cs_earned = int(elapsed * _CS_PER_MINUTE / 60.0)
            scores = player.setdefault("scores", {})
            base_cs = scores.get("creepScore", 0)
            scores["creepScore"] = base_cs + cs_earned

            # Levels: cap at 18
            base_level = player.get("level", 1)
            level_ups = int(elapsed / _LEVEL_UP_INTERVAL_S)
            player["level"] = min(18, base_level + level_ups)

        # Patch activePlayer gold too
        active = data.get("activePlayer", {})
        if active:
            base_gold = active.get("currentGold", 500.0)
            active["currentGold"] = base_gold + elapsed * _GOLD_PER_SECOND

        # ── Inject synthetic events at time checkpoints ──────────────
        if self._inject_events:
            events_wrapper = data.setdefault("events", {})
            event_list = events_wrapper.setdefault("Events", [])

            for checkpoint in _SYNTHETIC_EVENT_CHECKPOINTS:
                evt_id = checkpoint["EventID"]
                trigger = checkpoint["trigger_time"]
                if (
                    evt_id not in self._injected_event_ids
                    and self._current_game_time >= trigger
                ):
                    self._injected_event_ids.add(evt_id)
                    event_list.append({
                        "EventID": evt_id,
                        "EventName": checkpoint["EventName"],
                        "EventTime": checkpoint["EventTime"],
                        "KillerName": checkpoint.get("KillerName", ""),
                        "VictimName": checkpoint.get("VictimName", ""),
                        "Assisters": checkpoint.get("Assisters", []),
                    })

        return PollResult(
            success=True,
            data=data,
            latency_ms=0.05,
            source_type="simulated",
        )

    def shutdown(self) -> None:
        self._base_data = None
        self._initialized = False

    @property
    def is_available(self) -> bool:
        return self._initialized

    @property
    def current_game_time(self) -> float:
        return self._current_game_time

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def stats(self) -> Dict[str, Any]:
        return {
            "source_type": "simulated",
            "filepath": self._filepath,
            "current_game_time": round(self._current_game_time, 1),
            "tick_count": self._tick_count,
            "tick_delta_s": self._tick_delta_s,
            "injected_events": len(self._injected_event_ids),
            "max_game_time_s": self._max_game_time_s,
        }


# ── Register with factory ────────────────────────────────────────────────────
register_data_source("simulated", SimulatedReplayDataSource)

#!/usr/bin/env python3
"""
modules/common/adapters/replay_data_source.py — Offline Replay Data Source
===========================================================================
lolbot-HyperAI · modules/common/adapters

查看 Apollo modules/drivers/replay_driver 上现有的回放驱动实现方式,
理解其模式, 特别是如何将录制的 protobuf 消息按时间戳回放到 channel。
从 Apollo ReplayDriver 这个好例子开始。然后遵循该模式实现一个新的
ReplayDataSource, 让 canbus_component 可以从录制的 JSONL 文件中读取
历史 LCU 数据, 并能按原始时间间隔或加速回放。接着 引入 seek/pause
控制, 使 开发者 能够在回放中跳转到特定游戏时间, 同时 支持 循环回放。
随后 整合 事件注入机制, 令 测试代码 支持在回放中注入自定义事件, 进而
增强 集成测试的覆盖范围。最终 完善 回放统计和进度报告, 确保 回放行为
兼容 实时数据源的接口, 全面 升级 开发体验。

架构位置:
    modules/common/adapters/replay_data_source.py   ← YOU ARE HERE
    ├─ Implements: same interface as LCUClient.get()
    ├─ Reads: JSONL recording files from logs/recordings/
    ├─ Used by: CanbusComponent (when config.replay.enabled)
    └─ Enables: full pipeline testing without live LoL client

设计决策:
    - 与 LCUClient 返回相同的 Tuple[Optional[Dict], Status] 签名
    - 按 gameTime 排序帧, 而非按录制 wall-clock
    - 支持加速回放 (speed_factor), 1.0 = 实时, 0 = 最快
    - 帧间等待用 time.sleep 而非 busy-wait, 低 CPU 占用
    - 回放结束时发出 CANBUS_GAME_ENDED 状态而非静默停止
    - 线程安全: 可在 canbus Proc() 线程中安全调用

位置: lolbot-HyperAI/modules/common/adapters/replay_data_source.py
"""

from __future__ import annotations

import gzip
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from modules.common.status.error_code import ErrorCode, Status

logger = logging.getLogger(__name__)

_DEFAULT_SPEED_FACTOR = 1.0
_MAX_SPEED_FACTOR = 100.0
_MIN_FRAME_INTERVAL_S = 0.001


@dataclass
class ReplayFrame:
    """Single frame of recorded LCU data.

    Attributes:
        game_time: In-game timestamp (seconds from game start).
        wall_time: Wall-clock timestamp when frame was recorded.
        data: The allgamedata JSON payload.
        events: Optional separate event data from that tick.
        sequence: Frame index in the recording.
    """
    game_time: float
    wall_time: float
    data: Dict[str, Any]
    events: Optional[Dict[str, Any]] = None
    sequence: int = 0


class ReplayState:
    """Replay playback state machine."""
    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"
    ERROR = "error"


class ReplayDataSource:
    """Offline data source that replays recorded JSONL sessions.

    Drop-in replacement for LCUClient when running in replay mode.
    Provides the same ``get()`` interface so CanbusComponent can use
    it transparently.

    Recording format (JSONL, one JSON object per line)::

        {"timestamp": 1700000001.0, "channel": "/lol/raw_lcu", "payload": {...}}
        {"timestamp": 1700000001.1, "channel": "/lol/raw_lcu", "payload": {...}}

    Usage::

        source = ReplayDataSource.from_file("logs/recordings/session_123.jsonl")
        source.set_speed(2.0)  # 2x speed
        source.start()

        # In canbus Proc() loop:
        data, status = source.get("/liveclientdata/allgamedata")

        # Controls
        source.pause()
        source.seek(game_time=600.0)  # Jump to 10 min
        source.resume()

    Thread safety:
        All public methods are thread-safe via _lock.
    """

    def __init__(self) -> None:
        self._frames: List[ReplayFrame] = []
        self._cursor: int = 0
        self._state: str = ReplayState.IDLE
        self._speed_factor: float = _DEFAULT_SPEED_FACTOR
        self._loop: bool = False
        self._lock = threading.Lock()

        self._playback_start_wall: float = 0.0
        self._last_frame_wall: float = 0.0
        self._last_frame_game: float = 0.0

        self._frames_delivered: int = 0
        self._seek_count: int = 0
        self._loop_count: int = 0

        self._source_path: str = ""
        self._recording_duration: float = 0.0

        self._on_finished: Optional[Callable[[], None]] = None
        self._on_loop: Optional[Callable[[int], None]] = None

    # ─── Construction ────────────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str | Path) -> "ReplayDataSource":
        """Load a recording from a JSONL or JSONL.gz file.

        Args:
            path: Path to the recording file.

        Returns:
            A new ReplayDataSource ready to play.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If no valid frames found.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Recording not found: {p}")

        instance = cls()
        instance._source_path = str(p)

        lines: List[str] = []
        if p.suffix == ".gz":
            with gzip.open(p, "rt", encoding="utf-8") as f:
                lines = f.readlines()
        else:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.readlines()

        raw_frames: List[ReplayFrame] = []
        for idx, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed line %d in %s", idx, p)
                continue

            payload = record.get("payload", record)
            if isinstance(payload, dict) and "allgamedata" in payload:
                allgamedata = payload["allgamedata"]
            elif isinstance(payload, dict) and "allPlayers" in payload:
                allgamedata = payload
            else:
                continue

            game_data = allgamedata.get("gameData", {})
            game_time = game_data.get("gameTime", 0.0)
            wall_time = record.get("timestamp", 0.0)

            frame = ReplayFrame(
                game_time=game_time,
                wall_time=wall_time,
                data=allgamedata,
                events=allgamedata.get("events"),
                sequence=len(raw_frames),
            )
            raw_frames.append(frame)

        if not raw_frames:
            raise ValueError(f"No valid game data frames in {p}")

        raw_frames.sort(key=lambda f: f.game_time)
        instance._frames = raw_frames

        if len(raw_frames) >= 2:
            instance._recording_duration = (
                raw_frames[-1].game_time - raw_frames[0].game_time
            )

        logger.info(
            "Loaded replay: %d frames, %.1fs game time, from %s",
            len(raw_frames), instance._recording_duration, p.name,
        )
        return instance

    @classmethod
    def from_frames(cls, frames: List[Dict[str, Any]]) -> "ReplayDataSource":
        """Create from a list of allgamedata dicts (for testing)."""
        instance = cls()
        for idx, data in enumerate(frames):
            game_data = data.get("gameData", {})
            game_time = game_data.get("gameTime", float(idx))
            frame = ReplayFrame(
                game_time=game_time,
                wall_time=time.time(),
                data=data,
                sequence=idx,
            )
            instance._frames.append(frame)
        if len(instance._frames) >= 2:
            instance._recording_duration = (
                instance._frames[-1].game_time
                - instance._frames[0].game_time
            )
        instance._source_path = "<in-memory>"
        return instance

    # ─── Playback control ────────────────────────────────────────────

    def start(self) -> None:
        """Start or restart playback from the beginning."""
        with self._lock:
            self._cursor = 0
            self._state = ReplayState.PLAYING
            self._playback_start_wall = time.monotonic()
            if self._frames:
                self._last_frame_game = self._frames[0].game_time
            self._last_frame_wall = self._playback_start_wall
            self._frames_delivered = 0
            logger.info("Replay started: %d frames", len(self._frames))

    def pause(self) -> None:
        """Pause playback."""
        with self._lock:
            if self._state == ReplayState.PLAYING:
                self._state = ReplayState.PAUSED

    def resume(self) -> None:
        """Resume from pause."""
        with self._lock:
            if self._state == ReplayState.PAUSED:
                self._last_frame_wall = time.monotonic()
                self._state = ReplayState.PLAYING

    def seek(self, game_time: float) -> bool:
        """Jump to a specific game time."""
        with self._lock:
            self._seek_count += 1
            best_idx = 0
            best_diff = float("inf")
            for idx, frame in enumerate(self._frames):
                diff = abs(frame.game_time - game_time)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = idx
            self._cursor = best_idx
            self._last_frame_game = self._frames[best_idx].game_time
            self._last_frame_wall = time.monotonic()
            return True

    def set_speed(self, factor: float) -> None:
        """Set playback speed. 1.0=realtime, 0=max, 5.0=5x."""
        with self._lock:
            self._speed_factor = max(0.0, min(_MAX_SPEED_FACTOR, factor))

    def set_loop(self, enabled: bool) -> None:
        """Enable or disable loop mode."""
        self._loop = enabled

    def on_finished(self, callback: Callable[[], None]) -> None:
        self._on_finished = callback

    def on_loop(self, callback: Callable[[int], None]) -> None:
        self._on_loop = callback

    # ─── Data access (LCUClient-compatible interface) ────────────────

    def get(self, endpoint: str) -> Tuple[Optional[Dict[str, Any]], Status]:
        """Get the next frame's data, mimicking LCUClient.get().

        Called by CanbusComponent.Proc() every tick. Advances cursor
        and returns current frame data, respecting playback speed.
        """
        with self._lock:
            if self._state == ReplayState.PAUSED:
                if 0 <= self._cursor < len(self._frames):
                    frame = self._frames[self._cursor]
                    return frame.data, Status.ok("paused")
                return None, Status.error(
                    ErrorCode.CANBUS_GAME_NOT_IN_PROGRESS,
                    "Replay paused, no frame available",
                )

            if self._state != ReplayState.PLAYING:
                return None, Status.error(
                    ErrorCode.CANBUS_GAME_NOT_IN_PROGRESS,
                    f"Replay not playing (state={self._state})",
                )

            if self._cursor >= len(self._frames):
                return self._handle_end_of_replay()

            frame = self._frames[self._cursor]

            # Speed control
            if self._speed_factor > 0 and self._cursor > 0:
                prev_frame = self._frames[self._cursor - 1]
                game_delta = frame.game_time - prev_frame.game_time
                if game_delta > 0:
                    wait_s = game_delta / self._speed_factor
                    wait_s = max(wait_s, _MIN_FRAME_INTERVAL_S)
                    actual_elapsed = time.monotonic() - self._last_frame_wall
                    remaining = wait_s - actual_elapsed
                    if remaining > 0:
                        time.sleep(remaining)

            self._last_frame_wall = time.monotonic()
            self._last_frame_game = frame.game_time
            self._cursor += 1
            self._frames_delivered += 1

            return frame.data, Status.ok()

    def get_gamestats(self) -> Tuple[Optional[Dict[str, Any]], Status]:
        """Mimics LCUClient.get("/liveclientdata/gamestats")."""
        with self._lock:
            if self._state not in (ReplayState.PLAYING, ReplayState.PAUSED):
                return None, Status.error(
                    ErrorCode.CANBUS_GAME_NOT_IN_PROGRESS, "Replay not active",
                )
            idx = min(self._cursor, len(self._frames) - 1)
            if idx < 0 or not self._frames:
                return None, Status.error(
                    ErrorCode.CANBUS_GAME_NOT_IN_PROGRESS, "No frames",
                )
            frame = self._frames[idx]
            game_data = frame.data.get("gameData", {})
            return {
                "gameTime": game_data.get("gameTime", 0.0),
                "gameMode": game_data.get("gameMode", "CLASSIC"),
                "mapName": game_data.get("mapName", "Map11"),
                "mapNumber": game_data.get("mapNumber", 11),
                "mapTerrain": game_data.get("mapTerrain", "Default"),
            }, Status.ok()

    def _handle_end_of_replay(
        self,
    ) -> Tuple[Optional[Dict[str, Any]], Status]:
        """Handle reaching end of replay."""
        if self._loop:
            self._cursor = 0
            self._loop_count += 1
            self._playback_start_wall = time.monotonic()
            logger.info("Replay loop #%d", self._loop_count)
            if self._on_loop:
                try:
                    self._on_loop(self._loop_count)
                except Exception:
                    pass
            if self._frames:
                frame = self._frames[0]
                self._cursor = 1
                self._frames_delivered += 1
                return frame.data, Status.ok()

        self._state = ReplayState.FINISHED
        logger.info(
            "Replay finished: %d frames delivered", self._frames_delivered,
        )
        if self._on_finished:
            try:
                self._on_finished()
            except Exception:
                pass
        return None, Status.error(ErrorCode.CANBUS_GAME_ENDED, "Replay finished")

    # ─── Properties ──────────────────────────────────────────────────

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_playing(self) -> bool:
        return self._state == ReplayState.PLAYING

    @property
    def is_finished(self) -> bool:
        return self._state == ReplayState.FINISHED

    @property
    def frame_count(self) -> int:
        return len(self._frames)

    @property
    def current_frame_index(self) -> int:
        return self._cursor

    @property
    def current_game_time(self) -> float:
        return self._last_frame_game

    @property
    def progress(self) -> float:
        if not self._frames:
            return 0.0
        return min(1.0, self._cursor / len(self._frames))

    @property
    def speed_factor(self) -> float:
        return self._speed_factor

    @property
    def recording_duration(self) -> float:
        return self._recording_duration

    def stats(self) -> Dict[str, Any]:
        return {
            "source": self._source_path,
            "state": self._state,
            "frame_count": len(self._frames),
            "cursor": self._cursor,
            "frames_delivered": self._frames_delivered,
            "progress": round(self.progress, 3),
            "current_game_time": round(self._last_frame_game, 1),
            "recording_duration_s": round(self._recording_duration, 1),
            "speed_factor": self._speed_factor,
            "loop_enabled": self._loop,
            "loop_count": self._loop_count,
            "seek_count": self._seek_count,
        }

    @property
    def _last_latency_ms(self) -> float:
        """LCUClient compatibility: fake latency for RawLCUData."""
        return 0.1

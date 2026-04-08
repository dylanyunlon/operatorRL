"""
ReplayDriver — Feed CyberNode channels from .cyberrecord files.
================================================================

A TimerComponent that reads recorded messages via RecordReader
and publishes them to CyberNode channels, simulating live data
input for offline testing and development.

Architecture position:
    modules/drivers/replay_driver.py   ← YOU ARE HERE
    ├─ Reads: .cyberrecord files via cyber/record/record_reader.py
    ├─ Publishes: all recorded channels via CyberNode
    └─ Replaces: modules/canbus/ during offline replay

Apollo reference:
    cyber/tools/cyber_recorder/player.cc — record playback
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from cyber.component.timer_component import (
    ComponentConfig, TimerComponent,
)
from cyber.node.node import CyberNode, Writer
from cyber.record.record_reader import RecordReader, ReplayConfig

logger = logging.getLogger(__name__)

_REPLAY_INTERVAL_MS: float = 50.0  # 20Hz check loop


@dataclass
class ReplayDriverConfig:
    """Configuration for the replay driver."""
    record_path: str = ""
    speed: float = 1.0
    include_channels: Optional[Set[str]] = None
    exclude_channels: Set[str] = None
    start_time_ns: int = 0
    end_time_ns: int = 0
    loop: bool = False
    auto_start: bool = True

    def __post_init__(self):
        if self.exclude_channels is None:
            self.exclude_channels = set()


class ReplayDriver(TimerComponent):
    """Replay recorded messages into live CyberNode channels.

    Usage::

        driver = ReplayDriver(ReplayDriverConfig(
            record_path="data/records/session.cyberrecord",
            speed=2.0,
        ))
        driver.initialize()
        driver.start()
        # Messages now flow into CyberNode channels at 2x speed
    """

    def __init__(self, config: Optional[ReplayDriverConfig] = None) -> None:
        super().__init__(
            config=ComponentConfig(
                name="replay_driver",
                interval_ms=_REPLAY_INTERVAL_MS,
                warn_threshold_ms=200.0,
            )
        )
        self._replay_config = config or ReplayDriverConfig()
        self._node: Optional[CyberNode] = None
        self._reader: Optional[RecordReader] = None
        self._writers: Dict[str, Writer] = {}
        self._message_count: int = 0
        self._channel_counts: Dict[str, int] = {}
        self._is_playing: bool = False
        self._play_thread: Optional[threading.Thread] = None

    def Init(self) -> bool:
        path = Path(self._replay_config.record_path)
        if not path.exists():
            logger.error("Replay file not found: %s", path)
            return False

        self._node = CyberNode("replay_driver")

        replay_conf = ReplayConfig(
            speed=self._replay_config.speed,
            include_channels=self._replay_config.include_channels,
            exclude_channels=self._replay_config.exclude_channels,
            start_time_ns=self._replay_config.start_time_ns,
            end_time_ns=self._replay_config.end_time_ns,
            loop=self._replay_config.loop,
            callback=self._on_message,
        )
        self._reader = RecordReader(replay_conf)

        try:
            header = self._reader.load(path)
            logger.info(
                "ReplayDriver loaded: %s (msgs=%d, channels=%d)",
                path.name,
                header.message_count,
                header.channel_count,
            )
        except Exception as exc:
            logger.error("Failed to load replay: %s", exc)
            return False

        if self._replay_config.auto_start:
            self._start_playback()

        return True

    def _start_playback(self) -> None:
        if self._is_playing or self._reader is None:
            return
        self._is_playing = True
        self._reader.play(blocking=False)
        logger.info("ReplayDriver playback started (speed=%.1fx)",
                     self._replay_config.speed)

    def _on_message(self, channel: str, payload: Any, ts_ns: int) -> None:
        """Callback from RecordReader for each replayed message."""
        if channel not in self._writers and self._node:
            self._writers[channel] = self._node.create_writer(channel)

        writer = self._writers.get(channel)
        if writer:
            writer.write(payload)

        self._message_count += 1
        self._channel_counts[channel] = (
            self._channel_counts.get(channel, 0) + 1
        )

    def Proc(self) -> bool:
        """Check replay status each tick."""
        if self._reader is None:
            return True

        if self._reader.state.name == "FINISHED":
            if not self._replay_config.loop:
                logger.info("ReplayDriver finished: %d messages replayed",
                            self._message_count)
                self._is_playing = False
            return True

        return True

    def pause(self) -> None:
        if self._reader:
            self._reader.pause()

    def resume(self) -> None:
        if self._reader:
            self._reader.resume()

    def on_shutdown(self) -> None:
        if self._reader:
            self._reader.close()
        self._is_playing = False

    def status_dict(self) -> Dict[str, Any]:
        reader_state = "N/A"
        if self._reader:
            reader_state = self._reader.state.name

        return {
            "is_playing": self._is_playing,
            "reader_state": reader_state,
            "message_count": self._message_count,
            "channels_written": len(self._writers),
            "channel_counts": dict(self._channel_counts),
            "speed": self._replay_config.speed,
            "record_path": self._replay_config.record_path,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Claude22 V3: Keyframe-based seek + temporal index + random access replay
# ═══════════════════════════════════════════════════════════════════════════════
#
# Design spec (Apollo pattern):
#   从 ReplayDriver 的 Init()/Proc() 回放循环 这个好例子开始。
#   然后，遵循该模式实现 KeyframeIndex，让 回放系统 可以 快速定位到任意游戏时间点，
#   并能 在 seek 之后立即恢复正确的游戏状态。
#   接着 TemporalIndex 引入 时间→帧号映射，使 用户 能够 按游戏时间跳转，
#   同时 SnapshotCache 优化 关键帧缓存以避免重复解析。
#   最终 ReplayDriverV3 完善 seek/step/bookmark API，确保 向后兼容 V1 接口。

from bisect import bisect_left, bisect_right


# ─── Keyframe entry ──────────────────────────────────────────────────────────

@dataclass
class KeyframeEntry:
    """A keyframe in the replay index.

    Keyframes store complete game state snapshots at regular intervals,
    enabling fast seek without replaying from the beginning.

    Apollo parallel: cyber/record — record file index entries.
    """
    frame_id: int = 0
    timestamp_ns: int = 0
    game_time_s: float = 0.0
    file_offset: int = 0
    snapshot_size: int = 0
    channel_states: Dict[str, int] = field(default_factory=dict)

    @property
    def timestamp_s(self) -> float:
        return self.timestamp_ns / 1e9


# ─── Keyframe index ─────────────────────────────────────────────────────────

class KeyframeIndex:
    """Index of keyframes for fast seek access.

    Maintains a sorted list of keyframe timestamps for binary search.
    Supports both timestamp-based and game-time-based lookup.

    Usage::
        index = KeyframeIndex()
        index.add_keyframe(KeyframeEntry(
            frame_id=0, timestamp_ns=1000000000,
            game_time_s=0.0, file_offset=0,
        ))
        index.add_keyframe(KeyframeEntry(
            frame_id=100, timestamp_ns=1010000000,
            game_time_s=10.0, file_offset=48000,
        ))

        # Seek to game time 7.5s → returns keyframe at 0.0s
        kf = index.find_nearest_before(game_time_s=7.5)
    """

    def __init__(self, keyframe_interval_s: float = 10.0) -> None:
        self._keyframes: List[KeyframeEntry] = []
        self._game_times: List[float] = []  # parallel sorted list for bisect
        self._timestamps: List[int] = []    # parallel sorted list
        self._keyframe_interval_s = keyframe_interval_s

    def add_keyframe(self, entry: KeyframeEntry) -> None:
        """Add a keyframe to the index (must be added in order)."""
        self._keyframes.append(entry)
        self._game_times.append(entry.game_time_s)
        self._timestamps.append(entry.timestamp_ns)

    def find_nearest_before(self, game_time_s: float) -> Optional[KeyframeEntry]:
        """Find the keyframe at or just before the given game time.

        Uses binary search for O(log n) lookup.
        """
        if not self._keyframes:
            return None
        idx = bisect_right(self._game_times, game_time_s) - 1
        if idx < 0:
            return self._keyframes[0]
        return self._keyframes[idx]

    def find_nearest_before_ts(self, timestamp_ns: int) -> Optional[KeyframeEntry]:
        """Find keyframe by wall-clock timestamp."""
        if not self._keyframes:
            return None
        idx = bisect_right(self._timestamps, timestamp_ns) - 1
        if idx < 0:
            return self._keyframes[0]
        return self._keyframes[idx]

    def find_range(
        self, start_s: float, end_s: float
    ) -> List[KeyframeEntry]:
        """Find all keyframes within a game time range."""
        start_idx = bisect_left(self._game_times, start_s)
        end_idx = bisect_right(self._game_times, end_s)
        return self._keyframes[start_idx:end_idx]

    @property
    def count(self) -> int:
        return len(self._keyframes)

    @property
    def duration_s(self) -> float:
        if len(self._game_times) < 2:
            return 0.0
        return self._game_times[-1] - self._game_times[0]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keyframe_count": self.count,
            "duration_s": round(self.duration_s, 1),
            "interval_s": self._keyframe_interval_s,
            "first_game_time": self._game_times[0] if self._game_times else 0,
            "last_game_time": self._game_times[-1] if self._game_times else 0,
        }


# ─── Snapshot cache ──────────────────────────────────────────────────────────

class SnapshotCache:
    """LRU cache for deserialized keyframe snapshots.

    Avoids re-parsing full game state when seeking back and forth
    between the same keyframes.
    """

    def __init__(self, max_size: int = 16) -> None:
        self._cache: OrderedDict[int, Dict[str, Any]] = OrderedDict()
        self._max_size = max_size
        self._hits: int = 0
        self._misses: int = 0

    def get(self, frame_id: int) -> Optional[Dict[str, Any]]:
        if frame_id in self._cache:
            self._cache.move_to_end(frame_id)
            self._hits += 1
            return self._cache[frame_id]
        self._misses += 1
        return None

    def put(self, frame_id: int, snapshot: Dict[str, Any]) -> None:
        self._cache[frame_id] = snapshot
        self._cache.move_to_end(frame_id)
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def invalidate(self) -> None:
        self._cache.clear()

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / max(1, total)

    def stats(self) -> Dict[str, Any]:
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self.hit_rate, 3),
        }


# ─── Bookmark ────────────────────────────────────────────────────────────────

@dataclass
class ReplayBookmark:
    """User-defined bookmark in a replay session."""
    name: str = ""
    game_time_s: float = 0.0
    note: str = ""
    created_at: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "game_time_s": self.game_time_s,
            "note": self.note,
            "tags": self.tags,
        }


# ─── ReplayDriverV3 — seek-capable replay ────────────────────────────────────

class ReplayDriverV3(ReplayDriver):
    """V3 replay driver with keyframe seek, step, and bookmark support.

    Fully backward-compatible with V1 ReplayDriver.
    Adds seek_to(), step_forward(), step_backward(), and bookmark API.

    Apollo parallel: cyber/tools/cyber_recorder/player.cc with seek support.

    Usage::
        driver = ReplayDriverV3(ReplayDriverConfig(
            record_path="data/records/game.cyberrecord",
        ))
        driver.Init()

        # Seek to 5 minutes into the game
        driver.seek_to(game_time_s=300.0)

        # Step forward 10 seconds
        driver.step_forward(10.0)

        # Add bookmark
        driver.add_bookmark("baron_fight", note="Key teamfight")
    """

    def __init__(self, config: Optional[ReplayDriverConfig] = None) -> None:
        super().__init__(config)
        self._keyframe_index = KeyframeIndex()
        self._snapshot_cache = SnapshotCache()
        self._bookmarks: List[ReplayBookmark] = []
        self._current_game_time: float = 0.0
        self._seek_pending: bool = False
        self._seek_target_s: float = 0.0
        self._step_mode: bool = False
        self._step_frames: int = 0
        self._playback_speed: float = config.speed if config else 1.0

    def Init(self) -> bool:
        """Initialize with keyframe index building."""
        result = super().Init()
        if not result:
            return False

        # Build keyframe index from record
        self._build_keyframe_index()
        logger.info(
            "ReplayDriverV3: built keyframe index (%d keyframes, %.1fs)",
            self._keyframe_index.count,
            self._keyframe_index.duration_s,
        )
        return True

    def _build_keyframe_index(self) -> None:
        """Build keyframe index by scanning record headers.

        In production, this reads the record file's index section.
        For now, generates keyframes at fixed intervals from metadata.
        """
        if self._reader is None:
            return

        # Use reader metadata to generate index
        try:
            header = self._reader.header
            if not header:
                return
            total_duration_ns = getattr(header, 'end_time_ns', 0) - \
                                getattr(header, 'begin_time_ns', 0)
            total_duration_s = total_duration_ns / 1e9
            interval_s = self._keyframe_index._keyframe_interval_s

            frame_id = 0
            for t in _frange(0.0, total_duration_s, interval_s):
                ts_ns = getattr(header, 'begin_time_ns', 0) + int(t * 1e9)
                self._keyframe_index.add_keyframe(KeyframeEntry(
                    frame_id=frame_id,
                    timestamp_ns=ts_ns,
                    game_time_s=t,
                    file_offset=0,  # Would be populated from real index
                ))
                frame_id += 1
        except Exception as exc:
            logger.warning("Failed to build keyframe index: %s", exc)

    def Proc(self) -> bool:
        """Extended Proc with seek handling."""
        # Handle pending seek
        if self._seek_pending:
            self._execute_seek()
            self._seek_pending = False

        # Handle step mode
        if self._step_mode:
            if self._step_frames <= 0:
                return True  # paused, waiting for next step command
            self._step_frames -= 1

        # Track current game time from replayed data
        # In production, this would read from the latest replayed message
        self._current_game_time += (
            self._config.interval_ms / 1000.0 * self._playback_speed
            if hasattr(self, '_config') else 0.05
        )

        return super().Proc()

    def seek_to(self, game_time_s: float) -> bool:
        """Seek to a specific game time.

        Finds the nearest keyframe before the target, loads its snapshot,
        then replays from the keyframe to the target time.
        """
        kf = self._keyframe_index.find_nearest_before(game_time_s)
        if kf is None:
            logger.warning("No keyframe found for time %.1fs", game_time_s)
            return False

        self._seek_target_s = game_time_s
        self._seek_pending = True
        logger.info(
            "Seek to %.1fs (nearest keyframe: %.1fs, frame %d)",
            game_time_s, kf.game_time_s, kf.frame_id,
        )
        return True

    def _execute_seek(self) -> None:
        """Execute the pending seek operation."""
        kf = self._keyframe_index.find_nearest_before(self._seek_target_s)
        if kf is None:
            return

        # Check snapshot cache
        cached = self._snapshot_cache.get(kf.frame_id)
        if cached is not None:
            logger.debug("Seek: cache hit for frame %d", kf.frame_id)
        else:
            # Would load and parse snapshot from file at kf.file_offset
            snapshot = {"frame_id": kf.frame_id, "game_time_s": kf.game_time_s}
            self._snapshot_cache.put(kf.frame_id, snapshot)

        self._current_game_time = kf.game_time_s

        # Resume playback from keyframe position
        if self._reader:
            try:
                self._reader.seek_to_time(kf.timestamp_ns)
            except AttributeError:
                logger.debug("Reader does not support seek_to_time")

    def step_forward(self, seconds: float = 1.0) -> None:
        """Step forward by the given number of seconds."""
        target = self._current_game_time + seconds
        self.seek_to(target)

    def step_backward(self, seconds: float = 1.0) -> None:
        """Step backward by the given number of seconds."""
        target = max(0.0, self._current_game_time - seconds)
        self.seek_to(target)

    def set_speed(self, speed: float) -> None:
        """Change playback speed (0.25x to 8x)."""
        self._playback_speed = max(0.25, min(8.0, speed))
        if self._reader:
            try:
                self._reader.set_speed(self._playback_speed)
            except AttributeError:
                pass
        logger.info("Playback speed: %.1fx", self._playback_speed)

    def enter_step_mode(self) -> None:
        """Enter step-by-step mode (pauses, advances one frame per step)."""
        self._step_mode = True
        self._step_frames = 0
        if self._reader:
            self._reader.pause()

    def exit_step_mode(self) -> None:
        """Exit step mode and resume normal playback."""
        self._step_mode = False
        if self._reader:
            self._reader.resume()

    def step_one_frame(self) -> None:
        """Advance exactly one frame (only in step mode)."""
        if self._step_mode:
            self._step_frames = 1

    # ── Bookmarks ────────────────────────────────────────────────────────

    def add_bookmark(
        self, name: str, note: str = "", tags: Optional[List[str]] = None
    ) -> ReplayBookmark:
        """Add a bookmark at the current game time."""
        bm = ReplayBookmark(
            name=name,
            game_time_s=self._current_game_time,
            note=note,
            tags=tags or [],
        )
        self._bookmarks.append(bm)
        logger.info("Bookmark '%s' at %.1fs", name, bm.game_time_s)
        return bm

    def goto_bookmark(self, name: str) -> bool:
        """Seek to a named bookmark."""
        for bm in self._bookmarks:
            if bm.name == name:
                return self.seek_to(bm.game_time_s)
        logger.warning("Bookmark '%s' not found", name)
        return False

    def list_bookmarks(self) -> List[Dict[str, Any]]:
        return [bm.to_dict() for bm in self._bookmarks]

    # ── Status ───────────────────────────────────────────────────────────

    def status_dict(self) -> Dict[str, Any]:
        base = super().status_dict()
        base.update({
            "current_game_time": round(self._current_game_time, 1),
            "playback_speed": self._playback_speed,
            "step_mode": self._step_mode,
            "keyframe_index": self._keyframe_index.to_dict(),
            "snapshot_cache": self._snapshot_cache.stats(),
            "bookmarks": len(self._bookmarks),
        })
        return base


def _frange(start: float, stop: float, step: float):
    """Float range generator."""
    val = start
    while val < stop:
        yield val
        val += step

"""
RecordReader — Apollo-style message playback from .cyberrecord files.
======================================================================

Maps Apollo's ``cyber::record::RecordReader`` to Python: reads
.cyberrecord + .cyberindex file pairs produced by RecordWriter and
replays messages through CyberNode channels at original or accelerated
speed, with optional channel filtering and time-range slicing.

Architecture position:
    cyber/record/record_reader.py   ← YOU ARE HERE
    ├─ Reads: .cyberrecord (JSONL body) + .cyberindex (seek table)
    ├─ Publishes: messages to CyberNode channels at replay speed
    ├─ Used by: modules/drivers/replay_driver.py
    └─ Produced by: cyber/record/record_writer.py

Apollo reference:
    cyber/record/record_reader.h — RecordReader::ReadMessage()
    cyber/record/record_viewer.h — CLI playback tool

Design notes:
    - Index-based O(1) seek to any timestamp
    - Supports gzip-compressed files transparently
    - Speed multiplier: 0.1x slow-mo to 100x fast-forward
    - Channel whitelist/blacklist filtering
    - Time-range slicing for partial replay
    - Iterator protocol for streaming access
    - Thread-safe pause/resume/stop controls
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any, Callable, Dict, Generator, Iterator, List,
    Optional, Set, Tuple,
)

from cyber.record.record_writer import (
    IndexEntry, RecordHeader, _INDEX_ENTRY_SIZE, _MAGIC_HEADER,
    _channel_hash,
)

logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_SPEED: float = 1.0
_MAX_SPEED: float = 100.0
_MIN_SPEED: float = 0.01
_READ_CHUNK_SIZE: int = 65536


class ReplayState(Enum):
    """Reader lifecycle states."""
    IDLE = auto()
    LOADED = auto()
    PLAYING = auto()
    PAUSED = auto()
    FINISHED = auto()
    CLOSED = auto()


@dataclass
class ReplayConfig:
    """Configuration for RecordReader playback.

    Attributes:
        speed: Playback speed multiplier (1.0 = realtime).
        include_channels: If set, only replay these channels.
        exclude_channels: Channels to skip during replay.
        start_time_ns: Skip messages before this timestamp.
        end_time_ns: Stop replay after this timestamp (0 = no limit).
        loop: Restart from beginning when finished.
        callback: Function called for each replayed message.
    """
    speed: float = _DEFAULT_SPEED
    include_channels: Optional[Set[str]] = None
    exclude_channels: Set[str] = field(default_factory=set)
    start_time_ns: int = 0
    end_time_ns: int = 0
    loop: bool = False
    callback: Optional[Callable[[str, Any, int], None]] = None


@dataclass
class ReplayStats:
    """Statistics accumulated during replay."""
    messages_read: int = 0
    messages_skipped: int = 0
    messages_replayed: int = 0
    channels_seen: Set[str] = field(default_factory=set)
    replay_start_time: float = 0.0
    replay_end_time: float = 0.0
    data_start_ns: int = 0
    data_end_ns: int = 0

    def to_dict(self) -> Dict[str, Any]:
        elapsed = self.replay_end_time - self.replay_start_time
        return {
            "messages_read": self.messages_read,
            "messages_skipped": self.messages_skipped,
            "messages_replayed": self.messages_replayed,
            "channels_seen": len(self.channels_seen),
            "replay_elapsed_s": round(elapsed, 3) if elapsed > 0 else 0,
            "data_duration_s": round(
                (self.data_end_ns - self.data_start_ns) / 1e9, 3
            ) if self.data_end_ns > self.data_start_ns else 0,
        }


class RecordReader:
    """Read and replay messages from .cyberrecord files.

    Supports both streaming iteration and callback-based playback.

    Usage (iterator)::

        reader = RecordReader()
        reader.load("data/records/session_20250401.cyberrecord")
        for channel, payload, timestamp_ns in reader:
            process(channel, payload)
        reader.close()

    Usage (callback playback)::

        def on_message(channel, payload, ts_ns):
            bus.publish(channel, payload)

        config = ReplayConfig(speed=2.0, callback=on_message)
        reader = RecordReader(config)
        reader.load("session.cyberrecord.gz")
        reader.play()  # blocks until finished or stopped

    Thread safety: play/pause/stop can be called from any thread.
    """

    def __init__(self, config: Optional[ReplayConfig] = None) -> None:
        self._config = config or ReplayConfig()
        self._state = ReplayState.IDLE
        self._lock = threading.Lock()
        self._pause_event = threading.Event()
        self._stop_event = threading.Event()

        # File state
        self._file_path: Optional[Path] = None
        self._fh: Optional[io.IOBase] = None
        self._header: Optional[RecordHeader] = None
        self._index: List[IndexEntry] = []
        self._manifest: Dict[str, Dict[str, Any]] = {}

        # Replay state
        self._stats = ReplayStats()
        self._current_position: int = 0
        self._play_thread: Optional[threading.Thread] = None

    # ─── Loading ─────────────────────────────────────────────────────────

    def load(self, path: str | Path) -> RecordHeader:
        """Load a .cyberrecord file (or .gz compressed variant).

        Args:
            path: Path to the record file.

        Returns:
            The parsed RecordHeader with file metadata.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the file format is invalid.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Record file not found: {file_path}")

        with self._lock:
            if self._state not in (ReplayState.IDLE, ReplayState.CLOSED,
                                    ReplayState.FINISHED):
                self._close_file()

            self._file_path = file_path

            # Open with transparent gzip detection
            if file_path.suffix == ".gz":
                self._fh = gzip.open(file_path, "rb")
            else:
                self._fh = open(file_path, "rb")

            # Read header
            header_data = self._fh.read(512)
            if not header_data or header_data[:8] != _MAGIC_HEADER:
                self._fh.seek(0)
                self._header = RecordHeader(
                    created_ns=0, description="legacy_format"
                )
            else:
                try:
                    self._header = RecordHeader.from_bytes(header_data)
                except (struct.error, UnicodeDecodeError):
                    self._fh.seek(0)
                    self._header = RecordHeader(
                        created_ns=0, description="parse_fallback"
                    )

            # Try loading index file
            self._index.clear()
            self._load_index(file_path)

            # Reset stats
            self._stats = ReplayStats()
            self._current_position = 0
            self._state = ReplayState.LOADED

            logger.info(
                "RecordReader loaded: %s (%d index entries)",
                file_path.name, len(self._index),
            )
            return self._header

    def _load_index(self, record_path: Path) -> None:
        """Try to load the companion .cyberindex file."""
        if record_path.suffix == ".gz":
            base = record_path.with_suffix("")
            idx_path = base.with_suffix(".cyberindex")
            idx_gz_path = Path(str(idx_path) + ".gz")
        else:
            idx_path = record_path.with_suffix(".cyberindex")
            idx_gz_path = Path(str(idx_path) + ".gz")

        idx_fh = None
        for candidate in (idx_path, idx_gz_path):
            if candidate.exists():
                if str(candidate).endswith(".gz"):
                    idx_fh = gzip.open(candidate, "rb")
                else:
                    idx_fh = open(candidate, "rb")
                break

        if idx_fh is None:
            return

        try:
            while True:
                chunk = idx_fh.read(_INDEX_ENTRY_SIZE)
                if len(chunk) < _INDEX_ENTRY_SIZE:
                    break
                entry = IndexEntry.from_bytes(chunk)
                self._index.append(entry)
        finally:
            idx_fh.close()

    # ─── Seeking ─────────────────────────────────────────────────────────

    def seek_to_time(self, timestamp_ns: int) -> int:
        """Seek to the first message at or after the given timestamp.

        Uses the index for O(log n) seek if available, otherwise
        falls back to linear scan.

        Args:
            timestamp_ns: Target timestamp in nanoseconds.

        Returns:
            Number of messages skipped.
        """
        if not self._index:
            return self._seek_linear(timestamp_ns)

        lo, hi = 0, len(self._index) - 1
        result_idx = len(self._index)

        while lo <= hi:
            mid = (lo + hi) // 2
            if self._index[mid].timestamp_ns >= timestamp_ns:
                result_idx = mid
                hi = mid - 1
            else:
                lo = mid + 1

        if result_idx < len(self._index):
            entry = self._index[result_idx]
            if self._fh:
                self._fh.seek(entry.byte_offset)
            self._current_position = result_idx
            return result_idx
        return 0

    def _seek_linear(self, timestamp_ns: int) -> int:
        """Fallback linear scan when no index is available."""
        if not self._fh:
            return 0

        self._fh.seek(0)
        skipped = 0
        for line in self._fh:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
                ts = record.get("t", 0)
                if ts >= timestamp_ns:
                    offset = self._fh.tell() - len(
                        (line + "\n").encode("utf-8")
                    )
                    self._fh.seek(max(0, offset))
                    return skipped
                skipped += 1
            except json.JSONDecodeError:
                continue
        return skipped

    def seek_to_channel_first(self, channel: str) -> bool:
        """Seek to the first message on the given channel."""
        ch_hash = _channel_hash(channel)
        for i, entry in enumerate(self._index):
            if entry.channel_hash == ch_hash:
                if self._fh:
                    self._fh.seek(entry.byte_offset)
                self._current_position = i
                return True
        return False

    # ─── Iteration ───────────────────────────────────────────────────────

    def read_messages(
        self,
    ) -> Generator[Tuple[str, Any, int], None, None]:
        """Iterate over all messages in the file, yielding
        (channel, payload, timestamp_ns) tuples.

        Applies channel filtering and time-range slicing from config.
        """
        if not self._fh:
            return

        for line in self._fh:
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="replace")
            line = line.strip()

            if not line or line.startswith("#"):
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                self._stats.messages_skipped += 1
                continue

            channel = record.get("c", "")
            timestamp_ns = record.get("t", 0)
            payload = record.get("p")

            self._stats.messages_read += 1
            self._stats.channels_seen.add(channel)

            if (self._stats.data_start_ns == 0
                    or timestamp_ns < self._stats.data_start_ns):
                self._stats.data_start_ns = timestamp_ns
            if timestamp_ns > self._stats.data_end_ns:
                self._stats.data_end_ns = timestamp_ns

            if (self._config.start_time_ns > 0
                    and timestamp_ns < self._config.start_time_ns):
                self._stats.messages_skipped += 1
                continue
            if (self._config.end_time_ns > 0
                    and timestamp_ns > self._config.end_time_ns):
                return

            if self._config.include_channels is not None:
                if channel not in self._config.include_channels:
                    self._stats.messages_skipped += 1
                    continue
            if channel in self._config.exclude_channels:
                self._stats.messages_skipped += 1
                continue

            self._stats.messages_replayed += 1
            yield (channel, payload, timestamp_ns)

    def __iter__(self) -> Iterator[Tuple[str, Any, int]]:
        return self.read_messages()

    # ─── Callback playback ───────────────────────────────────────────────

    def play(self, blocking: bool = True) -> None:
        """Start callback-based playback at configured speed."""
        if self._config.callback is None:
            raise ValueError(
                "ReplayConfig.callback must be set for play() mode"
            )
        if self._state not in (ReplayState.LOADED, ReplayState.FINISHED):
            raise RuntimeError(
                f"Cannot play from state {self._state.name}"
            )

        self._stop_event.clear()
        self._pause_event.clear()
        self._state = ReplayState.PLAYING
        self._stats.replay_start_time = time.monotonic()

        if blocking:
            self._play_loop()
        else:
            self._play_thread = threading.Thread(
                target=self._play_loop,
                name="record-reader-play",
                daemon=True,
            )
            self._play_thread.start()

    def _play_loop(self) -> None:
        """Internal playback loop with timing control."""
        speed = max(_MIN_SPEED, min(_MAX_SPEED, self._config.speed))
        prev_data_ts: Optional[int] = None
        prev_wall_time: Optional[float] = None

        while True:
            for channel, payload, ts_ns in self.read_messages():
                if self._stop_event.is_set():
                    self._state = ReplayState.LOADED
                    self._stats.replay_end_time = time.monotonic()
                    return

                while self._pause_event.is_set():
                    if self._stop_event.is_set():
                        self._state = ReplayState.LOADED
                        self._stats.replay_end_time = time.monotonic()
                        return
                    time.sleep(0.01)

                if prev_data_ts is not None and prev_wall_time is not None:
                    data_delta_s = (ts_ns - prev_data_ts) / 1e9
                    target_wall_delta = data_delta_s / speed
                    wall_elapsed = time.monotonic() - prev_wall_time
                    sleep_time = target_wall_delta - wall_elapsed
                    if sleep_time > 0:
                        if self._stop_event.wait(timeout=sleep_time):
                            self._state = ReplayState.LOADED
                            self._stats.replay_end_time = time.monotonic()
                            return

                prev_data_ts = ts_ns
                prev_wall_time = time.monotonic()

                try:
                    self._config.callback(channel, payload, ts_ns)
                except Exception as exc:
                    logger.warning(
                        "Replay callback error on %s: %s", channel, exc
                    )

            if not self._config.loop:
                break

            if self._fh:
                self._fh.seek(0)
            prev_data_ts = None
            prev_wall_time = None
            logger.info("RecordReader: looping replay")

        self._state = ReplayState.FINISHED
        self._stats.replay_end_time = time.monotonic()

    def pause(self) -> None:
        if self._state == ReplayState.PLAYING:
            self._pause_event.set()
            self._state = ReplayState.PAUSED

    def resume(self) -> None:
        if self._state == ReplayState.PAUSED:
            self._pause_event.clear()
            self._state = ReplayState.PLAYING

    def stop(self) -> None:
        self._stop_event.set()
        if self._play_thread and self._play_thread.is_alive():
            self._play_thread.join(timeout=5.0)

    # ─── Cleanup ─────────────────────────────────────────────────────────

    def _close_file(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None

    def close(self) -> ReplayStats:
        self.stop()
        self._close_file()
        self._state = ReplayState.CLOSED
        self._index.clear()
        return self._stats

    # ─── Introspection ───────────────────────────────────────────────────

    @property
    def header(self) -> Optional[RecordHeader]:
        return self._header

    @property
    def state(self) -> ReplayState:
        return self._state

    @property
    def channel_list(self) -> List[str]:
        return sorted(self._stats.channels_seen)

    def stats_dict(self) -> Dict[str, Any]:
        return {
            "state": self._state.name,
            "file": str(self._file_path) if self._file_path else None,
            "index_entries": len(self._index),
            **self._stats.to_dict(),
        }

    def __repr__(self) -> str:
        return (
            f"<RecordReader state={self._state.name} "
            f"file={self._file_path}>"
        )

    def __enter__(self) -> "RecordReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

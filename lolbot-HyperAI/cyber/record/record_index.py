"""
cyber/record/record_index.py — Time-based Replay Record Index
===============================================================
lolbot-HyperAI · Cyber Record Layer

查看 cyber/record/record_writer.py 上现有 RecordWriter 的实现方式, 理解其
模式, 特别是 write_message() 是如何按时间顺序写入 JSONL 的。从 RecordWriter
这个好例子开始。然后, 遵循该模式实现一个新的 RecordIndex, 让 replay 工具
可以按游戏时间戳快速定位到任意时刻, 并能支持 channel 过滤 (只回放
/lol/game_state 而跳过 /lol/monitor_status)。接着在 record_reader.py 引入
seek_to_time() 接口, 使 ReplayDriver 能够快速跳转, 同时优化大文件的索引
加载 (延迟加载 + mmap-friendly 布局)。

Architecture position:
    cyber/record/record_index.py   ← YOU ARE HERE
    ├─ Built by: record_writer.py (writes index alongside record)
    ├─ Used by: record_reader.py (seek_to_time, channel filter)
    ├─ Used by: modules/drivers/replay_driver.py (replay seek)
    ├─ Used by: scripts/replay_simulator.py (interactive replay)
    └─ Format: .idx companion file (binary, sortable by time)

Apollo reference:
    cyber/record/record_file_base.h — file layout with index section
    cyber/record/record_reader.cc   — SeekToTime() via index lookup

Design notes:
    - Index stored as sorted list of (game_time, file_offset, channel)
    - Binary search for O(log n) time-based seek
    - Optional in-memory channel filter bitmask for fast sub-selection
    - Index file format: 4-byte header + N * IndexEntry (fixed-size)
    - Lazy loading: only parse index when first seek is requested
    - Thread-safe for concurrent read (replay + dashboard)
"""

from __future__ import annotations

import bisect
import io
import json
import logging
import os
import struct
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any, BinaryIO, Dict, Iterator, List, Optional, Set, Tuple,
)

from cyber.logger.cyber_logger import get_logger

logger = get_logger("record.index")

# ─── Constants ───────────────────────────────────────────────────────────────

_INDEX_MAGIC = b"LBIX"                   # 4-byte magic header
_INDEX_VERSION = 1
_ENTRY_FORMAT = "<dqH"                    # game_time(f64) + offset(i64) + channel_id(u16)
_ENTRY_SIZE = struct.calcsize(_ENTRY_FORMAT)  # 18 bytes per entry
_HEADER_FORMAT = "<4sBII"                 # magic(4) + version(1) + entry_count(4) + channel_count(4)
_HEADER_SIZE = struct.calcsize(_HEADER_FORMAT)
_CHANNEL_ENTRY_FORMAT = "<H"              # channel_id(u16)
_MAX_CHANNEL_NAME_LEN = 200
_INDEX_FLUSH_INTERVAL = 50                # flush index every N entries
_BATCH_SEEK_WINDOW_S = 0.5               # default window for range queries


# ─── Index Entry ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, order=True)
class IndexEntry:
    """Single index entry mapping game time to file position.

    Entries are naturally ordered by game_time, enabling binary
    search for efficient time-based seeks.

    Attributes:
        game_time: In-game timestamp in seconds.
        file_offset: Byte offset into the .rec JSONL file.
        channel_id: Numeric ID for the channel (maps to channel name).
    """
    game_time: float
    file_offset: int
    channel_id: int = 0


# ─── Channel Map ─────────────────────────────────────────────────────────────

class ChannelMap:
    """Bidirectional mapping between channel names and numeric IDs.

    Stored in the index file header so the index can use compact
    2-byte channel IDs instead of full strings.
    """

    def __init__(self) -> None:
        self._name_to_id: Dict[str, int] = {}
        self._id_to_name: Dict[int, str] = {}
        self._next_id: int = 0
        self._lock = threading.Lock()

    def get_or_create_id(self, channel_name: str) -> int:
        """Get numeric ID for a channel name, creating if needed."""
        with self._lock:
            if channel_name in self._name_to_id:
                return self._name_to_id[channel_name]
            cid = self._next_id
            self._name_to_id[channel_name] = cid
            self._id_to_name[cid] = channel_name
            self._next_id += 1
            return cid

    def get_name(self, channel_id: int) -> Optional[str]:
        """Look up channel name from numeric ID."""
        return self._id_to_name.get(channel_id)

    def get_id(self, channel_name: str) -> Optional[int]:
        """Look up numeric ID from channel name."""
        return self._name_to_id.get(channel_name)

    def all_channels(self) -> List[str]:
        """Return all registered channel names."""
        return list(self._name_to_id.keys())

    def count(self) -> int:
        return len(self._name_to_id)

    def to_dict(self) -> Dict[str, int]:
        return dict(self._name_to_id)

    @classmethod
    def from_dict(cls, mapping: Dict[str, int]) -> ChannelMap:
        cm = cls()
        for name, cid in mapping.items():
            cm._name_to_id[name] = cid
            cm._id_to_name[cid] = name
            cm._next_id = max(cm._next_id, cid + 1)
        return cm


# ─── Record Index Writer ─────────────────────────────────────────────────────

class RecordIndexWriter:
    """Writes a .idx companion file alongside a .rec record.

    Used by RecordWriter to build a searchable index as messages
    are recorded. The index is written incrementally and flushed
    periodically.

    Usage::

        writer = RecordIndexWriter("/path/to/game.idx")
        writer.open()
        writer.add_entry(game_time=123.4, offset=8192, channel="/lol/raw_lcu")
        writer.close()  # finalizes header
    """

    def __init__(self, index_path: str) -> None:
        self._path = Path(index_path)
        self._channel_map = ChannelMap()
        self._entries: List[IndexEntry] = []
        self._file: Optional[BinaryIO] = None
        self._entry_count = 0
        self._flush_counter = 0
        self._lock = threading.Lock()
        self._closed = False

    def open(self) -> None:
        """Open the index file for writing."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "wb")
        # Write placeholder header (will be overwritten on close)
        self._write_header_placeholder()
        logger.debug("RecordIndexWriter opened: %s", self._path)

    def add_entry(
        self,
        game_time: float,
        offset: int,
        channel: str,
    ) -> None:
        """Add an index entry.

        Args:
            game_time: In-game time in seconds.
            offset: Byte offset in the .rec file for this message.
            channel: Channel name, e.g. "/lol/raw_lcu".
        """
        if self._closed or self._file is None:
            return

        with self._lock:
            cid = self._channel_map.get_or_create_id(channel)
            entry = IndexEntry(
                game_time=game_time,
                file_offset=offset,
                channel_id=cid,
            )
            self._entries.append(entry)
            self._entry_count += 1
            self._flush_counter += 1

            # Write entry to file
            self._file.write(
                struct.pack(
                    _ENTRY_FORMAT,
                    entry.game_time,
                    entry.file_offset,
                    entry.channel_id,
                )
            )

            if self._flush_counter >= _INDEX_FLUSH_INTERVAL:
                self._file.flush()
                self._flush_counter = 0

    def close(self) -> None:
        """Finalize and close the index file.

        Rewrites the header with the final entry count and channel
        map, then writes the channel table at the end of the file.
        """
        if self._closed or self._file is None:
            return

        with self._lock:
            self._closed = True

            # Write channel table at current position
            channel_table_offset = self._file.tell()
            self._write_channel_table()

            # Rewrite header with final counts
            self._file.seek(0)
            self._file.write(
                struct.pack(
                    _HEADER_FORMAT,
                    _INDEX_MAGIC,
                    _INDEX_VERSION,
                    self._entry_count,
                    self._channel_map.count(),
                )
            )

            self._file.flush()
            self._file.close()
            self._file = None

            logger.info(
                "RecordIndex closed: %d entries, %d channels → %s",
                self._entry_count, self._channel_map.count(), self._path,
            )

    @property
    def entry_count(self) -> int:
        return self._entry_count

    @property
    def channel_map(self) -> ChannelMap:
        return self._channel_map

    # ── Private ──────────────────────────────────────────────────────────

    def _write_header_placeholder(self) -> None:
        """Write a placeholder header (overwritten on close)."""
        if self._file:
            self._file.write(
                struct.pack(
                    _HEADER_FORMAT,
                    _INDEX_MAGIC,
                    _INDEX_VERSION,
                    0,  # entry_count (placeholder)
                    0,  # channel_count (placeholder)
                )
            )

    def _write_channel_table(self) -> None:
        """Write the channel name → ID mapping table."""
        if self._file is None:
            return
        table = self._channel_map.to_dict()
        table_json = json.dumps(table, separators=(",", ":")).encode("utf-8")
        # Write length-prefixed JSON blob
        self._file.write(struct.pack("<I", len(table_json)))
        self._file.write(table_json)


# ─── Record Index Reader ─────────────────────────────────────────────────────

class RecordIndexReader:
    """Reads a .idx file for efficient time-based seek in replays.

    Supports binary search by game time and channel filtering.
    Lazy-loads: index is not parsed until the first query.

    Usage::

        reader = RecordIndexReader("/path/to/game.idx")
        reader.load()

        # Seek to game time 300.0
        entry = reader.seek_to_time(300.0)
        # entry.file_offset is the byte offset in the .rec file

        # Get all entries for a specific channel in a time range
        entries = reader.range_query(
            start_time=120.0,
            end_time=180.0,
            channels={"/lol/game_state"},
        )
    """

    def __init__(self, index_path: str) -> None:
        self._path = Path(index_path)
        self._entries: List[IndexEntry] = []
        self._channel_map = ChannelMap()
        self._loaded = False
        self._lock = threading.Lock()

        # Pre-sorted times for binary search
        self._sorted_times: List[float] = []

        # Per-channel entry lists (built on demand)
        self._channel_entries: Dict[int, List[IndexEntry]] = {}

    def load(self) -> bool:
        """Load the index from disk.

        Returns True on success, False if the file is missing or
        corrupt. Safe to call multiple times (no-op after first load).
        """
        if self._loaded:
            return True

        with self._lock:
            if self._loaded:
                return True

            if not self._path.exists():
                logger.warning("Index file not found: %s", self._path)
                return False

            try:
                self._parse_index_file()
                self._loaded = True
                logger.info(
                    "RecordIndex loaded: %d entries, %d channels from %s",
                    len(self._entries),
                    self._channel_map.count(),
                    self._path,
                )
                return True
            except Exception:
                logger.error(
                    "Failed to load index: %s", self._path, exc_info=True,
                )
                return False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    @property
    def channels(self) -> List[str]:
        """All channels present in the index."""
        return self._channel_map.all_channels()

    def time_range(self) -> Tuple[float, float]:
        """Return (min_time, max_time) in the index."""
        if not self._entries:
            return (0.0, 0.0)
        return (self._entries[0].game_time, self._entries[-1].game_time)

    def seek_to_time(
        self,
        target_time: float,
        channels: Optional[Set[str]] = None,
    ) -> Optional[IndexEntry]:
        """Find the nearest entry at or before the target game time.

        Uses binary search for O(log n) performance.

        Args:
            target_time: Game time in seconds.
            channels: Optional set of channel names to filter.

        Returns:
            The IndexEntry at or before target_time, or None.
        """
        self._ensure_loaded()

        if not self._entries:
            return None

        # Binary search for the position
        idx = bisect.bisect_right(self._sorted_times, target_time)

        if idx == 0:
            # target_time is before the first entry
            candidate = self._entries[0]
        else:
            candidate = self._entries[idx - 1]

        # If channel filter is specified, scan backward to find a match
        if channels is not None:
            channel_ids = self._resolve_channel_ids(channels)
            if not channel_ids:
                return None

            for i in range(max(0, idx - 1), -1, -1):
                entry = self._entries[i]
                if entry.channel_id in channel_ids:
                    return entry
            return None

        return candidate

    def seek_after_time(
        self,
        target_time: float,
        channels: Optional[Set[str]] = None,
    ) -> Optional[IndexEntry]:
        """Find the first entry strictly after target_time."""
        self._ensure_loaded()

        if not self._entries:
            return None

        idx = bisect.bisect_right(self._sorted_times, target_time)
        if idx >= len(self._entries):
            return None

        if channels is None:
            return self._entries[idx]

        channel_ids = self._resolve_channel_ids(channels)
        for i in range(idx, len(self._entries)):
            if self._entries[i].channel_id in channel_ids:
                return self._entries[i]
        return None

    def range_query(
        self,
        start_time: float,
        end_time: float,
        channels: Optional[Set[str]] = None,
        max_entries: int = 10000,
    ) -> List[IndexEntry]:
        """Get all entries within a time range.

        Args:
            start_time: Inclusive lower bound.
            end_time: Inclusive upper bound.
            channels: Optional channel filter.
            max_entries: Safety limit to prevent OOM.

        Returns:
            List of IndexEntry objects in time order.
        """
        self._ensure_loaded()

        if not self._entries:
            return []

        # Find start position via binary search
        start_idx = bisect.bisect_left(self._sorted_times, start_time)
        end_idx = bisect.bisect_right(self._sorted_times, end_time)

        channel_ids: Optional[Set[int]] = None
        if channels is not None:
            channel_ids = self._resolve_channel_ids(channels)
            if not channel_ids:
                return []

        result: List[IndexEntry] = []
        for i in range(start_idx, min(end_idx, len(self._entries))):
            if len(result) >= max_entries:
                break
            entry = self._entries[i]
            if channel_ids is None or entry.channel_id in channel_ids:
                result.append(entry)

        return result

    def entries_for_channel(self, channel: str) -> List[IndexEntry]:
        """Get all entries for a specific channel."""
        self._ensure_loaded()
        cid = self._channel_map.get_id(channel)
        if cid is None:
            return []
        if cid not in self._channel_entries:
            self._channel_entries[cid] = [
                e for e in self._entries if e.channel_id == cid
            ]
        return self._channel_entries[cid]

    def summary(self) -> Dict[str, Any]:
        """Summary statistics for the index."""
        self._ensure_loaded()
        tmin, tmax = self.time_range()
        return {
            "path": str(self._path),
            "loaded": self._loaded,
            "entry_count": len(self._entries),
            "channels": self._channel_map.all_channels(),
            "time_range_s": (round(tmin, 2), round(tmax, 2)),
            "duration_s": round(tmax - tmin, 2),
            "size_bytes": (
                self._path.stat().st_size if self._path.exists() else 0
            ),
        }

    # ── Private ──────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _parse_index_file(self) -> None:
        """Parse the binary index file into memory."""
        with open(self._path, "rb") as f:
            # Read header
            header_data = f.read(_HEADER_SIZE)
            if len(header_data) < _HEADER_SIZE:
                raise ValueError("Index file too short for header")

            magic, version, entry_count, channel_count = struct.unpack(
                _HEADER_FORMAT, header_data,
            )

            if magic != _INDEX_MAGIC:
                raise ValueError(
                    f"Invalid index magic: {magic!r}, expected {_INDEX_MAGIC!r}"
                )
            if version != _INDEX_VERSION:
                raise ValueError(
                    f"Unsupported index version: {version}"
                )

            # Read entries
            entries: List[IndexEntry] = []
            for _ in range(entry_count):
                entry_data = f.read(_ENTRY_SIZE)
                if len(entry_data) < _ENTRY_SIZE:
                    logger.warning(
                        "Truncated index: expected %d entries, got %d",
                        entry_count, len(entries),
                    )
                    break
                game_time, offset, cid = struct.unpack(
                    _ENTRY_FORMAT, entry_data,
                )
                entries.append(IndexEntry(
                    game_time=game_time,
                    file_offset=offset,
                    channel_id=cid,
                ))

            # Read channel table (at end of file)
            try:
                table_len_data = f.read(4)
                if len(table_len_data) == 4:
                    table_len = struct.unpack("<I", table_len_data)[0]
                    table_json = f.read(table_len).decode("utf-8")
                    table = json.loads(table_json)
                    self._channel_map = ChannelMap.from_dict(table)
            except Exception:
                logger.warning(
                    "Could not read channel table from %s", self._path,
                )

            # Sort entries by time and build search arrays
            entries.sort(key=lambda e: e.game_time)
            self._entries = entries
            self._sorted_times = [e.game_time for e in entries]

    def _resolve_channel_ids(self, channels: Set[str]) -> Set[int]:
        """Convert channel names to numeric IDs."""
        ids: Set[int] = set()
        for ch in channels:
            cid = self._channel_map.get_id(ch)
            if cid is not None:
                ids.add(cid)
        return ids


# ─── Convenience: auto-detect .idx path from .rec path ──────────────────────

def index_path_for_record(record_path: str) -> str:
    """Given a .rec file path, return the companion .idx path.

    Convention: /path/to/game_20240101_120000.rec
              → /path/to/game_20240101_120000.idx
    """
    p = Path(record_path)
    return str(p.with_suffix(".idx"))

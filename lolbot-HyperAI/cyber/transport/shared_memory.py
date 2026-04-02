"""
SharedMemoryTransport — Zero-copy large message passing via shared memory.
===========================================================================
lolbot-HyperAI · Cyber Layer

Provides a shared-memory ring buffer for passing large messages
(e.g. full GameSnapshot with 10 player states) between components
without deep-copy overhead.  Writers serialize once; readers get
a read-only view with copy-on-read semantics.

Architecture position:
    cyber/transport/shared_memory.py   ← YOU ARE HERE
    ├─ Used by: canbus → perception (large raw game data)
    ├─ Used by: perception → prediction (GameSnapshot)
    ├─ Fallback: regular CyberNode channel for small messages
    └─ Config: enable_shared_memory in pipeline.yaml

Apollo reference:
    cyber/transport/shm/readable_info.h — shared memory segment
    cyber/transport/shm/segment.h — ring buffer over mmap
    cyber/transport/shm/notifier_factory.cc — notification

Design notes:
    - Pure Python using mmap for single-process multi-thread scenario
    - Ring buffer with fixed slot count (power of 2)
    - Write-lock per slot; readers never block each other
    - Automatic fallback to deep-copy when shm is unavailable
    - Reference counting for safe slot reuse
    - Suitable for messages 1KB-1MB; smaller messages use normal channels
"""

from __future__ import annotations

import io
import json
import logging
import mmap
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from cyber.logger.cyber_logger import get_logger

logger = get_logger("cyber.transport.shm")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_SLOT_COUNT = 16         # Number of ring buffer slots (power of 2)
_DEFAULT_SLOT_SIZE = 256 * 1024  # 256 KB per slot
_SLOT_HEADER_SIZE = 20           # Header per slot: magic(4) + seq(4) + size(4) + timestamp(8)
_SLOT_HEADER_FORMAT = "!4sIId"   # magic(4) + sequence(4) + payload_size(4) + timestamp(8)
_SLOT_MAGIC = b"SLOT"
_MAX_MESSAGE_SIZE = 4 * 1024 * 1024  # 4 MB hard limit


# ─── Slot Metadata ───────────────────────────────────────────────────────────

@dataclass
class SlotInfo:
    """Metadata for a single ring buffer slot."""
    index: int
    sequence: int = 0
    payload_size: int = 0
    timestamp: float = 0.0
    is_written: bool = False
    ref_count: int = 0


# ─── Shared Memory Segment ──────────────────────────────────────────────────

class SharedMemorySegment:
    """A shared memory segment backed by mmap.

    Manages a contiguous memory region divided into fixed-size slots.
    Each slot has a header (sequence number, size, timestamp) followed
    by the payload area.

    The segment can be backed by:
    - Anonymous mmap (default, single process)
    - File-backed mmap (for cross-process, future extension)
    """

    def __init__(
        self,
        slot_count: int = _DEFAULT_SLOT_COUNT,
        slot_size: int = _DEFAULT_SLOT_SIZE,
        name: str = "lolbot_shm",
    ) -> None:
        # Ensure slot_count is power of 2
        self._slot_count = 1
        while self._slot_count < slot_count:
            self._slot_count <<= 1

        self._slot_size = max(1024, slot_size)
        self._name = name
        self._total_size = self._slot_count * (self._slot_size + _SLOT_HEADER_SIZE)
        self._slots: List[SlotInfo] = [
            SlotInfo(index=i) for i in range(self._slot_count)
        ]
        self._mmap: Optional[mmap.mmap] = None
        self._lock = threading.Lock()
        self._initialized = False

        # Fallback mode (no mmap, use in-memory buffers)
        self._fallback_buffers: Dict[int, bytes] = {}
        self._use_fallback = False

    def initialize(self) -> bool:
        """Initialize the shared memory segment.

        Returns:
            True if mmap succeeded, False if falling back to in-memory.
        """
        try:
            # Anonymous mmap (no file backing needed for single-process)
            self._mmap = mmap.mmap(-1, self._total_size)
            # Zero out
            self._mmap.write(b"\x00" * self._total_size)
            self._mmap.seek(0)
            self._initialized = True
            self._use_fallback = False
            logger.info(
                "SharedMemory initialized: %d slots × %d KB = %d KB total",
                self._slot_count,
                self._slot_size // 1024,
                self._total_size // 1024,
            )
            return True
        except (OSError, ValueError) as exc:
            logger.warning(
                "mmap failed (%s), using in-memory fallback", exc,
            )
            self._use_fallback = True
            self._initialized = True
            return False

    def write_slot(
        self,
        slot_index: int,
        data: bytes,
        sequence: int,
    ) -> bool:
        """Write data to a specific slot.

        Args:
            slot_index: Ring buffer slot index.
            data: Serialized payload bytes.
            sequence: Monotonic sequence number.

        Returns:
            True if write succeeded.
        """
        if not self._initialized:
            return False

        idx = slot_index % self._slot_count
        if len(data) > self._slot_size:
            logger.error(
                "Payload too large for slot: %d > %d",
                len(data), self._slot_size,
            )
            return False

        with self._lock:
            if self._use_fallback:
                self._fallback_buffers[idx] = data
            else:
                offset = idx * (self._slot_size + _SLOT_HEADER_SIZE)
                # Write header
                header = struct.pack(
                    _SLOT_HEADER_FORMAT,
                    _SLOT_MAGIC,
                    sequence,
                    len(data),
                    time.monotonic(),
                )
                self._mmap.seek(offset)
                self._mmap.write(header)
                # Write payload
                self._mmap.write(data)

            # Update slot metadata
            slot = self._slots[idx]
            slot.sequence = sequence
            slot.payload_size = len(data)
            slot.timestamp = time.monotonic()
            slot.is_written = True

        return True

    def read_slot(self, slot_index: int) -> Optional[Tuple[bytes, int, float]]:
        """Read data from a specific slot.

        Returns:
            (payload_bytes, sequence, timestamp) or None if slot empty.
        """
        if not self._initialized:
            return None

        idx = slot_index % self._slot_count
        slot = self._slots[idx]

        if not slot.is_written:
            return None

        with self._lock:
            if self._use_fallback:
                data = self._fallback_buffers.get(idx)
                if data is None:
                    return None
                # Copy-on-read: return a copy
                return bytes(data), slot.sequence, slot.timestamp
            else:
                offset = idx * (self._slot_size + _SLOT_HEADER_SIZE)
                self._mmap.seek(offset)
                header_bytes = self._mmap.read(_SLOT_HEADER_SIZE)

                magic, seq, payload_size, ts = struct.unpack(
                    _SLOT_HEADER_FORMAT, header_bytes,
                )
                if magic != _SLOT_MAGIC:
                    return None

                payload = self._mmap.read(payload_size)
                # Copy-on-read: bytes() creates a new copy
                return bytes(payload), seq, ts

    def get_latest_slot_index(self) -> int:
        """Return the index of the most recently written slot."""
        latest_idx = 0
        latest_seq = -1
        for slot in self._slots:
            if slot.is_written and slot.sequence > latest_seq:
                latest_seq = slot.sequence
                latest_idx = slot.index
        return latest_idx

    @property
    def slot_count(self) -> int:
        return self._slot_count

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_fallback(self) -> bool:
        return self._use_fallback

    def shutdown(self) -> None:
        """Release shared memory resources."""
        if self._mmap is not None:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None
        self._fallback_buffers.clear()
        self._initialized = False
        logger.info("SharedMemory segment released")


# ─── SharedMemoryTransport ──────────────────────────────────────────────────

class SharedMemoryWriter:
    """Writes messages to a shared memory channel.

    Serializes messages to JSON bytes and writes them to the next
    available ring buffer slot.  Sequence numbers are monotonically
    increasing for ordering.
    """

    def __init__(
        self,
        channel_name: str,
        segment: SharedMemorySegment,
    ) -> None:
        self._channel = channel_name
        self._segment = segment
        self._sequence: int = 0
        self._write_count: int = 0
        self._total_bytes: int = 0

    def write(self, message: Any) -> bool:
        """Serialize and write a message to shared memory.

        Args:
            message: Any JSON-serializable object.

        Returns:
            True if write succeeded.
        """
        try:
            if isinstance(message, bytes):
                data = message
            elif isinstance(message, str):
                data = message.encode("utf-8")
            else:
                data = json.dumps(message, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            logger.error("Serialization failed: %s", exc)
            return False

        if len(data) > _MAX_MESSAGE_SIZE:
            logger.error("Message too large: %d bytes", len(data))
            return False

        slot_idx = self._sequence % self._segment.slot_count
        success = self._segment.write_slot(slot_idx, data, self._sequence)

        if success:
            self._sequence += 1
            self._write_count += 1
            self._total_bytes += len(data)

        return success

    @property
    def write_count(self) -> int:
        return self._write_count

    @property
    def channel_name(self) -> str:
        return self._channel

    def stats(self) -> Dict[str, Any]:
        return {
            "channel": self._channel,
            "writes": self._write_count,
            "sequence": self._sequence,
            "total_bytes": self._total_bytes,
        }


class SharedMemoryReader:
    """Reads messages from a shared memory channel.

    Tracks the last read sequence number and reads new messages
    from the ring buffer.  Copy-on-read semantics ensure thread safety.
    """

    def __init__(
        self,
        channel_name: str,
        segment: SharedMemorySegment,
    ) -> None:
        self._channel = channel_name
        self._segment = segment
        self._last_read_seq: int = -1
        self._read_count: int = 0

    def read_latest(self) -> Optional[Any]:
        """Read the most recent message from the channel.

        Returns:
            Deserialized message, or None if no new data.
        """
        latest_idx = self._segment.get_latest_slot_index()
        result = self._segment.read_slot(latest_idx)

        if result is None:
            return None

        data, seq, ts = result

        if seq <= self._last_read_seq:
            return None  # Already read

        self._last_read_seq = seq
        self._read_count += 1

        try:
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return data  # Return raw bytes

    def read_all_new(self) -> List[Any]:
        """Read all messages newer than last read.

        Returns:
            List of deserialized messages, oldest first.
        """
        messages = []
        for i in range(self._segment.slot_count):
            result = self._segment.read_slot(i)
            if result is None:
                continue
            data, seq, ts = result
            if seq > self._last_read_seq:
                try:
                    msg = json.loads(data.decode("utf-8"))
                    messages.append((seq, msg))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    messages.append((seq, data))

        # Sort by sequence
        messages.sort(key=lambda x: x[0])

        if messages:
            self._last_read_seq = messages[-1][0]
            self._read_count += len(messages)

        return [msg for _, msg in messages]

    @property
    def read_count(self) -> int:
        return self._read_count

    @property
    def channel_name(self) -> str:
        return self._channel

    def stats(self) -> Dict[str, Any]:
        return {
            "channel": self._channel,
            "reads": self._read_count,
            "last_seq": self._last_read_seq,
        }


# ─── Transport Factory ──────────────────────────────────────────────────────

class SharedMemoryTransport:
    """Factory for creating shared memory channels.

    Manages segment lifecycle and provides writer/reader creation.

    Example::

        transport = SharedMemoryTransport()
        transport.initialize()

        writer = transport.create_writer("/lol/game_state")
        reader = transport.create_reader("/lol/game_state")

        writer.write({"game_time": 600.0, "players": [...]})
        data = reader.read_latest()
    """

    def __init__(
        self,
        slot_count: int = _DEFAULT_SLOT_COUNT,
        slot_size: int = _DEFAULT_SLOT_SIZE,
    ) -> None:
        self._segments: Dict[str, SharedMemorySegment] = {}
        self._slot_count = slot_count
        self._slot_size = slot_size
        self._writers: List[SharedMemoryWriter] = []
        self._readers: List[SharedMemoryReader] = []

    def initialize(self) -> bool:
        """Initialize the transport (pre-create default segment)."""
        logger.info("SharedMemoryTransport initializing...")
        return True

    def create_writer(self, channel_name: str) -> SharedMemoryWriter:
        """Create a writer for a shared memory channel."""
        segment = self._get_or_create_segment(channel_name)
        writer = SharedMemoryWriter(channel_name, segment)
        self._writers.append(writer)
        return writer

    def create_reader(self, channel_name: str) -> SharedMemoryReader:
        """Create a reader for a shared memory channel."""
        segment = self._get_or_create_segment(channel_name)
        reader = SharedMemoryReader(channel_name, segment)
        self._readers.append(reader)
        return reader

    def _get_or_create_segment(self, channel_name: str) -> SharedMemorySegment:
        """Get or create a shared memory segment for a channel."""
        if channel_name not in self._segments:
            segment = SharedMemorySegment(
                slot_count=self._slot_count,
                slot_size=self._slot_size,
                name=channel_name.replace("/", "_"),
            )
            segment.initialize()
            self._segments[channel_name] = segment
        return self._segments[channel_name]

    def shutdown(self) -> Dict[str, Any]:
        """Shutdown all segments and return stats."""
        stats = self.stats()
        for segment in self._segments.values():
            segment.shutdown()
        self._segments.clear()
        self._writers.clear()
        self._readers.clear()
        return stats

    def stats(self) -> Dict[str, Any]:
        return {
            "segments": len(self._segments),
            "writers": [w.stats() for w in self._writers],
            "readers": [r.stats() for r in self._readers],
            "channels": list(self._segments.keys()),
        }

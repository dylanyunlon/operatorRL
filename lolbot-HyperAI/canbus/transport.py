#!/usr/bin/env python3
"""
canbus/transport.py — CAN Bus Transport Layer with Replay & Diagnostics
=========================================================================
lolbot-HyperAI · Apollo-style CAN Bus Architecture

Apollo's cyber/transport manages reader/writer channels with QoS,
serialization, and replay from rosbag-like recordings. Our transport
adds:
    1. Message recording to JSONL files (like rosbag / Apollo's cyber_recorder)
    2. Replay from recordings for offline analysis & evolution evaluation
    3. Rate limiting per channel (prevent flood from a buggy module)
    4. Latency tracking per publisher→subscriber path
    5. Dead-letter queue for failed deliveries

The transport wraps MessageBus and adds these cross-cutting concerns
without modifying the bus itself (Decorator pattern).

Design principle: The transport is the ONLY thing that touches disk I/O
for message logging. Individual components never write messages to disk;
they publish to the bus, and the transport's recorder captures everything.
This separation is critical for the evolution loop — we can replay an
entire game session through a mutated module graph without any network.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any, Callable, Deque, Dict, Iterator, List, Optional, Set, Tuple,
)

from .channel_message import (
    ChannelMessage, MessageBus, MessageFactory, _monotonic_ms,
)


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------
@dataclass
class _RateLimit:
    """Token-bucket rate limiter for a single channel."""
    max_per_sec: float
    tokens: float = 0.0
    last_refill_ms: int = 0

    def allow(self, now_ms: int) -> bool:
        elapsed_sec = (now_ms - self.last_refill_ms) / 1000.0
        self.tokens = min(
            self.max_per_sec,
            self.tokens + elapsed_sec * self.max_per_sec,
        )
        self.last_refill_ms = now_ms
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


# ---------------------------------------------------------------------------
# Dead Letter Entry
# ---------------------------------------------------------------------------
@dataclass
class DeadLetter:
    """A message that failed delivery to at least one subscriber."""
    message: ChannelMessage
    error: str
    failed_subscriber: str
    timestamp_ms: int = field(default_factory=_monotonic_ms)


# ---------------------------------------------------------------------------
# Message Recorder (like Apollo cyber_recorder / rosbag)
# ---------------------------------------------------------------------------
class MessageRecorder:
    """
    Records all bus messages to a JSONL file for later replay.

    Each line is a JSON object with the full ChannelMessage plus
    a 'wall_clock_iso' field for human debugging.

    File format: .jsonl (optionally .jsonl.gz for completed sessions).

    Usage:
        recorder = MessageRecorder(Path("logs/session_001.jsonl"))
        recorder.start()
        # ... game runs, transport feeds messages to recorder ...
        recorder.stop()
        recorder.compress()  # → session_001.jsonl.gz
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file = None
        self._count = 0
        self._started = False
        self._bytes_written = 0

    def start(self) -> None:
        """Open the recording file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self._path, "a", encoding="utf-8")
        self._started = True

    def record(self, msg: ChannelMessage) -> None:
        """Write one message to the log."""
        if not self._started or self._file is None:
            return
        record = msg.to_dict()
        record["_wall_clock_iso"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        line = json.dumps(record, separators=(",", ":"), default=str)
        self._file.write(line + "\n")
        self._count += 1
        self._bytes_written += len(line) + 1
        # Flush every 100 messages for crash resilience
        if self._count % 100 == 0:
            self._file.flush()

    def stop(self) -> Dict[str, Any]:
        """
        Close the recording file and return summary stats.

        Returns dict with message_count, bytes_written, path.
        """
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None
        self._started = False
        return {
            "message_count": self._count,
            "bytes_written": self._bytes_written,
            "path": str(self._path),
        }

    def compress(self) -> Optional[Path]:
        """
        Gzip the JSONL file after session ends.

        Returns the path to the .gz file, or None if the source
        doesn't exist.
        """
        if not self._path.exists():
            return None
        gz_path = self._path.with_suffix(".jsonl.gz")
        with open(self._path, "rb") as f_in:
            with gzip.open(gz_path, "wb") as f_out:
                f_out.writelines(f_in)
        self._path.unlink()
        return gz_path

    @property
    def message_count(self) -> int:
        return self._count


# ---------------------------------------------------------------------------
# Message Replayer (like rosbag play)
# ---------------------------------------------------------------------------
class MessageReplayer:
    """
    Replays a recorded JSONL session through a MessageBus.

    Supports:
        - Real-time replay (preserving original timing)
        - Fast-forward replay (as fast as possible)
        - Channel filtering (replay only specific channels)
        - Time-range slicing (replay from T1 to T2)

    Used by the evolution controller to evaluate mutations:
        1. Record a game session
        2. Mutate a module (e.g. change strategy thresholds)
        3. Replay the session through the mutated module graph
        4. Compare output messages to the original
    """

    def __init__(
        self,
        path: Path,
        bus: MessageBus,
        *,
        speed: float = 0.0,
        channels: Optional[Set[str]] = None,
        time_range_ms: Optional[Tuple[int, int]] = None,
    ) -> None:
        """
        Args:
            path: Path to .jsonl or .jsonl.gz file.
            bus: Target bus to publish replayed messages to.
            speed: 0 = fast-forward, 1.0 = real-time, 2.0 = 2x speed.
            channels: If set, only replay messages on these channels.
            time_range_ms: (start_ms, end_ms) to slice the recording.
        """
        self._path = path
        self._bus = bus
        self._speed = speed
        self._channels = channels
        self._time_range = time_range_ms
        self._replayed = 0
        self._skipped = 0

    def _open_file(self):
        """Open JSONL or JSONL.GZ transparently."""
        if str(self._path).endswith(".gz"):
            return gzip.open(self._path, "rt", encoding="utf-8")
        return open(self._path, "r", encoding="utf-8")

    def _iter_messages(self) -> Iterator[ChannelMessage]:
        """Yield ChannelMessages from the recording file."""
        with self._open_file() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    self._skipped += 1
                    continue

                # Remove recorder metadata
                data.pop("_wall_clock_iso", None)

                # Channel filter
                ch = data.get("channel", "")
                if self._channels and ch not in self._channels:
                    self._skipped += 1
                    continue

                # Time range filter
                ts = data.get("timestamp_ms", 0)
                if self._time_range:
                    t_start, t_end = self._time_range
                    if ts < t_start or ts > t_end:
                        self._skipped += 1
                        continue

                try:
                    yield ChannelMessage(**data)
                except (TypeError, ValueError):
                    self._skipped += 1

    async def replay(self) -> Dict[str, Any]:
        """
        Run the replay, publishing messages to the bus.

        Returns summary dict with replayed count, skipped, duration.
        """
        start_wall = time.monotonic()
        prev_msg_ts: Optional[int] = None

        for msg in self._iter_messages():
            # Timing control
            if self._speed > 0 and prev_msg_ts is not None:
                delta_ms = msg.timestamp_ms - prev_msg_ts
                if delta_ms > 0:
                    await asyncio.sleep(
                        (delta_ms / 1000.0) / self._speed
                    )
            prev_msg_ts = msg.timestamp_ms

            self._bus.publish(msg)
            self._replayed += 1

        elapsed = time.monotonic() - start_wall
        return {
            "replayed": self._replayed,
            "skipped": self._skipped,
            "duration_sec": round(elapsed, 3),
            "path": str(self._path),
        }

    def replay_sync(self) -> Dict[str, Any]:
        """Synchronous fast-forward replay (no timing, no asyncio)."""
        for msg in self._iter_messages():
            self._bus.publish(msg)
            self._replayed += 1
        return {
            "replayed": self._replayed,
            "skipped": self._skipped,
        }


# ---------------------------------------------------------------------------
# Transport — the decorated bus with cross-cutting concerns
# ---------------------------------------------------------------------------
class Transport:
    """
    Production transport layer wrapping a MessageBus.

    Adds:
        - Per-channel rate limiting
        - Message recording
        - Dead letter queue
        - Publish/subscribe latency tracking
        - Channel health monitoring

    This is the object that gets injected into every component.
    Components call transport.publish(msg) and transport.subscribe(ch, cb).

    In Apollo terms, this is the combination of:
        - cyber::transport::Transport
        - cyber::record::RecordWriter
        - cyber::blocker::IntraReader/IntraWriter
    """

    def __init__(
        self,
        bus: Optional[MessageBus] = None,
        *,
        recording_path: Optional[Path] = None,
        default_rate_limit: float = 100.0,
    ) -> None:
        self._bus = bus or MessageBus()
        self._rate_limits: Dict[str, _RateLimit] = {}
        self._default_rate_limit = default_rate_limit
        self._dead_letters: Deque[DeadLetter] = deque(maxlen=500)
        self._latency_samples: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._publish_count = 0
        self._rate_limited_count = 0

        # Recorder
        self._recorder: Optional[MessageRecorder] = None
        if recording_path:
            self._recorder = MessageRecorder(recording_path)
            self._recorder.start()

    # -- Rate limiting --------------------------------------------------

    def set_rate_limit(self, channel: str, max_per_sec: float) -> None:
        """Set a custom rate limit for a specific channel."""
        self._rate_limits[channel] = _RateLimit(
            max_per_sec=max_per_sec,
            tokens=max_per_sec,
            last_refill_ms=_monotonic_ms(),
        )

    # -- Publish --------------------------------------------------------

    def publish(self, msg: ChannelMessage) -> int:
        """
        Publish with rate limiting, recording, and latency tracking.

        Returns number of subscribers notified (0 if rate-limited).
        """
        now = _monotonic_ms()

        # Rate limit check
        limiter = self._rate_limits.get(msg.channel)
        if limiter is None and self._default_rate_limit > 0:
            limiter = _RateLimit(
                max_per_sec=self._default_rate_limit,
                tokens=self._default_rate_limit,
                last_refill_ms=now,
            )
            self._rate_limits[msg.channel] = limiter

        if limiter and not limiter.allow(now):
            self._rate_limited_count += 1
            return 0

        self._publish_count += 1

        # Record
        if self._recorder is not None:
            self._recorder.record(msg)

        # Publish to underlying bus and track latency
        pre = _monotonic_ms()
        notified = self._bus.publish(msg)
        post = _monotonic_ms()

        latency_ms = post - pre
        self._latency_samples[msg.channel].append(latency_ms)

        # Pattern subscribers (if any)
        if hasattr(self._bus, "_pattern_subs"):
            for prefix, cb in self._bus._pattern_subs:
                if msg.channel.startswith(prefix):
                    try:
                        cb(msg)
                        notified += 1
                    except Exception as exc:
                        self._dead_letters.append(DeadLetter(
                            message=msg,
                            error=str(exc),
                            failed_subscriber=f"pattern:{prefix}",
                        ))

        return notified

    # -- Subscribe (delegate) -------------------------------------------

    def subscribe(
        self,
        channel: str,
        callback: Callable[[ChannelMessage], None],
    ) -> Callable[[], None]:
        """Subscribe to a channel (delegates to inner bus)."""
        return self._bus.subscribe(channel, callback)

    def subscribe_pattern(
        self,
        prefix: str,
        callback: Callable[[ChannelMessage], None],
    ) -> Callable[[], None]:
        """Subscribe to a channel prefix."""
        return self._bus.subscribe_pattern(prefix, callback)

    # -- Read (delegate) ------------------------------------------------

    def latest(self, channel: str) -> Optional[ChannelMessage]:
        return self._bus.latest(channel)

    def latest_payload(self, channel: str, default: Any = None) -> Any:
        return self._bus.latest_payload(channel, default)

    def history(
        self, channel: str, last_n: Optional[int] = None,
    ) -> List[ChannelMessage]:
        return self._bus.history(channel, last_n)

    # -- Recording control ----------------------------------------------

    def start_recording(self, path: Path) -> None:
        """Start a new recording session."""
        if self._recorder is not None:
            self._recorder.stop()
        self._recorder = MessageRecorder(path)
        self._recorder.start()

    def stop_recording(self) -> Optional[Dict[str, Any]]:
        """Stop recording and return summary."""
        if self._recorder is None:
            return None
        return self._recorder.stop()

    def compress_recording(self) -> Optional[Path]:
        """Compress the finished recording."""
        if self._recorder is None:
            return None
        return self._recorder.compress()

    # -- Diagnostics ----------------------------------------------------

    def dead_letters(self, last_n: int = 50) -> List[Dict[str, Any]]:
        """Get recent dead letters for debugging."""
        return [
            {
                "channel": dl.message.channel,
                "error": dl.error,
                "subscriber": dl.failed_subscriber,
                "age_ms": _monotonic_ms() - dl.timestamp_ms,
            }
            for dl in list(self._dead_letters)[-last_n:]
        ]

    def channel_latencies(self) -> Dict[str, Dict[str, float]]:
        """Per-channel publish latency statistics (ms)."""
        result = {}
        for ch, samples in self._latency_samples.items():
            if not samples:
                continue
            s = sorted(samples)
            result[ch] = {
                "p50": s[len(s) // 2],
                "p95": s[int(len(s) * 0.95)],
                "p99": s[int(len(s) * 0.99)] if len(s) >= 100 else s[-1],
                "max": s[-1],
                "count": len(s),
            }
        return result

    def stats(self) -> Dict[str, Any]:
        """Full transport statistics."""
        return {
            "publish_count": self._publish_count,
            "rate_limited_count": self._rate_limited_count,
            "dead_letter_count": len(self._dead_letters),
            "recording_active": self._recorder is not None
                                and self._recorder._started,
            "bus_stats": self._bus.stats(),
            "latencies": self.channel_latencies(),
        }

    def clear(self) -> None:
        """Full reset."""
        self._bus.clear()
        self._dead_letters.clear()
        self._latency_samples.clear()
        self._publish_count = 0
        self._rate_limited_count = 0

    # -- Replay integration ---------------------------------------------

    def create_replayer(
        self,
        path: Path,
        *,
        speed: float = 0.0,
        channels: Optional[Set[str]] = None,
    ) -> MessageReplayer:
        """Create a replayer that publishes into this transport's bus."""
        return MessageReplayer(
            path, self._bus, speed=speed, channels=channels,
        )

    # -- Shutdown -------------------------------------------------------

    def shutdown(self) -> Dict[str, Any]:
        """
        Graceful shutdown: stop recording, flush, return final stats.
        Called by the orchestrator on process exit.
        """
        final_stats = self.stats()
        if self._recorder is not None:
            rec_stats = self._recorder.stop()
            final_stats["recording_summary"] = rec_stats
        return final_stats

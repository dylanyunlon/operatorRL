"""
Session Capture Pipeline — buffered ingestion and dispatch.

Captures HTTPSessions from FiddlerBridgeClient, buffers them, applies filters,
and dispatches to registered callbacks when threshold is reached or flush is called.

Location: extensions/fiddler-bridge/src/fiddler_bridge/session_capture.py
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from fiddler_bridge.client import HTTPSession

logger = logging.getLogger(__name__)

# Type alias for capture callbacks
CaptureCallback = Callable[["CaptureEvent", ], Awaitable[None]]


@dataclass
class CaptureConfig:
    """Configuration for the capture pipeline.

    Attributes:
        buffer_threshold: Number of sessions to buffer before auto-flush.
        flush_interval_s: Max seconds between flushes (0 = only threshold-based).
        url_filter: Glob pattern; only matching URLs are ingested. Empty = all.
    """
    buffer_threshold: int = 100
    flush_interval_s: float = 0.0
    url_filter: str = ""


@dataclass
class CaptureEvent:
    """A batch of captured sessions dispatched to callbacks."""
    sessions: list[HTTPSession]
    timestamp: float = field(default_factory=time.time)
    batch_id: int = 0


@dataclass
class CaptureStats:
    """Aggregate statistics for the capture pipeline."""
    captured_count: int = 0
    dispatched_count: int = 0
    error_count: int = 0
    bytes_total: int = 0


class SessionCapturePipeline:
    """Production-grade buffered capture pipeline.

    Usage:
        pipeline = SessionCapturePipeline(config=CaptureConfig(buffer_threshold=50))
        pipeline.register_callback(my_handler)
        await pipeline.ingest(session)
        ...
        await pipeline.shutdown()
    """

    def __init__(self, config: CaptureConfig | None = None) -> None:
        self._config = config or CaptureConfig()
        self._buffer: list[HTTPSession] = []
        self._callbacks: list[CaptureCallback] = []
        self._stats = CaptureStats()
        self._running = False
        self._batch_counter = 0
        self._lock = asyncio.Lock()

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> CaptureStats:
        return self._stats

    def register_callback(self, callback: CaptureCallback) -> None:
        """Register an async callback for dispatched capture events."""
        self._callbacks.append(callback)

    async def ingest(self, session: HTTPSession) -> None:
        """Ingest a single HTTP session into the pipeline."""
        # Apply URL filter
        if self._config.url_filter:
            if not fnmatch.fnmatch(session.url, self._config.url_filter):
                return

        async with self._lock:
            self._buffer.append(session)
            self._stats.captured_count += 1
            self._stats.bytes_total += len(session.request_body) + len(session.response_body)

        # Auto-flush when threshold reached
        if len(self._buffer) >= self._config.buffer_threshold:
            await self.flush()

    async def flush(self) -> None:
        """Flush the buffer: dispatch all buffered sessions to callbacks."""
        async with self._lock:
            if not self._buffer:
                return
            sessions = list(self._buffer)
            self._buffer.clear()

        self._batch_counter += 1
        event = CaptureEvent(
            sessions=sessions,
            timestamp=time.time(),
            batch_id=self._batch_counter,
        )

        for cb in self._callbacks:
            try:
                await cb(event)
                self._stats.dispatched_count += len(sessions)
            except Exception as e:
                logger.error("Callback %s failed: %s", cb.__name__ if hasattr(cb, '__name__') else cb, e)
                self._stats.error_count += 1

    async def shutdown(self) -> None:
        """Gracefully shutdown: flush remaining buffer."""
        await self.flush()
        self._running = False
        logger.info("SessionCapturePipeline shutdown. Stats: %s", self._stats)

"""
Fiddler Live Capture — HTTP stream capture + filter + buffer.

Real-time HTTP traffic capture engine that connects to Fiddler's proxy
output, filters packets by URL pattern / content-type, and stores them
in a bounded ring buffer for downstream consumers (training pipeline,
anomaly detector, replay recorder).

Location: extensions/fiddler_bridge/src/fiddler_live_capture.py

Reference (拿来主义):
  - Akagi/mitm/bridge/majsoul/liqi.py: struct.unpack + protobuf parsing loop
  - Akagi/mitmproxy_addon.py: mitmproxy request/response interception
  - Seraphine/app/lol/connector.py: retry decorator, PastRequest tracking
  - Seraphine/app/lol/listener.py: LolProcessExistenceListener thread loop
  - extensions/fiddler-bridge/src/fiddler_packet_parser.py: packet categorization
  - extensions/fiddler-bridge/src/fiddler_session_manager.py: session lifecycle

Design Notes (Knuth-level critique):
  User perspective:
    - Ring buffer prevents OOM when game produces high-throughput traffic
    - URL/content-type filters reduce noise before it hits downstream
    - Thread safety ensures concurrent ingest from Fiddler callback threads
  System perspective:
    - Lock granularity is per-buffer, not global — avoids contention
    - Evolution callback fires on every capture for self-monitoring
    - Statistics are O(1) reads — no aggregation on access path
"""

from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_EVOLUTION_KEY: str = "extensions.fiddler_bridge.fiddler_live_capture.v1"

# ---------------------------------------------------------------------------
# Default configuration constants
# ---------------------------------------------------------------------------
_DEFAULT_BUFFER_CAPACITY: int = 1000
_DEFAULT_URL_PATTERNS: List[str] = []
_DEFAULT_CONTENT_TYPES: List[str] = []


class CaptureStats:
    """Accumulator for capture statistics.

    Thread-safe counters for total captures, filtered-out packets,
    buffer evictions, and error events.

    Reference: Seraphine PastRequest tracking pattern — lightweight
    per-request bookkeeping without deep copies.
    """

    __slots__ = (
        "_lock",
        "_capture_count",
        "_filtered_count",
        "_eviction_count",
        "_error_count",
        "_start_time",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._capture_count: int = 0
        self._filtered_count: int = 0
        self._eviction_count: int = 0
        self._error_count: int = 0
        self._start_time: float = time.time()

    # ---- increment helpers -------------------------------------------------
    def inc_capture(self) -> None:
        with self._lock:
            self._capture_count += 1

    def inc_filtered(self) -> None:
        with self._lock:
            self._filtered_count += 1

    def inc_eviction(self) -> None:
        with self._lock:
            self._eviction_count += 1

    def inc_error(self) -> None:
        with self._lock:
            self._error_count += 1

    # ---- read helpers ------------------------------------------------------
    @property
    def capture_count(self) -> int:
        with self._lock:
            return self._capture_count

    @property
    def filtered_count(self) -> int:
        with self._lock:
            return self._filtered_count

    @property
    def eviction_count(self) -> int:
        with self._lock:
            return self._eviction_count

    @property
    def error_count(self) -> int:
        with self._lock:
            return self._error_count

    @property
    def start_time(self) -> float:
        return self._start_time

    def snapshot(self) -> Dict[str, Any]:
        """Return a copy of all stats as a plain dict."""
        with self._lock:
            return {
                "capture_count": self._capture_count,
                "filtered_count": self._filtered_count,
                "eviction_count": self._eviction_count,
                "error_count": self._error_count,
                "start_time": self._start_time,
                "uptime": time.time() - self._start_time,
            }


class RingBuffer:
    """Bounded ring buffer backed by collections.deque.

    When capacity is reached, the oldest item is evicted silently.
    All operations are protected by a reentrant lock so multiple
    Fiddler callback threads can push concurrently.

    Reference: Akagi's mitmproxy addon queues captured frames into
    a bounded list — we formalise this as a first-class data structure.
    """

    __slots__ = ("_lock", "_buf", "_capacity")

    def __init__(self, capacity: int = _DEFAULT_BUFFER_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        self._capacity = capacity
        self._buf: Deque[Dict[str, Any]] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def push(self, item: Dict[str, Any]) -> bool:
        """Push *item* into the ring buffer.

        Returns True if an older item was evicted to make room.
        """
        with self._lock:
            evicted = len(self._buf) == self._capacity
            self._buf.append(item)
            return evicted

    def snapshot(self) -> List[Dict[str, Any]]:
        """Return a shallow copy of the buffer contents (oldest first)."""
        with self._lock:
            return list(self._buf)

    def clear(self) -> int:
        """Clear the buffer and return the number of items dropped."""
        with self._lock:
            n = len(self._buf)
            self._buf.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            return len(self._buf)


class URLFilter:
    """Compiled URL pattern filter.

    Each pattern is compiled to a ``re.Pattern`` at construction time
    so that hot-path matching is fast.  An empty pattern list means
    "accept everything".

    Reference: Akagi MITM addon URL-based routing — game traffic is
    identified by URL substring before any deeper parsing.
    """

    __slots__ = ("_patterns",)

    def __init__(self, patterns: Sequence[str] | None = None) -> None:
        if patterns:
            self._patterns = [re.compile(p, re.IGNORECASE) for p in patterns]
        else:
            self._patterns = []

    def matches(self, url: str) -> bool:
        """Return True if *url* matches any registered pattern."""
        if not self._patterns:
            return True  # no filter → accept all
        return any(p.search(url) for p in self._patterns)


class ContentTypeFilter:
    """Accept-list filter for HTTP Content-Type header values.

    Empty filter means "accept everything".  Matching is case-insensitive
    prefix — ``application/json`` matches ``application/json; charset=utf-8``.
    """

    __slots__ = ("_types",)

    def __init__(self, types: Sequence[str] | None = None) -> None:
        self._types = [t.lower() for t in types] if types else []

    def matches(self, headers: Dict[str, str] | None) -> bool:
        if not self._types:
            return True
        if not headers:
            return True  # no headers → can't filter → accept
        ct = headers.get("content-type", headers.get("Content-Type", "")).lower()
        return any(ct.startswith(t) for t in self._types)


# ===========================================================================
# Main class
# ===========================================================================

class FiddlerLiveCapture:
    """HTTP stream capture engine backed by Fiddler.

    Lifecycle:
        1. Instantiate with optional filters and buffer capacity.
        2. Call ``start()`` to mark the capture session as active.
        3. Feed packets via ``ingest(packet)`` — typically called from
           a Fiddler MCP server callback or a polling loop.
        4. Downstream consumers read from ``get_buffer()`` or register
           an ``on_capture`` callback.
        5. Call ``stop()`` when the game session ends.

    Attributes:
        buffer_capacity: Maximum ring-buffer size.
        is_running: Whether the capture session is active.
        capture_count: Total packets ingested (including evicted ones).
        on_capture: Optional callback ``(packet) -> None``.
        evolution_callback: Optional callback ``(event_dict) -> None``.

    Reference (拿来主义):
        - Seraphine LolProcessExistenceListener.run() — thread loop pattern
        - Akagi mitmproxy_addon: intercept → filter → enqueue
        - extensions/fiddler-bridge/src/fiddler_packet_parser.py: packet dict schema
    """

    def __init__(
        self,
        *,
        url_patterns: Sequence[str] | None = None,
        content_types: Sequence[str] | None = None,
        buffer_capacity: int = _DEFAULT_BUFFER_CAPACITY,
    ) -> None:
        # --- Filters ---
        self._url_filter = URLFilter(url_patterns)
        self._content_type_filter = ContentTypeFilter(content_types)

        # --- Storage ---
        self._buffer = RingBuffer(capacity=buffer_capacity)
        self._stats = CaptureStats()

        # --- State ---
        self._running = False
        self._lock = threading.Lock()

        # --- Callbacks ---
        self.on_capture: Optional[Callable[[Dict[str, Any]], None]] = None
        self.evolution_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def buffer_capacity(self) -> int:
        return self._buffer.capacity

    @property
    def capture_count(self) -> int:
        return self._stats.capture_count

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Mark the capture session as active.

        Mirrors Seraphine's LolProcessExistenceListener — signal-based
        lifecycle rather than direct thread management, so the caller
        decides *how* to drive the capture (polling, callback, thread).
        """
        with self._lock:
            if self._running:
                logger.warning("FiddlerLiveCapture.start() called while already running")
                return
            self._running = True
            logger.info("FiddlerLiveCapture started")
            self._fire_evolution({"action": "start"})

    def stop(self) -> None:
        """Mark the capture session as inactive."""
        with self._lock:
            if not self._running:
                logger.warning("FiddlerLiveCapture.stop() called while not running")
                return
            self._running = False
            logger.info("FiddlerLiveCapture stopped — %d packets captured", self.capture_count)
            self._fire_evolution({"action": "stop", "total": self.capture_count})

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def should_capture(self, packet: Dict[str, Any]) -> bool:
        """Evaluate whether *packet* passes all configured filters.

        Returns True if the packet should be ingested; False otherwise.
        The URL filter and content-type filter are applied in sequence —
        short-circuiting on the first rejection.

        Reference: Akagi MITM addon checks URL before parsing body.
        """
        url = packet.get("url", "")
        if not self._url_filter.matches(url):
            return False
        headers = packet.get("headers", None)
        if not self._content_type_filter.matches(headers):
            return False
        return True

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest(self, packet: Dict[str, Any]) -> bool:
        """Ingest a single captured HTTP packet.

        Applies filters, pushes into the ring buffer, fires callbacks.
        Returns True if the packet was accepted; False if filtered out.

        Thread-safe — may be called from multiple Fiddler callback threads.

        Reference: Akagi mitmproxy addon response() → self._queue.put()
        """
        # --- Filter check ---
        if not self.should_capture(packet):
            self._stats.inc_filtered()
            return False

        # --- Timestamp injection ---
        if "capture_ts" not in packet:
            packet["capture_ts"] = time.time()

        # --- Ring buffer push ---
        evicted = self._buffer.push(packet)
        if evicted:
            self._stats.inc_eviction()

        # --- Bookkeeping ---
        self._stats.inc_capture()

        # --- User callback ---
        cb = self.on_capture
        if cb is not None:
            try:
                cb(packet)
            except Exception:
                logger.exception("on_capture callback raised")
                self._stats.inc_error()

        # --- Evolution ---
        self._fire_evolution({"action": "capture", "url": packet.get("url", "")})

        return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_buffer(self) -> List[Dict[str, Any]]:
        """Return a snapshot of the ring buffer (oldest first)."""
        return self._buffer.snapshot()

    def get_stats(self) -> Dict[str, Any]:
        """Return capture statistics.

        Contains capture_count, filtered_count, eviction_count, error_count,
        buffer_size, start_time, uptime.
        """
        s = self._stats.snapshot()
        s["buffer_size"] = len(self._buffer)
        return s

    def clear_buffer(self) -> int:
        """Clear the ring buffer and return the count of dropped items."""
        n = self._buffer.clear()
        self._fire_evolution({"action": "clear_buffer", "dropped": n})
        return n

    # ------------------------------------------------------------------
    # Evolution integration
    # ------------------------------------------------------------------

    def _fire_evolution(self, event: Dict[str, Any]) -> None:
        """Dispatch an evolution event if a callback is registered."""
        event.setdefault("component", _EVOLUTION_KEY)
        event.setdefault("ts", time.time())
        cb = self.evolution_callback
        if cb is not None:
            try:
                cb(event)
            except Exception:
                logger.exception("evolution_callback raised")

    # ------------------------------------------------------------------
    # Batch ingest helpers
    # ------------------------------------------------------------------

    def batch_ingest(self, packets: Sequence[Dict[str, Any]]) -> Dict[str, int]:
        """Ingest a batch of packets and return accept/reject counts.

        Convenience wrapper around ``ingest()`` for bulk replay or
        Fiddler session import scenarios.
        """
        accepted = 0
        rejected = 0
        for pkt in packets:
            if self.ingest(pkt):
                accepted += 1
            else:
                rejected += 1
        return {"accepted": accepted, "rejected": rejected}

    # ------------------------------------------------------------------
    # Serialization helpers (for persistence / replay recording)
    # ------------------------------------------------------------------

    def export_buffer_json(self) -> str:
        """Serialize current buffer to JSON string."""
        buf = self.get_buffer()
        return json.dumps(buf, ensure_ascii=False, default=str)

    def import_buffer_json(self, data: str) -> int:
        """Import packets from a JSON string into the buffer.

        Returns the number of packets imported.
        """
        packets = json.loads(data)
        if not isinstance(packets, list):
            raise TypeError("Expected a JSON array of packets")
        result = self.batch_ingest(packets)
        return result["accepted"]

    # ------------------------------------------------------------------
    # Debug / introspection
    # ------------------------------------------------------------------

    def describe(self) -> Dict[str, Any]:
        """Return a human-readable description of the capture state.

        Useful for dashboard rendering or health-check endpoints.
        """
        return {
            "component": _EVOLUTION_KEY,
            "running": self.is_running,
            "buffer_capacity": self.buffer_capacity,
            "buffer_fill": len(self._buffer),
            "stats": self.get_stats(),
            "url_filter_active": bool(self._url_filter._patterns),
            "content_type_filter_active": bool(self._content_type_filter._types),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"FiddlerLiveCapture(running={self.is_running}, "
            f"captured={self.capture_count}, "
            f"buf={len(self._buffer)}/{self.buffer_capacity})"
        )


# ---------------------------------------------------------------------------
# Module-level convenience — mirrors Seraphine's `connector = Connector()`
# ---------------------------------------------------------------------------

default_capture: FiddlerLiveCapture = FiddlerLiveCapture()
"""Module-level singleton.  Import and use as:

    from extensions.fiddler_bridge.src.fiddler_live_capture import default_capture
    default_capture.start()
"""

"""
CyberNode — In-process pub/sub message bus for component communication.
========================================================================

Apollo's ``cyber::Node`` is the central communication primitive: each
component gets a Node, creates Readers/Writers on named channels, and
exchanges protobuf messages.  We replicate this pattern in Python using
thread-safe queues and callbacks, giving us the same decoupled
perception→prediction→planning pipeline.

Architecture position:
    cyber/node/node.py   ← YOU ARE HERE
    ├─ cyber/message/channel.py  (channel registry)
    ├─ Used by every *_component.py via self.node
    └─ Provides Reader[T] / Writer[T] generics

Apollo reference:
    cyber/node/node.h   — ``CreateReader``, ``CreateWriter``
    cyber/node/reader.h — ``Observe()``, ``GetLatestObserved()``
    cyber/node/writer.h — ``Write(msg)``

Design notes:
    - Type-parameterized Reader/Writer via Python generics
    - Bounded queue per Reader to avoid memory blowup (back-pressure)
    - Thread-safe: multiple components write/read concurrently
    - Channel is a string key; messages are arbitrary Python objects
    - Supports both callback-based and polling-based consumption
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import (
    Any, Callable, Deque, Dict, Generic, List,
    Optional, Set, Tuple, TypeVar,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ─── Constants ───────────────────────────────────────────────────────────────

_DEFAULT_QUEUE_SIZE: int = 64
_CHANNEL_REGISTRY_LOCK = threading.Lock()
_GLOBAL_CHANNELS: Dict[str, "_Channel"] = {}


# ─── Channel (internal) ─────────────────────────────────────────────────────

@dataclass
class _Subscriber:
    """Internal subscriber record attached to a channel."""
    reader_id: str
    queue: Deque[Tuple[float, Any]]
    max_size: int
    callback: Optional[Callable[[Any], None]]
    lock: threading.Lock = field(default_factory=threading.Lock)


class _Channel:
    """Internal channel: one per topic string.

    Manages fan-out to all subscribers.  Writing to a channel pushes
    a timestamped copy into every subscriber's bounded deque.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._subscribers: Dict[str, _Subscriber] = {}
        self._lock = threading.Lock()
        self._write_count: int = 0

    def add_subscriber(self, sub: _Subscriber) -> None:
        with self._lock:
            self._subscribers[sub.reader_id] = sub
            logger.debug(
                "Channel[%s] +subscriber %s (queue=%d)",
                self.name, sub.reader_id, sub.max_size,
            )

    def remove_subscriber(self, reader_id: str) -> None:
        with self._lock:
            self._subscribers.pop(reader_id, None)

    def publish(self, message: Any) -> int:
        """Fan-out message to all subscribers.

        Returns:
            Number of subscribers that received the message.
        """
        ts = time.monotonic()
        delivered = 0

        with self._lock:
            self._write_count += 1
            subs = list(self._subscribers.values())

        for sub in subs:
            with sub.lock:
                if len(sub.queue) >= sub.max_size:
                    sub.queue.popleft()  # drop oldest (back-pressure)
                sub.queue.append((ts, message))
            delivered += 1

            # Fire callback outside subscriber lock to avoid deadlock
            if sub.callback is not None:
                try:
                    sub.callback(message)
                except Exception:
                    logger.exception(
                        "Channel[%s] callback error for %s",
                        self.name, sub.reader_id,
                    )

        return delivered

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def write_count(self) -> int:
        return self._write_count


def _get_or_create_channel(name: str) -> _Channel:
    with _CHANNEL_REGISTRY_LOCK:
        if name not in _GLOBAL_CHANNELS:
            _GLOBAL_CHANNELS[name] = _Channel(name)
        return _GLOBAL_CHANNELS[name]


def reset_all_channels() -> None:
    """Clear all channels — used in tests."""
    with _CHANNEL_REGISTRY_LOCK:
        _GLOBAL_CHANNELS.clear()


# ─── Reader ──────────────────────────────────────────────────────────────────

class Reader(Generic[T]):
    """Read messages from a named channel.

    Supports two consumption patterns:

    1. **Callback**: pass ``callback=fn`` at creation; called on each
       new message (in the writer's thread).

    2. **Polling**: call ``Observe()`` then ``GetLatestObserved()``
       from your component's ``Proc()`` — mirrors Apollo's reader API.

    Example::

        reader = node.CreateReader("/lol/game_state", GameState)
        # In Proc():
        reader.Observe()
        state = reader.GetLatestObserved()
        if state is not None:
            process(state)
    """

    _counter: int = 0
    _counter_lock = threading.Lock()

    def __init__(
        self,
        channel_name: str,
        msg_type: type,
        pending_queue_size: int = _DEFAULT_QUEUE_SIZE,
        callback: Optional[Callable[[T], None]] = None,
        node_name: str = "",
    ) -> None:
        with Reader._counter_lock:
            Reader._counter += 1
            self._id = f"{node_name}:reader:{Reader._counter}"

        self._channel_name = channel_name
        self._msg_type = msg_type
        self._observed: Optional[T] = None
        self._observed_ts: float = 0.0

        self._sub = _Subscriber(
            reader_id=self._id,
            queue=collections.deque(maxlen=pending_queue_size),
            max_size=pending_queue_size,
            callback=callback,
        )

        channel = _get_or_create_channel(channel_name)
        channel.add_subscriber(self._sub)

    # ── Apollo-compatible API ────────────────────────────────────────────

    def Observe(self) -> None:
        """Latch the latest message from the queue.

        After calling this, ``GetLatestObserved()`` returns the
        most recent message.  This two-phase pattern matches Apollo's
        reader semantics.
        """
        with self._sub.lock:
            if self._sub.queue:
                self._observed_ts, self._observed = self._sub.queue[-1]

    def GetLatestObserved(self) -> Optional[T]:
        """Return the message latched by the last ``Observe()`` call."""
        return self._observed

    def GetLatestObservedTimestamp(self) -> float:
        """Monotonic timestamp of the latest observed message."""
        return self._observed_ts

    # ── Convenience ──────────────────────────────────────────────────────

    @property
    def channel_name(self) -> str:
        return self._channel_name

    @property
    def pending_count(self) -> int:
        """Number of unread messages in the queue."""
        with self._sub.lock:
            return len(self._sub.queue)

    @property
    def has_message(self) -> bool:
        with self._sub.lock:
            return len(self._sub.queue) > 0

    def drain(self) -> List[T]:
        """Pop and return all queued messages (oldest first)."""
        with self._sub.lock:
            msgs = [msg for _, msg in self._sub.queue]
            self._sub.queue.clear()
        return msgs

    def close(self) -> None:
        """Unsubscribe from the channel."""
        channel = _get_or_create_channel(self._channel_name)
        channel.remove_subscriber(self._id)

    def __repr__(self) -> str:
        return (
            f"<Reader channel={self._channel_name!r} "
            f"pending={self.pending_count}>"
        )


# ─── Writer ──────────────────────────────────────────────────────────────────

class Writer(Generic[T]):
    """Write messages to a named channel.

    Example::

        writer = node.CreateWriter("/lol/game_state", GameState)
        writer.Write(current_state)  # fans out to all readers
    """

    def __init__(
        self,
        channel_name: str,
        msg_type: type,
        node_name: str = "",
    ) -> None:
        self._channel_name = channel_name
        self._msg_type = msg_type
        self._channel = _get_or_create_channel(channel_name)
        self._write_count: int = 0

    def Write(self, message: T) -> int:
        """Publish a message to the channel.

        Args:
            message: The message to publish.

        Returns:
            Number of subscribers that received the message.
        """
        self._write_count += 1
        return self._channel.publish(message)

    @property
    def channel_name(self) -> str:
        return self._channel_name

    @property
    def write_count(self) -> int:
        return self._write_count

    def __repr__(self) -> str:
        return (
            f"<Writer channel={self._channel_name!r} "
            f"writes={self._write_count}>"
        )


# ─── Node ────────────────────────────────────────────────────────────────────

class CyberNode:
    """Central communication hub for a component.

    Each component owns one CyberNode.  The node creates typed
    Readers and Writers on named channels.

    Example::

        node = CyberNode("perception")
        writer = node.CreateWriter("/lol/game_state", dict)
        reader = node.CreateReader("/lol/raw_events", dict)
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._readers: List[Reader] = []
        self._writers: List[Writer] = []

    @property
    def name(self) -> str:
        return self._name

    def CreateReader(
        self,
        channel_name: str,
        msg_type: type = object,
        pending_queue_size: int = _DEFAULT_QUEUE_SIZE,
        callback: Optional[Callable] = None,
    ) -> Reader:
        """Create a Reader subscribed to the given channel.

        Args:
            channel_name: Topic string (e.g. "/lol/game_state").
            msg_type: Expected message type (for documentation).
            pending_queue_size: Max buffered messages.
            callback: Optional per-message callback.

        Returns:
            A new Reader instance.
        """
        reader: Reader = Reader(
            channel_name=channel_name,
            msg_type=msg_type,
            pending_queue_size=pending_queue_size,
            callback=callback,
            node_name=self._name,
        )
        self._readers.append(reader)
        logger.info(
            "[Node:%s] Created reader on %s", self._name, channel_name
        )
        return reader

    def CreateWriter(
        self,
        channel_name: str,
        msg_type: type = object,
    ) -> Writer:
        """Create a Writer for the given channel.

        Args:
            channel_name: Topic string.
            msg_type: Message type (for documentation).

        Returns:
            A new Writer instance.
        """
        writer: Writer = Writer(
            channel_name=channel_name,
            msg_type=msg_type,
            node_name=self._name,
        )
        self._writers.append(writer)
        logger.info(
            "[Node:%s] Created writer on %s", self._name, channel_name
        )
        return writer

    def shutdown(self) -> None:
        """Close all readers and writers."""
        for r in self._readers:
            r.close()
        self._readers.clear()
        self._writers.clear()
        logger.info("[Node:%s] Shutdown", self._name)

    def channel_summary(self) -> Dict[str, Any]:
        """Return a summary of connected channels."""
        return {
            "node": self._name,
            "readers": [
                {"channel": r.channel_name, "pending": r.pending_count}
                for r in self._readers
            ],
            "writers": [
                {"channel": w.channel_name, "writes": w.write_count}
                for w in self._writers
            ],
        }

    def __repr__(self) -> str:
        return (
            f"<CyberNode name={self._name!r} "
            f"readers={len(self._readers)} writers={len(self._writers)}>"
        )

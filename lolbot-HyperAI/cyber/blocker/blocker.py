"""
Blocker — Per-channel message buffer with subscriber notification.
===================================================================

Apollo reference: ``cyber/blocker/blocker.h``

A Blocker holds the latest N messages for a channel and notifies
registered callbacks when new messages arrive.  This is the core of
Apollo's intra-process pub/sub — faster than going through transport
when publisher and subscriber are in the same process.

Key difference from Transport:
    - Transport: serializes → shared memory → deserializes (cross-process)
    - Blocker: direct Python object reference sharing (in-process only)

Claude27: New file.
Location: lolbot-HyperAI/cyber/blocker/blocker.py
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Generic, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class BlockerAttr:
    """Blocker configuration attributes.

    Apollo equivalent: ``cyber::blocker::BlockerAttr``
    """
    channel_name: str = ""
    capacity: int = 16  # number of messages to buffer
    channel_id: int = 0


@dataclass
class BlockerStats:
    """Runtime statistics for a Blocker instance."""
    publish_count: int = 0
    subscriber_notify_count: int = 0
    subscriber_error_count: int = 0
    drop_count: int = 0
    last_publish_time: float = 0.0


class Blocker(Generic[T]):
    """Per-channel message buffer with subscriber notification.

    Apollo equivalent: ``cyber::blocker::Blocker<T>``

    Usage::

        blocker = Blocker[GameSnapshot](BlockerAttr(
            channel_name="/lol/game_state", capacity=16,
        ))
        blocker.subscribe("perception", my_callback)
        blocker.publish(snapshot)  # notifies all subscribers
        latest = blocker.get_latest_observed()
    """

    def __init__(self, attr: BlockerAttr) -> None:
        self._attr = attr
        self._buf: Deque[T] = deque(maxlen=max(1, attr.capacity))
        self._lock = threading.RLock()
        self._subscribers: Dict[str, Callable[[T], None]] = {}
        self._observed: Optional[T] = None
        self.stats = BlockerStats()

    @property
    def channel_name(self) -> str:
        return self._attr.channel_name

    @property
    def capacity(self) -> int:
        return self._attr.capacity

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._buf)

    def publish(self, msg: T) -> None:
        """Publish a message — store in buffer and notify subscribers.

        Apollo equivalent: ``Blocker::Publish(msg)``
        """
        with self._lock:
            if len(self._buf) >= self._attr.capacity:
                self.stats.drop_count += 1
            self._buf.append(msg)
            self._observed = msg
            self.stats.publish_count += 1
            self.stats.last_publish_time = time.time()
            # Snapshot subscribers under lock
            subs = list(self._subscribers.items())

        # Notify outside lock to avoid deadlock
        for sub_name, callback in subs:
            try:
                callback(msg)
                self.stats.subscriber_notify_count += 1
            except Exception as exc:
                self.stats.subscriber_error_count += 1
                logger.error(
                    "[Blocker:%s] Subscriber %r error: %s: %s",
                    self._attr.channel_name, sub_name,
                    type(exc).__name__, exc,
                )

    def observe(self) -> None:
        """Latch the latest message for ``get_latest_observed()``.

        Apollo equivalent: ``reader->Observe()``
        In our implementation, observed is always latest, but this
        method exists for API compatibility.
        """
        with self._lock:
            if self._buf:
                self._observed = self._buf[-1]

    def get_latest_observed(self) -> Optional[T]:
        """Get the latest observed message.

        Apollo equivalent: ``reader->GetLatestObserved()``
        """
        with self._lock:
            return self._observed

    def get_latest(self) -> Optional[T]:
        """Get the most recent message in the buffer."""
        with self._lock:
            return self._buf[-1] if self._buf else None

    def get_oldest(self) -> Optional[T]:
        """Get the oldest message in the buffer."""
        with self._lock:
            return self._buf[0] if self._buf else None

    def subscribe(self, name: str, callback: Callable[[T], None]) -> bool:
        """Register a subscriber callback.

        Args:
            name: Unique subscriber identifier.
            callback: Called with each new message.

        Returns True if subscribed, False if name already registered.
        """
        with self._lock:
            if name in self._subscribers:
                logger.warning(
                    "[Blocker:%s] Subscriber %r already exists",
                    self._attr.channel_name, name,
                )
                return False
            self._subscribers[name] = callback
        return True

    def unsubscribe(self, name: str) -> bool:
        """Remove a subscriber by name."""
        with self._lock:
            return self._subscribers.pop(name, None) is not None

    def clear_subscribers(self) -> int:
        """Remove all subscribers. Returns count removed."""
        with self._lock:
            count = len(self._subscribers)
            self._subscribers.clear()
            return count

    def clear_messages(self) -> int:
        """Clear the message buffer. Returns count removed."""
        with self._lock:
            count = len(self._buf)
            self._buf.clear()
            self._observed = None
            return count

    def snapshot(self) -> dict:
        """Return serializable status."""
        with self._lock:
            return {
                "channel_name": self._attr.channel_name,
                "capacity": self._attr.capacity,
                "size": len(self._buf),
                "subscriber_count": len(self._subscribers),
                "subscriber_names": list(self._subscribers.keys()),
                "stats": {
                    "publish_count": self.stats.publish_count,
                    "subscriber_notify_count": self.stats.subscriber_notify_count,
                    "subscriber_error_count": self.stats.subscriber_error_count,
                    "drop_count": self.stats.drop_count,
                    "last_publish_time": self.stats.last_publish_time,
                },
            }

    def __repr__(self) -> str:
        return (
            f"<Blocker channel={self._attr.channel_name!r} "
            f"size={self.size}/{self._attr.capacity}>"
        )

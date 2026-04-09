"""
IntraReader — In-process read handle bound to a Blocker.
==========================================================

Apollo reference: ``cyber/blocker/intra_reader.h``

An IntraReader subscribes to a Blocker channel and provides the same
``Observe() / GetLatestObserved()`` API as the transport Reader, but
without serialization overhead.

Claude27: New file.
Location: lolbot-HyperAI/cyber/blocker/intra_reader.py
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Generic, Optional, TypeVar

from cyber.blocker.blocker import Blocker
from cyber.blocker.blocker_manager import BlockerManager

T = TypeVar("T")


class IntraReader(Generic[T]):
    """In-process read handle for a Blocker channel.

    Apollo equivalent: ``cyber::blocker::IntraReader<T>``

    Usage::

        reader = IntraReader[GameSnapshot]("/lol/game_state", "prediction")
        reader.observe()
        snapshot = reader.get_latest_observed()
    """

    def __init__(
        self,
        channel_name: str,
        subscriber_name: str,
        callback: Optional[Callable[[T], None]] = None,
        pending_queue_size: int = 16,
    ) -> None:
        self._channel_name = channel_name
        self._subscriber_name = subscriber_name
        self._callback = callback
        self._observed: Optional[T] = None
        self._last_receive_time: float = 0.0
        self._receive_count: int = 0
        self._lock = threading.Lock()

        # Register with BlockerManager
        mgr = BlockerManager.instance()
        self._blocker: Blocker = mgr.get_or_create(
            channel_name, capacity=pending_queue_size,
        )
        self._blocker.subscribe(subscriber_name, self._on_message)

    @property
    def channel_name(self) -> str:
        return self._channel_name

    def _on_message(self, msg: T) -> None:
        """Internal callback from Blocker.publish()."""
        with self._lock:
            self._observed = msg
            self._last_receive_time = time.time()
            self._receive_count += 1
        if self._callback is not None:
            self._callback(msg)

    def Observe(self) -> None:
        """Latch latest message (API compat with transport Reader).

        Apollo equivalent: ``reader->Observe()``
        For IntraReader this is a no-op since _on_message already
        latches, but we keep the method for interface compatibility.
        """
        self._blocker.observe()
        with self._lock:
            latest = self._blocker.get_latest_observed()
            if latest is not None:
                self._observed = latest

    def GetLatestObserved(self) -> Optional[T]:
        """Get the latest observed message.

        Apollo equivalent: ``reader->GetLatestObserved()``
        """
        with self._lock:
            return self._observed

    def is_stale(self, max_age_s: float = 3.0) -> bool:
        """Check if the last received message is stale."""
        with self._lock:
            if self._last_receive_time <= 0:
                return False  # never received = not stale (startup)
            return (time.time() - self._last_receive_time) > max_age_s

    def shutdown(self) -> None:
        """Unsubscribe from the blocker."""
        self._blocker.unsubscribe(self._subscriber_name)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "channel_name": self._channel_name,
                "subscriber_name": self._subscriber_name,
                "receive_count": self._receive_count,
                "last_receive_time": self._last_receive_time,
                "has_observed": self._observed is not None,
            }

    def __repr__(self) -> str:
        return (
            f"<IntraReader channel={self._channel_name!r} "
            f"sub={self._subscriber_name!r}>"
        )
